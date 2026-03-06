import json

from src.services.subject_taxonomy import SubjectTaxonomyService


class InMemoryTaxonomyRepository:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def upsert_many(self, entries: list[dict]) -> dict:
        inserted = 0
        updated = 0
        for entry in entries:
            key = entry["normalized_name"]
            if key in self._rows:
                row = self._rows[key]
                row.update(entry)
                updated += 1
            else:
                self._rows[key] = dict(entry)
                inserted += 1
        return {"inserted": inserted, "updated": updated}

    def list_active(self) -> list[dict]:
        return [dict(row) for row in self._rows.values() if row.get("status") == "active"]

    def row_count(self) -> int:
        return len(self._rows)


def test_seed_sync_is_idempotent(tmp_path) -> None:
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps(
            [
                "Master of Science in Asset and Wealth Management",
                {"name_en": "Master of Science in Finance"},
            ]
        ),
        encoding="utf-8",
    )

    repository = InMemoryTaxonomyRepository()
    service = SubjectTaxonomyService(repository=repository)

    first = service.sync_seed_from_json(str(seed_file))
    second = service.sync_seed_from_json(str(seed_file))

    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert repository.row_count() == 2


def test_memory_index_loaded_after_sync(tmp_path) -> None:
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps(
            ["Master of Science in Asset and Wealth Management"]
        ),
        encoding="utf-8",
    )

    repository = InMemoryTaxonomyRepository()
    service = SubjectTaxonomyService(repository=repository)
    service.sync_seed_from_json(str(seed_file))

    assert service.memory_entry_count >= 1
    assert "science" in service.token_index
