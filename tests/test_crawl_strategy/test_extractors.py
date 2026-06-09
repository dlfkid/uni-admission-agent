from pathlib import Path

from src.services.crawl_strategy.extractors import EXTRACTORS, get_extractor
from src.services.crawl_strategy.types import ExtractKind

BASE = "https://courses.leeds.ac.uk/course-search/masters-courses"

_NUS_FIXTURE = (
    Path(__file__).parent.parent.parent
    / "golden_samples" / "cases" / "nus_render" / "index.md"
)


def test_heading_link_extracts_name_and_url():
    md = "##  [Accounting and Finance MSc](https://courses.leeds.ac.uk/f921/accounting-and-finance-msc) Duration\n"
    items = get_extractor(ExtractKind.HEADING_LINK)(md, BASE)
    assert [i.name_en for i in items] == ["Accounting and Finance MSc"]
    assert items[0].detail_url.endswith("/f921/accounting-and-finance-msc")


def test_inline_degree_extracts_ucl_style():
    md = ("[Search](https://www.ucl.ac.uk/x#tab1)\n"
          "[Anthropology BSc](https://www.ucl.ac.uk/prospective-students/undergraduate/degrees/anthropology-bsc)\n")
    items = get_extractor(ExtractKind.INLINE_DEGREE)(md, "https://www.ucl.ac.uk/")
    assert [i.name_en for i in items] == ["Anthropology BSc"]


def test_merged_columns_strips_duration():
    md = "[Accounting MSc (1 year)](https://www.manchester.ac.uk/study/masters/courses/list/10867/msc-accounting/)\n"
    items = get_extractor(ExtractKind.MERGED_COLUMNS)(md, "https://www.manchester.ac.uk/")
    assert [i.name_en for i in items] == ["Accounting MSc"]


def test_blob_extracts_english_name():
    md = ("[ 02022 | Sept 2026 Entry  Full-time - 1 year  Business Management - MSc - Master of Science  "
          "商業管理 ](https://www.polyu.edu.hk/study/pg/tpg/2026/02022)\n")
    items = get_extractor(ExtractKind.BLOB)(md, "https://www.polyu.edu.hk/")
    assert [i.name_en for i in items] == ["Business Management MSc"]


def test_text_heading_pairs_name_with_learn_more():
    md = ("### Doctor of Engineering (Biomedical Engineering)\n"
          "Intake Period: Aug\n"
          "[Learn More](https://nus.edu.sg/programme/doctor-of-engineering-biomedical)\n")
    items = get_extractor(ExtractKind.TEXT_HEADING)(md, "https://study.nus.edu.sg/")
    assert items[0].name_en == "Doctor of Engineering (Biomedical Engineering)"
    assert items[0].detail_url.endswith("/doctor-of-engineering-biomedical")


_NOISE_LABELS = {"Programme Type", "Area of Interest", "Mode of Study", "Intake Period", "School/Faculty"}


def test_nus_golden_fixture_text_heading():
    """extract_text_heading on the NUS render fixture yields exactly 10 programs."""
    md = _NUS_FIXTURE.read_text(encoding="utf-8")
    items = get_extractor(ExtractKind.TEXT_HEADING)(md, "https://study.nus.edu.sg/programme")
    names = [i.name_en for i in items]

    assert len(items) == 10, f"Expected 10 items, got {len(items)}: {names}"

    assert "Doctor of Engineering (Biomedical Engineering)" in names
    assert "Doctor of Medicine (MD)" in names
    assert "Doctor of Nursing Practice" in names

    for label in _NOISE_LABELS:
        assert label not in names, f"Noise label {label!r} leaked into results"

    for item in items:
        assert item.detail_url is None, (
            f"Expected detail_url=None for NUS (asset Learn More), "
            f"got {item.detail_url!r} for {item.name_en!r}"
        )


def test_every_extractkind_except_llm_is_registered():
    for kind in ExtractKind:
        if kind is ExtractKind.LLM:
            continue
        assert kind in EXTRACTORS
