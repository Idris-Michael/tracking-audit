"""Integration tests against real Postgres (testcontainers). They check what SQLite cannot: SKIP LOCKED
keeps parallel workers off each other's audits, racing retries cannot create two audits, and the
Alembic migration builds the schema the code expects."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from tracking_audit.db import Audit, Batch, claim_next, insert_audit, make_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    if os.environ.get("SKIP_DOCKER_TESTS"):
        pytest.skip("SKIP_DOCKER_TESTS is set")
    try:
        from testcontainers.postgres import PostgresContainer

        pg = PostgresContainer("postgres:17-alpine", driver="psycopg")
        pg.start()
    except Exception as e:
        pytest.skip(f"Docker unavailable: {e}")
    url = pg.get_connection_url()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    yield eng
    eng.dispose()
    pg.stop()


def test_migration_matches_models(engine: Engine) -> None:
    insp = inspect(engine)
    for model in (Audit, Batch):
        cols = {c["name"] for c in insp.get_columns(model.__tablename__)}
        assert cols == set(model.__table__.columns.keys()), model.__tablename__
    assert {"ix_audits_status", "ix_audits_batch_id"} <= {i["name"] for i in insp.get_indexes("audits")}


def test_parallel_workers_never_claim_the_same_audit(engine: Engine) -> None:
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as s:
        for i in range(40):
            insert_audit(s, f"https://p{i}.example/", None)
    claimed: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        while True:
            with sessions() as s:
                row = claim_next(s, lease_s=300, max_attempts=2)
                if row is None:
                    return
                with lock:
                    claimed.append(row.id)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 40
    assert len(set(claimed)) == 40  # no audit handed to two workers


def test_racing_retries_with_one_idempotency_key_create_one_audit(engine: Engine) -> None:
    sessions = sessionmaker(engine, expire_on_commit=False)

    def submit(_: int) -> tuple[str, bool]:
        with sessions() as s:
            row, created = insert_audit(s, "https://race.example/", "same-key")
            return row.id, created

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert len({rid for rid, _ in results}) == 1
    assert sum(created for _, created in results) == 1
