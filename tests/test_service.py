"""API and worker tests (SQLite, fixture shop). WHY:
- a retried request must not queue a second audit (Idempotency-Key), and racing retries cannot either
- private targets are refused at the door, before they reach a browser
- the scorecard is served so that nothing in it can run, even if escaping ever failed
- a site that cannot be audited is a finished result with a reason, not an endless retry
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tracking_audit.api import RateLimiter, create_app
from tracking_audit.config import Settings
from tracking_audit.db import DONE, FAILED, QUEUED, RUNNING, Audit, Base, claim_next, make_engine, now
from tracking_audit.worker import Worker

from .fixture_site import resolve, serve

TOKEN = "t" * 32
AUTH = {"authorization": f"Bearer {TOKEN}"}


def settings(**over: object) -> Settings:
    base: dict[str, object] = {"api_token": TOKEN, "database_url": "sqlite://", "worker_poll_s": 0.05}
    return Settings(**{**base, **over})  # type: ignore[arg-type]


@pytest.fixture
def sessions(tmp_path: Path) -> Callable[[], Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'audits.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(sessions: Callable[[], Session]) -> Iterator[TestClient]:
    with TestClient(create_app(settings(), sessions)) as c:
        yield c


def test_every_api_route_needs_the_token(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    for method, path in [
        ("get", "/v1/audits"),
        ("post", "/v1/audits"),
        ("get", "/v1/stats"),
        ("get", "/v1/audits/x"),
    ]:
        assert getattr(client, method)(path).status_code == 401
    assert client.get("/v1/stats", headers={"authorization": "Bearer wrong"}).status_code == 401


def test_a_retried_request_returns_the_first_audit(client: TestClient) -> None:
    h = {**AUTH, "Idempotency-Key": "form-123"}
    first = client.post("/v1/audits", json={"target": "shop.example"}, headers=h)
    again = client.post("/v1/audits", json={"target": "shop.example"}, headers=h)
    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert first.json()["target"] == "https://shop.example/"
    assert len(client.get("/v1/audits", headers=AUTH).json()) == 1


def test_private_targets_are_refused_before_they_are_queued(client: TestClient) -> None:
    r = client.post(
        "/v1/batches",
        json={"name": "b", "targets": ["shop.example", "http://10.0.0.1/", "localhost"]},
        headers=AUTH,
    )
    assert r.status_code == 422
    refused = r.json()["detail"]["refused"]
    assert [x["target"] for x in refused] == ["http://10.0.0.1/", "localhost"]
    assert client.get("/v1/audits", headers=AUTH).json() == []


def test_batches_dedupe_targets_and_cap_their_size(sessions: Callable[[], Session]) -> None:
    with TestClient(create_app(settings(max_batch=3), sessions)) as c:
        r = c.post(
            "/v1/batches",
            json={"name": "Q4", "targets": ["a.example", "https://a.example/", "b.example"]},
            headers={**AUTH, "Idempotency-Key": "b1"},
        )
        assert r.status_code == 202 and len(r.json()["audits"]) == 2
        again = c.post(
            "/v1/batches",
            json={"name": "Q4", "targets": ["a.example"]},
            headers={**AUTH, "Idempotency-Key": "b1"},
        )
        assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
        too_many = c.post(
            "/v1/batches", json={"name": "x", "targets": ["a.e", "b.e", "c.e", "d.e"]}, headers=AUTH
        )
        assert too_many.status_code == 422
        batch = c.get(f"/v1/batches/{r.json()['id']}", headers=AUTH).json()
        assert batch["counts"] == {"queued": 2}
        assert c.get("/v1/batches/nope", headers=AUTH).status_code == 404


def test_rate_limit_answers_429_with_retry_after(sessions: Callable[[], Session]) -> None:
    with TestClient(create_app(settings(rate_limit_per_minute=2), sessions)) as c:
        codes = [
            c.post("/v1/audits", json={"target": f"s{i}.example"}, headers=AUTH).status_code for i in range(3)
        ]
    assert codes == [202, 202, 429]


def test_rate_limiter_window_slides() -> None:
    t = [0.0]
    rl = RateLimiter(1, clock=lambda: t[0])
    assert rl.allow("a") and not rl.allow("a") and rl.allow("b")
    t[0] = 61
    assert rl.allow("a")


@pytest.mark.browser
def test_worker_audits_the_queue_and_the_scorecard_cannot_run_scripts(
    client: TestClient, sessions: Callable[[], Session]
) -> None:
    ok = client.post("/v1/audits", json={"target": "https://shop.example/"}, headers=AUTH).json()
    gone = client.post("/v1/audits", json={"target": "https://shop.example/gone"}, headers=AUTH).json()
    done = asyncio.run(Worker(settings(), sessions, resolver=resolve, fixture=serve).run(until_empty=True))
    assert done == 2

    a = client.get(f"/v1/audits/{ok['id']}", headers=AUTH).json()
    assert a["status"] == DONE and a["score"] == 10 and a["report"]["ids"]["gtm"] == ["GTM-FIX123"]
    assert a["blocked"] == ["192.168.1.1"]
    card = client.get(f"/v1/audits/{ok['id']}/scorecard.html", headers=AUTH)
    assert card.status_code == 200 and "shop.example" in card.text
    assert card.headers["content-security-policy"].startswith("default-src 'none'")
    pdf = client.get(f"/v1/audits/{ok['id']}/scorecard.pdf", headers=AUTH)
    assert pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]

    g = client.get(f"/v1/audits/{gone['id']}", headers=AUTH).json()
    assert g["status"] == FAILED and "HTTP 410" in g["error"]
    assert client.get(f"/v1/audits/{gone['id']}/scorecard.html", headers=AUTH).status_code == 409
    assert client.get(f"/v1/audits/{gone['id']}/scorecard.pdf", headers=AUTH).status_code == 409
    stats = client.get("/v1/stats", headers=AUTH).json()
    assert stats == {"by_status": {"done": 1, "failed": 1}, "median_score": 10, "audited": 1}
    assert len(client.get("/v1/audits?status=done", headers=AUTH).json()) == 1


def test_a_crashed_workers_audit_is_reclaimed_then_given_up(sessions: Callable[[], Session]) -> None:
    with sessions() as s:
        s.add(
            Audit(
                id="a1",
                target="https://shop.example/",
                status=RUNNING,
                attempts=1,
                locked_until=now() - timedelta(seconds=1),
            )
        )
        s.commit()
        row = claim_next(s, lease_s=60, max_attempts=2)
        assert row is not None and row.id == "a1" and row.attempts == 2
        row.locked_until = now() - timedelta(seconds=1)  # the second worker dies too
        s.commit()
        assert claim_next(s, lease_s=60, max_attempts=2) is None
        gave_up = s.get(Audit, "a1")
        assert gave_up is not None and gave_up.status == FAILED and "stopped 2 times" in (gave_up.error or "")


def test_queued_rows_are_claimed_oldest_first(sessions: Callable[[], Session]) -> None:
    with sessions() as s:
        s.add_all(
            [
                Audit(id="new", target="n", status=QUEUED, created_at=now()),
                Audit(id="old", target="o", status=QUEUED, created_at=now() - timedelta(minutes=5)),
            ]
        )
        s.commit()
        first = claim_next(s, 60, 2)
        assert first is not None and first.id == "old"


def test_console_is_served_with_a_restrictive_policy(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "Audit Desk" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_settings_refuse_a_short_token() -> None:
    with pytest.raises(ValueError, match="at least 24"):
        settings(api_token="short")
