"""Integration checks against the real release API — excluded by default.

Run with: uv run pytest tests/test_upgrade_integration.py -m integration
"""

import pytest

from src.services.upgrade.release import (
    fetch_latest_release,
    find_release_asset,
    get_platform_info,
)
from src.services.upgrade.versions import parse_tag


@pytest.mark.integration
def test_published_assets_still_match_the_expected_naming() -> None:
    """A rename in release.yml would silently break every user's upgrade.

    No fixture can catch this — only the real release can.
    """
    release = fetch_latest_release()
    assert parse_tag(release["tag_name"]) is not None

    for os_name, arch in (("macos", "arm64"), ("linux", "x86_64"), ("windows", "x86_64")):
        assert find_release_asset(release, os_name, arch) is not None, (
            f"No adm-agent asset published for {os_name}-{arch} in "
            f"{release['tag_name']}"
        )


@pytest.mark.integration
def test_current_platform_has_a_downloadable_asset() -> None:
    """Current platform should have a downloadable asset in the latest release."""
    os_name, arch = get_platform_info()
    release = fetch_latest_release()
    asset = find_release_asset(release, os_name, arch)
    assert asset is not None
    assert asset["size"] > 0
