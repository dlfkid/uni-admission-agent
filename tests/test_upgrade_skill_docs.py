"""The install skill must route through `upgrade`, not re-download — spec §8."""

from pathlib import Path

import pytest

SKILL = Path("skills/uni-admission-install/SKILL.md")


@pytest.fixture(name="skill_text")
def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_upgrade_section_calls_the_upgrade_command(skill_text: str) -> None:
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "adm-agent upgrade" in upgrade_section


def test_upgrade_section_no_longer_reruns_fresh_install(skill_text: str) -> None:
    """The re-download path had no backup, atomicity or verification."""
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "Run §1 (Fresh install)" not in upgrade_section


def test_skill_documents_the_exit_codes_the_agent_routes_on(skill_text: str) -> None:
    for code in ("10", "12", "13", "15"):
        assert f"| `{code}` |" in skill_text


def test_skill_documents_rollback(skill_text: str) -> None:
    assert "--rollback" in skill_text


def test_fresh_install_creates_the_versioned_layout(skill_text: str) -> None:
    assert "versions/" in skill_text
    assert "--strip-components=1 -C ~/.uni-agent/bin" not in skill_text


# ── legacy-layout migration branch (spec §3.5) ────────────────────────


def test_upgrade_section_checks_for_the_legacy_layout_before_upgrading(
    skill_text: str,
) -> None:
    """Spec §3.5's branch, asserted on the *condition* rather than a phrase.

    Every installed user is still on the pre-versioning binary, whose
    ``upgrade`` cannot reach this fix: it has no ``--json`` (click exits 2)
    and its string version compare reports "already latest" with exit 0.
    Without this branch the delivery channel stays closed for exactly the
    users the workstream exists for — and the previous five doc tests were
    all substring assertions, which is why the omission shipped unnoticed.
    """
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]

    # The detection condition itself, both halves of it.
    assert "bin/_internal" in upgrade_section
    assert "versions" in upgrade_section

    # It must be decided before the `upgrade` command is actually invoked.
    legacy_at = upgrade_section.index("bin/_internal")
    invoke_at = upgrade_section.index("adm-agent upgrade --json")
    assert legacy_at < invoke_at, "legacy check must precede the upgrade call"

    # And it must route to the fresh install, not to a retry.
    assert "§1" in upgrade_section


def test_upgrade_section_routes_old_binary_symptoms_to_the_reinstall(
    skill_text: str,
) -> None:
    """Exit 2 (unknown ``--json``) and non-JSON output are old-binary tells."""
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert "`2`" in upgrade_section
    assert "JSON" in upgrade_section


def test_legacy_branch_states_that_data_is_preserved(skill_text: str) -> None:
    """Spec §12 requires the one-time re-install to say so plainly."""
    upgrade_section = skill_text.split("## §3")[1].split("## §4")[0]
    assert ".env" in upgrade_section
    assert "admission.db" in upgrade_section


# ── windows install correctness ───────────────────────────────────────


def test_zip_extraction_strips_the_wrapper_directory(skill_text: str) -> None:
    """``unzip`` has no ``--strip-components``; the archive has one top-level
    directory, so a plain ``unzip -d versions/<V>`` nests the exe one level
    too deep and the shim resolves nothing."""
    assert "unzip -o \"$ARTIFACT\" -d ~/.uni-agent/versions/${VERSION}" not in skill_text
    assert "unzip" in skill_text
    assert "--strip-components" in skill_text


def test_windows_pointer_is_not_written_from_an_unexported_bash_variable(
    skill_text: str,
) -> None:
    """``VERSION`` is a bash shell variable; PowerShell would read
    ``$env:VERSION`` as empty and write an empty ``current.txt``."""
    assert "-Value $env:VERSION" not in skill_text
    assert "current.txt" in skill_text


def test_windows_gets_its_own_path_step(skill_text: str) -> None:
    """Nothing else puts ``%USERPROFILE%\\.uni-agent\\bin`` on PATH, yet the
    README promises a bare ``adm-agent`` command resolved via PATHEXT."""
    assert ".uni-agent\\bin" in skill_text
    assert "PATHEXT" in skill_text
    assert "$env:PATH" in skill_text


def test_fresh_install_never_unpacks_over_the_active_version(skill_text: str) -> None:
    """§1 also serves "重装" and §3's reinstall_to_replace_active_version route,
    so ${VERSION} is often the currently active version. Extracting on top of
    it leaves stale files and, on Windows, a half-updated directory — the very
    hazard perform_upgrade refuses a same-version --force for."""
    assert "tar -xzf \"$ARTIFACT\" -C ~/.uni-agent/versions/${VERSION}" not in skill_text
    assert "STAGE=~/.uni-agent/versions/.incoming-$$" in skill_text
    assert "${VERSION}.replaced-$$" in skill_text


def test_fresh_install_checks_the_right_executable_on_each_platform(
    skill_text: str,
) -> None:
    """The Windows archive's entry point is adm-agent.exe. A check hardcoded to
    the POSIX name fails every Windows install and every legacy migration."""
    assert "case \"$EXT\" in zip) EXE=adm-agent.exe ;; *) EXE=adm-agent ;; esac" in skill_text
    assert 'test -f "$STAGE/$EXE"' in skill_text
    # ...and nothing still hardcodes the POSIX name for the check.
    assert 'test -f "$STAGE/adm-agent"' not in skill_text


def test_fresh_install_restores_the_backup_if_placement_fails(
    skill_text: str,
) -> None:
    """Between moving the old directory aside and moving the new one in there
    is a window with no valid target — spec §3.3 rejected exactly this shape.
    A trap must put the backup back, and the backup must only be deleted once
    the new directory is actually in place."""
    assert "trap restore EXIT" in skill_text
    assert 'if [ -d "$BACKUP" ] && [ ! -d "$TARGET" ]; then' in skill_text
    # `set -e` stays in force inside an EXIT trap, so a failing cleanup would
    # abort the handler before the restore ran.
    handler = skill_text[skill_text.index("restore() {"):skill_text.index("trap restore EXIT")]
    assert "set +e" in handler
    assert handler.index("set +e") < handler.index('rm -rf "$STAGE"')
    # A restore that itself fails must be reported, not silently swallowed.
    assert "CRITICAL: could not restore the previous install." in skill_text
    # The delete comes after the placement, never before it.
    place = skill_text.index('mv "$STAGE" "$TARGET"')
    drop = skill_text.index('rm -rf "$BACKUP"')
    assert place < drop


def test_the_skill_routes_rollback_failure_apart_from_a_completed_rollback(
    skill_text: str,
) -> None:
    """The skill routes on the exit code alone, so 13 ("you are back on a
    working version") must not also cover the case where the rollback failed
    and the install needs a human."""
    assert "| `17` |" in skill_text
    thirteen = skill_text[skill_text.index("| `13` |") :].split("\n")[0]
    seventeen = skill_text[skill_text.index("| `17` |") :].split("\n")[0]
    assert "back on the working version" in thirteen
    assert "Mixed state" in seventeen
    assert "not** tell the user they recovered" in seventeen
    # 17 covers both "the rollback failed" and "there was nothing to roll back
    # to"; the remedy differs, so the row must send the agent to next_action
    # rather than unconditionally offering --rollback.
    assert "rollback_then_inspect" in seventeen
    assert "inspect_logs_then_retry" in seventeen
