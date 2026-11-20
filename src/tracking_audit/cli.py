"""Command line.

tracking-audit https://shop.example --html card.html --pdf card.pdf
tracking-audit https://shop.example --json
tracking-audit batch sites.txt --out audits/ --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from .audit import DEFAULT_CONCURRENCY, Outcome, audit_many
from .capture import launch
from .report import DEFAULT_PREPARED_BY, to_html, to_markdown, to_pdf


def slug(target: str) -> str:
    host = urlsplit(target if "://" in target else f"https://{target}").hostname or target
    return "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in host.removeprefix("www."))


async def write_cards(outcome: Outcome, html: Path | None, pdf: Path | None, prepared_by: str) -> None:
    if outcome.report is None:
        return
    card = to_html(outcome.report, prepared_by)
    if html:
        html.write_text(card, "utf-8")
    if pdf:
        async with async_playwright() as pw:
            browser = await launch(pw)
            try:
                pdf.write_bytes(await to_pdf(browser, card))
            finally:
                await browser.close()


def cmd_single(a: argparse.Namespace) -> int:
    [outcome] = asyncio.run(audit_many([a.target], concurrency=1))
    if outcome.report is None:
        print(f"audit failed: {outcome.error}", file=sys.stderr)
        return 2
    asyncio.run(write_cards(outcome, a.html, a.pdf, a.prepared_by))
    print(outcome.model_dump_json(indent=2) if a.json else to_markdown(outcome.report))
    return 0


def cmd_batch(a: argparse.Namespace) -> int:
    targets = a.file.read_text("utf-8").splitlines()
    a.out.mkdir(parents=True, exist_ok=True)

    def done(o: Outcome) -> None:
        state = f"{o.report.score}/10" if o.report else f"error: {o.error}"
        print(f"{o.seconds:5.1f}s  {o.target}  {state}", file=sys.stderr)

    outcomes = asyncio.run(audit_many(targets, concurrency=a.concurrency, on_done=done))
    for o in outcomes:
        name = slug(o.target)
        (a.out / f"{name}.json").write_text(o.model_dump_json(indent=2), "utf-8")
        if o.report:
            (a.out / f"{name}.html").write_text(to_html(o.report, a.prepared_by), "utf-8")
    with (a.out / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["target", "score", "decided", "gtm", "ga4", "consent", "pixel", "dedup", "error"])
        for o in outcomes:
            st = {c.name: c.status for c in o.report.checks} if o.report else {}
            w.writerow(
                [
                    o.target,
                    o.report.score if o.report else "",
                    o.report.decided if o.report else "",
                    st.get("Google Tag Manager", ""),
                    st.get("GA4", ""),
                    st.get("Consent Mode v2", ""),
                    st.get("Meta Pixel", ""),
                    st.get("Pixel and CAPI dedup", ""),
                    o.error or "",
                ]
            )
    ok = sum(1 for o in outcomes if o.report)
    print(
        json.dumps({"sites": len(outcomes), "audited": ok, "failed": len(outcomes) - ok, "out": str(a.out)})
    )
    return 0 if ok else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tracking-audit", description="Tracking health scorecard for a web page."
    )
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("batch", help="audit every URL in a file (one per line, # for comments)")
    b.add_argument("file", type=Path)
    b.add_argument("--out", type=Path, default=Path("audits"))
    b.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    b.add_argument("--prepared-by", default=DEFAULT_PREPARED_BY, help="name in the scorecard footer")
    return p


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "batch":
        return cmd_batch(parser().parse_args(args))
    p = argparse.ArgumentParser(prog="tracking-audit", description="Tracking health scorecard for one page.")
    p.add_argument("target", help="URL or hostname, e.g. shop.example")
    p.add_argument("--html", type=Path, help="write the HTML scorecard here")
    p.add_argument("--pdf", type=Path, help="write the PDF scorecard here")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--prepared-by", default=DEFAULT_PREPARED_BY)
    return cmd_single(p.parse_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
