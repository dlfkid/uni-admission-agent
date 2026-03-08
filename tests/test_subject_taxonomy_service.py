import json

from src.services.subject_taxonomy import (
    SubjectTaxonomyService,
    resolve_subject_taxonomy_seed_path,
)


class InMemoryTaxonomyRepository:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._program_names: list[str] = []

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

    def list_program_names(self) -> list[str]:
        return list(self._program_names)

    def delete_learned_by_normalized_names(self, normalized_names: list[str]) -> int:
        deleted = 0
        for normalized in normalized_names:
            row = self._rows.get(normalized)
            if not row:
                continue
            if str(row.get("source") or "").strip() != "learned":
                continue
            self._rows.pop(normalized, None)
            deleted += 1
        return deleted


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


def test_prune_orphaned_learned_names_only_removes_orphans() -> None:
    repository = InMemoryTaxonomyRepository()
    service = SubjectTaxonomyService(repository=repository)

    repository.upsert_many(
        [
            {
                "name_en": "Master of Science in Finance",
                "normalized_name": "masterofscienceinfinance",
                "aliases": ["Master of Science in Finance"],
                "source": "learned",
                "status": "active",
            },
            {
                "name_en": "Master of Science in Data Science",
                "normalized_name": "masterofscienceindatascience",
                "aliases": ["Master of Science in Data Science"],
                "source": "seed",
                "status": "active",
            },
        ]
    )

    # Still referenced by an existing program snapshot -> should not be pruned.
    repository._program_names = ["Master of Science in Finance"]
    first = service.prune_orphaned_learned_names(["Master of Science in Finance"])
    assert first["deleted"] == 0

    # Orphaned learned name -> should be pruned.
    repository._program_names = []
    second = service.prune_orphaned_learned_names(["Master of Science in Finance"])
    assert second["deleted"] == 1

    active_names = {row["name_en"] for row in repository.list_active()}
    assert "Master of Science in Finance" not in active_names
    assert "Master of Science in Data Science" in active_names


def test_resolve_seed_path_prefers_programs_names_in_cwd(tmp_path, monkeypatch) -> None:
    seed = tmp_path / "golden_samples" / "programs_names.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text(json.dumps(["Master of Science in Finance"]), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    resolved = resolve_subject_taxonomy_seed_path()
    assert resolved == str(seed)
