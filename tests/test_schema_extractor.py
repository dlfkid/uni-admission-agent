"""Tests for src.scrapers.schema_extractor."""

import json
from pathlib import Path

import pytest

from src.scrapers.schema_extractor import FieldSpec, SelectorSchema, SchemaManager


class TestSelectorSchema:
    """Tests for the SelectorSchema data model."""

    def test_create_schema_with_fields(self):
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com/page1",
            baseline_score=0.83,
            total_fields=6,
            fields={
                "faculty": FieldSpec(selector="div.school a", attribute="text", sample_value="Medical School"),
                "name_en": FieldSpec(selector="h1.page-title", attribute="text"),
            },
        )
        assert schema.univ_slug == "edinburgh"
        assert schema.baseline_score == 0.83
        assert schema.fields["faculty"].selector == "div.school a"

    def test_schema_roundtrip_json(self, tmp_path):
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com/page1",
            baseline_score=0.85,
            total_fields=6,
            fields={
                "name_en": FieldSpec(selector="h1", attribute="text"),
                "faculty": FieldSpec(selector="div.school", attribute="text", sample_value="Engineering"),
            },
        )
        path = tmp_path / "test_schema.json"
        path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
        loaded = SelectorSchema.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.univ_slug == "edinburgh"
        assert loaded.fields["faculty"].sample_value == "Engineering"


class TestSchemaManager:
    """Tests for SchemaManager file operations."""

    def test_save_and_load(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        schema = SelectorSchema(
            version=1,
            univ_slug="edinburgh",
            page_pattern="postgraduate-taught",
            source_url="https://example.com",
            baseline_score=0.80,
            total_fields=6,
            fields={"name_en": FieldSpec(selector="h1", attribute="text")},
        )
        mgr.save(schema)
        loaded = mgr.load("edinburgh", "postgraduate-taught")
        assert loaded is not None
        assert loaded.baseline_score == 0.80

    def test_load_nonexistent_returns_none(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        assert mgr.load("nonexistent", "pattern") is None

    def test_deprecate_renames_file(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        schema = SelectorSchema(
            version=1,
            univ_slug="ucl",
            page_pattern="taught-degrees",
            source_url="https://example.com",
            baseline_score=0.90,
            total_fields=6,
            fields={"name_en": FieldSpec(selector="h1", attribute="text")},
        )
        mgr.save(schema)
        mgr.deprecate("ucl", "taught-degrees")
        assert mgr.load("ucl", "taught-degrees") is None
        deprecated = list(tmp_path.glob("*.deprecated.json"))
        assert len(deprecated) == 1

    def test_load_corrupted_json_returns_none(self, tmp_path):
        mgr = SchemaManager(schemas_dir=tmp_path)
        schema_file = tmp_path / "edinburgh_postgraduate-taught.json"
        schema_file.write_text("{broken", encoding="utf-8")
        assert mgr.load("edinburgh", "postgraduate-taught") is None


from decimal import Decimal
from src.scrapers.schema_extractor import SelectorExtractor


SAMPLE_HTML = """
<html>
<body>
<h1 class="page-title">Cognitive Science MSc</h1>
<div class="field-school"><a href="/school">School of Informatics</a></div>
<div class="tuition-fee">£12,500 per year</div>
<div class="study-options">
  <div class="option">Full-time | 1 year</div>
  <div class="option">Part-time | 2 years</div>
</div>
<table class="deadlines">
  <tr><td>Round 1</td><td>31 January 2026</td></tr>
  <tr><td>Round 2</td><td>30 June 2026</td></tr>
</table>
<div class="entry-requirements">
  <p>A UK 2:1 honours degree in psychology, linguistics, or related discipline.</p>
</div>
</body>
</html>
"""


class TestSelectorExtractor:
    """Tests for CSS selector extraction."""

    def test_extract_text_field(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"name_en": FieldSpec(selector="h1.page-title", attribute="text")},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["name_en"] == "Cognitive Science MSc"

    def test_extract_missing_field_returns_none(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"faculty": FieldSpec(selector="div.nonexistent", attribute="text")},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["faculty"] is None

    def test_extract_list_field(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"study_options": FieldSpec(
                selector="div.study-options div.option",
                attribute="text", is_list=True,
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert len(result["study_options"]) == 2
        assert "Full-time" in result["study_options"][0]

    def test_extract_href_attribute(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"school_link": FieldSpec(
                selector="div.field-school a", attribute="href",
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["school_link"] == "/school"

    def test_post_process_extract_decimal(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test",
            fields={"tuition_amount": FieldSpec(
                selector="div.tuition-fee", attribute="text",
                post_process="extract_decimal",
            )},
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        assert result["tuition_amount"] == Decimal("12500")

    def test_compute_score(self):
        schema = SelectorSchema(
            univ_slug="test", page_pattern="test", total_fields=3,
            fields={
                "name_en": FieldSpec(selector="h1.page-title", attribute="text"),
                "faculty": FieldSpec(selector="div.nonexistent", attribute="text"),
                "tuition_amount": FieldSpec(selector="div.tuition-fee", attribute="text"),
            },
        )
        result = SelectorExtractor.extract(SAMPLE_HTML, schema)
        score = SelectorExtractor.compute_score(result, schema)
        assert abs(score - 2 / 3) < 0.01
