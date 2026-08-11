from __future__ import annotations

import pytest

from src.api import server
from src.services.crawler import ingest_program_records_external


def test_ingest_program_records_external_disables_auto_translation(monkeypatch) -> None:
    calls: list[dict] = []

    class _Program:
        def __init__(self, row_id: int) -> None:
            self.id = row_id

    class _DummyDB:
        def upsert_program(self, program_data, univ_slug, enable_auto_translation=True):
            calls.append(
                {
                    "program_data": dict(program_data),
                    "univ_slug": univ_slug,
                    "enable_auto_translation": enable_auto_translation,
                }
            )
            return _Program(100 + len(calls)), True

    monkeypatch.setattr("src.services.crawler.DatabaseManager", _DummyDB)
    monkeypatch.setattr(
        "src.services.crawler._build_review_items",
        lambda **_kwargs: [{"index": 1, "program_id": 101, "name_en": "MSc AI"}],
    )

    result = ingest_program_records_external(
        univ_slug="polyu",
        year=2026,
        programs=[{"name_en": "MSc AI", "source_url": "https://example.edu/ai"}],
    )

    assert result["imported_count"] == 1
    assert result["updated_count"] == 0
    assert result["failed_items"] == []
    assert calls[0]["enable_auto_translation"] is False
    assert calls[0]["program_data"]["academic_year"] == 2026


@pytest.mark.asyncio
async def test_mcp_ingest_calls_external_ingest_service(monkeypatch) -> None:
    captured: dict = {}

    def _fake_ingest_program_records_external(**kwargs):
        captured.update(kwargs)
        return {
            "imported_count": 1,
            "updated_count": 0,
            "failed_items": [],
            "review_token": "token-x",
            "review_items": [{"index": 1, "program_id": 1001, "name_en": "MSc Data"}],
        }

    monkeypatch.setattr(
        "src.api.server.ingest_program_records_external",
        _fake_ingest_program_records_external,
    )

    result = await server.mcp_ingest(
        univ_slug="edinburgh",
        year=2026,
        programs=[{"name_en": "MSc Data"}],
    )

    assert captured["univ_slug"] == "edinburgh"
    assert captured["year"] == 2026
    assert result["review_token"] == "token-x"
