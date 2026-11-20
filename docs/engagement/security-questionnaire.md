# Security questionnaire (answers for the client's review)

| Question | Answer |
|---|---|
| What data does the system collect? | During an audit, in memory only: the URLs the page requested on one load, its dataLayer (first 200 entries), Google's consent state and its HTML (capped at 2 MB). All of it is public, what any visitor's browser receives. It is analysed and discarded. |
| What is stored? | The target URL, the report (final URL, tag IDs, check statuses and findings), any blocked hosts, timings, and the scorecard HTML and PDF. Not the raw capture. |
| Personal data? | None is sought, and the raw page is not kept. A finding could only echo a tag ID or a URL the site itself published. |
| Where is it stored? | Postgres (Azure Flexible Server or RDS, encrypted at rest, private network only). Scorecards (HTML and PDF) sit on the audit row. |
| Retention | Not enforced in the pilot. Recommended: delete audits older than a year with a scheduled job, or on request for a named site. |
| Who can use it? | Holders of the API token (Key Vault / Secrets Manager). One shared token in the pilot; per-user SSO at the ingress is the production step. |
| Can it reach internal systems (SSRF)? | No, by design (ADR 3). Every browser connection goes through an egress proxy that allows only ports 80/443 to hosts resolving to public addresses, and connects to the address it checked. Redirects are covered. On Azure an NSG denies web ports to private ranges as a second line. Tests cover localhost, RFC 1918, link-local/metadata, IPv4-in-IPv6, names resolving to private addresses, and a redirect into localhost. |
| DNS rebinding? | The proxy connects to the address the guard approved instead of resolving again. |
| Can an audited page attack the operator? | Page-derived text is escaped in the scorecard (tested with a hostile URL). Scorecards are served with a CSP that allows no script and no loads. The console renders page text with `textContent` and shows scorecards in a sandboxed frame. PDFs render with JavaScript off and every request refused. |
| Can it be used to attack sites? | One page load per audit, no form submission, no clicks. Batches are capped (`MAX_BATCH`, default 50) and the API is rate-limited per client. The auditor does not disguise itself (ADR 6). |
| Browser isolation | A fresh browser context per audit: no profile, cookies, cache or service workers carried between sites. Chromium runs as a non-root user (uid 10001) in its own container. |
| Secrets | API token and database URL in Key Vault / Secrets Manager, read by a managed identity / task role. No secrets in the image or the repo. The API token's value never enters Terraform state (it is set out of band); the generated database password does, so state must live in an encrypted, access-controlled backend. |
| Logging | JSON logs with trace IDs: audit IDs, targets, scores, errors, blocked hosts. Page content is not logged. Traces via OpenTelemetry when an endpoint is set. |
| Dependencies and supply chain | `uv.lock` pinned; Dependabot weekly with a 7-day cooldown; CI runs gitleaks over full history, Semgrep, and produces a CycloneDX SBOM. Actions pinned to commit SHAs. |
| Third parties | None at runtime except the audited sites themselves. No model or analytics provider is called. |
