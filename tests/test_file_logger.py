"""Tests for src.core.file_logger."""

import logging
import sys
import time
from pathlib import Path

import pytest
from loguru import logger

from src.core.file_logger import resolve_log_dir, setup_file_logging


@pytest.fixture(autouse=True)
def _clean_loguru_sinks():
    """Remove all loguru sinks before each test to avoid cross-contamination."""
    logger.remove()
    yield
    logger.remove()


class TestResolveLogDir:
    """Tests for resolve_log_dir()."""

    def test_returns_data_logs_in_dev_mode(self, monkeypatch):
        """Non-frozen mode returns <project>/data/logs (gitignored), not CWD."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        from src.core.paths import get_data_dir
        result = resolve_log_dir()
        assert result == get_data_dir() / "logs"

    def test_returns_executable_parent_in_frozen_mode(self, monkeypatch):
        """Frozen mode returns parent directory of sys.executable."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        fake_exe = Path("/opt/myapp/bin/server")
        monkeypatch.setattr(sys, "executable", str(fake_exe))
        result = resolve_log_dir()
        assert result == Path("/opt/myapp/bin")


class TestSetupFileLogging:
    """Tests for setup_file_logging()."""

    def test_creates_log_file_on_setup(self, tmp_path):
        """Log file is created in the specified directory after first log."""
        setup_file_logging(log_dir=tmp_path)

        test_logger = logging.getLogger("test.setup")
        test_logger.info("hello from test")

        time.sleep(0.1)

        txt_files = list(tmp_path.glob("*.txt"))
        assert len(txt_files) >= 1, f"Expected >=1 .txt file, found {txt_files}"

        content = txt_files[0].read_text(encoding="utf-8")
        assert "hello from test" in content

    def test_log_file_contains_level_and_module(self, tmp_path):
        """Log lines include level and module name."""
        setup_file_logging(log_dir=tmp_path)

        test_logger = logging.getLogger("mymodule.sub")
        test_logger.warning("something went wrong")

        time.sleep(0.1)

        txt_files = list(tmp_path.glob("*.txt"))
        assert len(txt_files) >= 1
        content = txt_files[0].read_text(encoding="utf-8")
        assert "WARNING" in content
        assert "something went wrong" in content

    def test_creates_log_dir_if_missing(self, tmp_path):
        """If log_dir doesn't exist, create it."""
        nested_dir = tmp_path / "no" / "such" / "deeply" / "nested"
        setup_file_logging(log_dir=nested_dir)
        assert nested_dir.exists()


class TestIntegration:
    """Verify setup_logging activates file logging."""

    def test_setup_logging_creates_log_file(self, tmp_path, monkeypatch):
        """Calling setup_logging() should produce a .txt log file in data/logs."""
        from src.core.environment import setup_logging

        monkeypatch.delattr(sys, "frozen", raising=False)
        # Redirect the data dir to a temp location so the log lands there
        # (resolve_log_dir now returns get_data_dir()/logs, not cwd).
        monkeypatch.setattr("src.core.file_logger.get_data_dir", lambda: tmp_path)

        setup_logging(verbose=False)

        logging.getLogger("integration.test").info("integration check")
        time.sleep(0.1)

        txt_files = list((tmp_path / "logs").glob("*.txt"))
        assert len(txt_files) >= 1, f"No log files created in {tmp_path / 'logs'}"
        content = txt_files[0].read_text(encoding="utf-8")
        assert "integration check" in content
