"""Integration test for 20260911_0012: naive timestamp columns become aware.

Marked integration because it needs a real Postgres — SQLite has no distinct
timestamp types, so the behaviour under test does not exist there. Run with:

    uv run pytest tests/test_timestamptz_migration.py -m integration

The scenario reproduces the actual drift: a table whose timestamp column was
created naive (as create_all() used to make it), holding a value written the
way the application writes one — an aware UTC datetime. The migration must
leave that row pointing at the SAME INSTANT, which is the property that a
plausible-looking `USING ... AT TIME ZONE 'UTC'` clause silently breaks.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

MIGRATION = "20260911_0012"
PREVIOUS = "20260908_0011"


def _base_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("needs a postgresql DATABASE_URL")
    return url


@pytest.fixture(autouse=True)
def _fail_if_a_test_leaks_the_env_var():
    """Belt and braces: DATABASE_URL must look the same after every test."""
    before = os.environ.get("DATABASE_URL")
    yield
    assert os.environ.get("DATABASE_URL") == before, (
        "a test left DATABASE_URL pointing somewhere else"
    )


@pytest.fixture()
def throwaway_db() -> str:
    """A database created for this test and dropped afterwards."""
    base = _base_url()
    name = f"tztest_{uuid.uuid4().hex[:10]}"
    admin = create_engine(base.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    try:
        yield base.rsplit("/", 1)[0] + "/" + name
    finally:
        with admin.connect() as c:
            c.execute(text(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = :n"
            ), {"n": name})
            c.execute(text(f"DROP DATABASE IF EXISTS {name}"))


def _run_migration(db_url: str, direction: str, revision: str) -> None:
    """Run one migration against *db_url*, and only against *db_url*.

    migrations/env.py sets sqlalchemy.url from os.getenv("DATABASE_URL"),
    overriding whatever the caller put on the Config. So setting the Config
    alone is not enough — under `DATABASE_URL=<real db> pytest`, alembic
    would quietly migrate the real database instead of the throwaway one.
    (It did, once, which is why this is spelled out here.) The environment
    variable is the thing that has to point at the throwaway, and it is
    restored afterwards.
    """
    from alembic import command
    from alembic.config import Config

    assert "tztest_" in db_url, (
        f"refusing to migrate {db_url!r} — this helper only ever runs against "
        "a throwaway database created by the fixture"
    )

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", db_url)
        getattr(command, direction)(cfg, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_naive_column_becomes_aware_without_moving_the_instant(throwaway_db: str) -> None:
    eng = create_engine(throwaway_db)
    # 03:04:05 UTC. On a +08 server a naive column stores this as 11:04:05,
    # which is why the plain cast — not an AT TIME ZONE 'UTC' clause — is the
    # one that recovers it.
    written = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with eng.begin() as c:
        # A table the migration owns, with the drifted column shape.
        c.execute(text("create table university (id serial primary key, "
                       "name text, slug text, updated_at timestamp)"))
        c.execute(text("create table alembic_version (version_num varchar(32) not null)"))
        c.execute(text("insert into alembic_version values (:v)"), {"v": PREVIOUS})
        c.execute(text("insert into university (name, slug, updated_at) values "
                       "('probe', 'probe', :v)"), {"v": written})

    with eng.connect() as c:
        before = c.execute(text("select updated_at from university")).scalar()
        assert before.tzinfo is None, "precondition: the column starts naive"

    _run_migration(throwaway_db, "upgrade", MIGRATION)

    with eng.connect() as c:
        dtype = c.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='university' and column_name='updated_at'"
        )).scalar()
        after = c.execute(text("select updated_at from university")).scalar()

    assert dtype == "timestamp with time zone"
    assert after.tzinfo is not None
    assert after == written, (
        f"the instant moved: wrote {written}, read back {after}. A "
        "USING ... AT TIME ZONE 'UTC' clause causes exactly this."
    )


def test_an_already_aware_column_is_left_alone(throwaway_db: str) -> None:
    """The migration must be a no-op on a database built by migrations.

    It selects only columns that are currently naive, so an aware column is
    never re-cast — re-casting one WOULD move its instant.
    """
    eng = create_engine(throwaway_db)
    written = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with eng.begin() as c:
        c.execute(text("create table university (id serial primary key, "
                       "name text, slug text, updated_at timestamptz)"))
        c.execute(text("create table alembic_version (version_num varchar(32) not null)"))
        c.execute(text("insert into alembic_version values (:v)"), {"v": PREVIOUS})
        c.execute(text("insert into university (name, slug, updated_at) values "
                       "('probe', 'probe', :v)"), {"v": written})

    _run_migration(throwaway_db, "upgrade", MIGRATION)

    with eng.connect() as c:
        after = c.execute(text("select updated_at from university")).scalar()
    assert after == written


def test_downgrade_restores_the_naive_shape(throwaway_db: str) -> None:
    eng = create_engine(throwaway_db)
    written = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    with eng.begin() as c:
        c.execute(text("create table university (id serial primary key, "
                       "name text, slug text, updated_at timestamp)"))
        c.execute(text("create table alembic_version (version_num varchar(32) not null)"))
        c.execute(text("insert into alembic_version values (:v)"), {"v": PREVIOUS})
        c.execute(text("insert into university (name, slug, updated_at) values "
                       "('probe', 'probe', :v)"), {"v": written})

    _run_migration(throwaway_db, "upgrade", MIGRATION)
    _run_migration(throwaway_db, "downgrade", PREVIOUS)

    with eng.connect() as c:
        dtype = c.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='university' and column_name='updated_at'"
        )).scalar()
    assert dtype == "timestamp without time zone"


def test_a_table_the_project_does_not_own_is_untouched(throwaway_db: str) -> None:
    eng = create_engine(throwaway_db)
    with eng.begin() as c:
        c.execute(text("create table university (id serial primary key, "
                       "name text, slug text, updated_at timestamp)"))
        c.execute(text("create table someone_elses (id serial primary key, "
                       "updated_at timestamp)"))
        c.execute(text("create table alembic_version (version_num varchar(32) not null)"))
        c.execute(text("insert into alembic_version values (:v)"), {"v": PREVIOUS})

    _run_migration(throwaway_db, "upgrade", MIGRATION)

    with eng.connect() as c:
        foreign = c.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='someone_elses' and column_name='updated_at'"
        )).scalar()
        ours = c.execute(text(
            "select data_type from information_schema.columns "
            "where table_name='university' and column_name='updated_at'"
        )).scalar()
    assert foreign == "timestamp without time zone", "must not touch foreign tables"
    assert ours == "timestamp with time zone"
