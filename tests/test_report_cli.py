"""Scorecard and CLI tests. WHY:
- the card goes to a prospect, so it states its limits and nothing from the audited page can inject
  markup into it (a hostile page controls its own URL and the IDs we echo back)
- batch mode must finish the whole list even when some sites fail or are blocked
"""

from __future__ import annotations

import csv
import functools
import json
from pathlib import Path

import pytest

from tracking_audit import cli
from tracking_audit.analyse import Capture, analyse
from tracking_audit.audit import audit_many
from tracking_audit.report import to_html, to_markdown

from .fixture_site import resolve, serve

GTM = "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"


def report(**over: object):  # type: ignore[no-untyped-def]
    cap = Capture(final_url="https://shop.example/", requests=[GTM], **over)  # type: ignore[arg-type]
    return analyse("shop.example", cap, checked_at="2026-11-20T10:00:00+00:00")


def test_markdown_states_its_limits_and_keeps_table_cells_intact() -> None:
    md = to_markdown(report())
    assert "before any cookie banner was accepted" in md
    assert "CAPI cannot be seen from a browser" in md
    for line in (ln for ln in md.splitlines() if ln.startswith("| ") and not ln.startswith("| :")):
        assert line.count(" | ") == 2, line


def test_html_card_escapes_everything_the_audited_page_controls() -> None:
    r = report().model_copy(update={"final_url": 'https://evil.example/"><script>alert(1)</script>'})
    html = to_html(r, prepared_by="<b>Agency</b>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>Agency</b>" not in html
    assert "Not visible" in html and "/10" in html


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's audits run against the in-memory fixture shop instead of the internet."""
    monkeypatch.setattr(cli, "audit_many", functools.partial(audit_many, resolver=resolve, fixture=serve))


@pytest.mark.browser
@pytest.mark.usefixtures("offline")
def test_single_audit_writes_html_and_pdf_cards(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html, pdf = tmp_path / "card.html", tmp_path / "card.pdf"
    assert cli.main(["https://shop.example/", "--html", str(html), "--pdf", str(pdf)]) == 0
    assert "Score **10/10**" in capsys.readouterr().out
    assert "shop.example" in html.read_text("utf-8")
    assert pdf.read_bytes().startswith(b"%PDF")


@pytest.mark.browser
@pytest.mark.usefixtures("offline")
def test_single_audit_json_and_failure_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["https://shop.example/", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["report"]["score"] == 10
    assert cli.main(["http://10.0.0.8/"]) == 2
    assert "blocked private address" in capsys.readouterr().err


@pytest.mark.browser
@pytest.mark.usefixtures("offline")
def test_batch_finishes_the_list_when_sites_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sites = tmp_path / "sites.txt"
    sites.write_text("# prospects\nhttps://shop.example/\nhttps://shop.example/gone\nlocalhost\n\n", "utf-8")
    out = tmp_path / "out"
    assert cli.main(["batch", str(sites), "--out", str(out), "--concurrency", "2"]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"sites": 3, "audited": 1, "failed": 2, "out": str(out)}
    rows = list(csv.DictReader((out / "summary.csv").open(encoding="utf-8")))
    assert [r["score"] for r in rows] == ["10", "", ""]  # input order kept
    assert "HTTP 410" in rows[1]["error"] and "blocked local host" in rows[2]["error"]
    assert (out / "shop.example.html").exists()


def test_slug_is_filesystem_safe() -> None:
    assert cli.slug("https://www.Shop.example:8443/a?b") == "shop.example"
    assert cli.slug("shop.example") == "shop.example"
