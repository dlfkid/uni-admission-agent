from sqlalchemy.orm import configure_mappers


def test_sqlalchemy_mappers_configure() -> None:
    import src.models.admission  # noqa: F401
    import src.models.ingestion  # noqa: F401
    import src.models.requirement  # noqa: F401

    configure_mappers()
