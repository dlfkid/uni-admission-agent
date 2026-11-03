"""One timestamp column type, shared by every model.

Every Alembic migration in this project declares its timestamp columns as
``sa.DateTime(timezone=True)``. SQLModel, given a bare ``datetime``
annotation, maps it to ``DateTime()`` — *without* a timezone. So the two ways
this schema can come into existence disagreed:

* created by a migration  → ``timestamp with time zone``
* created by ``create_all()`` (DatabaseManager does this on first session)
  → ``timestamp without time zone``

A database bootstrapped the second way therefore drifts from the declared
schema, silently and column by column. The live database showed 26 naive
columns next to 3 aware ones — the 3 belonging to tables added late enough
that only a migration ever created them.

Drift is not cosmetic. Postgres drops the offset on write to a naive column,
so a value stored as 17:58+08:00 reads back as a naive 17:58, and comparing
it with an aware value from one of the other columns raises
``TypeError: can't compare offset-naive and offset-aware datetimes``.

Annotate every model datetime with ``sa_type=UTC_DATETIME`` so both paths
produce the same column. `tests/test_timestamp_columns.py` fails if one is
missed.
"""

from sqlalchemy import DateTime

# Not a shared Column instance: SQLAlchemy Column objects cannot be attached
# to more than one table, whereas a *type* is immutable and safe to share.
UTC_DATETIME = DateTime(timezone=True)

__all__ = ["UTC_DATETIME"]
