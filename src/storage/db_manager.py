import os
import logging
from typing import Optional, Tuple
from sqlmodel import create_engine, Session, select, SQLModel
from sqlalchemy_utils import database_exists, create_database
from src.models.admission import University, Program
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class DatabaseManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance.engine = None
        return cls._instance

    def init_db(self, db_url: Optional[str] = None):
        """Initialize database connection and create tables."""
        if self.engine:
            return

        if not db_url:
            db_url = os.getenv("DATABASE_URL")
            if not db_url:
                # Default fallback (user should configure .env)
                # Note: This requires a running Postgres instance
                db_url = "postgresql+psycopg2://postgres:postgres@localhost:5432/uni_admission"
        
        try:
            self.engine = create_engine(db_url)
            
            # Self-healing: Create DB if not exists
            if not database_exists(self.engine.url):
                logger.info(f"Database does not exist. Creating: {self.engine.url}")
                create_database(self.engine.url)
            
            # Create tables
            SQLModel.metadata.create_all(self.engine)
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def get_session(self) -> Session:
        if not self.engine:
            self.init_db()
        return Session(self.engine)

    def upsert_program(self, program_data: dict, univ_slug: str) -> Tuple[Program, bool]:
        """
        Upsert a program record.
        Logic: Update if (university_id + academic_year + name_en) exists, else insert.
        Requires 'academic_year' in program_data.
        Returns: (Program, created: bool)
        """
        with self.get_session() as session:
            # 1. Ensure University exists (Lookup by Slug)
            univ = session.exec(select(University).where(University.slug == univ_slug)).first()
            if not univ:
                # Fallback: create with name=slug if not exists
                # In strict mode we might want to fail, but "not exist create" is requested.
                # using slug as name initially.
                univ = University(name=univ_slug, slug=univ_slug)
                session.add(univ)
                session.commit()
                session.refresh(univ)
            
            # 2. Check Composite Unique Constraint: univ_id + academic_year + name_en
            name_en = program_data.get("name_en")
            academic_year = program_data.get("academic_year")
            
            if not name_en:
                raise ValueError("Program data must contain 'name_en'")
            if not academic_year:
                raise ValueError("Program data must contain 'academic_year'")

            statement = select(Program).where(
                Program.university_id == univ.id,
                Program.academic_year == academic_year,
                Program.name_en == name_en
            )
            existing_program = session.exec(statement).first()
            
            if existing_program:
                # Update existing
                for key, value in program_data.items():
                    if value is not None: # Only update non-null values
                        setattr(existing_program, key, value)
                existing_program.university_id = univ.id
                session.add(existing_program)
                session.commit()
                session.refresh(existing_program)
                logger.debug(f"Updated program: {name_en} ({academic_year})")
                return existing_program, False
            else:
                # Insert new
                new_program = Program(**program_data)
                new_program.university_id = univ.id
                session.add(new_program)
                session.commit()
                session.refresh(new_program)
                logger.debug(f"Inserted program: {name_en} ({academic_year})")
                return new_program, True
