# Runbook

## Run it locally
```bash
cp .env.example .env            # set API_TOKEN (24+ characters)
docker compose up -d --build    # Postgres, migrations, API, worker, Jaeger
open http://localhost:8080      # Audit Desk; traces at http://localhost:16686
```
Without Docker, one site or a list from the command line:
```bash
uv sync && uv run playwright install chromium
uv run tracking-audit shop.example --html card.html --pdf card.pdf
uv run tracking-audit batch sites.txt --out audits/ --concurrency 4
```

## Configuration
| Variable | Default | Meaning |
|---|---|---|
| `API_TOKEN` | none (required) | Bearer token for every `/v1` route and the console |
| `DATABASE_URL` | local Postgres | SQLAlchemy URL |
| `PREPARED_BY` | Fieldnote Digital (fictional agency) | Footer of every scorecard |
| `RATE_LIMIT_PER_MINUTE` | 30 | Per client, on the POST routes |
| `MAX_BATCH` | 50 | Most targets in one batch |
| `WORKER_CONCURRENCY` | 4 | Audits at once per worker (2 per vCPU is comfortable) |
| `WORKER_LEASE_S` | 300 | How long a worker owns a running audit |
| `WORKER_MAX_ATTEMPTS` | 2 | Times a crashed audit is retried before it is failed |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | Turns tracing on |

## Day to day
| Symptom | Check | Fix |
|---|---|---|
| Audit says the site "likely refuses automated browsers" | HTTP 403 or a dropped connection on the first load | Expected for sites with bot management (12 of 20 benchmark sites). Audit from a normal browser, or ask the owner to allow-list the auditor's egress IP (ADR 6). |
| Audit says "led to a blocked host" | `blocked` on the audit lists the host | The page redirected somewhere private. Working as intended; nothing to fix. |
| Audit says "gave up after 90 s" | Worker logs for `audit_finished` with that ID | Very slow site. Re-submit once; if it repeats, the site is the problem. |
| Audits stay `queued` | `docker compose logs worker`: is `worker_started` there? | Start or scale the worker. Queued rows are picked up as soon as one runs. |
| Audit `failed` with "worker stopped 2 times" | Worker logs: `audit_crashed` or `browser_relaunch` near that time | Chromium crashed on that page twice. Usually memory: give the worker 2 GiB per 2 concurrent audits. |
| Worker exits on start in a container | Logs mention shared memory or `/tmp` | Chromium needs a writable `/tmp`; on read-only roots mount an ephemeral volume there. |
| A finding looks wrong | Record a HAR of the same page and read its `/g/collect`, `gtm.js` and `/tr` requests (`bench/hand_check.md` shows how) | If the tool disagrees with the raw traffic, add a failing test in `tests/test_analyse.py` first, then fix. |

## Releases
Build one image; run `alembic upgrade head` once with it before rolling the API and the worker.
Re-run `uv run tracking-audit batch bench/sites.txt` after any change to a check, and compare with
`bench/results.json` and `bench/hand_check.md`.
