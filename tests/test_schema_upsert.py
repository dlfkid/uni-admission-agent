import os
import pytest
from sqlmodel import SQLModel, select
from src.models.admission import Program, University
from src.models.requirement import (
    ProgramRequirement,
    RequirementVersion,
    SubjectDim,
    ExamDim,
    RequirementEvidence,
)
from src.storage.db_manager import DatabaseManager
from sqlalchemy_utils import database_exists, create_database, drop_database

pytestmark = pytest.mark.integration

@pytest.fixture(name="db_manager")
def fixture_db_manager():
    # Force reset singleton to ensure we can init with test URL
    DatabaseManager._instance = None
    
    # Define test DB URL based on env or default
    # Note: User must have a running Postgres.
    base_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/uni_admission")
    
    # Construct test DB URL
    if "/uni_admission" in base_url:
        test_db_url = base_url.replace("/uni_admission", "/uni_admission_test")
    else:
        # Fallback if URL format is different
        test_db_url = base_url.rsplit('/', 1)[0] + "/uni_admission_test"

    # Clean start
    try:
        if database_exists(test_db_url):
            drop_database(test_db_url)
    except Exception as e:
        print(f"Warning: Could not drop test DB: {e}")

    try:
        create_database(test_db_url)
    except Exception as e:
        pytest.skip(f"Could not create test database (Postgres running?): {e}")

    # Init Manager
    mgr = DatabaseManager()
    mgr.init_db(test_db_url)
    
    yield mgr
    
    # Cleanup
    mgr.engine.dispose()
    try:
        if database_exists(test_db_url):
            drop_database(test_db_url)
    except Exception:
        pass

def test_upsert_program_robustness(db_manager):
    slug = "test-u-robust"
    year = 2025
    name = "Program Robust"
    
    # 1. Initial Insert
    data_1 = {
        "academic_year": year,
        "name_en": name,
        "tuition_amount": 10000,
        "program_group_code": "GRP001",
        "is_active": True
    }
    prog, created = db_manager.upsert_program(data_1, slug)
    assert created # effectively
    assert prog.tuition_amount == 10000
    assert prog.program_group_code == "GRP001"
    
    # 2. Partial Update (Incremental)
    # New data has None for tuition, but new value for faculty
    data_2 = {
        "academic_year": year,
        "name_en": name,
        "tuition_amount": None, # Should NOT overwrite 10000
        "faculty": "Engineering",
        "program_group_code": "GRP001"
    }
    prog_2, _ = db_manager.upsert_program(data_2, slug)
    
    # Verify persistence of old value
    assert prog_2.tuition_amount == 10000 
    # Verify update of new value
    assert prog_2.faculty == "Engineering"
    
    # 3. Explicit Update
    data_3 = {
        "academic_year": year,
        "name_en": name,
        "tuition_amount": 20000 # Should update
    }
    prog_3, _ = db_manager.upsert_program(data_3, slug)
    assert prog_3.tuition_amount == 20000
    assert prog_3.faculty == "Engineering" # Should persist

    # Clean up
    with db_manager.get_session() as s:
        p = s.get(Program, prog_3.id)
        u = s.get(University, prog_3.university_id)
        if p: s.delete(p)
        if u: s.delete(u)
        s.commit()

def test_program_history(db_manager):
    slug = "test-u-history"
    group = "GRP_HIST"
    
    # Insert year 2024
    db_manager.upsert_program({
        "academic_year": 2024,
        "name_en": "Old Name",
        "program_group_code": group
    }, slug)
    
    # Insert year 2025
    db_manager.upsert_program({
        "academic_year": 2025,
        "name_en": "New Name",
        "program_group_code": group
    }, slug)
    
    history = db_manager.get_program_history(group)
    assert len(history) == 2
    assert history[0].academic_year == 2024
    assert history[1].academic_year == 2025
    assert history[0].name_en == "Old Name"
    assert history[1].name_en == "New Name"

    # Clean up
    with db_manager.get_session() as s:
        for p in history:
            s.delete(p)
        u = s.exec(select(University).where(University.slug == slug)).first()
        if u: s.delete(u)
        s.commit()


def test_requirement_versioning_with_dimensions_and_evidence(db_manager):
    slug = "test-u-req-version"
    year = 2026

    base_data = {
        "academic_year": year,
        "name_en": "MSc Data Science",
        "program_group_code": "REQ-VERSION-001",
        "source_url": "https://example.edu/ds",
        "requirements": [
            {
                "category": "academic_subject",
                "subject_name": "Mathematics",
                "minimum_value": "A",
                "requirement_text": "Mathematics grade A",
            },
            {
                "category": "language",
                "subject_name": "IELTS",
                "minimum_value": "6.5",
                "unit": "band",
                "requirement_text": "IELTS overall 6.5",
            },
        ],
    }

    program, _ = db_manager.upsert_program(base_data, slug)

    # Idempotent re-upsert: no new version should be created.
    db_manager.upsert_program(base_data, slug)

    with db_manager.get_session() as s:
        versions = s.exec(
            select(RequirementVersion)
            .where(RequirementVersion.program_id == program.id)
            .order_by(RequirementVersion.version_no)
        ).all()
        assert len(versions) == 1
        assert versions[0].version_no == 1
        assert versions[0].valid_to is None

    # Update requirement payload to trigger a new version snapshot.
    updated_data = {
        **base_data,
        "requirements": [
            {
                "category": "academic_subject",
                "subject_name": "Mathematics",
                "minimum_value": "B",
                "requirement_text": "Mathematics grade B or above",
            },
            {
                "category": "language",
                "subject_name": "IELTS",
                "minimum_value": "7.0",
                "unit": "band",
                "requirement_text": "IELTS overall 7.0",
            },
        ],
    }
    db_manager.upsert_program(updated_data, slug)

    with db_manager.get_session() as s:
        versions = s.exec(
            select(RequirementVersion)
            .where(RequirementVersion.program_id == program.id)
            .order_by(RequirementVersion.version_no)
        ).all()
        assert len(versions) == 2
        assert versions[0].version_no == 1
        assert versions[1].version_no == 2
        assert versions[0].valid_to is not None
        assert versions[1].valid_to is None
        assert (versions[1].diff_payload or {}).get("added_count", 0) >= 1

        latest_rows = s.exec(
            select(ProgramRequirement).where(ProgramRequirement.version_id == versions[1].id)
        ).all()
        assert len(latest_rows) == 2

        math_dim = s.exec(
            select(SubjectDim).where(SubjectDim.normalized_name == "mathematics")
        ).first()
        assert math_dim is not None

        ielts_dim = s.exec(select(ExamDim).where(ExamDim.code == "ielts")).first()
        assert ielts_dim is not None

        evidence_count = s.exec(select(RequirementEvidence)).all()
        assert len(evidence_count) >= 1

        # cleanup
        for req in s.exec(
            select(ProgramRequirement).where(ProgramRequirement.program_id == program.id)
        ).all():
            s.delete(req)
        for rv in versions:
            s.delete(rv)
        for e in s.exec(select(RequirementEvidence)).all():
            s.delete(e)
        for ex in s.exec(select(ExamDim)).all():
            s.delete(ex)
        for sub in s.exec(select(SubjectDim)).all():
            s.delete(sub)
        p = s.get(Program, program.id)
        if p:
            s.delete(p)
        u = s.exec(select(University).where(University.slug == slug)).first()
        if u:
            s.delete(u)
        s.commit()
