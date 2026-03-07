import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app
from src.api.task_manager import TaskManager
from src.services.crawler import CrawlResult


def test_api_crawl_passes_browser_provider_args(monkeypatch) -> None:
    captured: dict = {}

    async def fake_crawl_url(**kwargs):
        captured.update(kwargs)
        return CrawlResult(
            imported_count=0,
            univ_slug="u",
            year=2026,
            ingestion_job_id="job-api",
        )

    monkeypatch.setattr("src.api.server.crawl_url", fake_crawl_url)
    monkeypatch.setattr("src.api.server.task_manager", TaskManager())
    with (
        patch("src.api.server.DatabaseManager"),
        patch("src.api.server.bootstrap_subject_taxonomy", return_value=None),
        TestClient(app) as client,
    ):
        res = client.post(
            "/crawl",
            json={
                "url": "https://example.edu",
                "univ_slug": "u",
                "year": 2026,
                "browser_provider": "client",
                "client_id": "c1",
                "strict_client": True,
                "candidate_taxonomy_filter_enabled": True,
                "candidate_taxonomy_filter_threshold": 0.82,
                "candidate_taxonomy_filter_top_k": 9,
            },
        )
        assert res.status_code == 200
        task_id = res.json()["task_id"]
        state = ""
        for _ in range(50):
            task = client.get(f"/tasks/{task_id}").json()
            state = task["state"]
            if state in {"DONE", "FAILED"}:
                break
            time.sleep(0.02)
        assert state == "DONE"

    assert captured["browser_provider"] == "client"
    assert captured["client_id"] == "c1"
    assert captured["strict_client"] is True
    assert captured["candidate_taxonomy_filter_enabled"] is True
    assert captured["candidate_taxonomy_filter_threshold"] == 0.82
    assert captured["candidate_taxonomy_filter_top_k"] == 9
