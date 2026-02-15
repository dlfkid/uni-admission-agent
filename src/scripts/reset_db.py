
from sqlmodel import SQLModel, create_engine
from src.models.admission import University, Program
from src.storage.db_manager import DatabaseManager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_db():
    db = DatabaseManager()
    db.init_db() # Ensure engine is created
    
    logger.info("Dropping all tables...")
    SQLModel.metadata.drop_all(db.engine)
    
    logger.info("Recreating all tables...")
    SQLModel.metadata.create_all(db.engine)
    
    logger.info("Database reset complete.")

if __name__ == "__main__":
    reset_db()
