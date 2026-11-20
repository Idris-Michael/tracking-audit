# 4. The API queues audits; workers claim them with a lease

Status: accepted

## Context
An audit takes seconds to a minute and runs a browser against a site the agency does not control. An
account manager may paste 30 prospects at once and close the tab.

## Decision
- `POST /v1/audits` and `POST /v1/batches` check the targets, store rows and answer `202`. An
  `Idempotency-Key` makes a retried request return the original audit. A unique constraint decides, so
  racing retries cannot create two.
- Workers claim the oldest queued row with `FOR UPDATE SKIP LOCKED` and a lease, run up to
  `WORKER_CONCURRENCY` audits in one long-lived browser, and relaunch the browser if it dies.
- A site that cannot be audited (refused, HTTP error, timeout, blocked) is a **finished** audit with
  its reason. Retrying would not help and would hit the site again.
- Only a worker that dies mid-audit leaves a running row. When its lease expires another worker takes
  it, at most `WORKER_MAX_ATTEMPTS` times (default 2), and then it is failed with that reason.
- The report, the HTML card and the PDF are stored on the row, so serving a scorecard needs no browser.

## Consequences
- On real Postgres, eight threads claiming from 40 queued audits claimed each exactly once, and eight
  racing retries with one Idempotency-Key created one audit (`tests/test_postgres.py`).
- The PDF is rendered once, by the worker, offline (scripts off, every request refused).
- Storing PDFs in Postgres is fine at pilot volume (about 100 KB each). At scale, move them to blob
  storage and keep the key on the row.
