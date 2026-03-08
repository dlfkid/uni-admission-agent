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
