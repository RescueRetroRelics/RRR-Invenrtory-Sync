"""A tiny fake Shopify-style store for testing the shopper offline.

python tests/mock_store.py 8765   then   python -m rrr_shopper --site http://127.0.0.1:8765
Any hit on /PAYMENT-ATTEMPTED means the shopper clicked a pay button (it never should).
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PRODUCTS = {
    "snoopy-hippers-belle": ("Snoopy Hippers Belle Pink Bow Sonny Angel Mini Figure", True),
    "re-ment-snoopy-ketchup": ("Re-Ment Snoopy Life in a Bottle Ketchup", True),
    "betty-boop-lip-oil": ("Celavi Betty Boop Lip Oil Set", False),
}
CART = {"items": [], "discount": 0}
HITS = []

HEAD = ('<header><a href="/">RRR</a> <a href="/search" aria-label="Search">🔍</a> '
        '<a href="/collections/all">Shop</a></header>')
FOOT = ('<footer><a href="/policies/shipping-policy">Shipping</a> <a href="/policies/refund-policy">Returns</a> '
        '<a href="/pages/contact">Contact</a> <a href="/pages/does-not-exist">Old link</a></footer>')


def page(body, title="RRR"):
    return f"<!DOCTYPE html><html><head><title>{title}</title><meta name=viewport content='width=device-width'></head><body>{HEAD}<main>{body}</main>{FOOT}</body></html>"


def tile(h):
    return f'<a href="/products/{h}">{PRODUCTS[h][0]}</a><br>'


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def redirect(self, to):
        self.send(302, "", headers={"Location": to})

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        HITS.append(p)
        if p == "/":
            return self.send(200, page('<h1>Rescue Retro Relics</h1><p>Every sale helps rescue dogs.</p>'
                                       '<img src="/img/hero.png"><img src="/img/missing.png" alt="broken">'
                                       '<a href="/products/snoopy-hippers-belle" style="font-size:8px">x</a>'))
        if p.startswith("/img/"):
            if "missing" in p:
                return self.send(404, "nope")
            png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.end_headers()
            return self.wfile.write(png)
        if p == "/search":
            term = q.get("q", [""])[0].lower()
            hits = [h for h, (t, _) in PRODUCTS.items() if any(w in t.lower() for w in term.split())]
            return self.send(200, page("<h1>Search</h1>" + "".join(tile(h) for h in hits)))
        if p == "/collections/all":
            return self.send(200, page('<h1>All</h1><select name="sort_by"><option>Price</option></select>'
                                       + "".join(tile(h) for h in PRODUCTS)))
        if p.startswith("/products/") and p.endswith(".js"):
            h = p[len("/products/"):-3]
            return self.send(200, json.dumps({"variants": [{"id": list(PRODUCTS).index(h) + 1, "available": PRODUCTS[h][1]}]}), "application/json")
        if p.startswith("/products/"):
            h = p[len("/products/"):]
            if h not in PRODUCTS:
                return self.send(404, page("<h1>Not found</h1>"))
            title, avail = PRODUCTS[h]
            btn = '<button type="submit" name="add">Add to cart</button>' if avail else '<button type="submit" name="add" disabled>Sold out</button>'
            return self.send(200, page(f'<h1>{title}</h1><span class="price">$16.00</span><p>Ships in 2 days. Authentic.</p>'
                                       f'<form method="post" action="/cart/add"><input type="hidden" name="id" value="{h}">{btn}</form>'
                                       '<button>Buy it now</button>', title))
        if p == "/products.json":
            if q.get("page", ["1"])[0] != "1":
                return self.send(200, '{"products":[]}', "application/json")
            return self.send(200, json.dumps({"products": [{"handle": h, "variants": [{"available": a}]} for h, (_, a) in PRODUCTS.items()]}), "application/json")
        if p == "/cart.js":
            return self.send(200, json.dumps({"item_count": len(CART["items"]), "total_discount": CART["discount"]}), "application/json")
        if p == "/cart":
            items = "".join(f"<li>{i}</li>" for i in CART["items"])
            return self.send(200, page(f'<h1>Cart</h1><ul>{items}</ul><p>Shipping calculated at checkout</p>'
                                       '<form method="post" action="/cart"><button name="checkout">Check out</button></form>'))
        if p.startswith("/discount/"):
            CART["discount"] = 100 if p.endswith("RESCUE10") else 0
            return self.redirect(q.get("redirect", ["/"])[0])
        if p.startswith("/checkouts/"):
            return self.send(200, "<html><body><h1>Checkout</h1><input placeholder='Card number'>"
                                  "<form method=post action='/PAYMENT-ATTEMPTED'><button>Pay now</button></form></body></html>")
        if p == "/policies/shipping-policy":
            return self.send(200, page("<h1>Shipping</h1><p>" + "We ship fast. " * 60 + "</p>"))
        if p == "/pages/contact":
            return self.send(200, page("<h1>Contact</h1><p>Email us.</p>"))
        return self.send(404, page("<h1>Not found</h1>"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n).decode()
        p = urlparse(self.path).path
        HITS.append("POST " + p)
        if p == "/PAYMENT-ATTEMPTED":
            print("!!!!! PAYMENT ATTEMPTED - SAFETY FAILURE !!!!!", flush=True)
            return self.send(500, "no")
        if p == "/cart/add":
            CART["items"].append(parse_qs(data).get("id", ["?"])[0])
            return self.redirect("/cart")
        if p == "/cart/add.js":
            CART["items"].append("js-item")
            return self.send(200, "{}", "application/json")
        if p == "/cart":
            return self.redirect("/checkouts/cn/test123")
        return self.send(404, "")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"mock store on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
