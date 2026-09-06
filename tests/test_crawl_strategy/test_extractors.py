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
    """extract_text_heading on the NUS render fixture yields exactly 10 programs.

    Each "Learn More" link's label wraps two decorative arrow icons in nested
    ``![Arrow](...)`` image links (Salesforce Lightning markup), e.g.:
    ``[Learn More![Arrow](icon1)![Arrow](icon2)](https://cde.nus.edu.sg/...)``.
    A link-label regex that cannot handle one level of nested "[...]" breaks
    on the first icon's "]" and never reaches the real outer target, so it
    used to see nothing here at all. With that fixed, the real "Learn More"
    destination is captured and passed through the existing path filter,
    which only keeps URLs containing "programme"/"course" in their path
    (several genuine department pages here happen not to, e.g.
    ".../research-degrees/", so those legitimately stay None).
    """
    md = _NUS_FIXTURE.read_text(encoding="utf-8")
    items = get_extractor(ExtractKind.TEXT_HEADING)(md, "https://study.nus.edu.sg/programme")
    names = [i.name_en for i in items]
    by_name = {i.name_en: i.detail_url for i in items}

    assert len(items) == 10, f"Expected 10 items, got {len(items)}: {names}"

    assert "Doctor of Engineering (Biomedical Engineering)" in names
    assert "Doctor of Medicine (MD)" in names
    assert "Doctor of Nursing Practice" in names

    for label in _NOISE_LABELS:
        assert label not in names, f"Noise label {label!r} leaked into results"

    assert by_name["Doctor of Engineering (Biomedical Engineering)"] == (
        "https://cde.nus.edu.sg/bme/graduate-research-programmes/"
    )
    assert by_name["Doctor of Medicine (MD)"] == (
        "https://www.duke-nus.edu.sg/education/our-programmes/md-programme"
    )
    assert by_name["Doctor of Nursing Practice"] == (
        "https://medicine.nus.edu.sg/nursing/education-programmes/postgraduate/"
        "doctorate/doctor-of-nursing-practice-dnp/"
    )
    # Genuine "Learn More" destinations whose path lacks "programme"/"course"
    # are correctly filtered out by the existing path check, not silently
    # replaced by the decorative arrow-icon asset URLs.
    assert by_name["Doctor of Engineering (Built Environment)"] is None
    for name, url in by_name.items():
        if url is not None:
            assert "org-asset" not in url and "/resource/" not in url, (
                f"Decorative asset URL leaked as detail_url for {name!r}: {url!r}"
            )


def test_inline_degree_extracts_cuhk_prefix_style():
    """extract_inline_degree must match degree-prefix link text (MA in X, MPhil in Y)."""
    md = (
        "[MA in Anthropology](https://www.gs.cuhk.edu.hk/programmes/arts/ma-anthropology)\n"
        "[MPhil in Chinese Studies](https://www.gs.cuhk.edu.hk/programmes/arts/mphil-chinese-studies)\n"
        "[PhD in Chinese Studies](https://www.gs.cuhk.edu.hk/programmes/arts/phd-chinese-studies)\n"
        "[MPhil-PhD in Cultural Studies](https://www.gs.cuhk.edu.hk/programmes/arts/mphil-phd-cultural-studies)\n"
        "[Executive MBA](https://www.gs.cuhk.edu.hk/programmes/business-administration/executive-mba)\n"
        "[Juris Doctor/MBA](https://www.gs.cuhk.edu.hk/programmes/business-administration/juris-doctormba)\n"
        "[Postgraduate Diploma in Law](https://www.gs.cuhk.edu.hk/programmes/law/postgraduate-diploma-law)\n"
        "[About Us](https://www.gs.cuhk.edu.hk/about-us)\n"  # noise — must be excluded
    )
    items = get_extractor(ExtractKind.INLINE_DEGREE)(md, "https://www.gs.cuhk.edu.hk/")
    names = [i.name_en for i in items]
    assert "MA in Anthropology" in names
    assert "MPhil in Chinese Studies" in names
    assert "PhD in Chinese Studies" in names
    assert "MPhil-PhD in Cultural Studies" in names
    assert "Executive MBA" in names
    assert "Juris Doctor/MBA" in names
    assert "Postgraduate Diploma in Law" in names
    assert "About Us" not in names


def test_inline_degree_cuhk_golden_fixture_counts():
    """extract_inline_degree on the CUHK golden sample must find ≥200 programmes."""
    from pathlib import Path
    md_path = (
        Path(__file__).parent.parent.parent
        / "golden_samples" / "cases" / "cuhk_masters_arts" / "index.md"
    )
    md = md_path.read_text(encoding="utf-8")
    items = get_extractor(ExtractKind.INLINE_DEGREE)(md, "https://www.gs.cuhk.edu.hk/")
    assert len(items) >= 200, f"Expected ≥200 CUHK programmes, got {len(items)}"


def test_every_markdown_extractkind_is_registered():
    # LLM (future) and JSON_API (config-driven via make_json_api_extractor, not
    # a markdown extractor) are intentionally absent from the EXTRACTORS dict.
    exempt = {ExtractKind.LLM, ExtractKind.JSON_API}
    for kind in ExtractKind:
        if kind in exempt:
            continue
        assert kind in EXTRACTORS


def test_inline_degree_hkbu_golden_fixture_counts():
    """extract_inline_degree on the HKBU golden sample must find exactly the
    49 programmes the live 2027 crawl imported — each with its own detail URL,
    so no two programmes collapse into one catalog row."""
    from pathlib import Path
    md_path = (
        Path(__file__).parent.parent.parent
        / "golden_samples" / "cases" / "hkbu_masters_communication" / "index.md"
    )
    md = md_path.read_text(encoding="utf-8")
    items = get_extractor(ExtractKind.INLINE_DEGREE)(
        md, "https://ar.hkbu.edu.hk/tpg-admissions/programmes"
    )
    names = [it.name_en for it in items]
    assert len(names) == 49, f"Expected 49 HKBU programmes, got {len(names)}"
    assert len(set(names)) == 49
    assert len({it.detail_url for it in items}) == 49
