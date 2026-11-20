# 2. One clean page load before consent; facts only, unknowns not scored

Status: accepted

## Context
The scorecard goes to a prospect who did not ask for it. Every line has to be something the auditor
can show, and the score must not punish what a browser cannot see.

## Decision
- One page load in a throwaway browser: no profile, no cookies, no clicks. The cookie banner is never
  touched, because the state before consent is what Consent Mode has to handle.
- Checks: GTM, GA4, Consent Mode v2, Meta Pixel, Pixel and CAPI dedup, Meta CAPI. Each is pass,
  warning, fail or "not visible".
- The score is 0 to 10 over the checks that could be decided. "Not visible" results (server-side
  CAPI, a Pixel that rightly waits for consent, a site on a tag manager this audit does not read) are
  left out rather than counted as failures. The card says how many checks the score is over.
- The card states its limits: one load, before consent, nothing about the quality of the data.

## Consequences
- A site that loads nothing before consent fails GA4. The finding says why that matters (GA4 loses
  the cookieless pings it models from) and that GA4 may still be installed behind the banner.
- No benchmark site fired a Pixel event before consent, so the Pixel checks are proven by the fixture
  tests only. The README says so.
- What happens after consent is out of scope. Clicking "accept" would mean scripting every consent
  manager, and would audit a state many first-time visitors never reach.
