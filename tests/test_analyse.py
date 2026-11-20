"""Analysis tests. WHY:
- the scorecard goes to prospects, so every verdict must come from traffic the browser saw, never a guess
- duplicate tags and missing event_ids are the findings that cost money (inflated sessions, double counts)
- Consent Mode v2 is judged by its real signals: v2 keys in a default, Google's consent state, or gcd
- server-side CAPI cannot be seen from a browser, so it is always unknown and never scored
"""

from __future__ import annotations

from typing import Any

from tracking_audit.analyse import Capture, Check, Report, analyse, param, score

GTM = "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"
V2_DEFAULT = [
    "consent",
    "default",
    {"ad_storage": "denied", "ad_user_data": "denied", "ad_personalization": "denied"},
]


def ga4(en: str, extra: str = "") -> str:
    return f"https://region1.google-analytics.com/g/collect?v=2&tid=G-TEST1234&en={en}{extra}"


def px(ev: str, eid: str | None = None) -> str:
    return f"https://www.facebook.com/tr/?id=1234567890123&ev={ev}" + (f"&eid={eid}" if eid else "")


def cap(**over: Any) -> Capture:
    return Capture(final_url="https://shop.example/", **over)


def status(r: Report, name: str) -> str | None:
    return next((c.status for c in r.checks if c.name == name), None)


def detail(r: Report, name: str) -> str:
    return next(c.detail for c in r.checks if c.name == name)


def test_a_healthy_store_scores_10_and_capi_stays_unknown_not_scored() -> None:
    r = analyse(
        "shop.example",
        cap(
            requests=[
                GTM,
                ga4("page_view", "&gcd=13l3l3l3l1"),
                px("PageView", "e1"),
                px("ViewContent", "e2"),
            ],
            data_layer=[V2_DEFAULT],
        ),
    )
    assert r.score == 10
    assert r.decided == 5
    assert status(r, "Meta CAPI (server)") == "unknown"
    assert r.ids.model_dump() == {"gtm": ["GTM-ABC123"], "ga4": ["G-TEST1234"], "pixel": ["1234567890123"]}


def test_duplicate_page_view_is_flagged_because_it_inflates_sessions() -> None:
    r = analyse("x", cap(requests=[GTM, ga4("page_view"), ga4("page_view")]))
    assert status(r, "GA4") == "warn"
    assert "2x for G-TEST1234" in detail(r, "GA4")


def test_two_ga4_properties_receiving_hits_is_a_warning_to_confirm() -> None:
    other = ga4("page_view").replace("G-TEST1234", "G-OTHER999")
    r = analyse("x", cap(requests=[ga4("page_view"), other]))
    assert status(r, "GA4") == "warn"
    assert "2 GA4 properties" in detail(r, "GA4")


def test_ga4_configured_but_silent_before_consent_warns_and_names_basic_mode() -> None:
    r = analyse("x", cap(html="<script>gtag('config','G-SILENT12')</script>"))
    assert status(r, "GA4") == "warn"
    assert "basic mode" in detail(r, "GA4")
    assert status(analyse("x", cap()), "GA4") == "fail"


def test_pixel_events_without_event_id_fail_dedup_because_they_double_count() -> None:
    r = analyse("x", cap(requests=[px("PageView"), px("Purchase")]))
    assert status(r, "Pixel and CAPI dedup") == "fail"
    partial = analyse("x", cap(requests=[px("PageView", "e1"), px("Purchase")]))
    assert status(partial, "Pixel and CAPI dedup") == "warn"


def test_consent_v1_default_warns_none_fails_and_gcd_alone_passes() -> None:
    v1 = cap(data_layer=[["consent", "default", {"ad_storage": "denied"}]])
    assert status(analyse("x", v1), "Consent Mode v2") == "warn"
    assert status(analyse("x", cap()), "Consent Mode v2") == "fail"
    gcd = cap(requests=[ga4("page_view", "&gcd=13r3r3r3r5")])
    assert status(analyse("x", gcd), "Consent Mode v2") == "pass"


def test_consent_set_by_a_gtm_template_passes_without_any_datalayer_entry() -> None:
    # CMP templates call the tag's consent API directly, so the dataLayer never sees the default.
    state = {
        k: {"default": False}
        for k in ("ad_storage", "analytics_storage", "ad_user_data", "ad_personalization")
    }
    r = analyse("x", cap(consent_state=state))
    assert status(r, "Consent Mode v2") == "pass"
    assert "Google's tag holds" in detail(r, "Consent Mode v2")
    v1_state = analyse("x", cap(consent_state={"ad_storage": {"default": False}}))
    assert status(v1_state, "Consent Mode v2") == "warn"


def test_a_container_in_the_html_that_never_loads_is_a_warning_not_a_pass() -> None:
    r = analyse("x", cap(html="<script>/* GTM-ZZZ999 */</script>"))
    assert status(r, "Google Tag Manager") == "warn"


def test_two_gtm_containers_loading_can_double_fire_tags() -> None:
    r = analyse("x", cap(requests=[GTM, GTM.replace("ABC123", "DEF456")]))
    assert status(r, "Google Tag Manager") == "warn"


def test_no_meta_pixel_is_not_a_failure_because_not_every_business_runs_meta_ads() -> None:
    r = analyse("x", cap(requests=[GTM]))
    assert status(r, "Meta Pixel") == "unknown"
    assert status(r, "Pixel and CAPI dedup") is None


def test_a_pixel_that_holds_events_until_consent_is_unknown_not_failed() -> None:
    r = analyse(
        "x",
        cap(
            requests=["https://connect.facebook.net/en_GB/fbevents.js"],
            html="<script>fbq('init', '998877665544')</script>",
        ),
    )
    assert status(r, "Meta Pixel") == "unknown"
    assert "998877665544" in detail(r, "Meta Pixel")


def test_score_rounds_half_up_and_ignores_unknowns() -> None:
    def c(s: str) -> Check:
        return Check(name="x", status=s, detail="")  # type: ignore[arg-type]

    assert score([c("pass"), c("warn"), c("fail"), c("fail")]) == (4, 4)  # 3/8 = 3.75 -> 4
    assert score([c("pass"), c("warn"), c("warn"), c("fail"), c("unknown")]) == (5, 4)  # unknown not counted
    assert score([c("unknown")]) == (0, 0)
    # 13 of 20 points = 6.5: Python's round() gives 6 (half to even); a reader of the card expects 7.
    assert score([c("pass")] * 3 + [c("warn")] * 7) == (7, 10)


def test_requests_beyond_the_cap_are_ignored() -> None:
    noise = ["https://cdn.example/x.js"] * 2_000
    r = analyse("x", cap(requests=[*noise, GTM]))
    assert status(r, "Google Tag Manager") == "fail"


def test_param_survives_malformed_urls() -> None:
    assert param("https://a.example/?id=1", "id") == "1"
    assert param("not a url", "id") is None
