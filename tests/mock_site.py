"""A tiny fake Shopify-style store with known problems, served on localhost for testing the site audit."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HEADER = """<header class="header"><a href="/">Rescue Retro Relics</a><nav>
<a href="/collections/all">Shop</a><a href="/pages/about">About</a><a href="/pages/contact">Contact</a>
<a href="/pages/terms-of-service">Terms of Service</a></nav></header>"""
FOOTER = """<div id="shopify-section-sections--1__footer" class="shopify-section shopify-section-group-footer-group"><footer>
<a href="/policies/shipping-policy">Shipping</a><a href="/policies/refund-policy">Returns</a>
<a href="/policies/terms-of-service">Terms of Service</a><a href="/pages/contact">Contact</a>
<a href="https://www.instagram.com/rescueretrorelics">Instagram</a></footer></div>"""

GOOD_DESC = "Vintage and retro collectibles, Snoopy, Sonny Angel and Re-Ment finds. Every sale helps save shelter dogs."


def page(title, body, desc=GOOD_DESC, jsonld=None, canonical=True):
    meta = f'<meta name="description" content="{desc}">' if desc is not None else ""
    ld = "".join(f'<script type="application/ld+json">{j}</script>' for j in (jsonld or []))
    can = '<link rel="canonical" href="/">' if canonical else ""
    return f"<!doctype html><html><head><title>{title}</title>{meta}{can}{ld}</head><body>{HEADER}<main>{body}</main>{FOOTER}</body></html>"


def product_ld(name, brand="Peanuts", price="24.99"):
    return json.dumps({"@context": "https://schema.org", "@type": "Product", "name": name, "image": "/img/x.jpg",
                       "brand": {"@type": "Brand", "name": brand},
                       "offers": {"@type": "Offer", "price": price, "priceCurrency": "USD",
                                  "availability": "https://schema.org/InStock"}})


ORG = json.dumps({"@context": "https://schema.org", "@type": "Organization", "name": "Rescue Retro Relics"})
CHARIZARD = ("Charizard holo Pokemon card from the 1999 base set in great shape with bright colors and sharp corners, "
             "a must have for any serious collector who wants a centerpiece for their binder or display case and")

PAGES = {
    "/": page("Rescue Retro Relics | Vintage Collectibles That Save Dogs",
              "<h1>Rescue Retro Relics</h1><h2>New arrivals</h2><img src='/img/hero.jpg' alt='Snoopy shelf'>"
              "<a href='/products/snoopy-hippers-belle'>Belle</a><a href='/products/charizard-holo'>Charizard</a>"
              "<a href='/products/deleted-thing'>Old item</a>", jsonld=[ORG]),
    "/pages/about": page("About Us", "<h1>About</h1><img src='/img/bailee.jpg'><img src='/img/team.jpg'>"
                         "<h2>Our story</h2><h4>Why dogs</h4>", desc=None),
    "/pages/contact": page("Contact", "<h1>Contact us</h1><p>email us</p>", desc="Contact us"),
    "/pages/terms-of-service": page("Terms", "<h1>Terms</h1>", desc=GOOD_DESC),
    "/policies/terms-of-service": page("Terms of Service", "<h1>Terms of Service</h1>"),
    "/policies/shipping-policy": page("Shipping policy", "<h1>Shipping</h1>"),
    "/policies/refund-policy": page("Refund policy", "<h1>Refunds</h1>"),
    "/collections/all": page("All products", "<h1>All</h1><a href='/collections/all/products/snoopy-hippers-belle'>Belle</a>"
                             "<img src='/img/missing.jpg' alt='gone'>"),
    "/products/snoopy-hippers-belle": page(
        "Snoopy Hippers Belle", "<h1>Snoopy Hippers Belle</h1><img src='/img/belle.jpg' alt='Belle figure'>",
        jsonld=[product_ld("Snoopy Hippers Belle")]),
    "/products/charizard-holo": page(
        "Charizard Holo", "<h1>Charizard Holo</h1><h1>Also a heading</h1><img src='/img/char.jpg' alt=''>",
        desc=CHARIZARD, jsonld=[product_ld("Charizard Holo", brand="Rescue Retro Relics"), '{"broken": ']),
}
IMAGES = {"/img/hero.jpg", "/img/bailee.jpg", "/img/team.jpg", "/img/belle.jpg", "/img/char.jpg", "/img/x.jpg"}
ROBOTS = "User-agent: *\nDisallow: /cart\nDisallow: /checkout\nSitemap: /sitemap.xml\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command == "GET":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        host = f"http://{self.headers['Host']}"
        if path == "/robots.txt":
            return self._send(200, ROBOTS.encode(), "text/plain")
        if path == "/sitemap.xml":
            urls = "".join(f"<url><loc>{host}{p}</loc></url>" for p in ("/", "/products/snoopy-hippers-belle", "/products/charizard-holo"))
            xml = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            return self._send(200, xml.encode(), "application/xml")
        if path in PAGES:
            return self._send(200, PAGES[path].encode())
        if path in IMAGES:
            return self._send(200, b"GIF89a", "image/gif")
        if path.startswith("/cart"):
            raise AssertionError("crawler must never touch the cart")
        return self._send(404, b"<h1>404</h1>")


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
