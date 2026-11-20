"""Worker: claims queued audits from the ledger and runs them in one long-lived browser.

A site that fails to load is a finished audit with an error, not a retry: the scorecard says why.
Only a worker that dies mid-audit leaves a row behind, and its lease expiry hands it to another
worker, at most `worker_max_attempts` times (ADR 4).
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from typing import Any

import structlog
from playwright.async_api import Browser, Playwright, async_playwright
from sqlalchemy.orm import Session, sessionmaker

from . import telemetry
from .audit import Outcome, audit_one
from .capture import FixtureFn, launch
from .config import Settings
from .db import DONE, FAILED, Audit, claim_next, make_engine, now
from .guard import Resolver, system_resolver
from .report import to_html, to_pdf

log = structlog.get_logger(__name__)


def save(
    sessions: Callable[[], Session], audit_id: str, out: Outcome, html: str | None, pdf: bytes | None
) -> None:
    with sessions() as s:
        row = s.get(Audit, audit_id)
        if row is None:  # pragma: no cover - deleted while running
            return
        row.status = DONE if out.report else FAILED
        row.score = out.report.score if out.report else None
        row.report = out.report.model_dump(mode="json") if out.report else None
        row.error = out.error
        row.blocked = out.blocked
        row.seconds = out.seconds
        row.scorecard_html = html
        row.scorecard_pdf = pdf
        row.finished_at = now()
        row.locked_until = None
        s.commit()


class Worker:
    def __init__(
        self,
        settings: Settings,
        sessions: Callable[[], Session],
        *,
        resolver: Resolver = system_resolver,
        fixture: FixtureFn | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.resolver = resolver
        self.fixture = fixture
        self.stopping = False
        self._browser: Browser | None = None

    async def browser(self, pw: Playwright) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            if self._browser is not None:
                log.warning("browser_relaunch")
            self._browser = await launch(pw)
        return self._browser

    def claim(self) -> tuple[str, str] | None:
        with self.sessions() as s:
            row = claim_next(s, self.settings.worker_lease_s, self.settings.worker_max_attempts)
            return (row.id, row.target) if row else None

    async def process(self, pw: Playwright, audit_id: str, target: str) -> None:
        browser = await self.browser(pw)
        out = await audit_one(browser, target, resolver=self.resolver, fixture=self.fixture)
        html = pdf = None
        if out.report:
            html = to_html(out.report, self.settings.prepared_by)
            pdf = await to_pdf(browser, html)
        await asyncio.to_thread(save, self.sessions, audit_id, out, html, pdf)
        log.info(
            "audit_finished",
            audit_id=audit_id,
            target=target,
            score=out.report.score if out.report else None,
            error=out.error,
            seconds=out.seconds,
        )

    async def run(self, *, until_empty: bool = False) -> int:
        """Runs until stopped (or, with until_empty, until the queue is drained). Returns audits done."""
        gate = asyncio.Semaphore(self.settings.worker_concurrency)
        running: set[asyncio.Task[Any]] = set()
        done = 0
        async with async_playwright() as pw:
            try:
                while not self.stopping:
                    await gate.acquire()
                    job = await asyncio.to_thread(self.claim)
                    if job is None:
                        gate.release()
                        if until_empty and not running:
                            break
                        await asyncio.sleep(self.settings.worker_poll_s)
                        continue

                    async def one(job: tuple[str, str] = job) -> None:
                        try:
                            await self.process(pw, *job)
                        except Exception:
                            # The lease expires and another attempt picks it up (bounded by max_attempts).
                            log.exception("audit_crashed", audit_id=job[0])
                        finally:
                            gate.release()

                    task = asyncio.create_task(one())
                    running.add(task)
                    task.add_done_callback(running.discard)
                    done += 1
                if running:
                    await asyncio.gather(*running)
            finally:
                if self._browser is not None:
                    await self._browser.close()
        return done


def main() -> None:
    settings = Settings()  # fields come from the environment
    telemetry.setup(settings.service_name + "-worker", settings.otel_exporter_otlp_endpoint)
    worker = Worker(settings, sessionmaker(make_engine(settings.database_url), expire_on_commit=False))

    def stop(*_: object) -> None:
        worker.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log.info("worker_started", concurrency=settings.worker_concurrency)
    asyncio.run(worker.run())
