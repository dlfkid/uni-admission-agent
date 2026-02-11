import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

# Load environment variables
load_dotenv()

from src.agents.cleaner_agent import LLMCleanerAgent
from src.core.token_tracker import tracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_agent():
    # Example Row Data (Complex)
    raw_row = {
        "Course Name": "Master of Data Science",
        "Tuition Fee": "HK$ 350,000 per year",
        "Duration": "Full-time 1 year / Part-time 2 years",
        "Deadlines": "Round 1: 12 Dec 2025; Round 2: 30 Mar 2026"
    }
    
    logger.info("Initializing Agent...")
    agent = LLMCleanerAgent()
    
    if not agent.client:
        logger.error("Agent client not initialized (missing API Key). check .env")
        return

    logger.info(f"Cleaning row: {raw_row}")
    try:
        result = agent.clean_row(raw_row)
        if result:
            logger.info("Successfully parsed data!")
            if result.tuition:
                logger.info(f"Tuition: {result.tuition}")
                assert result.tuition.amount == 350000
                assert result.tuition.currency == "HKD"
            else:
                logger.warning("Tuition was None")
            assert len(result.study_options) == 2
            assert len(result.deadlines) == 2
            logger.info("✅ Verification PASSED")
        else:
            logger.error("Failed to parse row (returned None)")
    except Exception as e:
        logger.error(f"Test Failed: {e}")
        import traceback
        traceback.print_exc()

    
    # Log token usage
    tracker.log_summary()

if __name__ == "__main__":
    test_agent()
