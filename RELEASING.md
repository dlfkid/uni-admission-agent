# Releasing

Version numbers live in several files (pyproject, plugin/marketplace manifests,
`src/__init__.py`, the FastAPI app, and the frontend package/manifest/lockfile).
**Never edit them by hand** — a single command keeps them all in lock-step,
commits, and tags. The git tag is the ultimate source of truth: `build_dist.py`
derives the artifact version from `git describe --tags` at build time.

## Cut a release

From an up-to-date `main` (working tree clean):

```bash
uv run bump-my-version bump patch   # 0.7.5 -> 0.7.6  (bug-fix only)
uv run bump-my-version bump minor   # 0.7.5 -> 0.8.0  (new features, back-compat)
uv run bump-my-version bump major   # 0.7.5 -> 1.0.0  (stable / breaking)

git push --follow-tags               # push the commit AND the new tag
```

That one `bump` command:
1. rewrites the version in every configured file (see `[tool.bumpversion]` in `pyproject.toml`),
2. creates the commit `chore: release vX.Y.Z (was vA.B.C)`,
3. creates the annotated tag `vX.Y.Z`.

Pushing the tag triggers `.github/workflows/release.yml`, which builds and
uploads the release artifacts.

## Preview without changing anything

```bash
uv run bump-my-version bump minor --dry-run --verbose
```

## Notes

- **Versioning policy:** stay on `0.x` while hardening against real universities.
  Reserve `1.0.0` for when the crawler is validated broadly (not just on the
  universities we have debugged).
- `frontend/package-lock.json`'s root version is bumped too; only the project's
  own two entries equal the project version (dependencies carry their own), so
  the `"version"` match is safe. If a dependency ever coincides, npm will reject
  the mismatch on the next install — a loud, catchable failure.
- `uv.lock` is bumped as well, but its matcher is anchored on
  `name = "uni-admission-agent"` because a bare version match there is **not**
  safe — dependencies live in the same file and one can coincidentally share our
  version number (`tabulate` was itself at `0.9.0`). Keeping it in lock-step
  matters beyond tidiness: `uv run` re-syncs `uv.lock` on the fly, so a stale
  lock silently dirties the working tree and blocks the next `bump`
  (`allow_dirty = false`).
- Adding a new version location? Add a `[[tool.bumpversion.files]]` block for it.
- Publishing is gated on the upgrade verification job (`upgrade-verify` in
  `release.yml`). If it fails, no GitHub Release is created and no artifacts
  are uploaded — the tag exists but nothing is downloadable. Fix and cut the
  next patch tag.
