"""Shared types for the upgrade subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class UpgradeError(Exception):
    """Raised when an upgrade operation fails."""


class UnparseableVersionError(UpgradeError):
    """Raised when a version tag cannot be interpreted (spec §4)."""

    def __init__(self, current: str, latest: str) -> None:
        super().__init__(
            f"Cannot compare versions: current={current!r} latest={latest!r}"
        )
        self.current = current
        self.latest = latest


class ChecksumMismatchError(UpgradeError):
    """Raised when a downloaded artifact fails size or digest verification.

    A distinct type (not a string pattern on the message) so the transaction
    can map a failure to ``blocked_reason=checksum_mismatch`` without risking
    a false match on unrelated text — e.g. a staged binary's own stdout.
    """


class StagedBinaryError(UpgradeError):
    """Raised when the staged candidate binary fails its self-check.

    A distinct type for the same reason as :class:`ChecksumMismatchError`:
    the binary's own captured stdout/stderr is interpolated into the message,
    so dispatching on message content could be fooled by the binary's output.
    """


class ExitCode(IntEnum):
    """Stable CLI exit codes — the agent routes on these (spec §7)."""

    OK = 0
    UNEXPECTED = 1
    SERVER_RUNNING = 10
    NO_ASSET_FOR_PLATFORM = 11
    VERIFICATION_FAILED = 12
    POST_CHECK_FAILED = 13
    NOT_FROZEN = 14
    LEGACY_LAYOUT = 15


class BlockedReason(str):
    """Stable `blocked_reason` values (spec §7)."""

    SERVER_RUNNING = "server_running"
    NO_ASSET_FOR_PLATFORM = "no_asset_for_platform"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    STAGED_BINARY_FAILED = "staged_binary_failed"
    UNPARSEABLE_VERSION = "unparseable_version"
    POST_CHECK_FAILED = "post_check_failed"
    NOT_FROZEN = "not_frozen"
    LEGACY_LAYOUT = "legacy_layout"
    UNEXPECTED = "unexpected"


@dataclass
class UpgradeResult:
    """The `--json` payload (spec §7). Field names are API."""

    current_version: str = ""
    latest_version: str = ""
    is_newer: bool = False
    asset_available: bool = False
    checksum_verified: bool = False
    action_taken: str = "none"  # none | upgraded | rolled_back | blocked
    active_version: str = ""
    previous_version: str = ""
    blocked_reason: str | None = None
    next_action: str | None = None
    warnings: list[str] = field(default_factory=list)
    exit_code: int = int(ExitCode.OK)

    def to_json_dict(self) -> dict:
        """Serialise, omitting the internal-only exit code."""
        payload = self.__dict__.copy()
        payload.pop("exit_code")
        return payload
