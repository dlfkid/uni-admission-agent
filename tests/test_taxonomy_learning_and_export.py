import json

from src.services.subject_taxonomy import SubjectTaxonomyService, normalize_name


class InMemoryTaxonomyRepository:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def upsert_many(self, entries: list[dict]) -> dict:
        inserted = 0
        updated = 0
        for entry in entries:
            key = str(entry.get("normalized_name") or "").strip()
            if not key:
                continue
            if key in self._rows:
                self._rows[key].update(entry)
                updated += 1
            else:
                self._rows[key] = dict(entry)
                inserted += 1
        return {"inserted": inserted, "updated": updated}

    def list_active(self) -> list[dict]:
        return [
            dict(row)
            for row in self._rows.values()
            if str(row.get("status") or "active") == "active"
        ]


def test_learning_inserts_new_high_confidence_name_once() -> None:
    repository = InMemoryTaxonomyRepository()
    service = SubjectTaxonomyService(repository=repository)

    service.maybe_learn_name(
        name_en="Master of Science in Finance",
        confidence=0.95,
        source_url="https://example.com/finance",
        enabled=True,
    )
    service.maybe_learn_name(
        name_en="Master of Science in Finance",
        confidence=0.96,
        source_url="https://example.com/finance-v2",
        enabled=True,
    )

    rows = repository.list_active()
    normalized = normalize_name("Master of Science in Finance")
    learned_rows = [row for row in rows if row["normalized_name"] == normalized]
    assert len(learned_rows) == 1
    assert learned_rows[0]["source"] == "learned"


def test_taxonomy_export_includes_seed_and_learned(tmp_path) -> None:
    repository = InMemoryTaxonomyRepository()
    repository.upsert_many(
        [
            {
                "name_en": "Master of Science in Asset and Wealth Management",
                "normalized_name": normalize_name(
                    "Master of Science in Asset and Wealth Management"
                ),
                "aliases": [],
                "source": "seed",
                "status": "active",
            }
        ]
    )
    service = SubjectTaxonomyService(repository=repository)
    service.maybe_learn_name(
        name_en="Master of Science in Finance",
        confidence=0.95,
        source_url="https://example.com/finance",
        enabled=True,
    )

    output_path = tmp_path / "taxonomy.json"
    service.export_to_json(
        output_path=str(output_path),
        include_learned=True,
        min_confidence=0.9,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    names = {item["name_en"] for item in payload}
    assert "Master of Science in Asset and Wealth Management" in names
    assert "Master of Science in Finance" in names
