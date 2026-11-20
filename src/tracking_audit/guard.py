"""SSRF guard: the auditor only ever talks to public http(s) hosts.

The browser runs on the auditor's own network, so a hostile page (or a hostile target URL) could
point it at a router admin page, a cloud metadata endpoint or a service on localhost. Every request
the page makes goes through `check_url`, not just the first navigation (ADR 3).

A hostname is judged by what it resolves to, so `internal.example.com -> 10.0.0.5` and the
decimal form `http://2130706433/` (127.0.0.1) are both refused. The browser resolves the name
again on its own, so the browser's traffic goes through `proxy.EgressProxy`, which connects to the
address the guard checked. That also covers redirects, which Playwright routing never sees (ADR 3).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

Resolver = Callable[[str], Awaitable[list[str]]]

BLOCKED = "blocked:"
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


class BlockedURL(ValueError):
    """The URL is not a public http(s) address."""


def normalise(raw: str) -> str:
    """Accepts `example.com` or a full URL; returns an absolute http(s) URL or raises."""
    text = raw.strip()
    if not text:
        raise BlockedURL("empty URL")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"blocked scheme {parts.scheme!r}: only http and https")
    if not parts.hostname:
        raise BlockedURL(f"no host in {raw!r}")
    if parts.username or parts.password:
        raise BlockedURL("credentials in the URL are not allowed")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def blocked_by_name(host: str) -> bool:
    h = host.lower().rstrip(".")
    return h == "localhost" or h.endswith(BLOCKED_SUFFIXES)


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class Guard:
    """Checks URLs against the rules above, caching each host's verdict for one audit."""

    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self._resolve = resolver
        self._verdicts: dict[str, str] = {}  # host -> address to use, or BLOCKED + reason

    async def check_url(self, raw: str) -> str:
        url = normalise(raw)
        await self.public_address(urlsplit(url).hostname or "")
        return url

    async def public_address(self, host: str) -> str:
        """The address to connect to for `host`, or BlockedURL. Connecting to this exact address
        (rather than letting something resolve the name again) is what defeats DNS rebinding."""
        if host not in self._verdicts:
            self._verdicts[host] = await self._judge(host)
        verdict = self._verdicts[host]
        if verdict.startswith(BLOCKED):
            raise BlockedURL(verdict.removeprefix(BLOCKED))
        return verdict

    async def _judge(self, host: str) -> str:
        if blocked_by_name(host):
            return f"{BLOCKED}blocked local host {host}"
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            return str(literal) if is_public_ip(str(literal)) else f"{BLOCKED}blocked private address {host}"
        try:
            addrs = await self._resolve(host)
        except OSError as e:
            return f"{BLOCKED}cannot resolve {host}: {e}"
        if not addrs:
            return f"{BLOCKED}cannot resolve {host}"
        private = [a for a in addrs if not is_public_ip(a)]
        if private:
            return f"{BLOCKED}{host} resolves to a private address ({', '.join(private)})"
        return addrs[0]

    @property
    def blocked_hosts(self) -> list[str]:
        return sorted(h for h, v in self._verdicts.items() if v.startswith(BLOCKED))


def precheck(raw: str) -> str:
    """The checks that need no DNS, for refusing a target at the API before it is queued. The worker
    still runs the full guard (with resolution) on every connection."""
    url = normalise(raw)
    host = urlsplit(url).hostname or ""
    if blocked_by_name(host):
        raise BlockedURL(f"blocked local host {host}")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return url
    if not is_public_ip(str(literal)):
        raise BlockedURL(f"blocked private address {host}")
    return url
