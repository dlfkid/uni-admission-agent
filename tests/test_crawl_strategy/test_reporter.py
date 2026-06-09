import json
import zipfile
from pathlib import Path

from src.services.crawl_strategy.reporter import export_report_zip


def test_export_writes_zip_with_required_members(tmp_path):
    zip_path = export_report_zip(
        out_dir=tmp_path,
        index_url="https://study.nus.edu.sg/programme",
        html="<html>nus</html>",
        markdown="# NUS\nFind a programme",
        params={
            "fetch_level_used": "client_wait",
            "fetch_levels_tried": ["server", "client", "client_wait"],
            "content_signal": {"chars": 17000, "links": 70, "degree_hits": 0, "nav_ratio": 0.9},
            "feature_signals": {"heading_link": 0, "inline_degree": 0, "blob": 0, "text_heading": 0},
            "strategy_scores": {"heading_link": 0},
            "llm_classified_as": None,
            "llm_extract_count": 0,
            "outcome": "unsupported",
        },
        run_log="server→empty\nclient→empty\nclient_wait→17KB nav only\n",
        timestamp="20260609-120000",
    )
    p = Path(zip_path)
    assert p.exists() and p.suffix == ".zip"
    with zipfile.ZipFile(p) as zf:
        names = set(zf.namelist())
        assert {"index.html", "index.md", "params.json", "run.log"} <= names
        params = json.loads(zf.read("params.json"))
        assert params["outcome"] == "unsupported"
        assert params["index_url"] == "https://study.nus.edu.sg/programme"


def test_zip_named_by_domain_and_timestamp(tmp_path):
    zip_path = export_report_zip(
        out_dir=tmp_path, index_url="https://study.nus.edu.sg/programme",
        html="x", markdown="y", params={"outcome": "unsupported"},
        run_log="", timestamp="20260609-120000",
    )
    assert Path(zip_path).name == "study.nus.edu.sg-20260609-120000.zip"
