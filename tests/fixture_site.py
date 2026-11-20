"""A fictional shop served from memory, so browser tests need no network and no real site."""

from __future__ import annotations

from tracking_audit.capture import Fixture

PUBLIC = "93.184.216.34"

SHOP = """<!doctype html><html><head><title>Fixture shop</title>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){ dataLayer.push(arguments); }
  gtag('consent', 'default', {ad_storage: 'denied', analytics_storage: 'denied',
                              ad_user_data: 'denied', ad_personalization: 'denied'});
  dataLayer.push({event: 'circular', self: null});
  dataLayer[dataLayer.length - 1].self = dataLayer[dataLayer.length - 1];
</script>
<script async src="https://www.googletagmanager.com/gtm.js?id=GTM-FIX123"></script>
</head><body>
<h1>Fixture shop</h1>
<img src="http://192.168.1.1/router.png" alt="">
<script>
  new Image().src = 'https://region1.google-analytics.com/g/collect?v=2&tid=G-FIXTURE1&en=page_view&gcd=13l3l3l3l1';
  new Image().src = 'https://www.facebook.com/tr/?id=1234567890123&ev=PageView&eid=ev-1';
</script>
</body></html>"""

PAGES = {
    "https://shop.example/": Fixture(200, SHOP),
    "https://shop.example/gone": Fixture(410, "gone"),
    "https://shop.example/to-lan": Fixture(302, "", headers={"location": "http://127.0.0.1:8080/admin"}),
    "https://www.googletagmanager.com/gtm.js?id=GTM-FIX123": Fixture(200, "/* gtm */", "text/javascript"),
}

HOSTS = {
    "shop.example": [PUBLIC],
    "www.googletagmanager.com": [PUBLIC],
    "region1.google-analytics.com": [PUBLIC],
    "www.facebook.com": [PUBLIC],
}


def serve(url: str) -> Fixture | None:
    if url in PAGES:
        return PAGES[url]
    return Fixture(204, "", "text/plain")  # beacons and anything else


async def resolve(host: str) -> list[str]:
    if host not in HOSTS:
        raise OSError("NXDOMAIN")
    return HOSTS[host]
