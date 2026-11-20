"""Turns one page capture into checks and a score. Pure functions: no browser, no network.

Facts only: what the browser saw on one load, before any cookie banner was touched. Server-side
tagging and the Meta Conversions API run on servers, so they are reported as unknown, never scored.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field

Status = Literal["pass", "warn", "fail", "unknown"]
MAX_REQUESTS = 2_000

GA4_HIT = re.compile(
    r"^https://([a-z0-9-]+\.)?(google-analytics\.com|analytics\.google\.com)/(g/collect|[a-z]/collect)", re.I
)
GA4_TID = re.compile(r"[?&]tid=G-", re.I)
PIXEL_HIT = re.compile(r"^https://(www\.)?facebook\.com/tr/?\?", re.I)
GTM_JS = re.compile(r"googletagmanager\.com/gtm\.js", re.I)
GTAG_JS = re.compile(r"googletagmanager\.com/gtag/js", re.I)
FBEVENTS = re.compile(r"connect\.facebook\.net/.+/fbevents\.js", re.I)
GTM_ID = re.compile(r"\bGTM-[A-Z0-9]{4,10}\b")
GA4_ID = re.compile(r"\bG-[A-Z0-9]{6,12}\b")
FBQ_INIT = re.compile(r"""fbq\(\s*['"]init['"]\s*,\s*['"](\d{8,20})['"]""")
V2_KEYS = ("ad_user_data", "ad_personalization")


class Capture(BaseModel):
    """What one page load produced. `consent_state` is Google's own record of consent defaults."""

    final_url: str
    html: str = ""
    requests: list[str] = Field(default_factory=list)
    data_layer: list[Any] = Field(default_factory=list)
    consent_state: dict[str, Any] = Field(default_factory=dict)
    blocked: list[str] = Field(default_factory=list)


class Check(BaseModel):
    name: str
    status: Status
    detail: str


class Ids(BaseModel):
    gtm: list[str] = Field(default_factory=list)
    ga4: list[str] = Field(default_factory=list)
    pixel: list[str] = Field(default_factory=list)


class Report(BaseModel):
    target: str
    final_url: str
    checked_at: str
    ids: Ids
    checks: list[Check]
    score: int = Field(ge=0, le=10, description="0-10 over checks that could be decided")
    decided: int = Field(description="how many checks the score is over")


def uniq(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


def param(url: str, key: str) -> str | None:
    try:
        values = parse_qs(urlsplit(url).query).get(key)
    except ValueError:
        return None
    return values[0] if values else None


def gtm_check(html: str, requests: list[str]) -> tuple[list[str], Check]:
    loaded = uniq([param(u, "id") or "" for u in requests if GTM_JS.search(u)])
    in_html = uniq(GTM_ID.findall(html))
    ids = uniq(loaded + in_html)
    name = "Google Tag Manager"
    if not loaded:
        if in_html:
            return ids, Check(
                name=name,
                status="warn",
                detail=f"Container {', '.join(in_html)} is in the HTML but gtm.js never loaded.",
            )
        return ids, Check(name=name, status="fail", detail="No GTM container loaded.")
    if len(loaded) > 1:
        return ids, Check(
            name=name,
            status="warn",
            detail=f"{len(loaded)} containers load ({', '.join(loaded)}); tags can double-fire.",
        )
    return ids, Check(name=name, status="pass", detail=f"Container {loaded[0]} loads.")


def ga4_check(html: str, requests: list[str]) -> tuple[list[str], Check, list[str]]:
    hits = [u for u in requests if GA4_HIT.search(u) and GA4_TID.search(u)]
    fired = uniq([param(u, "tid") or "" for u in hits])
    tagged = [param(u, "id") or "" for u in requests if GTAG_JS.search(u)] + GA4_ID.findall(html)
    ids = uniq(fired + [t for t in tagged if t.startswith("G-")])
    name = "GA4"
    if not hits:
        if ids:
            return (
                ids,
                Check(
                    name=name,
                    status="warn",
                    detail=(
                        f"GA4 {', '.join(ids)} is configured but sent no hit before consent. "
                        "That is expected only "
                        "with Consent Mode in basic mode; in advanced mode cookieless pings should appear."
                    ),
                ),
                hits,
            )
        return ids, Check(name=name, status="fail", detail="No GA4 tag or hit found."), hits
    page_views = Counter(param(u, "tid") for u in hits if param(u, "en") == "page_view")
    doubled = [(tid, n) for tid, n in page_views.items() if n > 1]
    if doubled:
        what = ", ".join(f"{n}x for {tid}" for tid, n in doubled)
        return (
            ids,
            Check(
                name=name,
                status="warn",
                detail=f"page_view fired {what}: likely duplicate tags, inflating sessions.",
            ),
            hits,
        )
    if len(fired) > 1:
        return (
            ids,
            Check(
                name=name,
                status="warn",
                detail=f"{len(fired)} GA4 properties receive hits ({', '.join(fired)}). "
                "Confirm each is intended.",
            ),
            hits,
        )
    extra = " including page_view" if page_views else ""
    return ids, Check(name=name, status="pass", detail=f"{fired[0]} sends hits{extra}."), hits


def consent_defaults(data_layer: list[Any]) -> list[dict[str, Any]]:
    """gtag('consent','default',{...}) lands in dataLayer as an array-like entry."""
    return [
        e[2]
        for e in data_layer
        if isinstance(e, list)
        and len(e) > 2
        and e[0] == "consent"
        and e[1] == "default"
        and isinstance(e[2], dict)
    ]


def consent_check(data_layer: list[Any], consent_state: dict[str, Any], ga4_hits: list[str]) -> Check:
    defaults = consent_defaults(data_layer)
    found = []
    if any(all(k in d for k in V2_KEYS) for d in defaults):
        found.append("a consent default sets ad_user_data and ad_personalization")
    if all(k in consent_state for k in V2_KEYS):
        # CMP templates in GTM set defaults through the tag's own API, not the dataLayer.
        found.append("Google's tag holds ad_user_data and ad_personalization consent states")
    if any(param(u, "gcd") for u in ga4_hits):
        found.append("GA4 hits carry the gcd consent signal")
    if found:
        return Check(name="Consent Mode v2", status="pass", detail="; ".join(found).capitalize() + ".")
    if defaults or consent_state:
        return Check(
            name="Consent Mode v2",
            status="warn",
            detail=(
                "Consent defaults exist but without ad_user_data / ad_personalization (v1 only). Google Ads "
                "audiences and conversions in the UK and EEA are limited."
            ),
        )
    return Check(
        name="Consent Mode v2",
        status="fail",
        detail=(
            "No consent default found before the banner. "
            "UK and EEA ad measurement and remarketing are at risk."
        ),
    )


def pixel_checks(html: str, requests: list[str]) -> tuple[list[str], list[Check]]:
    hits = [u for u in requests if PIXEL_HIT.search(u)]
    ids = uniq([param(u, "id") or "" for u in hits] + FBQ_INIT.findall(html))
    capi = Check(
        name="Meta CAPI (server)",
        status="unknown",
        detail="Server-side, so not visible from a browser. Confirm in Events Manager.",
    )
    if not hits:
        loaded = any(FBEVENTS.search(u) for u in requests)
        if loaded or ids:
            # Holding events until consent is correct behaviour; firing after consent is not seen here.
            what = ", ".join(ids) or "script"
            detail = f"Pixel {what} loads but holds events until consent; check firing after acceptance."
        else:
            detail = "No Meta Pixel. Skip if the business doesn't run Meta ads."
        return ids, [Check(name="Meta Pixel", status="unknown", detail=detail), capi]
    with_eid = sum(1 for u in hits if param(u, "eid"))
    n = len(hits)
    if with_eid == n:
        dedup = Check(
            name="Pixel and CAPI dedup",
            status="pass",
            detail=f"All {n} pixel events carry an event_id, so CAPI events can be deduplicated.",
        )
    else:
        dedup = Check(
            name="Pixel and CAPI dedup",
            status="warn" if with_eid else "fail",
            detail=f"{with_eid}/{n} pixel events carry an event_id. "
            "Without it, Pixel + CAPI double-count conversions.",
        )
    plural = "s" if n > 1 else ""
    fires = Check(
        name="Meta Pixel", status="pass", detail=f"Pixel {', '.join(ids)} fires ({n} event{plural})."
    )
    return ids, [fires, dedup, capi]


POINTS: dict[Status, int | None] = {"pass": 2, "warn": 1, "fail": 0, "unknown": None}


def score(checks: list[Check]) -> tuple[int, int]:
    pts = [p for p in (POINTS[c.status] for c in checks) if p is not None]
    if not pts:
        return 0, 0
    # Half rounds up (7.5 -> 8). Python's round() would give 8 here but 6 for 6.5.
    return int(10 * sum(pts) / (2 * len(pts)) + 0.5), len(pts)


def analyse(target: str, cap: Capture, checked_at: str | None = None) -> Report:
    requests = cap.requests[:MAX_REQUESTS]
    gtm_ids, gtm = gtm_check(cap.html, requests)
    ga4_ids, ga4, ga4_hits = ga4_check(cap.html, requests)
    pixel_ids, meta = pixel_checks(cap.html, requests)
    checks = [gtm, ga4, consent_check(cap.data_layer, cap.consent_state, ga4_hits), *meta]
    total, decided = score(checks)
    return Report(
        target=target,
        final_url=cap.final_url,
        checked_at=checked_at or datetime.now(UTC).isoformat(timespec="seconds"),
        ids=Ids(gtm=gtm_ids, ga4=ga4_ids, pixel=pixel_ids),
        checks=checks,
        score=total,
        decided=decided,
    )
