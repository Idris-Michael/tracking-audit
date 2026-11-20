# Handover

## What was delivered
- A Python service that audits a page's tracking set-up on one clean load before consent: GTM, GA4
  (including server-side endpoints), Consent Mode v2, Meta Pixel and Pixel/CAPI dedup.
- A CLI with batch mode, an API with idempotent requests and batches, Postgres-backed workers, and an
  operator console (Audit Desk) with prospect-ready HTML and PDF scorecards.
- An SSRF guard enforced by an egress proxy every browser connection goes through (ADR 3).
- Terraform for Azure Container Apps and AWS ECS Fargate, validated offline, not applied.
- CI: lint, types, 74 tests with an 80% coverage gate (about 94%), Terraform checks, gitleaks, Semgrep,
  SBOM, and a container smoke test.

## Measured
| | Result | Evidence |
|---|---|---|
| 20 UK retail homepages | 8 audited, 12 refused an automated browser | `bench/results.json` |
| Time | Median 4.45 s per audited site; the 20 ran in 18 s, four at a time | Measured locally |
| Accuracy | Run 1 wrong on 3 of 8 and misleading on a fourth; run 2 agrees with raw network captures on all 8 | `bench/hand_check.md` |

## What is not done
| Item | Why it matters | Suggested next step |
|---|---|---|
| Sites behind bot protection | 12 of 20 benchmark retailers refused the browser | Allow-listing for client work (ADR 6); measure the hit rate on smaller sites before cold outreach |
| Meta Pixel on real sites | No benchmark site fired a Pixel event before consent, so Pixel checks are proven on the fixture shop only | Audit sites known to fire the Pixel pre-consent, or with the client's consent state |
| Other tag managers | Tealium and Adobe Launch sites get "not checked" for GTM | Add checks for those if clients use them |
| Per-user access | One shared API token | SSO (Entra ID / Okta) at the ingress |
| Retention | Audits are kept indefinitely | Scheduled deletion (see the security questionnaire) |
| Deployment | Terraform validated, never applied | Apply to a sandbox subscription / account, then run the smoke test there |

## Where to start reading
`src/tracking_audit/analyse.py` (the rules), `guard.py` and `proxy.py` (what the browser may reach),
then `docs/adr/`.
