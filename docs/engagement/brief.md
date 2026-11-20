# Engagement brief

> **The customer is fictional.** "Fieldnote Digital" is an invented UK performance-marketing agency,
> used to frame the work the way a solutions engagement would be framed. The code, tests and
> benchmark results are real, measured against real public websites.

## The problem
Fieldnote's account managers open sales conversations with a free tracking check: is GTM there, is GA4
firing, is Consent Mode v2 set up for UK and EEA ads, is the Meta Pixel deduplicated against the
Conversions API. Today someone does it by hand in Chrome DevTools, it takes a while per site, results
vary by who did it, and the write-up is a screenshot pasted into an email.

They had an internal script that did one site at a time. It was not safe to run on a shared server
(it could be pointed at internal addresses), and nobody trusted its verdicts enough to send them.

## Stakeholders
| Role | Cares about |
|---|---|
| Head of growth (sponsor) | A credible, consistent opener for prospect conversations |
| Account managers (users) | Paste a list of prospects, get scorecards they can send without editing |
| Analytics lead | Verdicts that are right; nothing claimed that the browser did not see |
| Information security | The tool cannot be used to reach the agency's own network |
| Whoever maintains it | Something they can run, read and change |

## Success criteria (agreed up front)
1. Verdicts agree with a person reading the raw network traffic for the same page.
2. Nothing on the card is claimed beyond what one browser load can show; unknowns are not scored.
3. The auditor cannot be steered at private or local addresses, including through redirects.
4. A batch of prospects runs unattended and finishes even when some sites fail.
5. A prospect-ready scorecard (HTML and PDF) for every site that could be audited.

## Constraints
- Pre-consent only: the audit never clicks a cookie banner.
- No credentials, no logins, nothing submitted on the audited site.
- The agency does not want the tool to evade sites' bot protection (ADR 6).
- Pilot: runs locally or in the agency's cloud; Terraform for Azure and AWS, not applied yet.

## Approach
A Python port of the original diagnostic (ADR 1), with an SSRF guard enforced by an egress proxy
(ADR 3), a queue with leased workers (ADR 4), an API and a small console for account managers, and
HTML/PDF scorecards. Measured on 20 UK retail homepages, with every audited result checked against a
raw network capture (`bench/hand_check.md`).

## Out of scope
Post-consent behaviour, server-side tag configuration, data quality inside GA4 or Meta, and tag
managers other than GTM.
