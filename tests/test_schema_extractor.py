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
