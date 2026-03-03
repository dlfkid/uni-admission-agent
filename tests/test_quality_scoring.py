import json
from pathlib import Path

from src.services.quality_scoring import extract_offline_observation, score_manifest


def test_extract_offline_observation_basic() -> None:
    markdown = """
# MSc Business Psychology

Tuition fees: GBP 15,000

Full-time 1 year

Entry requirements: IELTS 7.0 overall.
"""
    observed = extract_offline_observation(
        markdown=markdown,
        source_url="https://example.com/program",
    )

    assert observed["name_en"] == "MSc Business Psychology"
    assert observed["tuition"]["currency"] == "GBP"
    assert observed["study_options"][0]["mode"] == "FullTime"
    assert observed["requirements"][0]["category"] in {
        "language",
        "academic_subject",
        "standardized_test",
        "other",
    }


def test_score_manifest_passes_with_seed_case(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    case_dir = cases_dir / "case_a"
    case_dir.mkdir(parents=True)

    (case_dir / "detail.md").write_text(
        "# AI for Business MSc\n\nTuition: GBP 20,000\n\nFull-time 1 year\n\nIELTS 6.5",
        encoding="utf-8",
    )
    (case_dir / "metadata.json").write_text(
        json.dumps({"pages": {"detail": {"url": "https://example.com/case-a"}}}),
        encoding="utf-8",
    )
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "expected_name": "AI for Business MSc",
                "expected_keywords": ["AI", "Business", "MSc"],
                "required_fields": ["name_en"],
                "case_threshold": 0.5,
            }
        ),
        encoding="utf-8",
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case_a",
                        "name": "Case A",
                        "index_url": "https://example.com/index",
                        "detail_url": "https://example.com/case-a",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = score_manifest(
        manifest_path=str(manifest),
        base_dir=str(cases_dir),
        output_report_path=str(tmp_path / "report.json"),
        global_threshold=0.5,
    )

    assert report["global_pass"] is True
    assert report["aggregate"]["passed_case_count"] == 1
    assert report["cases"][0]["status"] == "passed"
