from sqlalchemy.dialects import postgresql

from src.models.ingestion import IngestionJob, IngestionStage, IngestionTask


def _bind_stage(column, stage: IngestionStage) -> str:
    processor = column.type.bind_processor(postgresql.dialect())
    assert processor is not None
    return processor(stage)


def test_job_current_stage_binds_enum_value() -> None:
    bound = _bind_stage(IngestionJob.__table__.c.current_stage, IngestionStage.FETCH_RAW)
    assert bound == IngestionStage.FETCH_RAW.value


def test_task_stage_binds_enum_value() -> None:
    bound = _bind_stage(IngestionTask.__table__.c.stage, IngestionStage.FETCH_RAW)
    assert bound == IngestionStage.FETCH_RAW.value
