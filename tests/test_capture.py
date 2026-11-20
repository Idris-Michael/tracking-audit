"""Browser capture tests (headless Chromium, no network). WHY:
- the audit must record what the page really requested, including beacons fired by inline scripts
- a page must not be able to make the auditor fetch from its LAN, even through an <img> or a redirect
- one odd dataLayer entry (circular, a DOM node) must not lose the rest of the capture
"""

from __future__ import annotations

import pytest

from tracking_audit.analyse import analyse
from tracking_audit.capture import CaptureError, capture, refusal_hint
from tracking_audit.guard import Guard

from .fixture_site import resolve, serve

pytestmark = pytest.mark.browser


async def test_capture_records_tags_and_the_consent_default_before_any_banner() -> None:
    cap = await capture("https://shop.example/", guard=Guard(resolve), fixture=serve)
    assert cap.final_url == "https://shop.example/"
    assert any("gtm.js?id=GTM-FIX123" in u for u in cap.requests)
    assert cap.data_layer[0][:2] == ["consent", "default"]
    assert cap.data_layer[0][2]["ad_user_data"] == "denied"
    assert None in cap.data_layer  # the circular entry kept its slot, the rest survived

    r = analyse("shop.example", cap)
    statuses = {c.name: c.status for c in r.checks}
    assert statuses["Google Tag Manager"] == "pass"
    assert statuses["GA4"] == "pass"
    assert statuses["Consent Mode v2"] == "pass"
    assert statuses["Pixel and CAPI dedup"] == "pass"
    assert r.score == 10


async def test_a_lan_image_on_the_page_is_blocked_and_reported() -> None:
    cap = await capture("https://shop.example/", guard=Guard(resolve), fixture=serve)
    # The request is still listed (the browser asked), but the guard aborted it before it left.
    assert cap.blocked == ["192.168.1.1"]


async def test_a_redirect_into_localhost_fails_the_capture() -> None:
    # Playwright routing never sees a redirect's target; the egress proxy does.
    with pytest.raises(CaptureError, match=r"led to a blocked host \(127\.0\.0\.1\)"):
        await capture("https://shop.example/to-lan", guard=Guard(resolve), fixture=serve)


async def test_an_http_error_page_is_not_audited() -> None:
    with pytest.raises(CaptureError, match="HTTP 410"):
        await capture("https://shop.example/gone", guard=Guard(resolve), fixture=serve)


async def test_a_private_target_is_refused_before_a_page_opens() -> None:
    with pytest.raises(CaptureError, match="blocked"):
        await capture("http://127.0.0.1:3000", guard=Guard(resolve), fixture=serve)


def test_refusals_get_a_plain_hint_and_other_errors_do_not() -> None:
    assert "refuses automated browsers" in refusal_hint("403")
    assert "refuses automated browsers" in refusal_hint("net::ERR_HTTP2_PROTOCOL_ERROR at https://x/")
    assert refusal_hint("410") == ""
