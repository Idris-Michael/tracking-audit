"""The audit ledger: one row per requested audit, claimed by workers with a lease (ADR 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    or_,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"), index=True)
    target: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), index=True, default=QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[int | None] = mapped_column(Integer)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    blocked: Mapped[list[str] | None] = mapped_column(JSON)
    scorecard_html: Mapped[str | None] = mapped_column(Text)
    scorecard_pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    error: Mapped[str | None] = mapped_column(Text)
    seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def insert_audit(
    s: Session, target: str, idempotency_key: str | None, batch_id: str | None = None
) -> tuple[Audit, bool]:
    """Inserts once per Idempotency-Key: a retried request gets the original audit back (created=False).
    The unique constraint decides, so two racing retries cannot both create one."""
    row = Audit(target=target, idempotency_key=idempotency_key, batch_id=batch_id)
    s.add(row)
    try:
        s.commit()
    except IntegrityError:
        s.rollback()
        existing = s.scalars(select(Audit).where(Audit.idempotency_key == idempotency_key)).first()
        if existing is None:  # pragma: no cover - constraint fired but the row vanished
            raise
        return existing, False
    return row, True


def insert_batch(
    s: Session, name: str, targets: list[str], idempotency_key: str | None
) -> tuple[Batch, list[Audit], bool]:
    batch = Batch(name=name, idempotency_key=idempotency_key)
    s.add(batch)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        existing = s.scalars(select(Batch).where(Batch.idempotency_key == idempotency_key)).one()
        return existing, list(s.scalars(select(Audit).where(Audit.batch_id == existing.id))), False
    rows = [Audit(target=t, batch_id=batch.id) for t in targets]
    s.add_all(rows)
    s.commit()
    return batch, rows, True


def claim_next(s: Session, lease_s: int, max_attempts: int) -> Audit | None:
    """The oldest queued audit, or a running one whose worker's lease ran out (it crashed). On Postgres
    `FOR UPDATE SKIP LOCKED` lets workers claim in parallel without double-claiming."""
    t = now()
    row = s.scalars(
        select(Audit)
        .where(
            or_(
                Audit.status == QUEUED,
                (Audit.status == RUNNING) & (Audit.locked_until.is_(None) | (Audit.locked_until < t)),
            )
        )
        .order_by(Audit.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if row is None:
        s.rollback()
        return None
    if row.attempts >= max_attempts:
        row.status, row.error, row.finished_at = (
            FAILED,
            f"worker stopped {row.attempts} times on this site",
            t,
        )
        s.commit()
        return claim_next(s, lease_s, max_attempts)
    row.status = RUNNING
    row.attempts += 1
    row.started_at = t
    row.locked_until = t + timedelta(seconds=lease_s)
    s.commit()
    return row
