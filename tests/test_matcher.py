import pytest
from src.models.scraper_models import ProgramContext
from src.core.matcher import ProgramMatcher

@pytest.fixture
def sample_contexts():
    return [
        ProgramContext(name_en="Master of Science in Computer Science", program_group_code="hku-msc-cs", faculty="Engineering", tuition_amount=210000.0),
        ProgramContext(name_en="Master of Arts in History", program_group_code="hku-ma-history", faculty="Arts", tuition_amount=150000.0),
        ProgramContext(name_en="MSc in Data Science", program_group_code="hku-msc-ds", faculty="Engineering", tuition_amount=250000.0),
    ]

def test_exact_match(sample_contexts):
    matcher = ProgramMatcher(sample_contexts)
    assert matcher.match_fast("Master of Science in Computer Science") == "hku-msc-cs"
    assert matcher.match_fast("Master of Arts in History") == "hku-ma-history"
    assert matcher.match_fast("Non Existent Program") is None

def test_normalized_match(sample_contexts):
    matcher = ProgramMatcher(sample_contexts)
    # Case insensitive, punctuation ignored
    assert matcher.match_fast("master of science in computer science") == "hku-msc-cs"
    assert matcher.match_fast("Master of Science in Computer Science!") == "hku-msc-cs"
    assert matcher.match_fast("M.Sc. in Data Science") == "hku-msc-ds" # "mscindatascience" matches "mscindatascience"

def test_slow_path_ranking(sample_contexts):
    matcher = ProgramMatcher(sample_contexts)
    
    # Target: "MSc Computer Science" (Similar to "Master of Science in Computer Science")
    # Should rank hku-msc-cs higher than hku-msc-ds
    matches = matcher.find_top_matches("MSc Computer Science", target_faculty="Engineering", limit=5)
    assert len(matches) > 0
    assert matches[0].program_group_code == "hku-msc-cs"
    
def test_faculty_boost(sample_contexts):
    matcher = ProgramMatcher(sample_contexts)
    
    # "Master History" could match "Master of Arts in History"
    # If faculty matches "Arts", it gets a boost.
    matches = matcher.find_top_matches("Master History", target_faculty="Arts", limit=1)
    assert matches[0].program_group_code == "hku-ma-history"

def test_tuition_boost(sample_contexts):
    matcher = ProgramMatcher(sample_contexts)
    
    # "Data Science Master"
    # Tuition 250000 matches hku-msc-ds exactly
    matches = matcher.find_top_matches("Data Science Master", target_tuition=250000.0, limit=1)
    assert matches[0].program_group_code == "hku-msc-ds"
