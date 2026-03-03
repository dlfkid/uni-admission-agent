from pathlib import Path

from src.services.golden_samples import collect_golden_samples, slugify_case_id


def test_slugify_case_id() -> None:
    assert slugify_case_id("UCL Anthropology") == "ucl_anthropology"
    assert slugify_case_id("  ") == "case"


def test_collect_golden_samples_writes_files(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"cases": [{"case_id": "case_a", "name": "Case A", "index_url": "https://x/index", "detail_url": "https://x/detail"}]}',
        encoding="utf-8",
    )

    def fake_fetch(url: str, timeout_seconds: int = 40):
        _ = timeout_seconds
        return {
            "status_code": 200,
            "html": f"<html><head><title>{url}</title></head><body><h1>Program A</h1></body></html>",
            "error": None,
        }

    monkeypatch.setattr("src.services.golden_samples._fetch_html", fake_fetch)

    report = collect_golden_samples(
        manifest_path=str(manifest),
        output_root=str(tmp_path / "cases"),
        overwrite=True,
    )

    assert report["collected"] == 1
    case_dir = tmp_path / "cases" / "case_a"
    assert (case_dir / "index.html").exists()
    assert (case_dir / "index.md").exists()
    assert (case_dir / "detail.html").exists()
    assert (case_dir / "detail.md").exists()
    assert (case_dir / "metadata.json").exists()
