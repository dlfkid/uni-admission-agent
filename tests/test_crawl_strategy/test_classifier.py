from src.services.crawl_strategy.classifier import classify, feature_signals
from src.services.crawl_strategy.types import ExtractKind

LEEDS = "https://courses.leeds.ac.uk/course-search/masters-courses"


def test_classify_heading_link_page():
    md = "".join(
        f"##  [Programme {i} MSc](https://courses.leeds.ac.uk/c{i}/programme-{i}-msc) Duration\n"
        for i in range(8))
    result = classify(md, LEEDS)
    assert result.kind is ExtractKind.HEADING_LINK
    assert result.confident is True
    assert result.count >= 8


def test_classify_inline_degree_page():
    md = "".join(
        f"[Programme {i} BSc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/programme-{i}-bsc)\n"
        for i in range(8))
    result = classify(md, "https://www.ucl.ac.uk/")
    assert result.kind is ExtractKind.INLINE_DEGREE
    assert result.confident is True


def test_nav_only_page_is_not_confident():
    md = "[Home](https://x/)\n[Search](https://x/s)\n[Apply Now](https://x/a)\n"
    result = classify(md, "https://x/")
    assert result.confident is False
    assert result.kind is None


def test_classify_cuhk_prefix_page():
    """Degree-prefix links (MA in X, MPhil in Y) must score as INLINE_DEGREE."""
    md = "".join(
        f"[MA in Subject {i}](https://www.gs.cuhk.edu.hk/programmes/arts/ma-subject-{i})\n"
        for i in range(8))
    result = classify(md, "https://www.gs.cuhk.edu.hk/")
    assert result.kind is ExtractKind.INLINE_DEGREE
    assert result.confident is True


def test_classify_cuhk_golden_fixture():
    """Classifier must auto-select inline_degree on the CUHK golden sample."""
    from pathlib import Path
    md_path = (
        Path(__file__).parent.parent.parent
        / "golden_samples" / "cases" / "cuhk_masters_arts" / "index.md"
    )
    md = md_path.read_text(encoding="utf-8")
    result = classify(md, "https://www.gs.cuhk.edu.hk/programme-filter?programme_type=All&study_mode=All&keys=")
    assert result.kind is ExtractKind.INLINE_DEGREE
    assert result.confident is True
    assert result.count >= 200


def test_feature_signals_counts():
    md = "##  [A MSc](https://x/a-msc)\n[B BSc](https://x/b-bsc)\n"
    sig = feature_signals(md, "https://x/")
    assert sig["heading_link"] >= 1
    assert sig["link_total"] >= 2
