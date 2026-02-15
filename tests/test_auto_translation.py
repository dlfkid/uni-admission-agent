import pytest
from unittest.mock import MagicMock, patch
from src.storage.db_manager import DatabaseManager
from src.models.admission import University, Program

pytestmark = pytest.mark.integration

@pytest.fixture
def mock_db_manager():
    # Helper to mock DB session interactions
    with patch("src.storage.db_manager.create_engine"), \
         patch("src.storage.db_manager.Session") as mock_session_cls, \
         patch("src.storage.db_manager.select"):
        
        manager = DatabaseManager()
        manager.engine = MagicMock()
        mock_session = mock_session_cls.return_value
        mock_session.__enter__.return_value = mock_session
        yield manager, mock_session

def test_upsert_program_auto_translation_zh_to_en(mock_db_manager):
    manager, session = mock_db_manager
    
    # Mock Univ lookup
    mock_univ = University(id=1, name="TestUniv", slug="test-univ")
    session.exec.return_value.first.side_effect = [mock_univ, None] # Univ found, Program not found

    # Mock TranslationAgent
    with patch("src.agents.translation_agent.TranslationAgent") as MockAgent:
        mock_translator = MockAgent.return_value
        mock_translator.translate_program_name.return_value = "Computer Science"
        
        # Input data missing name_en but has name_zh
        program_data = {
            "name_zh": "计算机科学",
            "academic_year": 2025
        }
        
        manager.upsert_program(program_data, "test-univ")
        
        # Verify translation called
        mock_translator.translate_program_name.assert_called_with("计算机科学", to_lang="en")
        
        # Verify DB insert used translated name
        args, _ = session.add.call_args_list[0]
        inserted_program = args[0]
        assert isinstance(inserted_program, Program)
        assert inserted_program.name_en == "Computer Science"
        assert inserted_program.name_zh == "计算机科学"

def test_upsert_program_auto_translation_en_to_zh(mock_db_manager):
    manager, session = mock_db_manager
    
    # Mock Univ lookup
    mock_univ = University(id=1, name="TestUniv", slug="test-univ")
    session.exec.return_value.first.side_effect = [mock_univ, None]

    with patch("src.agents.translation_agent.TranslationAgent") as MockAgent:
        mock_translator = MockAgent.return_value
        mock_translator.translate_program_name.return_value = "测试专业"
        
        # Input data missing name_zh
        program_data = {
            "name_en": "Test Program",
            "academic_year": 2025
        }
        
        manager.upsert_program(program_data, "test-univ")
        
        mock_translator.translate_program_name.assert_called_with("Test Program", to_lang="zh")
        
        args, _ = session.add.call_args_list[0]
        inserted_program = args[0]
        assert inserted_program.name_zh == "测试专业"
