"""Egress proxy: the only way the audit browser reaches the network.

Playwright routing sees a request's first URL only; when a server answers with a redirect, the
browser follows it without asking the route again. So the guard is enforced here instead, on every
connection the browser opens (redirects, iframes, beacons, websockets alike). The proxy connects to
the address the guard checked, never re-resolving the name, which also defeats DNS rebinding.

Plain HTTP gets one request per connection (`Connection: close`), so a kept-alive proxy connection
can never carry a second request to a different host. HTTPS is tunnelled with CONNECT and stays
end-to-end encrypted: the proxy sees host and port, not content.
"""

from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

import structlog

from .guard import BlockedURL, Guard

log = structlog.get_logger()

ALLOWED_PORTS = frozenset({80, 443})
CONNECT_TIMEOUT_S = 15


class EgressProxy:
    def __init__(self, guard: Guard) -> None:
        self.guard = guard
        self.blocked: list[str] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:  # the reader caps headers at 64 KiB
            await _refuse(writer, 431, "request header too large")
            return
        except asyncio.IncompleteReadError:
            writer.close()
            return
        request_line, *header_lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, version = request_line.split(" ", 2)
        except ValueError:
            await _refuse(writer, 400, "bad request line")
            return
        if method == "CONNECT":
            host, _, port_s = target.rpartition(":")
            path_line = b""
        else:
            parts = urlsplit(target)
            host, port_s = parts.hostname or "", str(parts.port or 80)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            path_line = f"{method} {path} {version}\r\n".encode("latin-1")
        host = host.strip("[]")
        try:
            port = int(port_s)
            if port not in ALLOWED_PORTS:
                raise BlockedURL(f"blocked port {port}")
            address = await self.guard.public_address(host)
        except (ValueError, BlockedURL) as e:
            self.blocked.append(host)
            log.info("egress_blocked", host=host, port=port_s, reason=str(e))
            await _refuse(writer, 403, str(e))
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError) as e:
            await _refuse(writer, 502, f"cannot reach {host}: {e}")
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            kept = [h for h in header_lines if h and not h.lower().startswith(("proxy-", "connection:"))]
            up_writer.write(path_line + "\r\n".join([*kept, "Connection: close", "", ""]).encode("latin-1"))
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while chunk := await src.read(64 * 1024):
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, OSError):
        pass  # either side hanging up ends the tunnel; nothing to report
    finally:
        with contextlib.suppress(Exception):
            dst.close()


async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    body = reason.encode()
    writer.write(
        f"HTTP/1.1 {status} Blocked\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()
        writer.close()
