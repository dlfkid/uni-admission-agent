"""
Automatic file logging via loguru.

Captures all Python stdlib ``logging`` output into timestamped ``.txt`` files
with size-based rotation (10 MB) and retention (10 most recent files).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.core.paths import get_data_dir


def resolve_log_dir() -> Path:
    """Return the directory where log files should be written.

    Always ``<data dir>/logs/`` — ``<project>/data/logs/`` in dev mode,
    ``~/.uni-agent/logs/`` when frozen.

    Frozen mode deliberately does **not** use ``Path(sys.executable).parent``.
    Under the versioned install layout that is ``versions/<v>/``, which
    ``InstallLayout.prune()`` deletes after a successful upgrade and which
    the upgrade transaction ``rmtree``s outright when a post-check fails —
    destroying exactly the logs whose ``next_action="inspect_logs_then_retry"``
    tells the agent to read them. Logs are user data, not install payload
    (spec §3.2's install/data separation).
    """
    return get_data_dir() / "logs"


class InterceptHandler(logging.Handler):
    """Route stdlib *logging* records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level name → loguru level
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the call stack so loguru reports the real caller
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).bind(
            logger_name=record.name
        ).log(level, record.getMessage())


# Custom VERBOSE level — below DEBUG, for heartbeat / health-check noise
def _log_format(record: dict) -> str:
    """Format log line, preferring original stdlib logger name when available."""
    name = record["extra"].get("logger_name", record["name"])
    return (
        "{time:YYYY-MM-DD HH:mm:ss} - "
        + name
        + " - {level} - {message}\n"
    )


_VERBOSE_LEVEL_NO = 5
_VERBOSE_REGISTERED = False


def setup_file_logging(log_dir: Path | None = None) -> None:
    """Activate file logging with rotation and retention.

    * Registers a custom **VERBOSE** level (``no=5``).
    * Creates a loguru file sink at ``<log_dir>/<startup-timestamp>.txt``.
    * Installs :class:`InterceptHandler` on the stdlib root logger so that
      every existing ``logging.getLogger()`` call is captured.

    Parameters
    ----------
    log_dir:
        Directory for log files.  Defaults to :func:`resolve_log_dir`.
    """
    global _VERBOSE_REGISTERED  # noqa: PLW0603  # pylint: disable=global-statement

    # 1. Register VERBOSE level (idempotent guard)
    if not _VERBOSE_REGISTERED:
        try:
            logger.level("VERBOSE", no=_VERBOSE_LEVEL_NO)
        except TypeError:
            pass  # Already registered in this process
        _VERBOSE_REGISTERED = True

    # 2. Resolve log directory
    if log_dir is None:
        log_dir = resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    # 3. Build file path with startup timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = log_dir / f"{timestamp}.txt"

    # 4. Add loguru file sink
    logger.add(
        str(log_path),
        level=_VERBOSE_LEVEL_NO,
        rotation="10 MB",
        retention=10,
        encoding="utf-8",
        format=_log_format,
        enqueue=False,  # synchronous writes for predictable test behavior
    )

    # 5. Install InterceptHandler on stdlib root logger
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
