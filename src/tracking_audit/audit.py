"""One place that turns targets into outcomes; the CLI, the API worker and the benchmark all call it."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable

from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel

from .analyse import Report, analyse
from .capture import CaptureError, FixtureFn, capture_page, launch
from .guard import Guard, Resolver, system_resolver

DEFAULT_CONCURRENCY = 4
SITE_TIMEOUT_S = 90  # load 30 s + settle 8 s + reads; anything past this is stuck


class Outcome(BaseModel):
    target: str
    report: Report | None = None
    error: str | None = None
    seconds: float
    blocked: list[str] = []

    @property
    def ok(self) -> bool:
        return self.report is not None


async def audit_one(
    browser: Browser,
    target: str,
    *,
    resolver: Resolver = system_resolver,
    fixture: FixtureFn | None = None,
) -> Outcome:
    """Never raises for a bad site: an unreachable or blocked target is an outcome, not a crash."""
    t0 = time.monotonic()
    try:
        cap = await asyncio.wait_for(
            capture_page(browser, target, guard=Guard(resolver), fixture=fixture), SITE_TIMEOUT_S
        )
    except CaptureError as e:
        return Outcome(target=target, error=str(e), seconds=round(time.monotonic() - t0, 1))
    except TimeoutError:
        return Outcome(target=target, error=f"gave up after {SITE_TIMEOUT_S} s", seconds=SITE_TIMEOUT_S)
    return Outcome(
        target=target,
        report=analyse(target, cap),
        seconds=round(time.monotonic() - t0, 1),
        blocked=cap.blocked,
    )


async def audit_many(
    targets: Iterable[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    resolver: Resolver = system_resolver,
    fixture: FixtureFn | None = None,
    on_done: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Batch mode: one browser, a fresh context and proxy per site, `concurrency` sites at a time.
    Results come back in input order."""
    items = list(dict.fromkeys(t.strip() for t in targets if t.strip() and not t.lstrip().startswith("#")))
    gate = asyncio.Semaphore(max(1, concurrency))
    async with async_playwright() as pw:
        browser = await launch(pw)
        try:

            async def run(t: str) -> Outcome:
                async with gate:
                    out = await audit_one(browser, t, resolver=resolver, fixture=fixture)
                if on_done:
                    on_done(out)
                return out

            return list(await asyncio.gather(*(run(t) for t in items)))
        finally:
            await browser.close()
