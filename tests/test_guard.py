"""SSRF guard tests. WHY: the browser runs inside the auditor's network, so a target URL or a page
must never be able to steer it at localhost, the LAN or a cloud metadata endpoint."""

from __future__ import annotations

import pytest

from tracking_audit.guard import BlockedURL, Guard, is_public_ip, normalise, system_resolver

PUBLIC = "93.184.216.34"


def resolver(table: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def resolve(host: str) -> list[str]:
        calls.append(host)
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    resolve.calls = calls  # type: ignore[attr-defined]
    return resolve


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("shop.example", "https://shop.example/"),
        ("http://shop.example/a?b=1#frag", "http://shop.example/a?b=1"),
    ],
)
def test_normalise_adds_https_and_drops_fragments(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "file:///etc/passwd", "ftp://shop.example", "https://user:pw@shop.example/"]
)
def test_normalise_refuses_non_web_urls_and_embedded_credentials(raw: str) -> None:
    with pytest.raises(BlockedURL):
        normalise(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3000",
        "http://localhost/",
        "http://printer.local/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/",
        "http://192.168.1.1/admin",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4 hidden inside IPv6
        "http://0.0.0.0/",
    ],
)
async def test_private_and_local_targets_are_refused_without_a_dns_lookup(url: str) -> None:
    r = resolver({})
    with pytest.raises(BlockedURL):
        await Guard(r).check_url(url)
    assert r.calls == []  # type: ignore[attr-defined]


async def test_a_public_name_that_resolves_to_a_private_address_is_refused() -> None:
    g = Guard(resolver({"intranet.example": [PUBLIC, "10.1.2.3"]}))
    with pytest.raises(BlockedURL, match="private address"):
        await g.check_url("https://intranet.example/")
    assert g.blocked_hosts == ["intranet.example"]


async def test_unresolvable_hosts_are_refused_with_a_reason() -> None:
    with pytest.raises(BlockedURL, match="cannot resolve"):
        await Guard(resolver({})).check_url("https://nowhere.example/")
    with pytest.raises(BlockedURL, match="cannot resolve"):
        await Guard(resolver({"empty.example": []})).check_url("https://empty.example/")


async def test_each_host_is_resolved_once_per_audit() -> None:
    r = resolver({"shop.example": [PUBLIC]})
    g = Guard(r)
    for path in ("/", "/a.js", "/b.css"):
        assert await g.check_url(f"https://shop.example{path}") == f"https://shop.example{path}"
    assert r.calls == ["shop.example"]  # type: ignore[attr-defined]


def test_public_ip_rules() -> None:
    assert is_public_ip(PUBLIC)
    assert is_public_ip("2606:4700::1111")
    for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "100.64.0.1", "224.0.0.1", "fe80::1%eth0", "fd00::1"):
        assert not is_public_ip(ip), ip


async def test_the_system_resolver_resolves_localhost_to_loopback() -> None:
    addrs = await system_resolver("localhost")
    assert addrs and not any(is_public_ip(a) for a in addrs)
