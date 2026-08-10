# Batch Delete Programs by University/Year Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user delete every stored program snapshot for one university (optionally scoped to one academic year) from the CLI or REST API, with a mandatory preview step, without touching unrelated data.

**Architecture:** Three layers, each with one clear job, following the codebase's existing split for program mutations: `DatabaseManager` (raw DB cascade, no side effects) → `src/services/crawler.py` module-level wrapper (adds the taxonomy-prune side effect, the same layer `delete_program_snapshot` already lives at) → CLI/REST entrypoints (call the wrapper, never `DatabaseManager` directly).

**Tech Stack:** Python 3.12, SQLModel, FastAPI, Typer, pytest.

## Global Constraints

- `university_slug` (CLI `--university`, REST `univ_slug`) is **required** for every batch-delete call — no whole-table nuke from this feature, matching the existing `quarantine clear --university` requirement.
- Preview-before-destroy is mandatory: CLI without `--yes` and REST with `confirm=false` (the default) must perform **zero writes** — read-only count/preview only.
- Cascade scope is fixed: only `Program` + its existing child tables (`ProgramRequirement`, `RequirementVersion`, `ProgramStudyOption`, `ProgramDeadline`) + `ProgramCatalog` cleanup when a catalog has no remaining programs. `University`, `Quarantine`, and `ExtractionAudit` rows are never touched by this feature.
- Taxonomy-prune (`SubjectTaxonomyService.prune_orphaned_learned_names`) must run once per batch after a successful delete, and a failure there must be logged and swallowed — it must never fail the delete response or appear to roll back an already-committed delete.
- No MCP tool this round — CLI + REST only.
- Unknown/empty scope (bad slug, or a year with no matches) is a no-op returning `count=0` — never an error.
- See spec: [`docs/superpowers/specs/2026-08-10-batch-delete-programs-design.md`](../specs/2026-08-10-batch-delete-programs-design.md).

---

### Task 1: Data layer — `DatabaseManager.count_programs_by_scope` / `delete_programs_by_scope`

**Files:**
- Modify: `src/storage/db_manager.py:8` (imports), `src/storage/db_manager.py:71` (new `ProgramDeleteScope` dataclass, inserted between `_attach_sqlite_pragmas` and `class DatabaseManager`), `src/storage/db_manager.py:832` (two new methods, inserted right after `delete_program_snapshot` and before `patch_program_snapshot`)
- Test: `tests/test_db_manager.py` (append new test classes at end of file)

**Interfaces:**
- Consumes: existing `University`, `Program`, `ProgramCatalog`, `ProgramRequirement`, `RequirementVersion`, `ProgramStudyOption`, `ProgramDeadline` models (all already imported in `db_manager.py`); `self.get_session()`.
- Produces: `ProgramDeleteScope` dataclass (`university_slug: str`, `count: int`, `years: list[int]`, `deleted_names: list[str]`) — imported by `src/services/crawler.py` in Task 2, and by `src/api/server.py` / `src/cmd/cli.py` test files in Tasks 3–4 (tests construct it directly to stub wrapper return values). `DatabaseManager.count_programs_by_scope(university_slug: str, year: Optional[int] = None) -> ProgramDeleteScope` and `DatabaseManager.delete_programs_by_scope(university_slug: str, year: Optional[int] = None) -> ProgramDeleteScope`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db_manager.py`:

```python
# ── count_programs_by_scope / delete_programs_by_scope ───────────────


class TestProgramDeleteScope:
    def setup_method(self) -> None:
        DatabaseManager._instance = None
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.dm = DatabaseManager()
        self.dm.engine = self.engine

    def teardown_method(self) -> None:
        DatabaseManager._instance = None

    def _seed(self):
        """One university (leeds) with 2 programs sharing a catalog (2025,
        2026) plus full child rows on the 2025 one; one other university
        (hku) with 1 unrelated program — proves scoping."""
        with Session(self.engine) as session:
            leeds = University(name="Leeds", slug="leeds")
            hku = University(name="HKU", slug="hku")
            session.add(leeds)
            session.add(hku)
            session.commit()
            session.refresh(leeds)
            session.refresh(hku)

            catalog = ProgramCatalog(
                university_id=leeds.id,
                catalog_key="msc-cs",
                canonical_name_en="MSc Computer Science",
            )
            session.add(catalog)
            session.commit()
            session.refresh(catalog)

            p2025 = Program(
                university_id=leeds.id,
                program_catalog_id=catalog.id,
                academic_year=2025,
                name_en="MSc Computer Science",
            )
            p2026 = Program(
                university_id=leeds.id,
                program_catalog_id=catalog.id,
                academic_year=2026,
                name_en="MSc Computer Science",
            )
            other_program = Program(
                university_id=hku.id,
                academic_year=2026,
                name_en="MSc Finance",
            )
            session.add(p2025)
            session.add(p2026)
            session.add(other_program)
            session.commit()
            session.refresh(p2025)
            session.refresh(p2026)
            session.refresh(other_program)

            version = RequirementVersion(program_id=p2025.id)
            session.add(version)
            session.commit()
            session.refresh(version)

            session.add(
                ProgramRequirement(
                    program_id=p2025.id,
                    version_id=version.id,
                    requirement_text="IELTS 6.5",
                )
            )
            session.add(ProgramStudyOption(program_id=p2025.id, duration_months=12))
            session.add(
                ProgramDeadline(program_id=p2025.id, round=1, description="Main")
            )
            session.commit()

            return {
                "catalog_id": catalog.id,
                "p2025_id": p2025.id,
                "p2026_id": p2026.id,
                "other_program_id": other_program.id,
                "other_university_id": hku.id,
            }

    def test_count_programs_by_scope_slug_only(self) -> None:
        self._seed()
        result = self.dm.count_programs_by_scope("leeds")
        assert result.university_slug == "leeds"
        assert result.count == 2
        assert result.years == [2025, 2026]
        assert sorted(result.deleted_names) == ["MSc Computer Science", "MSc Computer Science"]

    def test_count_programs_by_scope_slug_and_year(self) -> None:
        self._seed()
        result = self.dm.count_programs_by_scope("leeds", year=2025)
        assert result.count == 1
        assert result.years == [2025]

    def test_count_programs_by_scope_unknown_slug_is_noop(self) -> None:
        self._seed()
        result = self.dm.count_programs_by_scope("ghost")
        assert result.count == 0
        assert result.years == []
        assert result.deleted_names == []

    def test_count_programs_by_scope_unknown_year_is_noop(self) -> None:
        self._seed()
        result = self.dm.count_programs_by_scope("leeds", year=2099)
        assert result.count == 0

    def test_delete_programs_by_scope_slug_only_deletes_all_years_and_cascades(self) -> None:
        ids = self._seed()

        result = self.dm.delete_programs_by_scope("leeds")

        assert result.count == 2
        assert result.years == [2025, 2026]

        with Session(self.engine) as session:
            assert session.get(Program, ids["p2025_id"]) is None
            assert session.get(Program, ids["p2026_id"]) is None
            # No siblings left in the catalog -> catalog itself is removed.
            assert session.get(ProgramCatalog, ids["catalog_id"]) is None
            assert session.exec(
                select(ProgramRequirement).where(
                    ProgramRequirement.program_id == ids["p2025_id"]
                )
            ).all() == []
            assert session.exec(
                select(RequirementVersion).where(
                    RequirementVersion.program_id == ids["p2025_id"]
                )
            ).all() == []
            assert session.exec(
                select(ProgramStudyOption).where(
                    ProgramStudyOption.program_id == ids["p2025_id"]
                )
            ).all() == []
            assert session.exec(
                select(ProgramDeadline).where(
                    ProgramDeadline.program_id == ids["p2025_id"]
                )
            ).all() == []
            # Other university's program is untouched.
            assert session.get(Program, ids["other_program_id"]) is not None
            assert session.get(University, ids["other_university_id"]) is not None

    def test_delete_programs_by_scope_with_year_keeps_sibling_and_catalog(self) -> None:
        ids = self._seed()

        result = self.dm.delete_programs_by_scope("leeds", year=2025)

        assert result.count == 1
        assert result.years == [2025]

        with Session(self.engine) as session:
            assert session.get(Program, ids["p2025_id"]) is None
            # 2026 sibling survives -> catalog must survive too.
            assert session.get(Program, ids["p2026_id"]) is not None
            assert session.get(ProgramCatalog, ids["catalog_id"]) is not None

    def test_delete_programs_by_scope_unknown_slug_is_noop(self) -> None:
        self._seed()
        result = self.dm.delete_programs_by_scope("ghost")
        assert result.count == 0
        assert result.years == []
        assert result.deleted_names == []

    def test_delete_programs_by_scope_unknown_year_is_noop(self) -> None:
        ids = self._seed()
        result = self.dm.delete_programs_by_scope("leeds", year=2099)
        assert result.count == 0
        with Session(self.engine) as session:
            assert session.get(Program, ids["p2025_id"]) is not None
            assert session.get(Program, ids["p2026_id"]) is not None
```

In `tests/test_db_manager.py`, add a new `sqlmodel` import line right after the existing `import pytest` line:

```python
from sqlmodel import Session, SQLModel, create_engine, select
```

Then **replace** the existing `from src.models.admission import StudyMode` line with:

```python
from src.models.admission import StudyMode, University, Program, ProgramCatalog
from src.models.requirement import (
    ProgramDeadline,
    ProgramRequirement,
    ProgramStudyOption,
    RequirementVersion,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_manager.py -k ProgramDeleteScope -v`
Expected: FAIL — `AttributeError: 'DatabaseManager' object has no attribute 'count_programs_by_scope'` (and similarly for `delete_programs_by_scope`).

- [ ] **Step 3: Add the `dataclass` import and `ProgramDeleteScope`**

In `src/storage/db_manager.py`, change the `typing` import line (currently `from typing import Any, Optional, Tuple, List, Dict`) to also import `dataclasses`:

```python
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple, List, Dict
```

Then insert this new class right after `_attach_sqlite_pragmas` (before `class DatabaseManager:`):

```python
@dataclass
class ProgramDeleteScope:
    """Result of a university/year-scoped program count or delete.

    `deleted_names` carries each matched program's `name_en` — populated
    by both count and delete so `src.services.crawler`'s wrapper can feed
    a delete's names to the taxonomy pruner without a second query.
    """

    university_slug: str
    count: int
    years: list[int] = field(default_factory=list)
    deleted_names: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Implement `count_programs_by_scope`**

Insert into `class DatabaseManager`, immediately after `delete_program_snapshot` (right before `def patch_program_snapshot`):

```python
    def count_programs_by_scope(
        self, university_slug: str, year: Optional[int] = None
    ) -> ProgramDeleteScope:
        """Read-only preview: count/years/names of Program rows matching scope."""
        with self.get_session() as session:
            univ = session.exec(
                select(University).where(University.slug == university_slug)
            ).first()
            if not univ:
                return ProgramDeleteScope(university_slug=university_slug, count=0)

            stmt = select(Program).where(Program.university_id == univ.id)
            if year is not None:
                stmt = stmt.where(Program.academic_year == year)
            rows = session.exec(stmt).all()

        return ProgramDeleteScope(
            university_slug=university_slug,
            count=len(rows),
            years=sorted({row.academic_year for row in rows}),
            deleted_names=[row.name_en for row in rows],
        )
```

- [ ] **Step 5: Implement `delete_programs_by_scope`**

Insert directly after `count_programs_by_scope`:

```python
    def delete_programs_by_scope(
        self, university_slug: str, year: Optional[int] = None
    ) -> ProgramDeleteScope:
        """Delete all Program rows matching scope + their children, one transaction."""
        with self.get_session() as session:
            univ = session.exec(
                select(University).where(University.slug == university_slug)
            ).first()
            if not univ:
                return ProgramDeleteScope(university_slug=university_slug, count=0)

            stmt = select(Program).where(Program.university_id == univ.id)
            if year is not None:
                stmt = stmt.where(Program.academic_year == year)
            programs = session.exec(stmt).all()

            if not programs:
                return ProgramDeleteScope(university_slug=university_slug, count=0)

            program_ids = [p.id for p in programs if p.id is not None]
            years = sorted({p.academic_year for p in programs})
            names = [p.name_en for p in programs]
            catalog_ids = {
                p.program_catalog_id for p in programs if p.program_catalog_id is not None
            }

            for row in session.exec(
                select(ProgramRequirement).where(
                    col(ProgramRequirement.program_id).in_(program_ids)
                )
            ).all():
                session.delete(row)

            for row in session.exec(
                select(RequirementVersion).where(
                    col(RequirementVersion.program_id).in_(program_ids)
                )
            ).all():
                session.delete(row)

            for row in session.exec(
                select(ProgramStudyOption).where(
                    col(ProgramStudyOption.program_id).in_(program_ids)
                )
            ).all():
                session.delete(row)

            for row in session.exec(
                select(ProgramDeadline).where(
                    col(ProgramDeadline.program_id).in_(program_ids)
                )
            ).all():
                session.delete(row)

            for program in programs:
                session.delete(program)
            session.flush()

            for catalog_id in catalog_ids:
                has_sibling = session.exec(
                    select(Program.id).where(Program.program_catalog_id == catalog_id)
                ).first()
                if has_sibling is None:
                    catalog = session.get(ProgramCatalog, catalog_id)
                    if catalog is not None:
                        session.delete(catalog)

            session.commit()

        return ProgramDeleteScope(
            university_slug=university_slug,
            count=len(program_ids),
            years=years,
            deleted_names=names,
        )
```

`col(...).in_(...)` is already used for exactly this shape in `src/storage/audit_repo.py:109` — no new import needed (`col` is already imported at the top of `db_manager.py`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_db_manager.py -k ProgramDeleteScope -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Run the full storage test file to check for regressions**

Run: `uv run pytest tests/test_db_manager.py -v`
Expected: PASS, no regressions in pre-existing tests.

- [ ] **Step 8: Commit**

```bash
git add src/storage/db_manager.py tests/test_db_manager.py
git commit -m "feat: add DatabaseManager.count_programs_by_scope/delete_programs_by_scope"
```

---

### Task 2: Service wrapper — `src/services/crawler.py` (taxonomy-prune side effect)

**Files:**
- Modify: `src/services/crawler.py:53` (import), `src/services/crawler.py:1259` (two new module-level functions, inserted right after `delete_program_snapshot` and before `patch_program_snapshot`)
- Test: `tests/test_crawler_taxonomy_sync.py` (append)

**Interfaces:**
- Consumes: `ProgramDeleteScope`, `DatabaseManager.count_programs_by_scope`/`delete_programs_by_scope` (Task 1); `get_subject_taxonomy_service()` (already imported in `crawler.py`).
- Produces: `count_programs_by_scope(university_slug: str, year: Optional[int] = None) -> ProgramDeleteScope` and `delete_programs_by_scope(university_slug: str, year: Optional[int] = None) -> ProgramDeleteScope`, both at module level in `src.services.crawler` — imported by name in Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crawler_taxonomy_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_crawler_taxonomy_sync.py -v`
Expected: FAIL — `AttributeError: module 'src.services.crawler' has no attribute 'delete_programs_by_scope'` (and `count_programs_by_scope`).

- [ ] **Step 3: Add the import**

In `src/services/crawler.py`, change the `DatabaseManager` import line to also bring in `ProgramDeleteScope`:

```python
from src.storage.db_manager import DatabaseManager, ProgramDeleteScope
```

- [ ] **Step 4: Implement the two wrapper functions**

Insert into `src/services/crawler.py`, immediately after `delete_program_snapshot` (right before `def patch_program_snapshot`):

```python
def count_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Read-only preview of programs matching a university/year scope."""
    db = DatabaseManager()
    return db.count_programs_by_scope(university_slug, year)


def delete_programs_by_scope(
    university_slug: str, year: Optional[int] = None
) -> ProgramDeleteScope:
    """Delete all programs matching a university/year scope.

    Prunes orphaned learned taxonomy names for the whole deleted batch in
    one call after the delete commits, mirroring delete_program_snapshot's
    per-row taxonomy prune. A prune failure is logged and swallowed — the
    delete has already committed and must be reported regardless.
    """
    db = DatabaseManager()
    result = db.delete_programs_by_scope(university_slug, year)
    if result.count > 0 and result.deleted_names:
        try:
            get_subject_taxonomy_service().prune_orphaned_learned_names(
                result.deleted_names
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "Failed pruning taxonomy after batch-deleting university_slug=%s year=%s: %s",
                university_slug,
                year,
                exc,
            )
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_crawler_taxonomy_sync.py -v`
Expected: PASS (5 tests — 1 pre-existing + 4 new).

- [ ] **Step 6: Commit**

```bash
git add src/services/crawler.py tests/test_crawler_taxonomy_sync.py
git commit -m "feat: add crawler.count_programs_by_scope/delete_programs_by_scope wrappers"
```

---

### Task 3: REST endpoint — `DELETE /programs`

**Files:**
- Modify: `src/api/schemas.py:502` (new `BatchDeleteProgramsResponse`, inserted right after `DeleteProgramResponse`), `src/api/server.py:45` (schema import), `src/api/server.py:77` (crawler import), `src/api/server.py:1490` (new endpoint, inserted between `api_programs` (GET) and `api_delete_program` (single DELETE))
- Test: Create `tests/test_programs_batch_delete.py` (REST test class; CLI test class added in Task 4)

**Interfaces:**
- Consumes: `count_programs_by_scope`, `delete_programs_by_scope` (Task 2); `ProgramDeleteScope` (Task 1, used directly by tests to stub return values).
- Produces: `BatchDeleteProgramsResponse` Pydantic schema (`university_slug: str`, `year: Optional[int]`, `count: int`, `years: list[int]`, `deleted: bool`, `message: str`); `DELETE /programs?univ_slug=&year=&confirm=` endpoint.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_programs_batch_delete.py`:

```python
"""Tests for batch-deleting programs by university/year (CLI + REST).

CLI coverage is added in a follow-up task in the same plan — this file
starts with the REST surface.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import app as fastapi_app
from src.storage.db_manager import ProgramDeleteScope


class TestDeleteProgramsRestEndpoint:
    def test_preview_without_confirm_performs_no_delete(self) -> None:
        scope = ProgramDeleteScope(
            university_slug="leeds", count=42, years=[2025, 2026], deleted_names=[]
        )
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete("/programs", params={"univ_slug": "leeds"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] is False
        assert payload["count"] == 42
        assert payload["years"] == [2025, 2026]
        mock_count.assert_called_once_with("leeds", None)
        mock_delete.assert_not_called()

    def test_confirm_true_executes_delete(self) -> None:
        scope = ProgramDeleteScope(
            university_slug="leeds", count=42, years=[2025, 2026], deleted_names=[]
        )
        with (
            patch("src.api.server.count_programs_by_scope") as mock_count,
            patch("src.api.server.delete_programs_by_scope", return_value=scope) as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete(
                "/programs", params={"univ_slug": "leeds", "confirm": "true"}
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["deleted"] is True
        assert payload["count"] == 42
        mock_count.assert_not_called()
        mock_delete.assert_called_once_with("leeds", None)

    def test_year_filter_passed_through(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=0)
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete(
                "/programs", params={"univ_slug": "leeds", "year": "2099"}
            )

        assert response.status_code == 200
        assert response.json()["count"] == 0
        mock_count.assert_called_once_with("leeds", 2099)
        mock_delete.assert_not_called()

    def test_zero_match_preview_message_mentions_university(self) -> None:
        scope = ProgramDeleteScope(university_slug="ghost", count=0)
        with (
            patch("src.api.server.count_programs_by_scope", return_value=scope),
            patch("src.api.server.delete_programs_by_scope") as mock_delete,
            TestClient(fastapi_app) as client,
        ):
            response = client.delete("/programs", params={"univ_slug": "ghost"})

        assert response.status_code == 200
        assert "ghost" in response.json()["message"]
        mock_delete.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_programs_batch_delete.py -v`
Expected: FAIL — `ImportError`/`AttributeError` for `src.api.server.count_programs_by_scope` (endpoint + import don't exist yet), or a 404/405 on the `DELETE /programs` route.

- [ ] **Step 3: Add the schema**

In `src/api/schemas.py`, insert right after `DeleteProgramResponse`:

```python
class BatchDeleteProgramsResponse(BaseModel):
    """Response for `DELETE /programs?univ_slug=...` (preview or execute)."""

    university_slug: str
    year: Optional[int] = None
    count: int
    years: list[int] = Field(default_factory=list)
    deleted: bool
    message: str
```

- [ ] **Step 4: Wire up the imports in `src/api/server.py`**

Add `BatchDeleteProgramsResponse` to the `from src.api.schemas import (...)` block, right after `DeleteProgramResponse`:

```python
    DeleteProgramResponse,
    BatchDeleteProgramsResponse,
```

Add `count_programs_by_scope` and `delete_programs_by_scope` to the `from src.services.crawler import (...)` block, right after `delete_program_snapshot`:

```python
    delete_program_snapshot,
    count_programs_by_scope,
    delete_programs_by_scope,
```

- [ ] **Step 5: Implement the endpoint**

Insert into `src/api/server.py`, between `api_programs` (the `GET /programs` handler) and `api_delete_program` (the `DELETE /programs/{program_id}` handler):

```python
@app.delete("/programs", response_model=BatchDeleteProgramsResponse)
async def api_delete_programs_by_scope(
    univ_slug: str = Query(..., description="University slug"),
    year: Optional[int] = Query(None, description="Academic year filter"),
    confirm: bool = Query(
        False, description="Set true to actually delete; false (default) previews the count."
    ),
) -> BatchDeleteProgramsResponse:
    """Preview or execute a batch delete of programs scoped by university/year."""
    if not confirm:
        scope = count_programs_by_scope(univ_slug, year)
        if scope.count:
            message = (
                f"This will delete {scope.count} programs across years {scope.years} "
                f'for "{univ_slug}". Re-run with confirm=true to execute.'
            )
        else:
            suffix = f" in {year}" if year is not None else ""
            message = f'No programs found for "{univ_slug}"{suffix}.'
        return BatchDeleteProgramsResponse(
            university_slug=scope.university_slug,
            year=year,
            count=scope.count,
            years=scope.years,
            deleted=False,
            message=message,
        )

    scope = delete_programs_by_scope(univ_slug, year)
    return BatchDeleteProgramsResponse(
        university_slug=scope.university_slug,
        year=year,
        count=scope.count,
        years=scope.years,
        deleted=True,
        message=f'Deleted {scope.count} programs for "{univ_slug}".',
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_programs_batch_delete.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the existing program CRUD REST tests to check for regressions**

Run: `uv run pytest tests/test_api_program_crud.py -v`
Expected: PASS, no regressions (new `/programs` DELETE route must not shadow or break the existing `/programs/{program_id}` route or `GET /programs`).

- [ ] **Step 8: Commit**

```bash
git add src/api/schemas.py src/api/server.py tests/test_programs_batch_delete.py
git commit -m "feat: add DELETE /programs batch-delete endpoint with preview/confirm"
```

---

### Task 4: CLI command — `uni-admission programs delete`

**Files:**
- Modify: `src/cmd/cli.py:42` (crawler import), `src/cmd/cli.py:1680` (new `programs_app` sub-Typer + `programs delete` command, inserted right before the `# Quarantine subcommands` comment block)
- Test: Extend `tests/test_programs_batch_delete.py` (add CLI test class)

**Interfaces:**
- Consumes: `count_programs_by_scope`, `delete_programs_by_scope` (Task 2); `ProgramDeleteScope` (Task 1).
- Produces: `uni-admission programs delete --university <slug> [--year Y] [--yes]` CLI command.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_programs_batch_delete.py` (new imports at top, new class at the bottom):

```python
from typer.testing import CliRunner

from src.cmd.cli import app as cli_app
```

```python
class TestProgramsDeleteCli:
    def test_preview_without_yes_performs_no_delete(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=42, years=[2025, 2026])
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "leeds"]
            )

        assert result.exit_code == 0
        assert "42" in result.stdout
        assert "2025" in result.stdout and "2026" in result.stdout
        mock_count.assert_called_once_with("leeds", None)
        mock_delete.assert_not_called()

    def test_yes_executes_delete(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=42, years=[2025, 2026])
        with (
            patch("src.cmd.cli.count_programs_by_scope") as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope", return_value=scope) as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "leeds", "--yes"]
            )

        assert result.exit_code == 0
        assert "42" in result.stdout
        mock_count.assert_not_called()
        mock_delete.assert_called_once_with("leeds", None)

    def test_year_filter_passed_through(self) -> None:
        scope = ProgramDeleteScope(university_slug="leeds", count=10, years=[2025])
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope) as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app,
                ["programs", "delete", "--university", "leeds", "--year", "2025"],
            )

        assert result.exit_code == 0
        mock_count.assert_called_once_with("leeds", 2025)
        mock_delete.assert_not_called()

    def test_requires_university(self) -> None:
        with (
            patch("src.cmd.cli.count_programs_by_scope") as mock_count,
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(cli_app, ["programs", "delete"])

        assert result.exit_code != 0
        mock_count.assert_not_called()
        mock_delete.assert_not_called()

    def test_zero_match_preview_shows_friendly_message(self) -> None:
        scope = ProgramDeleteScope(university_slug="ghost", count=0)
        with (
            patch("src.cmd.cli.count_programs_by_scope", return_value=scope),
            patch("src.cmd.cli.delete_programs_by_scope") as mock_delete,
        ):
            result = CliRunner().invoke(
                cli_app, ["programs", "delete", "--university", "ghost"]
            )

        assert result.exit_code == 0
        assert "No programs found" in result.stdout
        mock_delete.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_programs_batch_delete.py -k ProgramsDeleteCli -v`
Expected: FAIL — Typer reports no such command `programs`.

- [ ] **Step 3: Add the import**

In `src/cmd/cli.py`, add `count_programs_by_scope` and `delete_programs_by_scope` to the existing `from src.services.crawler import (...)` block:

```python
from src.services.crawler import (
    check_environment,
    crawl_url,
    export_data,
    get_ingestion_job,
    get_db_status,
    import_file,
    list_ingestion_jobs,
    resume_crawl_job,
    count_programs_by_scope,
    delete_programs_by_scope,
)
```

- [ ] **Step 4: Implement the `programs` sub-app**

Insert into `src/cmd/cli.py`, right before the `# ---------------------------------------------------------------------------\n#  Quarantine subcommands` comment block:

```python
# ---------------------------------------------------------------------------
#  Programs subcommands
# ---------------------------------------------------------------------------

programs_app = typer.Typer(
    name="programs",
    help="Manage stored program records.",
    add_completion=False,
)
app.add_typer(programs_app)


@programs_app.command(name="delete")
def programs_delete(
    university: str = typer.Option(
        ..., "--university", "-u",
        help="University slug to delete programs for (required).",
    ),
    year: Optional[int] = typer.Option(
        None, "--year", "-y", help="Academic year filter.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Skip the preview and execute the delete.",
    ),
) -> None:
    """Batch-delete program snapshots for a university, optionally scoped to one year.

    Without --yes, only previews the affected count — no data is deleted.
    Deletes each matching program and its child rows (requirements, deadlines,
    study options, requirement versions), collapsing any program catalog left
    with zero remaining programs. University/quarantine/audit records are
    never touched by this command.
    """
    if not yes:
        scope = count_programs_by_scope(university, year)
        if not scope.count:
            suffix = f" in {year}" if year is not None else ""
            typer.echo(f"No programs found for {university!r}{suffix}.")
            return
        typer.echo(
            f"⚠️  This will delete {scope.count} programs across years {scope.years} "
            f"for {university!r}. Re-run with --yes to confirm."
        )
        return

    scope = delete_programs_by_scope(university, year)
    typer.echo(f"✅ Deleted {scope.count} programs for {university!r}.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_programs_batch_delete.py -v`
Expected: PASS (9 tests — 4 REST from Task 3 + 5 CLI).

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest -q`
Expected: PASS, same pass count as baseline plus the new tests added across Tasks 1–4, no unrelated failures.

- [ ] **Step 7: Run pylint on touched files**

Run: `uv run pylint src/storage/db_manager.py src/services/crawler.py src/api/server.py src/api/schemas.py src/cmd/cli.py`
Expected: no new errors introduced by this change (pre-existing warnings in these files, if any, are not this task's concern).

- [ ] **Step 8: Commit**

```bash
git add src/cmd/cli.py tests/test_programs_batch_delete.py
git commit -m "feat: add 'uni-admission programs delete' CLI command"
```

- [ ] **Step 9: Update README.md's CLI Commands table**

Add a row to the CLI Commands table in `README.md` (alongside the other `quarantine`/`audit`/`diagnostics` sub-app rows, which are documented separately just below that table — check how `quarantine list`/`quarantine clear` are currently documented and follow the same style):

```markdown
| `uni-admission programs delete --university <slug> [--year Y] [--yes]` | Batch-delete program snapshots for a university, optionally scoped to one year. Preview-only without `--yes`. |
```

- [ ] **Step 10: Commit the docs update**

```bash
git add README.md
git commit -m "docs: document 'uni-admission programs delete' CLI command"
```
