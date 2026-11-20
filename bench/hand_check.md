# Hand check: the tool's verdicts against raw network captures

**What was checked.** The 8 benchmark sites the tool could audit (the other 12 refused an automated
browser). For each one, a separate capture was recorded as a HAR file with plain Playwright, using none
of this repository's code. I then read the raw request list myself: `gtm.js` loads, every request to a
`/g/collect` endpoint with its `tid`, `en` and `gcd`, Meta's `fbevents.js` and `/tr` hits, and any
consent defaults in the HTML or JavaScript. The plan asked for 5 sites; after the first fix changed a
sixth site's score, I checked all 8.

**Limit.** Both captures use the same browser engine, so this checks the analysis against what was
requested, not the browser itself. Sites change between loads; each comparison used loads taken minutes
apart on 20 November 2026.

## Result

Run 1 disagreed with the raw capture on 3 of 8 sites and misled on a fourth. All four came from two
gaps, both fixed, with a regression test each in `tests/test_analyse.py`. Run 2 agrees on all 8.

| Site | Raw capture shows | Run 1 said | Run 2 says |
|---|---|---|---|
| Superdry | GA4 `G-W525WY38RK` `page_view` sent to **`sgtm.superdry.com/g/collect`** | **GA4 fail: no tag or hit** | GA4 pass, via first-party endpoint (server-side tagging) |
| Dunelm | GA4 `G-PJ5SMW7K4T` `page_view` sent to **`sstc.dunelm.com/g/collect`** | **GA4 warn: configured, no hit** | GA4 pass, via first-party endpoint |
| Hobbycraft | Two GA4 properties send hits: one to Google, one to **`sgtm.hobbycraft.co.uk`** | **GA4 pass, one property** | GA4 warn: 2 properties receive hits, confirm each |
| Marks & Spencer | No Google request before consent; **Tealium** (`tags.tiqcdn.com/utag`) sets Google consent defaults incl. `ad_user_data` | **GTM fail** | GTM not checked: site uses Tealium iQ. Consent pass, GA4 fail (nothing before consent) |
| Gymshark | `gtm.js`, GA4 `page_view` with `gcd`, `fbevents.js` but no `/tr` hits | Agree | Agree |
| Halfords | `gtag/js?id=G-VK44BRER97` loads; no GA4 collect hits, only Ads `ccm` pings with `gcd` | Agree | Agree |
| Wickes | 5 GTM containers; GA4 hits for 3 properties, one of them `G-123456` (looks like a placeholder) | Agree | Agree |
| Cotswold Outdoor | `gtm.js` (`GTM-XCPB`); no GA4 hits; also loads Adobe Launch and Tealium | Agree | Agree (the extra tag managers are not mentioned: a known gap) |

## What the two gaps were

1. **Server-side tagging.** The tool recognised GA4 hits only on Google's hostnames. Sites that run
   server-side GTM send GA4 to their own subdomain, so the tool missed real GA4 traffic. A hit is now
   any `/g/collect` request carrying a `tid=G-…`, on any host, and the card names the first-party
   endpoint.
2. **Other tag managers.** A site on Tealium or Adobe Launch was failed for "no GTM". It is now marked
   not checked, with the tag manager named.

## Not covered by the real-site run

No audited site fired a Meta Pixel event before consent, so the Pixel and Pixel/CAPI dedup checks were
"not visible" on all 8. Those checks are covered by tests against the fixture shop only.
