from types import SimpleNamespace

from src.services import crawler


def test_delete_program_snapshot_prunes_taxonomy_after_delete(monkeypatch) -> None:
    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, program_id):
            _ = model, program_id
            return SimpleNamespace(name_en="Master of Science in Finance")

    class _FakeDb:
        def get_session(self):
            return _FakeSession()

        def delete_program_snapshot(self, _program_id: int) -> bool:
            return True

    class _FakeTaxonomyService:
        def __init__(self) -> None:
            self.calls = []

        def prune_orphaned_learned_names(self, names):
            self.calls.append(list(names))
            return {"deleted": 1}

    fake_db = _FakeDb()
    fake_taxonomy = _FakeTaxonomyService()

    monkeypatch.setattr("src.services.crawler.DatabaseManager", lambda: fake_db)
    monkeypatch.setattr(
        "src.services.crawler.get_subject_taxonomy_service",
        lambda: fake_taxonomy,
    )

    deleted = crawler.delete_program_snapshot(1001)
    assert deleted is True
    assert fake_taxonomy.calls == [["Master of Science in Finance"]]


def test_delete_programs_by_scope_prunes_taxonomy_once_for_whole_batch(monkeypatch) -> None:
    from src.storage.db_manager import ProgramDeleteScope

    class _FakeDb:
        def delete_programs_by_scope(self, university_slug, year=None):
            assert university_slug == "leeds"
            assert year is None
            return ProgramDeleteScope(
                university_slug="leeds",
                count=2,
                years=[2025, 2026],
                deleted_names=["MSc Computer Science", "MSc Computer Science"],
            )

    class _FakeTaxonomyService:
        def __init__(self) -> None:
            self.calls = []

        def prune_orphaned_learned_names(self, names):
            self.calls.append(list(names))
            return {"deleted": 1}

    fake_db = _FakeDb()
    fake_taxonomy = _FakeTaxonomyService()

    monkeypatch.setattr("src.services.crawler.DatabaseManager", lambda: fake_db)
    monkeypatch.setattr(
        "src.services.crawler.get_subject_taxonomy_service",
        lambda: fake_taxonomy,
    )

    result = crawler.delete_programs_by_scope("leeds")
    assert result.count == 2
    # Exactly one call, with the full deleted-name list — not one call per name.
    assert fake_taxonomy.calls == [["MSc Computer Science", "MSc Computer Science"]]


def test_delete_programs_by_scope_skips_taxonomy_prune_when_nothing_deleted(monkeypatch) -> None:
    from src.storage.db_manager import ProgramDeleteScope

    class _FakeDb:
        def delete_programs_by_scope(self, university_slug, year=None):
            return ProgramDeleteScope(university_slug=university_slug, count=0)

    class _FakeTaxonomyService:
        def __init__(self) -> None:
            self.calls = []

        def prune_orphaned_learned_names(self, names):
            self.calls.append(list(names))
            return {"deleted": 0}

    fake_db = _FakeDb()
    fake_taxonomy = _FakeTaxonomyService()

    monkeypatch.setattr("src.services.crawler.DatabaseManager", lambda: fake_db)
    monkeypatch.setattr(
        "src.services.crawler.get_subject_taxonomy_service",
        lambda: fake_taxonomy,
    )

    result = crawler.delete_programs_by_scope("nonexistent")
    assert result.count == 0
    assert fake_taxonomy.calls == []


def test_delete_programs_by_scope_swallows_taxonomy_prune_failure(monkeypatch) -> None:
    from src.storage.db_manager import ProgramDeleteScope

    class _FakeDb:
        def delete_programs_by_scope(self, university_slug, year=None):
            return ProgramDeleteScope(
                university_slug="leeds", count=1, years=[2025], deleted_names=["MSc CS"]
            )

    class _FakeTaxonomyService:
        def prune_orphaned_learned_names(self, names):
            raise RuntimeError("taxonomy db unavailable")

    monkeypatch.setattr("src.services.crawler.DatabaseManager", lambda: _FakeDb())
    monkeypatch.setattr(
        "src.services.crawler.get_subject_taxonomy_service",
        lambda: _FakeTaxonomyService(),
    )

    # Must not raise, and must still report the delete that already committed.
    result = crawler.delete_programs_by_scope("leeds")
    assert result.count == 1


def test_count_programs_by_scope_never_touches_taxonomy(monkeypatch) -> None:
    from src.storage.db_manager import ProgramDeleteScope

    class _FakeDb:
        def count_programs_by_scope(self, university_slug, year=None):
            return ProgramDeleteScope(
                university_slug=university_slug, count=3, years=[2026], deleted_names=["A", "B", "C"]
            )

    class _FakeTaxonomyService:
        def __init__(self) -> None:
            self.calls = []

        def prune_orphaned_learned_names(self, names):
            self.calls.append(list(names))
            return {"deleted": 0}

    fake_db = _FakeDb()
    fake_taxonomy = _FakeTaxonomyService()

    monkeypatch.setattr("src.services.crawler.DatabaseManager", lambda: fake_db)
    monkeypatch.setattr(
        "src.services.crawler.get_subject_taxonomy_service",
        lambda: fake_taxonomy,
    )

    result = crawler.count_programs_by_scope("leeds")
    assert result.count == 3
    assert fake_taxonomy.calls == []
