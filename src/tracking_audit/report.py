"""Scorecards a prospect can receive: Markdown, HTML and PDF. Facts from the report only."""

from __future__ import annotations

from importlib.metadata import version as pkg_version
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader, select_autoescape
from playwright.async_api import Browser

from .analyse import Report

LABELS = {"pass": "Pass", "warn": "Warning", "fail": "Fail", "unknown": "Not visible"}
DEFAULT_PREPARED_BY = "Fieldnote Digital (fictional agency)"

_env = Environment(
    loader=PackageLoader("tracking_audit", "templates"),
    autoescape=select_autoescape(["html"]),  # every value on the card came from someone else's page
    trim_blocks=True,
    lstrip_blocks=True,
)


def host_of(report: Report) -> str:
    return (urlsplit(report.final_url).hostname or report.target).removeprefix("www.")


def checked_label(report: Report) -> str:
    return report.checked_at[:16].replace("T", " ")


def to_markdown(report: Report) -> str:
    rows = "\n".join(
        f"| {c.name} | {LABELS[c.status]} | {c.detail.replace('|', '/')} |" for c in report.checks
    )
    ids = report.ids
    seen = " · ".join(
        f"{label} {', '.join(values) or 'none'}"
        for label, values in (("GTM", ids.gtm), ("GA4", ids.ga4), ("Pixel", ids.pixel))
    )
    scored = f"Score **{report.score}/10** over {report.decided} checks that could be decided"
    return f"""# Tracking health scorecard: {report.final_url}

Checked {checked_label(report)} UTC · {scored}

| Check | Result | Finding |
| :-- | :-- | :-- |
{rows}

IDs seen: {seen}

Method: one page load in a clean headless browser, before any cookie banner was accepted. \
Server-side tagging and CAPI cannot be seen from a browser.
"""


def to_html(report: Report, prepared_by: str = DEFAULT_PREPARED_BY) -> str:
    return _env.get_template("scorecard.html").render(
        report=report,
        host=host_of(report),
        checked=checked_label(report),
        labels=LABELS,
        prepared_by=prepared_by,
        version=pkg_version("tracking-audit"),
    )


async def to_pdf(browser: Browser, html: str) -> bytes:
    """Prints the HTML card. Offline: scripts off and every request refused, so nothing on the card
    can make the renderer fetch anything."""
    context = await browser.new_context(java_script_enabled=False, offline=True)
    try:
        page = await context.new_page()
        await page.route("**/*", lambda route: route.abort())
        await page.set_content(html, wait_until="load")
        return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
    finally:
        await context.close()
