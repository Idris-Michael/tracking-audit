# 3. Enforce the SSRF rule in an egress proxy, not only in browser routing

Status: accepted

## Context
The browser runs inside the agency's network and loads pages written by strangers. A page, or a
target URL typed in by mistake, can point it at `localhost`, a router, another service on the VNet or
the cloud metadata endpoint (`169.254.169.254`).

The original code checked the target URL and then every navigation through Playwright routing. The
first test of the port showed that was not enough: a page answering with a redirect to
`http://127.0.0.1:8080/admin` reached a real service on the test machine. Playwright calls a route
handler for a request's first URL only, so the browser followed the redirect unchecked.

## Decision
- `guard.py` decides: only `http` and `https`, ports 80 and 443, no credentials in the URL, and the
  host must resolve only to public addresses. A name resolving to `10.x` is refused, and so is
  `http://2130706433/` (127.0.0.1 written as one number).
- `proxy.py` enforces: every connection the browser opens goes through a local proxy that applies the
  guard. Redirects, iframes, beacons and websockets are all connections, so all are checked. The proxy
  connects to the exact address the guard approved, so a DNS answer that changes between check and use
  (rebinding) cannot slip through.
- Chromium skips proxies for loopback by default; it is launched with `--proxy-bypass-list=<-loopback>`
  so the proxy sees those requests too.
- The API refuses obviously private targets before queueing them (no DNS at that point); the worker
  applies the full rule.
- On Azure an NSG also denies web ports to private ranges, as a second line.

## Consequences
- Blocked requests are recorded on the audit (`blocked`), so a page that probes the LAN shows up.
- A proxy per audit costs nothing noticeable: the median audit on the benchmark took 4.45 s.
- AWS security groups cannot deny ranges, so on AWS the proxy is the only control for private ranges.
- The first 20-site batch hung: since Python 3.12, a server's `wait_closed` waits for open
  connections, and browsers keep tunnels open. The proxy now closes its tunnels itself, and each audit
  has a 90-second cap.
