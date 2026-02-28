"""Tests for src/storage/db_manager.py – DatabaseManager.

Database-touching methods use a mock engine or skip with reason.
Pure functions (_normalize_text_payload, _sanitize_db_url) are tested directly.
"""

from unittest.mock import MagicMock, patch, PropertyMock
from typing import Any

import pytest

from src.storage.db_manager import _normalize_text_payload, DatabaseManager


# ── _normalize_text_payload ──────────────────────────────────────────


class TestNormalizeTextPayload:
    def test_string_passthrough(self) -> None:
        assert _normalize_text_payload("hello") == "hello"

    def test_none_passthrough(self) -> None:
        assert _normalize_text_payload(None) is None

    def test_int_passthrough(self) -> None:
        assert _normalize_text_payload(42) == 42

    def test_bytes_utf8(self) -> None:
        result = _normalize_text_payload("你好".encode("utf-8"))
        assert result == "你好"

    def test_bytes_gb18030(self) -> None:
        raw = "中文".encode("gb18030")
        result = _normalize_text_payload(raw)
        assert result == "中文"

    def test_bytes_latin1_fallback(self) -> None:
        raw = bytes([0xff, 0xfe])  # Not valid utf-8 or gb18030
        result = _normalize_text_payload(raw)
        assert isinstance(result, str)

    def test_dict_recursive(self) -> None:
        payload = {"key": "hello".encode("utf-8"), "num": 1}
        result = _normalize_text_payload(payload)
        assert result == {"key": "hello", "num": 1}

    def test_list_recursive(self) -> None:
        payload = ["ok", b"bytes"]
        result = _normalize_text_payload(payload)
        assert result == ["ok", "bytes"]

    def test_tuple_recursive(self) -> None:
        payload = (b"a", "b")
        result = _normalize_text_payload(payload)
        assert result == ("a", "b")

    def test_nested_dict_list(self) -> None:
        payload = {"items": [{"val": b"test"}]}
        result = _normalize_text_payload(payload)
        assert result == {"items": [{"val": "test"}]}

    def test_bytes_key_in_dict(self) -> None:
        payload = {b"key": "value"}
        result = _normalize_text_payload(payload)
        assert result == {"key": "value"}


# ── _sanitize_db_url ────────────────────────────────────────────────


class TestSanitizeDbUrl:
    def test_appends_client_encoding(self) -> None:
        url = "postgresql+psycopg2://user:pass@localhost/db"
        result = DatabaseManager._sanitize_db_url(url)
        assert "client_encoding=utf8" in result
        assert result.endswith("?client_encoding=utf8")

    def test_ampersand_if_existing_params(self) -> None:
        url = "postgresql+psycopg2://user:pass@localhost/db?sslmode=require"
        result = DatabaseManager._sanitize_db_url(url)
        assert "&client_encoding=utf8" in result

    def test_no_duplicate_encoding(self) -> None:
        url = "postgresql+psycopg2://user:pass@localhost/db?client_encoding=utf8"
        result = DatabaseManager._sanitize_db_url(url)
        assert result.count("client_encoding") == 1

    def test_non_postgres_no_encoding(self) -> None:
        url = "sqlite:///test.db"
        result = DatabaseManager._sanitize_db_url(url)
        assert "client_encoding" not in result

    def test_pure_ascii_url(self) -> None:
        url = "postgresql+psycopg2://user:pass@localhost:5432/mydb"
        result = DatabaseManager._sanitize_db_url(url)
        assert result.startswith("postgresql+psycopg2://")

    def test_unicode_url_repaired(self) -> None:
        """Non-ASCII URL should be preserved or re-decoded."""
        url = "postgresql+psycopg2://user:密码@localhost/db"
        result = DatabaseManager._sanitize_db_url(url)
        assert "localhost" in result


# ── DatabaseManager singleton ────────────────────────────────────────


class TestDatabaseManagerSingleton:
    """Test singleton behaviour without actual DB connection."""

    def setup_method(self) -> None:
        # Reset singleton between tests
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_singleton_same_instance(self) -> None:
        a = DatabaseManager()
        b = DatabaseManager()
        assert a is b

    def test_initialized_flag_false_initially(self) -> None:
        dm = DatabaseManager()
        assert dm.initialized is False


# ── init_db ──────────────────────────────────────────────────────────


class TestInitDb:
    def setup_method(self) -> None:
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_init_db_uses_env_var(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@localhost/test")
        dm = DatabaseManager()

        with (
            patch("src.storage.db_manager.create_engine") as mock_ce,
            patch("src.storage.db_manager.database_exists", return_value=True),
            patch.object(dm, "_sync_schema"),
            patch("sqlmodel.SQLModel.metadata") as mock_meta,
        ):
            mock_engine = MagicMock()
            mock_ce.return_value = mock_engine
            mock_meta.create_all = MagicMock()

            dm.init_db()

        assert dm.engine is mock_engine

    def test_init_db_explicit_url(self) -> None:
        dm = DatabaseManager()
        url = "postgresql+psycopg2://user:pass@host/db"

        with (
            patch("src.storage.db_manager.create_engine") as mock_ce,
            patch("src.storage.db_manager.database_exists", return_value=True),
            patch.object(dm, "_sync_schema"),
            patch("sqlmodel.SQLModel.metadata") as mock_meta,
        ):
            mock_engine = MagicMock()
            mock_ce.return_value = mock_engine
            mock_meta.create_all = MagicMock()

            dm.init_db(db_url=url)

        mock_ce.assert_called_once()
        call_url = mock_ce.call_args[0][0]
        assert "user:pass@host/db" in call_url

    def test_init_db_skips_if_engine_exists(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        with patch("src.storage.db_manager.create_engine") as mock_ce:
            dm.init_db()  # Should return early

        mock_ce.assert_not_called()

    def test_init_db_creates_database_if_missing(self) -> None:
        dm = DatabaseManager()
        url = "postgresql+psycopg2://user:pass@host/newdb"

        with (
            patch("src.storage.db_manager.create_engine") as mock_ce,
            patch("src.storage.db_manager.database_exists", return_value=False),
            patch("src.storage.db_manager.create_database") as mock_create,
            patch.object(dm, "_sync_schema"),
            patch("sqlmodel.SQLModel.metadata") as mock_meta,
        ):
            mock_engine = MagicMock()
            mock_ce.return_value = mock_engine
            mock_meta.create_all = MagicMock()

            dm.init_db(db_url=url)

        mock_create.assert_called_once()

    def test_init_db_raises_on_error(self) -> None:
        dm = DatabaseManager()

        with (
            patch("src.storage.db_manager.create_engine", side_effect=RuntimeError("fail")),
        ):
            with pytest.raises(RuntimeError, match="fail"):
                dm.init_db(db_url="postgresql+psycopg2://u:p@h/d")


# ── get_session ──────────────────────────────────────────────────────


class TestGetSession:
    def setup_method(self) -> None:
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_get_session_calls_init_if_no_engine(self) -> None:
        dm = DatabaseManager()

        with patch.object(dm, "init_db") as mock_init:
            mock_init.side_effect = lambda: setattr(dm, "engine", MagicMock())
            with patch("src.storage.db_manager.Session") as MockSession:
                dm.get_session()

            mock_init.assert_called_once()

    def test_get_session_returns_session(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        with patch("src.storage.db_manager.Session") as MockSession:
            session = dm.get_session()

        MockSession.assert_called_once_with(dm.engine)


# ── get_university_by_slug ───────────────────────────────────────────


class TestGetUniversityBySlug:
    def setup_method(self) -> None:
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_returns_university(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        mock_univ = MagicMock()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.first.return_value = mock_univ

        with patch("src.storage.db_manager.Session", return_value=mock_session):
            result = dm.get_university_by_slug("hku")

        assert result is mock_univ

    def test_returns_none_when_not_found(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.first.return_value = None

        with patch("src.storage.db_manager.Session", return_value=mock_session):
            result = dm.get_university_by_slug("nonexistent")

        assert result is None


# ── get_program_history ──────────────────────────────────────────────


class TestGetProgramHistory:
    def setup_method(self) -> None:
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_returns_ordered_programs(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        mock_programs = [MagicMock(), MagicMock()]
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = mock_programs

        with patch("src.storage.db_manager.Session", return_value=mock_session):
            result = dm.get_program_history("MSC-FIN")

        assert result == mock_programs


# ── get_program_contexts ─────────────────────────────────────────────


class TestGetProgramContexts:
    def setup_method(self) -> None:
        DatabaseManager._instance = None

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def test_deduplicates_by_name_and_group(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        p1 = MagicMock()
        p1.name_en = "MSc Finance"
        p1.program_group_code = "FIN"
        p1.academic_year = 2025
        p1.faculty = "Business"
        p1.tuition_amount = None
        p1.currency = None

        p2 = MagicMock()
        p2.name_en = "MSc Finance"
        p2.program_group_code = "FIN"
        p2.academic_year = 2024
        p2.faculty = "Business"
        p2.tuition_amount = None
        p2.currency = None

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = [p1, p2]

        with patch("src.storage.db_manager.Session", return_value=mock_session):
            result = dm.get_program_contexts(1)

        # Same (name_en, group_code) → deduplicated to 1
        assert len(result) == 1
        assert result[0].name_en == "MSc Finance"

    def test_multiple_groups_returned(self) -> None:
        dm = DatabaseManager()
        dm.engine = MagicMock()

        p1 = MagicMock()
        p1.name_en = "MSc Finance"
        p1.program_group_code = "FIN"
        p1.academic_year = 2025
        p1.faculty = "Business"
        p1.tuition_amount = None
        p1.currency = None

        p2 = MagicMock()
        p2.name_en = "MA English"
        p2.program_group_code = "ENG"
        p2.academic_year = 2025
        p2.faculty = "Arts"
        p2.tuition_amount = None
        p2.currency = None

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.exec.return_value.all.return_value = [p1, p2]

        with patch("src.storage.db_manager.Session", return_value=mock_session):
            result = dm.get_program_contexts(1)

        assert len(result) == 2
