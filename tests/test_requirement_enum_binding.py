from sqlalchemy.dialects import postgresql

from src.models.admission import StudyMode
from src.models.requirement import ProgramRequirement, ProgramStudyOption, RequirementCategory


def _bind_enum(column, value) -> str:
    processor = column.type.bind_processor(postgresql.dialect())
    assert processor is not None
    return processor(value)


def test_program_study_option_mode_binds_enum_value() -> None:
    bound = _bind_enum(ProgramStudyOption.__table__.c.mode, StudyMode.FULL_TIME)
    assert bound == StudyMode.FULL_TIME.value


def test_program_requirement_category_binds_enum_value() -> None:
    bound = _bind_enum(
        ProgramRequirement.__table__.c.category,
        RequirementCategory.ACADEMIC_SUBJECT,
    )
    assert bound == RequirementCategory.ACADEMIC_SUBJECT.value
