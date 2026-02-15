import pytest
import os
from sqlmodel import Session, select
from datetime import datetime, timezone
from src.models.admission import University, Program
from src.storage.db_manager import DatabaseManager
from sqlalchemy_utils import database_exists, create_database, drop_database

pytestmark = pytest.mark.integration

# Re-use the fixture logic from test_schema_upsert.py for DB setup
TEST_DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/uni_admission") + "_test_evolution"

@pytest.fixture(scope="module")
def db_manager():
    # Force reset singleton to ensure we can init with test URL
    DatabaseManager._instance = None

    if database_exists(TEST_DB_URL):
        drop_database(TEST_DB_URL)
    create_database(TEST_DB_URL)
    
    # Init Manager with test URL
    manager = DatabaseManager()
    manager.init_db(TEST_DB_URL)
    
    yield manager
    
    # Cleanup
    manager.engine.dispose()
    if database_exists(TEST_DB_URL):
        drop_database(TEST_DB_URL)

def test_evolution_mapping_integration(db_manager):
    """
    Test that we can retrieve the program group map correctly.
    """
    with db_manager.get_session() as session:
        # 1. Create University
        univ = University(name="Evolution Test Univ", slug="evo-univ")
        session.add(univ)
        session.commit()
        session.refresh(univ)
        
        # 2. Add Programs using the Upsert or direct add
        p1 = Program(
            university_id=univ.id,
            academic_year=2024,
            name_en="MSc Data Science",
            name_zh="数据科学",
            program_group_code="evo-msc-ds", # Existing lineage
            is_active=True
        )
        p2 = Program(
            university_id=univ.id,
            academic_year=2024,
            name_en="MSc Computer Science",
            name_zh="计算机科学",
            program_group_code="evo-msc-cs",
            is_active=True
        )
        p3 = Program(
            university_id=univ.id,
            academic_year=2024,
            name_en="MSc New Program",
            name_zh="新项目",
            program_group_code=None, # Should not appear in map
            is_active=True
        )
        session.add(p1)
        session.add(p2)
        session.add(p3)
        session.commit()
        
        univ_id = univ.id

    # 3. Test get_program_group_map
    mapping = db_manager.get_program_group_map(univ_id)
    
    assert "MSc Data Science" in mapping
    assert mapping["MSc Data Science"] == "evo-msc-ds"
    
    assert "MSc Computer Science" in mapping
    assert mapping["MSc Computer Science"] == "evo-msc-cs"
    
    assert "MSc New Program" not in mapping
    
    print(f"Mapping retrieved: {mapping}")

