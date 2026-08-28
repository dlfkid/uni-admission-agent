"""The upgrade package's public surface must stay importable."""

from pathlib import Path

import pytest


def test_public_names_are_importable() -> None:
    """Names exported from the upgrade package are importable."""
    from src.services import upgrade

    for name in (
        "UpgradeError",
        "UpgradeResult",
        "ExitCode",
        "BlockedReason",
        "InstallLayout",
        "check_for_updates",
        "perform_upgrade",
        "rollback",
        "get_current_version",
        "get_platform_info",
    ):
        assert hasattr(upgrade, name), f"{name} missing from the package API"


def test_default_layout_uses_the_frozen_data_dir(tmp_path: Path, monkeypatch) -> None:
    """The install root is the frozen data dir — spec §3.1."""
    from src.services.upgrade import default_install_layout

    monkeypatch.setattr("src.services.upgrade.get_data_dir", lambda: tmp_path)
    assert default_install_layout().root == tmp_path


def test_packaging_is_declared_for_pyinstaller() -> None:
    """packaging drives version comparison; a missing bundle breaks upgrade."""
    spec = Path("adm-agent.spec").read_text(encoding="utf-8")
    assert '"packaging"' in spec
