"""HTTP surface. Requests are checked, stored and answered with 202; the browser work happens in the
worker, so a slow or hostile site never holds an API request open (ADR 4)."""

import hmac
import statistics
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from importlib import resources  # nosemgrep: python37-compatibility-importlib2 -- requires Python 3.12
from typing import Annotated, Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import telemetry
from .config import Settings
from .db import DONE, Audit, Batch, insert_audit, insert_batch, make_engine
from .guard import BlockedURL, precheck

log = structlog.get_logger(__name__)

# The scorecard echoes text from someone else's site. Served on its own it may run no script and load
# nothing; styles are inline. Even an escaping bug in the template could not execute anything.
SCORECARD_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"
)
CONSOLE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
    "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; frame-src blob:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class RateLimiter:
    """Sliding one-minute window per client, in-process. Several replicas need the limit at the
    ingress or in a shared store instead."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        t = self._clock()
        with self._lock:
            q = self._hits[key]
            while q and t - q[0] >= 60:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(t)
            return True


class AuditIn(BaseModel):
    target: str = Field(min_length=1, max_length=2048, examples=["https://shop.example/"])


class BatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    targets: list[str] = Field(min_length=1)


def summary(row: Audit) -> dict[str, Any]:
    return {
        "id": row.id,
        "target": row.target,
        "batch_id": row.batch_id,
        "status": row.status,
        "score": row.score,
        "error": row.error,
        "seconds": row.seconds,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def create_app(settings: Settings, session_factory: Callable[[], Session] | None = None) -> FastAPI:
    sessions = session_factory or sessionmaker(make_engine(settings.database_url), expire_on_commit=False)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    app = FastAPI(
        title="tracking-audit",
        version="0.1.0",
        description="Queue tracking audits of public web pages and fetch their scorecards.",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    def db() -> Iterator[Session]:
        with sessions() as s:
            yield s

    def caller(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
        expected = f"Bearer {settings.api_token.get_secret_value()}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "API token required")
        return request.client.host if request.client else "unknown"

    def rate_limited(who: str) -> None:
        if not limiter.allow(who):
            raise HTTPException(429, "rate limit exceeded", headers={"retry-after": "60"})

    def checked(targets: list[str]) -> list[str]:
        good, bad = [], []
        for t in targets:
            try:
                good.append(precheck(t))
            except BlockedURL as e:
                bad.append({"target": t[:200], "reason": str(e)})
        if bad:
            raise HTTPException(422, {"message": "some targets are not public web pages", "refused": bad})
        return good

    def found(s: Session, audit_id: str) -> Audit:
        row = s.get(Audit, audit_id)
        if row is None:
            raise HTTPException(404, "no such audit")
        return row

    Db = Annotated[Session, Depends(db)]
    Caller = Annotated[str, Depends(caller)]
    IdemKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def console() -> HTMLResponse:
        page = resources.files("tracking_audit").joinpath("static/console.html").read_text("utf-8")
        return HTMLResponse(page, headers={"Content-Security-Policy": CONSOLE_CSP})

    @app.post("/v1/audits", status_code=202)
    async def create_audit(
        body: AuditIn, s: Db, who: Caller, idempotency_key: IdemKey = None
    ) -> JSONResponse:
        rate_limited(who)
        [target] = checked([body.target])
        row, created = await run_in_threadpool(insert_audit, s, target, idempotency_key)
        log.info("audit_queued" if created else "audit_repeat", audit_id=row.id, target=target)
        return JSONResponse(summary(row), status_code=202 if created else 200)

    @app.post("/v1/batches", status_code=202)
    async def create_batch(
        body: BatchIn, s: Db, who: Caller, idempotency_key: IdemKey = None
    ) -> JSONResponse:
        rate_limited(who)
        if len(body.targets) > settings.max_batch:
            raise HTTPException(422, f"at most {settings.max_batch} targets per batch")
        targets = list(dict.fromkeys(checked(body.targets)))
        batch, rows, created = await run_in_threadpool(insert_batch, s, body.name, targets, idempotency_key)
        payload = {"id": batch.id, "name": batch.name, "audits": [summary(r) for r in rows]}
        return JSONResponse(payload, status_code=202 if created else 200)

    @app.get("/v1/audits")
    def list_audits(
        s: Db,
        _: Caller,
        batch_id: str | None = None,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        q = select(Audit).order_by(Audit.created_at.desc()).limit(limit)
        if batch_id:
            q = q.where(Audit.batch_id == batch_id)
        if status:
            q = q.where(Audit.status == status)
        return [summary(r) for r in s.scalars(q)]

    @app.get("/v1/audits/{audit_id}")
    def get_audit(audit_id: str, s: Db, _: Caller) -> dict[str, Any]:
        row = found(s, audit_id)
        return {**summary(row), "report": row.report, "blocked": row.blocked or [], "attempts": row.attempts}

    @app.get("/v1/audits/{audit_id}/scorecard.html")
    def scorecard_html(audit_id: str, s: Db, _: Caller) -> HTMLResponse:
        row = found(s, audit_id)
        if row.status != DONE or not row.scorecard_html:
            raise HTTPException(409, f"audit is {row.status}; no scorecard")
        return HTMLResponse(row.scorecard_html, headers={"Content-Security-Policy": SCORECARD_CSP})

    @app.get("/v1/audits/{audit_id}/scorecard.pdf")
    def scorecard_pdf(audit_id: str, s: Db, _: Caller) -> Response:
        row = found(s, audit_id)
        if row.status != DONE or not row.scorecard_pdf:
            raise HTTPException(409, f"audit is {row.status}; no scorecard")
        name = f"scorecard-{row.id[:8]}.pdf"
        return Response(
            row.scorecard_pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/v1/batches/{batch_id}")
    def get_batch(batch_id: str, s: Db, _: Caller) -> dict[str, Any]:
        batch = s.get(Batch, batch_id)
        if batch is None:
            raise HTTPException(404, "no such batch")
        rows = list(s.scalars(select(Audit).where(Audit.batch_id == batch_id).order_by(Audit.created_at)))
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {"id": batch.id, "name": batch.name, "counts": counts, "audits": [summary(r) for r in rows]}

    @app.get("/v1/stats")
    def stats(s: Db, _: Caller) -> dict[str, Any]:
        by_status = dict(s.execute(select(Audit.status, func.count()).group_by(Audit.status)).tuples().all())
        done = select(Audit.score, Audit.seconds).where(Audit.status == DONE)
        rows = s.execute(done).tuples().all()
        scores = [r[0] for r in rows if r[0] is not None]
        return {
            "by_status": by_status,
            "median_score": statistics.median(scores) if scores else None,
            "audited": len(rows),
        }

    return app


def main() -> None:
    settings = Settings()  # fields come from the environment
    telemetry.setup(settings.service_name, settings.otel_exporter_otlp_endpoint)
    app = create_app(settings)
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_config=None)  # noqa: S104  # inside a container
