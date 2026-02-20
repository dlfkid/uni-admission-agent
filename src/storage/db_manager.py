import os
import logging
import threading
from typing import Any, Optional, Tuple, List, Dict
from datetime import datetime, timezone
from sqlmodel import create_engine, Session, select, SQLModel, col
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from src.models.admission import University, Program
from src.models.scraper_models import ProgramContext
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _normalize_text_payload(value: Any) -> Any:
    """Normalize payload values for DB writes.

    Recursively converts unexpected bytes into string to avoid
    Unicode decode errors in database adapters on Windows.
    """
    if isinstance(value, dict):
        return {
            _normalize_text_payload(k): _normalize_text_payload(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_normalize_text_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_text_payload(item) for item in value)
    if isinstance(value, bytes):
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return value

class DatabaseManager:
    _instance: Optional["DatabaseManager"] = None
    _lock = threading.Lock()
    initialized: bool
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseManager, cls).__new__(cls)
                    cls._instance.initialized = False
        return cls._instance

    def init_db(self, db_url: Optional[str] = None):
        """Initialize database connection and create tables."""
        if getattr(self, "engine", None):
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
        if not getattr(self, "engine", None):
            self.init_db()
        return Session(self.engine)

    def get_university_by_slug(self, slug: str) -> Optional[University]:
        """Retrieve a university by its slug."""
        with self.get_session() as session:
            statement = select(University).where(University.slug == slug)
            return session.exec(statement).first()

    def upsert_program(self, program_data: dict, univ_slug: str) -> Tuple[Program, bool]:
        """
        Upsert a program record using PostgreSQL ON CONFLICT.
        Logic: 
           - Match on (university_id, academic_year, name_en).
           - Do UPDATE on conflict, but coalesce() to avoid overwriting existing non-nulls with new nulls.
        Returns: (Program, created: bool) - Note: created boolean is harder to determine precisely with one query in SQLAlchemy 1.4/2.0 without RETURNING details, checking if system columns changed, or using xmax. 
                   For simplicity in this Agent context, we might return True (potentially created/updated) or fetch after.
                   Actually, let's stick to the interface but the 'created' bool might be just 'True' (processed).
        """
        from sqlalchemy.dialects.postgresql import insert
        from sqlalchemy import func

        with self.get_session() as session:
            # 1. Ensure University exists
            univ = session.exec(select(University).where(University.slug == univ_slug)).first()
            if not univ:
                univ = University(name=univ_slug, slug=univ_slug)
                session.add(univ)
                session.commit()
                session.refresh(univ)
            
            # 2. Prepare Data & Translations
            name_en = program_data.get("name_en")
            name_zh = program_data.get("name_zh")
            
            # --- Auto-Translation Logic ---
            if (not name_en and name_zh) or (not name_zh and name_en):
                try:
                    from src.agents.translation_agent import TranslationAgent
                    translator = TranslationAgent()
                    if not name_en and name_zh:
                        logger.info(f"Translating program name to EN: {name_zh}")
                        name_en = translator.translate_program_name(name_zh, to_lang="en")
                        program_data["name_en"] = name_en
                    elif not name_zh and name_en:
                        logger.info(f"Translating program name to ZH: {name_en}")
                        name_zh = translator.translate_program_name(name_en, to_lang="zh")
                        program_data["name_zh"] = name_zh
                except Exception as e:
                    logger.error(f"Auto-translation failed: {e}")
            
            if not name_en or not program_data.get("academic_year"):
                raise ValueError("Program data must contain 'name_en' and 'academic_year'")

            # 3. Construct Insert Statement
            # Add university_id to data
            full_data = _normalize_text_payload(program_data.copy())
            full_data["university_id"] = univ.id
            if "updated_at" not in full_data:
                full_data["updated_at"] = datetime.now(timezone.utc)

            stmt = insert(Program).values(**full_data)
            
            # 4. Construct ON CONFLICT ... DO UPDATE Set
            # We want to update all fields provided in full_data, BUT if the new value is None 
            # and the DB has a value, we keep the DB value (COALESCE).
            # Columns to theoretically update: everything except PK and constraints.
            
            # Note: We need to be careful. If we pass specific fields, we want to update them.
            # The Requirement: "如果数据库已有记录，新入库的 null 字段不应覆盖已有值"
            # This implies if provided key is None, ignore it? Or if provided key IS provided but None?
            # Usually upsert dictionary `full_data` only contains keys we scraped. 
            # If a key is NOT in `full_data`, `insert` uses default.
            # `excluded` table contains the values tried for insert.
            
            # Let's iterate over ALL columns in Program model to build the set_ dict
            # excluding primary keys and the unique constraint keys.
            constraint_keys = {"university_id", "academic_year", "name_en"}
            excluded_cols = constraint_keys | {"id"}
            
            update_dict = {}
            from sqlalchemy import inspect as sa_inspect
            mapper = sa_inspect(Program)
            for col_obj in mapper.columns:
                if col_obj.name not in excluded_cols:
                    # COALESCE(excluded.col, existing.col)
                    # If the INSERT attempted to put NULL (e.g. because data didn't have it and default is None), 
                    # keep existing.
                    update_dict[col_obj.name] = func.coalesce(getattr(stmt.excluded, col_obj.name), getattr(Program, col_obj.name))
            
            # Force update updated_at
            update_dict["updated_at"] = datetime.now(timezone.utc)

            stmt = stmt.on_conflict_do_update(
                constraint="uq_program_year", # Must match the name in SQLModel
                set_=update_dict
            )
            
            # Execute
            result = session.exec(stmt)
            session.commit()
            
            # Fetch and return
            # To be 100% strictly compatible with previous 'new_program, created', 
            # we can't easily know if it was insert or update without more complex SQL/returning.
            # But the caller mostly cares about the object.
            # We re-fetch.
            refreshed = session.exec(
                select(Program).where(
                    Program.university_id == univ.id,
                    Program.academic_year == full_data["academic_year"],
                    Program.name_en == full_data["name_en"]
                )
            ).first()
            
            if not refreshed:
                # Should not happen
                raise RuntimeError("Upsert failed to produce a record")

            return refreshed, True # Returning True for 'processed'

    def get_program_history(self, program_group_code: str) -> List[Program]:
        """
        Retrieve all historical records for a given program group code,
        ordered by academic year.
        """
        with self.get_session() as session:
            statement = select(Program).where(
                Program.program_group_code == program_group_code
            ).order_by(col(Program.academic_year))
            results = session.exec(statement).all()
            return list(results)

    def get_program_group_map(self, university_id: int) -> Dict[str, str]:
        """
        Get a mapping of {name_en: program_group_code} for a university.
        Used to provide context to LLM for consistent group code assignment.
        Only returns entries where program_group_code is set.
        """
        with self.get_session() as session:
            statement = select(Program.name_en, Program.program_group_code).where(
                Program.university_id == university_id,
                Program.program_group_code.is_not(None) # type: ignore
            )
            results = session.exec(statement).all()
            return {name: code for name, code in results if name and code}

    def get_program_contexts(self, university_id: int) -> List[ProgramContext]:
        """
        Fetch rich context for all programs with a group code.
        Returns a list of ProgramContext objects.
        """
        
        with self.get_session() as session:
            # Fetch most recent record for each group code to get latest metadata
            # Window function would be ideal, but for simplicity/portability (sqlite), 
            # we can fetch all and dedup in python or just fetch meaningful ones.
            # Let's fetch all non-null group codes.
            statement = select(Program).where(
                Program.university_id == university_id,
                Program.program_group_code.is_not(None) # type: ignore
            )
            programs = session.exec(statement).all()
            
            contexts = []
            seen_groups = set()
            
            # Sort by year desc to prioritize latest info
            sorted_programs = sorted(programs, key=lambda p: p.academic_year, reverse=True)
            
            for p in sorted_programs:
                if not p.program_group_code: continue
                
                # We might want multiple aliases for the same group code if names changed?
                # User said: "Fetch all history (name_en, group_code)". 
                # So we should include ALL name variations for the same group code.
                # But deduplicate if (name_en, group_code) is identical.
                
                key = (p.name_en, p.program_group_code)
                if key in seen_groups:
                    continue
                seen_groups.add(key)
                
                contexts.append(ProgramContext(
                    name_en=p.name_en,
                    program_group_code=p.program_group_code,
                    faculty=p.faculty,
                    tuition_amount=float(p.tuition_amount) if p.tuition_amount else None,
                    currency=p.currency.value if p.currency else None
                ))
                
            return contexts
