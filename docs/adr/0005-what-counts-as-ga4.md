# 5. A GA4 hit is any /g/collect request with a G- measurement ID

Status: accepted (changed after the hand check)

## Context
The original rule counted GA4 hits only on Google's hostnames. The hand check against raw network
captures (`bench/hand_check.md`) found three of eight audited sites where that was wrong: they run
server-side GTM and send GA4 to their own subdomain (`sgtm.superdry.com`, `sstc.dunelm.com`,
`sgtm.hobbycraft.co.uk`). The tool failed GA4 on one, warned on another and missed a second property
on the third. A fourth site, on Tealium, was failed for having no GTM.

## Decision
- A GA4 hit is any HTTPS request whose path ends in `/g/collect` and whose `tid` starts with `G-`, on
  any host. That pair is specific enough that ordinary first-party traffic does not match.
- When hits go to a non-Google host, the card says so: "via first-party endpoint sgtm.shop.example
  (server-side tagging)". That is useful to the prospect and to the person pitching.
- Sites on Tealium iQ or Adobe Launch are not failed for having no GTM. The GTM line names their tag
  manager and says the audit does not check it.

## Consequences
- Run 2 agrees with the raw captures on all 8 audited sites. Each fix has a regression test.
- A site that sends GA4 to a path not ending in `/g/collect` would still be missed. None of the 20
  benchmark sites did; a hand check is how the next one will be found.
