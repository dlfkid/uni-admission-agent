# Upgrade Reliability (Atomic Self-Update) — Design

**Date:** 2026-08-27
**Status:** Approved (pending spec review)
**Part 1 of 3** in the "v1.0 release readiness" series:
**upgrade reliability [this spec]** → unknown-university self-service loop →
release hygiene (coverage debt, real-site nightly regression, measured
success rate).

Sequenced first deliberately: the upgrade path is the delivery channel for
everything else. While it is broken, no improvement shipped for the other
two ever reaches an already-installed user — they stay on whatever version
they installed and come back to the maintainer, which is the exact loop v1.0
is meant to eliminate.

## 1. Problem

The target user for v1.0 has no development background and drives the tool
through an agent CLI (Claude Code / Codex / OpenCode) that reads this repo's
skills. The requirement is: *the maintainer fixes something, the user runs
one command, and continues working.* Four defects make that impossible
today.

### 1.1 Version comparison is a string compare (shipped, live)

[`src/services/upgrade.py:318`](../../../src/services/upgrade.py):

```python
is_newer = latest_version != current_version and latest_version > current_version
```

Measured behaviour:

| Installed | Latest on GitHub | `is_newer` | User sees |
|---|---|---|---|
| `v0.9.0` | `v0.10.0` | `False` | ✅ Already on latest version |
| `v0.8.0` | `v0.10.0` | `False` | ✅ Already on latest version |
| `v0.10.0` | `v0.9.1` | `True` | silently **downgrades** |

`"v0.10.0" > "v0.9.0"` is `False` (character-wise, `1` < `9`). Every user on
`0.8.x`/`0.9.x` is permanently pinned: `upgrade` reports success-with-nothing-
to-do, and `upgrade --check` prints "Already on latest version." The only
escape is `--force`, which no skill or doc mentions.

### 1.2 Two competing upgrade paths, and the agent is pointed at the unsafe one

| Path | Used by | State |
|---|---|---|
| `adm-agent upgrade` | the CLI command; has backup, rollback-on-replace-failure, post-upgrade `db-migrate` with `repair --auto` fallback | dead-ended by §1.1 |
| `uni-admission-install` §3 | **what the agent actually executes**: re-run fresh install — `curl` + `tar -xzf -C ~/.uni-agent/bin --strip-components=1` over the live install | no backup, no atomicity, no post-install verification, no `db-migrate` |

A truncated download or an interrupted extract on the second path leaves a
corrupt install directory with nothing to recover from. The product's own
upgrade command is bypassed, so its (partially correct) safety logic never
runs.

### 1.3 Replacement is not atomic and rollback is incomplete

`upgrade_artifact` ordering is: download → backup **exe only** →
`sync_installation_payload` (which `rmtree`s and overwrites `_internal` and
every other bundled resource) → `replace_executable`. If the executable
replacement fails, `replace_executable` restores the exe from backup but
nothing restores the payload — the install is left as *new resources + old
program*, a state no test covers and a non-technical user cannot diagnose.
`tarfile.extractall` is also called without a member filter (path-traversal
exposure, and the default changes in Python 3.14).

There is no checksum verification on the downloaded artifact, no smoke test
of the new binary before it becomes the live one, and no user-facing
rollback command.

### 1.4 Zero test coverage

`tests/` contains no `test_upgrade*` file. `src/services/upgrade.py`
measures **17% line coverage** — the lowest in the project, on the one code
path that can break an already-installed user's environment in a way they
cannot repair themselves.

## 2. Scope

### In scope

Rework `adm-agent upgrade` into an atomic, verified, reversible,
agent-drivable operation; converge the two upgrade paths onto it; give it
test coverage and CI proof.

**Upgrade semantics (confirmed with user): file-level only.** `upgrade`
atomically replaces files, self-verifies, and rolls back on failure. It does
**not** orchestrate the server lifecycle: if the server is running, it
refuses with a distinct exit code and lets the agent stop/restart it per
skill instructions.

**Primary interface (confirmed with user): the agent.** The user talks to an
agent CLI that reads the shipped skills; the frontend is for result display.
Therefore the CLI's contract with the agent — stable JSON, stable exit codes
— is a first-class requirement, not a nicety. The agent must never have to
parse emoji prose to decide what to do next.

### Out of scope

- **Server lifecycle orchestration** (stop → upgrade → restart → health
  poll). Explicitly deferred by user decision; the agent owns it.
- Delta/incremental updates; upgrade downloads the full artifact.
- Chrome extension auto-update (unchanged: manual download, per README).
- Plugin/skill self-update (`§4 Update the plugin itself` in the install
  skill) — separate mechanism, untouched.
- The other two v1.0 workstreams (unknown-university self-service, release
  hygiene). Each gets its own spec.

## 3. Install layout

### 3.1 Current layout

The install tree and the writable data tree are **the same directory**
(install skill §1.4 puts the binary in `~/.uni-agent/bin/`;
[`paths.py:70`](../../../src/core/paths.py) resolves frozen-mode writable
data to `~/.uni-agent/`):

```
~/.uni-agent/
├── bin/adm-agent            # PyInstaller onedir exe
├── bin/_internal/           # bundled resources
├── .env                     # user's LLM keys
├── admission.db             # user's data
└── ...                      # schemas/, strategy cache, logs
```

`~/.local/bin/adm-agent` is a symlink to `~/.uni-agent/bin/adm-agent`
(install skill §1.5).

### 3.2 New layout

```
~/.uni-agent/
├── versions/
│   ├── v0.10.0/             # last-good, retained
│   │   ├── adm-agent
│   │   └── _internal/
│   └── v0.11.0/             # newly activated
├── staging/<tmp>/           # download + extract + verify; never touches the above
├── current -> versions/v0.11.0        # POSIX: symlink
├── current.txt                        # Windows: one-line pointer file
├── bin/adm-agent            # stable entry point → resolves through the pointer
├── .env                     # untouched by upgrade
├── admission.db             # untouched by upgrade
└── ...                      # untouched by upgrade
```

**Invariant:** `upgrade` may only ever write to `versions/`, `staging/`,
`current` / `current.txt`, and `bin/`. Everything else in `~/.uni-agent/` —
`.env`, `admission.db` and its WAL/SHM siblings, `schemas/`, the strategy
cache, logs — is off limits. This is asserted in tests, not just documented.

`staging/` lives under `~/.uni-agent/`, not `/tmp`, so that promoting a
verified staged tree into `versions/` is a same-filesystem rename (atomic)
rather than a cross-device copy (not atomic, and slow for a
several-hundred-MB payload).

Retention: keep the active version plus **one** previous (last-good); prune
older ones after a successful activation. Two retention cases that differ:

- **Automatic rollback** (§5 step 7 failed): the newly activated version is
  proven bad, so its directory is deleted and retention returns to the
  pre-upgrade state. A retry re-downloads.
- **Manual `upgrade --rollback`**: the version rolled back *from* is
  retained, so the user can move forward again without re-downloading. It is
  pruned on the next successful activation.

### 3.3 Why versioned directories (approach chosen)

Three approaches were considered:

1. **Versioned directories + pointer switch** *(chosen)*
2. Shadow directory + directory rename swap (`bin` → `bin.old`, `bin.new` → `bin`)
3. In-place file-by-file overwrite with a backup manifest (today's approach, hardened)

Approach 1 wins on one decisive property: **the running `upgrade` process's
own files are never touched.** The upgrading binary executes from
`versions/vOLD/`, so "replace a running executable" — the root cause of
§1.3 and of every Windows-specific hazard here — stops being a problem on
all platforms. Rollback is repointing, not restoring: the last-good tree is
still on disk, byte-identical, never mutated. A failure before activation
leaves *nothing changed at all*, which for a non-technical user is
categorically different from "recoverable".

Approach 2 has a window between the two renames where `bin` does not exist
(a crash there yields a broken install needing start-up self-repair), and
renaming a directory containing a running or memory-mapped executable fails
on Windows. Approach 3 cannot be made atomic in principle and is rejected.

Cost of approach 1, accepted: a one-time layout migration for existing
installs (§3.5) and roughly one extra copy of the payload on disk.

### 3.4 Pointer mechanics per platform

| Platform | Pointer | Entry point | Switch |
|---|---|---|---|
| macOS / Linux | `current` symlink → `versions/<v>/` | `bin/adm-agent` symlink → `../current/adm-agent` | `os.replace` of a temp symlink onto `current` — atomic |
| Windows | `current.txt` containing the version dir name | `bin\adm-agent.cmd` reading `current.txt` and exec'ing `%~dp0..\versions\<v>\adm-agent.exe` | `os.replace` of a temp file onto `current.txt` — atomic on NTFS |

The POSIX symlink chain is safe with PyInstaller onedir because the
bootloader resolves the real executable path (`/proc/self/exe`,
`_NSGetExecutablePath`) and derives `_internal` from it. This is already
proven in production: today's `~/.local/bin/adm-agent` symlink works exactly
this way. `~/.local/bin/adm-agent` therefore needs no change — a symlink to
a symlink resolves fine.

Windows avoids symlinks entirely (they need privilege) and avoids putting
any executable inside `bin\`, so nothing in `bin\` is ever file-locked.

**Documentation impact:** on Windows the invoked name becomes `adm-agent`
(`PATHEXT` resolves `adm-agent.cmd`) instead of `adm-agent.exe`. README's
Windows notes and the install skill must be updated. Accepted as part of
this change.

### 3.5 Migrating existing installs — the bootstrap problem

The fixed upgrade code ships *inside* a new version, but the process
performing the upgrade is the **old, broken** binary. A user on
`v0.9.0`/`v0.10.0` cannot reach the fix via `upgrade`: §1.1 tells them
they're already current, and even `--force` would apply the old
non-atomic overwrite into the flat `bin/` layout.

**Therefore the layout transition is performed by the install skill, not by
the old binary.** For the transition release:

- `uni-admission-install` §1 (fresh install) is rewritten to create the
  §3.2 layout. Re-running it is the documented, agent-driven path onto the
  new scheme, and it already preserves `.env` and `admission.db` (different
  directories).
- The install skill gains an explicit branch: if `~/.uni-agent/bin/_internal`
  exists (flat legacy layout), the agent performs the one-time re-install
  rather than calling `upgrade`.
- The new binary's `upgrade` also detects the legacy layout and, rather than
  attempting an in-place conversion from a process running inside the
  directory being converted, exits with the dedicated code from §7 telling
  the agent to run the re-install path.

After one re-install, `upgrade` is self-sufficient forever.

## 4. Version comparison

Replace the string compare with `packaging.version.Version`, which is
already resolvable in the environment and orders every historical tag shape
in this repo correctly (verified):

```
v0.9.0       < v0.10.0  → True
v0.7.5-alpha < v0.8.0   → True
v1.0.0-alpha < v1.0.0   → True
v0.0.0-dev   < v0.10.0  → True
```

Rules:

- Compare parsed versions, never strings. The `v` prefix is stripped before
  parsing and preserved for display.
- An unparseable tag (either side) is **not** treated as newer: report
  `blocked_reason="unparseable_version"` with both raw values and exit `12`
  (§7). Never crash, never guess.
- Pre-releases sort below their final release, so a `1.0.0-rc1` tag never
  supersedes `1.0.0`. GitHub's `/releases/latest` already excludes
  pre-releases unless explicitly marked latest; this is the second line of
  defence.
- `--force` continues to bypass the newness check (it does not bypass the
  verification gates in §6).

## 5. The upgrade transaction

```
 1. preflight     → frozen mode? legacy layout? server running? (§9)
 2. resolve       → GitHub /releases/latest → tag + platform asset (§4)
 3. stage         → download into staging/<tmp>, extract with a member filter
 4. verify-artifact → SHA256 against the release's SHA256SUMS (§6.1)
 5. verify-binary   → run the STAGED binary's self-check (§6.2)
 6. activate      → move staged tree to versions/<new>, atomic pointer switch
 7. post-check    → `check` + `db-migrate` on the now-live version (§6.3)
 8. settle        → prune to active + one previous
```

Failure handling:

| Fails at | Action | Resulting state |
|---|---|---|
| 1–5 | delete `staging/<tmp>`, report | **nothing changed** — install byte-identical to before |
| 6 | pointer switch is atomic: it either happened or it didn't; on failure delete the new version dir | nothing changed |
| 7 | **asymmetric, see §6.3** — `db-migrate` failure that `repair --auto` cannot resolve triggers automatic rollback; `check` failure only warns | rolled back to previous version (migrate), or upgraded-with-warnings (`check`) |
| 8 | log a warning only | upgraded successfully; stale dirs pruned next run |

`upgrade --rollback` is a first-class command: repoint to the retained
last-good version and re-run the §6.3 post-check. This is the escape hatch
a non-technical user is told about when anything looks wrong.

`sync_installation_payload`, `backup_current_executable` and
`replace_executable` are deleted — the activation model makes all three
obsolete. `find_release_asset` / `get_platform_info` / `download_and_extract`
are retained (the latter gains the tar member filter and loses its
copy-into-target behaviour).

## 6. Verification

### 6.1 Artifact checksum

`release.yml` starts publishing a `SHA256SUMS` asset covering every artifact
in the release. `upgrade` downloads it and verifies the artifact before
extraction.

**Backward compatibility:** releases predating this change have no
`SHA256SUMS`. A missing checksum file degrades to a size sanity check plus
an explicit warning in the output (and `checksum_verified=false` in JSON) —
it does not hard-fail, otherwise every existing user becomes permanently
unable to upgrade, reintroducing §1.1's symptom by a different route. A
checksum file that *is* present and does not match is always fatal.

The size sanity check compares the downloaded byte count against the `size`
field GitHub already returns for each asset (exact equality — a mismatch
means a truncated or intercepted download). It catches truncation, not
tampering, which is exactly why it is a degraded fallback reported as
`checksum_verified=false` rather than a substitute for the hash.

### 6.2 Staged-binary self-check (the gate that matters most)

Before the pointer moves, run the **staged** binary in a subprocess:

```
<staging>/adm-agent version --json
```

Requirements: exit code 0, parseable JSON, and a version equal to the
release tag being installed. This catches truncated downloads, wrong-arch
artifacts, a missing `_internal`, and a broken build — all while the live
install is still untouched.

`version` therefore needs a `--json` mode (§7); it currently only prints
prose.

On macOS, clear the quarantine xattr on the staged tree
(`xattr -cr <staging>`) before this step, otherwise Gatekeeper blocks
execution and every upgrade fails the gate. Today this is a manual step the
README asks the user to perform; it becomes automatic.

### 6.3 Post-activation check

On the now-live version, in order:

1. `check` — existing command; validates dependencies, Chromium, SQLite
   reachability, and at least one real LLM key. Its failures are treated as
   **warnings** reported to the user, not rollback triggers: a missing
   Chromium or an expired API key is an environment problem the new version
   did not cause, and rolling back would not fix it.
2. `db-migrate --yes` — existing logic in `_run_migration_after_upgrade`,
   including its `repair --auto` fallback, is preserved. A migration failure
   that `repair --auto` cannot resolve **does** trigger rollback, because a
   half-migrated database with a new binary is precisely the state a
   non-technical user cannot escape.

## 7. Machine-readable interface

The agent is the primary caller, so the contract is data, not prose.

`--json` is added to `version` and to `upgrade` (both `--check` and the
real run). Stable fields:

```jsonc
{
  "current_version": "v0.10.0",
  "latest_version": "v0.11.0",
  "is_newer": true,
  "asset_available": true,
  "checksum_verified": true,
  "action_taken": "upgraded",      // none | upgraded | rolled_back | blocked
  "active_version": "v0.11.0",
  "previous_version": "v0.10.0",
  "blocked_reason": null,          // stable enum, see below
  "next_action": null,             // imperative hint for the agent
  "warnings": []
}
```

Exit codes (stable, skill routes on them):

| Code | Meaning | `blocked_reason` |
|---|---|---|
| 0 | upgraded, or already current | — |
| 10 | server is running; stop it and retry | `server_running` |
| 11 | no artifact for this platform/arch | `no_asset_for_platform` |
| 12 | resolve or verification failed; nothing changed | `checksum_mismatch` / `staged_binary_failed` / `unparseable_version` |
| 13 | activated then rolled back | `post_check_failed` |
| 14 | source checkout, not a packaged install | `not_frozen` |
| 15 | legacy flat layout; re-install required (§3.5) | `legacy_layout` |
| 1 | unexpected error | `unexpected` |

Human-readable output stays exactly as friendly as it is now (emoji and
Chinese-facing phrasing are the frontend of a CLI aimed at non-developers);
`--json` is additive.

### 7.1 Overridable release endpoint

`GITHUB_RELEASE_API` is currently a module constant
([`upgrade.py:42`](../../../src/services/upgrade.py)). It becomes
overridable via an environment variable (`ADM_AGENT_RELEASE_API_BASE`),
defaulting to the real GitHub API.

This is a requirement, not a testing convenience — two things depend on it:

- The §11 release gate must verify an upgrade *into a version that is not
  published yet* (the whole point of gating publication on it), so it has to
  point `upgrade` at a locally served fake release.
- The §10 unit suite is hermetic by design; without the override it would
  have to monkeypatch a module constant, which tests the patch rather than
  the resolution path.

The variable is undocumented in user-facing docs and carries no promise of
stability — it is a build/test seam, and pointing it at an untrusted host
would bypass the origin the checksums are meant to anchor to.

## 8. Skill convergence

- `uni-admission-install` §3 ("Upgrade in place") stops re-downloading. It
  calls `adm-agent upgrade --json`, routes on the §7 exit codes, and for
  code 10 stops the server (per §2 the agent owns lifecycle) and retries.
- Fresh install (§1) remains, retargeted at the §3.2 layout, and is
  repositioned as the *reinstall / corrupt-install recovery / legacy-layout
  migration* path rather than the routine upgrade path.
- The skill documents `upgrade --rollback` as the user-facing escape hatch.
- `using-uni-admission-agent`'s routing table entry for "升级 / upgrade"
  continues to point at the install skill; no new skill is added (five
  skills stay five).

## 9. Refusing to run while the server is up

Detect via `~/.adm-agent/server.pid` (written by `serve`, per README) plus a
`GET /health` probe on the configured port. Either signal positive → exit
`10` with `blocked_reason="server_running"` and
`next_action="stop_server_then_retry"`.

Rationale for refusing rather than orchestrating: a running server holds an
open SQLite connection and imports the old code; migrating the database
underneath it or letting it keep serving stale code are both incoherent
states. The stop/start decision belongs to whoever knows what the user was
doing — the agent.

A stale PID file whose process is gone must not block the upgrade: verify
liveness before refusing.

## 10. Testing

TDD applies: each behaviour below gets a failing test before implementation.
All unit tests are hermetic — a local HTTP fixture serves fake releases via
the §7.1 endpoint override, `tmp_path` holds fake install layouts, no
network, no real GitHub.

**Version comparison**
- Truth table across all historical tag shapes, **including `v0.9.0` →
  `v0.10.0` explicitly named as the §1.1 regression guard**
- Unparseable tags on either side → exit non-zero, `is_newer=false`, no crash

**Transaction / atomicity**
- Checksum mismatch → abort; assert pointer unchanged and no new version dir
- Staged binary exits non-zero → abort; assert pointer unchanged
- Staged binary reports a version different from the tag → abort
- Missing `SHA256SUMS` → proceeds with `checksum_verified=false` + warning
- Truncated/corrupt archive → abort during extract, `staging` cleaned up
- Successful upgrade → pointer moved, last-good retained, older pruned
- Post-check `db-migrate` failure → rollback; assert pointer back to
  last-good and a `13` exit
- `check` failure → **no** rollback; warning surfaced (asserts the §6.3
  asymmetry deliberately)
- `--rollback` → pointer returns to previous version
- Tar member filter rejects an entry escaping the extraction root

**Guard rails**
- Data-safety invariant: after every failure mode above, assert `.env`,
  `admission.db`, `schemas/` are byte-identical (this is the §3.2 invariant
  as an executable check)
- Server running (live PID + `/health`) → exit `10`, nothing staged
- Stale PID file, process dead → upgrade proceeds
- Source checkout → exit `14` with a clear message
- Legacy flat layout → exit `15`

**Interface**
- `version --json` and `upgrade --check --json` schema stability
- Every exit code in §7 is produced by at least one test

**Integration** (`@pytest.mark.integration`, excluded from the default run,
so CI stays hermetic): resolve the real GitHub `/releases/latest` and assert
the asset naming convention still matches `find_release_asset`'s
expectation — the one thing a fixture cannot catch is the maintainer
renaming artifacts in `release.yml`.

## 11. CI

**Trigger and placement (confirmed with user): a release gate in
`release.yml`, never on day-to-day pushes or merges.** The verification runs
on tag pushes only, and it sits *between* the existing build jobs and the
existing publish job:

```
build-extension ─┐
build-backend   ─┼→ upgrade-verify ─→ release   (softprops/action-gh-release)
build-client    ─┘
```

`release.yml`'s `release` job adds `upgrade-verify` to its `needs:`. This is
the point of the placement: **a tag existing and a release being published
are two different events.** If verification fails, the `release` job never
runs, no GitHub Release is created, and no artifacts are uploaded — so a
version whose upgrade path is unproven is not merely flagged, it is
undownloadable. The tag remains but is inert.

Consequently `RELEASING.md`'s documented flow is unchanged: still
`bump-my-version bump <part>` then `git push --follow-tags`. No manual
pre-flight step, no `workflow_dispatch` choreography. When the gate fails,
fix and cut the next patch tag (tags are cheap) or delete and re-push the
tag.

Job definition — matrix over `ubuntu-latest` / `macos-latest` /
`windows-latest`:

1. **Reuse the built artifacts.** `download-artifact` pulls the
   `backend-<os>` archive the build job just produced — the verification
   runs against the exact binary that would ship, not a rebuilt copy. No
   second build, which is what keeps this affordable.
2. **"vA" is the previous real release.** Download the current published
   `latest` backend artifact from GitHub. The test is then the upgrade a
   real user will actually perform — published version → this version —
   rather than a synthetic pair.
   - For the transition release, the previous version has the legacy flat
     layout, so this leg asserts the §3.5 detection path: exit `15`,
     `blocked_reason="legacy_layout"`, install untouched.
   - For every release after it, the previous version already has the §3.2
     layout, so this leg asserts the real happy path end to end.
   - If no previous release exists (first ever), skip this leg with an
     explicit log line rather than silently passing.
3. **Same-version `--force` upgrade** against a local fake release built
   from the artifact in step 1, with a generated `SHA256SUMS`. This
   exercises the full happy path — stage → verify → activate → post-check —
   even on the transition release where step 2 lands on the legacy branch.
4. Assert `active_version` is the new version and that the binary invoked
   through `bin/adm-agent` (symlink on POSIX, `.cmd` shim on Windows)
   reports it.
5. Run `upgrade --rollback`; assert the previous version is active again.
6. Assert `.env` and a seeded `admission.db` survived every operation above
   byte-identically (§3.2 invariant).

This is the evidence that "one command upgrades and the user keeps working"
actually holds on every platform we ship — the thing that cannot be
established by unit tests alone, and the reason §1.4's zero-coverage state
is unacceptable for a v1.0.

Because this gate does not run on merges, the §10 unit suite carries the
day-to-day regression burden alone. It must therefore cover the upgrade
*logic* (pointer switching, verification gates, rollback, retention) against
fake install layouts on `tmp_path`, leaving only genuinely
platform-dependent behaviour — real PyInstaller payloads, real symlink and
`.cmd` shim resolution, Gatekeeper — to the release gate. A logic regression
that only the three-platform gate would catch indicates the unit suite has a
gap worth closing.

A failing gate blocks publication, so gate flakiness blocks releases. Every
step above must be deterministic: no reliance on network resources other
than the GitHub release API, and the fake release in step 3 served from a
local process with fixed content.

`release.yml` also gains the `SHA256SUMS` generation-and-upload step (§6.1).

## 12. Risks and open items

- **Windows `.cmd` shim changes the invoked name** (`adm-agent.exe` →
  `adm-agent`). Documented in §3.4; README and install-skill updates are part
  of the work, not a follow-up.
- **One-time re-install for existing users.** Unavoidable (§3.5). The
  install skill must make it a single agent-driven step and must state
  clearly that `.env` and the database are preserved.
- **`~/.uni-agent/` is both install root and data root.** The §3.2 invariant
  plus its executable assertions in §10 are the mitigation. Splitting the
  two trees would be cleaner but is a larger, separate migration.
- **Disk usage** grows by roughly one payload copy (retention = 2). PyInstaller
  onedir payloads are on the order of a few hundred MB with Chromium
  excluded (Chromium lives in the Playwright cache, outside `~/.uni-agent/`),
  so this is acceptable; retention is a constant that can be tuned if not.
- **`packaging` availability in the frozen bundle.** It resolves in the dev
  environment today, but PyInstaller only bundles what it detects. The
  implementation must add it to `adm-agent.spec`'s hidden imports if the
  frozen smoke test shows it missing — and the §11 CI job is what would
  catch that.
