import json
from pathlib import Path

from src.services.program_name_resolution import resolve_program_name
from src.scrapers.helpers import extract_program_name


def test_leeds_detail_resolves_ai_for_business_name() -> None:
    case_dir = Path("golden_samples/cases/leeds_masters_ai_business")
    markdown = (case_dir / "detail.md").read_text(encoding="utf-8")
    html = (case_dir / "detail.html").read_text(encoding="utf-8")
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))

    markdown_name = extract_program_name(markdown)
    result = resolve_program_name(
        markdown_name=markdown_name,
        selected_anchor_text="AI for Business MSc",
        detail_url="https://courses.leeds.ac.uk/k198/ai-for-business-msc",
        html_title="AI for Business MSc | University of Leeds",
        is_index_mode=True,
        llm_fallback_enabled=False,
    )

    assert html
    assert result.status == "resolved"
    assert result.name == expected["expected_name"]
