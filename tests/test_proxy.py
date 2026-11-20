"""Egress proxy tests over real sockets. WHY: the proxy is the control that stops the browser reaching
private hosts through redirects, so its refusals and its forwarding both need proving."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from tracking_audit.guard import Guard
from tracking_audit.proxy import EgressProxy


class LoopbackGuard(Guard):
    """Lets one named host through to a local test server; everything else follows the real rules."""

    def __init__(self, allowed: dict[str, str]) -> None:
        async def nothing(host: str) -> list[str]:
            raise OSError("NXDOMAIN")

        super().__init__(nothing)
        self.allowed = allowed

    async def public_address(self, host: str) -> str:
        if host in self.allowed:
            return self.allowed[host]
        return await super().public_address(host)


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[int, list[bytes]]]:
    """An HTTP server that records what it received and answers 200."""
    seen: list[bytes] = []

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        seen.append(await r.readuntil(b"\r\n\r\n"))
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await w.drain()
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1], seen
    server.close()


async def ask(proxy_url: str, raw: bytes) -> bytes:
    host, port = proxy_url.removeprefix("http://").split(":")
    r, w = await asyncio.open_connection(host, int(port))
    w.write(raw)
    await w.drain()
    data = await asyncio.wait_for(r.read(), 5)
    w.close()
    return data


async def started(guard: Guard) -> tuple[EgressProxy, str]:
    p = EgressProxy(guard)
    return p, await p.start()


async def test_plain_http_is_forwarded_with_one_request_per_connection(
    upstream: tuple[int, list[bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, seen = upstream
    monkeypatch.setattr("tracking_audit.proxy.ALLOWED_PORTS", frozenset({port}))
    proxy, url = await started(LoopbackGuard({"shop.example": "127.0.0.1"}))
    req = (
        f"GET http://shop.example:{port}/p?q=1 HTTP/1.1\r\nHost: shop.example\r\n"
        "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n"
    ).encode()
    reply = await ask(url, req)
    await proxy.close()
    assert reply.startswith(b"HTTP/1.1 200") and reply.endswith(b"ok")
    sent = seen[0].decode()
    assert sent.startswith("GET /p?q=1 HTTP/1.1\r\n")
    assert "Connection: close" in sent and "keep-alive" not in sent.lower()


async def test_connect_tunnels_to_the_checked_address(
    upstream: tuple[int, list[bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _ = upstream
    monkeypatch.setattr("tracking_audit.proxy.ALLOWED_PORTS", frozenset({port}))
    proxy, url = await started(LoopbackGuard({"shop.example": "127.0.0.1"}))
    reply = await ask(url, f"CONNECT shop.example:{port} HTTP/1.1\r\n\r\nGET / HTTP/1.1\r\n\r\n".encode())
    await proxy.close()
    assert reply.startswith(b"HTTP/1.1 200 Connection established")
    assert b"\r\n\r\nok" in reply


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n", b"403"),  # private address
        (b"CONNECT 169.254.169.254:80 HTTP/1.1\r\n\r\n", b"403"),  # cloud metadata
        (b"GET http://localhost/ HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT 93.184.216.34:22 HTTP/1.1\r\n\r\n", b"403"),  # not a web port
        (b"CONNECT shop.example:notaport HTTP/1.1\r\n\r\n", b"403"),
        (b"NONSENSE\r\n\r\n", b"400"),
    ],
)
async def test_refusals(raw: bytes, status: bytes) -> None:
    proxy, url = await started(Guard())
    reply = await ask(url, raw)
    await proxy.close()
    assert reply.split(b" ")[1] == status


async def test_an_unreachable_upstream_is_a_502_not_a_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    dead = sock.sockets[0].getsockname()[1]
    sock.close()
    await sock.wait_closed()
    monkeypatch.setattr("tracking_audit.proxy.ALLOWED_PORTS", frozenset({dead}))
    proxy, url = await started(LoopbackGuard({"down.example": "127.0.0.1"}))
    reply = await ask(url, f"CONNECT down.example:{dead} HTTP/1.1\r\n\r\n".encode())
    await proxy.close()
    assert reply.startswith(b"HTTP/1.1 502")


async def test_oversized_headers_are_refused() -> None:
    proxy, url = await started(Guard())
    reply = await ask(url, b"GET http://a.example/ HTTP/1.1\r\nX: " + b"a" * 70_000 + b"\r\n\r\n")
    await proxy.close()
    assert reply.split(b" ")[1] == b"431"
