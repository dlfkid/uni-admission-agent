"""Smoke test for the `crawl-index` CLI command."""
import json
from unittest.mock import patch

from typer.testing import CliRunner

from src.cmd.cli import app
from src.services.crawl_strategy.types import CrawlOutcome

runner = CliRunner()


def test_crawl_index_prints_outcome_json():
    fake = CrawlOutcome(status="ok", university="leeds", names=["A MSc", "B MSc"],
                        names_count=2, strategy_used="server×heading_link",
                        message_for_user="成功抓取 2 门课程名字。")
    with patch("src.cmd.cli.crawl_index", return_value=fake):
        result = runner.invoke(app, ["crawl-index", "https://courses.leeds.ac.uk/x", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "ok"
    assert payload["names_count"] == 2
    assert payload["message_for_user"]
