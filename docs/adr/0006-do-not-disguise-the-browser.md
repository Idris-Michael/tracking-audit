# 6. The auditor does not disguise itself to get past bot protection

Status: accepted

## Context
On the 20-site benchmark, 12 sites refused the headless browser: 11 with HTTP 403 and one by
dropping the HTTP/2 connection. For the two checked that way (John Lewis and Next), a plain
Playwright load without this tool's proxy was refused identically, so the refusals come from the
sites' bot protection, not from the tool.

Changing the user agent, patching browser fingerprints or routing through residential proxies would
probably get further. It would also mean deliberately defeating a control the site owner chose to run.

## Decision
- The browser presents as what it is. A refusal is a finished audit with a plain explanation: the site
  likely refuses automated browsers; audit it from a normal browser session or ask the owner to
  allow-list the auditor.
- The benchmark counts refusals in its headline rather than leaving those sites out.

## Consequences
- For cold outreach the tool worked on a minority of large UK retailers (8 of 20 here). Smaller sites
  without enterprise bot management are likely a better fit; that has not been measured.
- For client work the fix is an allow-list entry for the auditor's egress IP, agreed with the client,
  which is how an agency should get access anyway.
