"""The release gate must run both §11 legs, not only the local fake one.

Step 2 — download the currently published ``latest`` artifact, install it,
and assert the real published→new upgrade — is the one leg that would have
caught the missing legacy branch in the install skill. These are hermetic
checks on the gate's own wiring; the gate itself runs only on tag pushes.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import scripts.verify_upgrade as gate

WORKFLOW = Path(".github/workflows/release.yml")
SCRIPT = Path("scripts/verify_upgrade.py")


@pytest.fixture(name="upgrade_verify_job")
def _upgrade_verify_job() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["upgrade-verify"]


# ── workflow wiring (spec §11 step 2) ─────────────────────────────────


def test_gate_resolves_the_previously_published_release(upgrade_verify_job: dict) -> None:
    """The previous *real* release, resolved the same way upgrade resolves it."""
    steps = upgrade_verify_job["steps"]
    resolve = next(s for s in steps if s.get("id") == "previous")
    assert "releases/latest" in resolve["run"]
    assert "gh release download" in resolve["run"]


def test_gate_skips_explicitly_when_no_previous_release_exists(
    upgrade_verify_job: dict,
) -> None:
    """Spec §11 step 2: skip with an explicit log line, never silently pass."""
    resolve = next(s for s in upgrade_verify_job["steps"] if s.get("id") == "previous")
    assert "SKIP:" in resolve["run"]


def test_gate_passes_the_previous_artifact_to_the_verifier(
    upgrade_verify_job: dict,
) -> None:
    verify = next(
        s for s in upgrade_verify_job["steps"] if "verify_upgrade.py" in s.get("run", "")
    )
    assert "--previous-artifact" in verify["run"]
    assert "--previous-version" in verify["run"]


def test_release_job_still_gates_on_upgrade_verify() -> None:
    """A version whose upgrade path is unproven must be undownloadable."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "upgrade-verify" in workflow["jobs"]["release"]["needs"]


# ── verifier legs ─────────────────────────────────────────────────────


def test_missing_previous_artifact_skips_with_an_explicit_line(capsys) -> None:
    import argparse

    args = argparse.Namespace(previous_artifact=None)
    assert gate._step_previous_release(args, "http://127.0.0.1:1") is None
    assert "SKIP:" in capsys.readouterr().out


def test_previous_era_is_decided_by_capability_not_by_tag(tmp_path: Path) -> None:
    """Every artifact contains ``_internal`` (PyInstaller onedir), so archive
    contents cannot distinguish the two eras — only ``version --json`` can."""
    exe = tmp_path / "adm-agent"
    exe.write_text("")

    old = subprocess.CompletedProcess([], returncode=2, stdout="", stderr="no --json")
    with patch("scripts.verify_upgrade.subprocess.run", return_value=old):
        assert gate._previous_ships_the_versioned_layout(exe) is False

    new = subprocess.CompletedProcess([], returncode=0, stdout='{"version": "v0.11.0"}')
    with patch("scripts.verify_upgrade.subprocess.run", return_value=new):
        assert gate._previous_ships_the_versioned_layout(exe) is True


def test_unrunnable_previous_binary_is_treated_as_the_legacy_era(
    tmp_path: Path,
) -> None:
    exe = tmp_path / "adm-agent"
    exe.write_text("")
    with patch("scripts.verify_upgrade.subprocess.run", side_effect=OSError("nope")):
        assert gate._previous_ships_the_versioned_layout(exe) is False


def test_legacy_refusal_leg_asserts_exit_15_and_an_untouched_install(
    tmp_path: Path,
) -> None:
    """The transition release's leg: exit 15, blocked_reason, nothing moved."""
    home = tmp_path / "home"
    root = home / ".uni-agent"
    gate._seed_user_data(root)
    (root / "bin" / "_internal").mkdir(parents=True)
    (root / "bin" / "adm-agent").write_text("old binary")

    refused = subprocess.CompletedProcess(
        [],
        returncode=15,
        stdout='{"action_taken": "blocked", "blocked_reason": "legacy_layout"}',
        stderr="",
    )
    with patch("scripts.verify_upgrade.subprocess.run", return_value=refused):
        error = gate._verify_legacy_refusal(
            tmp_path / "newbin" / "adm-agent", root, home, "http://127.0.0.1:1"
        )
    assert error is None


@pytest.mark.parametrize(
    ("returncode", "stdout", "fragment"),
    [
        (0, '{"action_taken": "upgraded"}', "expected exit 15"),
        (15, "not json at all", "unparseable output"),
        (15, '{"blocked_reason": "server_running"}', "blocked_reason"),
    ],
)
def test_legacy_refusal_leg_fails_loudly_on_every_deviation(
    tmp_path: Path, returncode: int, stdout: str, fragment: str
) -> None:
    home = tmp_path / "home"
    root = home / ".uni-agent"
    gate._seed_user_data(root)
    (root / "bin" / "_internal").mkdir(parents=True)

    completed = subprocess.CompletedProcess([], returncode=returncode, stdout=stdout, stderr="")
    with patch("scripts.verify_upgrade.subprocess.run", return_value=completed):
        error = gate._verify_legacy_refusal(
            tmp_path / "newbin" / "adm-agent", root, home, "http://127.0.0.1:1"
        )
    assert error is not None
    assert fragment in error


def test_legacy_refusal_leg_detects_a_modified_install(tmp_path: Path) -> None:
    """'Install untouched' is asserted byte-for-byte, not assumed."""
    home = tmp_path / "home"
    root = home / ".uni-agent"
    gate._seed_user_data(root)
    (root / "bin" / "_internal").mkdir(parents=True)
    (root / "bin" / "adm-agent").write_text("old binary")

    def tamper(*_args, **_kwargs):
        (root / "bin" / "adm-agent").write_text("clobbered")
        return subprocess.CompletedProcess(
            [], returncode=15, stdout='{"blocked_reason": "legacy_layout"}', stderr=""
        )

    with patch("scripts.verify_upgrade.subprocess.run", side_effect=tamper):
        error = gate._verify_legacy_refusal(
            tmp_path / "newbin" / "adm-agent", root, home, "http://127.0.0.1:1"
        )
    assert error is not None
    assert "modified" in error


# ── archive handling ──────────────────────────────────────────────────


def test_extract_flat_strips_the_single_wrapper_directory(tmp_path: Path) -> None:
    """``build_dist.py`` archives with ``base_dir=<base_name>``; every consumer
    of an artifact has to strip that wrapper (the same defect as the install
    skill's zip branch)."""
    import tarfile

    payload = tmp_path / "adm-agent-v0.11.0-linux-x86_64"
    (payload / "_internal").mkdir(parents=True)
    (payload / "adm-agent").write_text("binary")
    archive = tmp_path / "adm-agent-v0.11.0-linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname=payload.name)

    dest = tmp_path / "out" / "v0.11.0"
    gate._extract_flat(archive, dest)

    assert (dest / "adm-agent").read_text() == "binary"
    assert (dest / "_internal").is_dir()
    assert not (dest / payload.name).exists()
