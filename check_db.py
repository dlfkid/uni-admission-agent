import sys
from src.storage.db_manager import DatabaseManager
from src.models.admission import Program, University
from sqlmodel import select

db = DatabaseManager()
# Ensure DB initialized
db.init_db()

with db.get_session() as session:
    # Get HKU id
    univ = session.exec(select(University).where(University.slug == "hku")).first()
    if not univ:
        print("HKU not found")
        sys.exit()
    
    # Get programs
    programs = session.exec(select(Program).where(Program.university_id == univ.id).limit(20)).all()
    
    print(f"Found {len(programs)} programs for HKU:")
    print("-" * 80)
    print(f"{'Faculty':<20} | {'Name':<40} | {'Group Code':<30}")
    print("-" * 80)
    for p in programs:
        print(f"{p.faculty:<20} | {p.name_en[:38]:<40} | {p.program_group_code or 'None':<30}")
