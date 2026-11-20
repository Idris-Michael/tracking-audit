# 1. Port the diagnostic to Python

Status: accepted

## Context
The audit started life as about 190 lines of TypeScript inside a larger Node service: a Playwright
capture, an analysis function and a Markdown scorecard, with 8 tests. The agency wants it as a
service its account managers can use (an API, a queue, scorecards a prospect can receive), and the
team that will maintain it works in Python.

## Decision
Rewrite in Python 3.12 with Playwright for Python, FastAPI, SQLAlchemy and Postgres. Port every
original test case first, so the rewrite starts from the same verdicts.

## Consequences
- The 8 original cases pass with the same meaning (`tests/test_analyse.py`); new behaviour comes with
  new tests on top.
- The port surfaced two problems the original had: redirects bypassed the SSRF check (ADR 3) and
  server-side GA4 was invisible (ADR 5). Rewriting against a test suite doubled as an audit of the
  old code.
- Python's `round()` rounds half to even, unlike `Math.round`. The score rounds half up explicitly,
  so 6.5 shows as 7, as a reader of the card expects.
