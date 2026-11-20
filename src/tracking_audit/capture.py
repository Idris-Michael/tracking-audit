"""One page load in a throwaway headless browser: what requests fired, what the dataLayer held.

The page is captured before any cookie banner is touched, which is the state Consent Mode has to
handle. No profile, no cookies, no clicks. Every request is checked by the SSRF guard first.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from playwright.async_api import Browser, Playwright, Request, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

from .analyse import MAX_REQUESTS, Capture
from .guard import BlockedURL, Guard
from .proxy import EgressProxy

log = structlog.get_logger()

NAV_TIMEOUT_MS = 30_000
SETTLE_TIMEOUT_MS = 8_000  # tags keep firing after `load`; busy pages never reach networkidle
MAX_HTML_CHARS = 2_000_000
MAX_DATA_LAYER = 200


class CaptureError(RuntimeError):
    """The page could not be loaded (blocked, unreachable, or an HTTP error)."""


@dataclass(frozen=True)
class Fixture:
    status: int
    body: str
    content_type: str = "text/html"
    headers: dict[str, str] | None = None


# Tests and demos serve pages from memory instead of the network; production passes None.
FixtureFn = Callable[[str], Fixture | None]

# Reads the dataLayer and Google's consent state. gtag() pushes `arguments` objects, which are
# turned into arrays here so they survive JSON. google_tag_data.ics is internal to Google's tag; if it
# is missing or changes shape the capture still works and the consent check falls back to the rest.
PAGE_STATE_JS = """(limit) => {
  const safe = (v) => {
    try {
      return JSON.parse(JSON.stringify(v, (k, x) =>
        typeof x === "function" || (typeof Node !== "undefined" && x instanceof Node) ? undefined : x));
    } catch (e) { return null; }  // circular or exotic entry: keep its slot, drop its content
  };
  const out = { dataLayer: [], consent: {} };
  const dl = window.dataLayer;
  if (Array.isArray(dl)) {
    out.dataLayer = dl.slice(0, limit).map((e) =>
      safe(e && typeof e === "object" && !Array.isArray(e) && "length" in e ? Array.from(e) : e));
  }
  try {
    const gtd = window.google_tag_data;
    const entries = gtd && gtd.ics && gtd.ics.entries;
    if (entries && typeof entries === "object") {
      for (const [k, v] of Object.entries(entries)) out.consent[k] = { default: safe(v && v.default) };
    }
  } catch (e) { /* not present: nothing to record */ }
  return JSON.stringify(out);
}"""


async def capture_page(
    browser: Browser,
    target: str,
    *,
    guard: Guard | None = None,
    fixture: FixtureFn | None = None,
) -> Capture:
    guard = guard or Guard()
    try:
        url = await guard.check_url(target)
    except BlockedURL as e:
        raise CaptureError(str(e)) from e
    proxy = EgressProxy(guard)
    proxy_url = await proxy.start()
    context = await browser.new_context(locale="en-GB", service_workers="block", proxy={"server": proxy_url})
    requests: list[str] = []

    async def route(route: Route, request: Request) -> None:
        try:
            await guard.check_url(request.url)
        except BlockedURL:
            await route.abort("blockedbyclient")
            return
        if fixture is None:
            await route.continue_()
            return
        found = fixture(request.url)
        if found is None:
            await route.fulfill(status=404, body="")
        else:
            await route.fulfill(
                status=found.status, body=found.body, content_type=found.content_type, headers=found.headers
            )

    try:
        await context.route("**/*", route)
        page = await context.new_page()
        page.on("request", lambda r: requests.append(r.url) if len(requests) < MAX_REQUESTS else None)
        try:
            res = await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)
        except PlaywrightError as e:
            raise CaptureError(f"could not load {url}: {str(e).splitlines()[0]}") from e
        if res is None:
            raise CaptureError(f"no response from {url}")
        if res.status >= 400:
            if proxy.blocked:  # the egress proxy answered: a redirect led somewhere private
                raise CaptureError(f"{url} led to a blocked host ({', '.join(sorted(set(proxy.blocked)))})")
            raise CaptureError(f"{url} returned HTTP {res.status}")
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightError:
            log.info("settle_timeout", url=url)  # expected on busy pages; the capture is still valid
        state: dict[str, Any] = json.loads(await page.evaluate(PAGE_STATE_JS, MAX_DATA_LAYER))
        html = (await page.content())[:MAX_HTML_CHARS]
        return Capture(
            final_url=page.url,
            html=html,
            requests=list(requests),
            data_layer=state.get("dataLayer", []),
            consent_state=state.get("consent", {}),
            blocked=sorted(set(guard.blocked_hosts) | set(proxy.blocked)),
        )
    finally:
        await context.close()
        await proxy.close()


# Chromium sends loopback traffic around any proxy unless told not to; the proxy must see it all.
LAUNCH_ARGS = ["--proxy-bypass-list=<-loopback>"]


async def launch(pw: Playwright) -> Browser:
    return await pw.chromium.launch(headless=True, args=LAUNCH_ARGS)


async def capture(target: str, *, guard: Guard | None = None, fixture: FixtureFn | None = None) -> Capture:
    """Convenience for one audit: launches Chromium, captures, closes it."""
    async with async_playwright() as pw:
        browser = await launch(pw)
        try:
            return await capture_page(browser, target, guard=guard, fixture=fixture)
        finally:
            await browser.close()
