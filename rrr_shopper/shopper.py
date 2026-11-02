"""Drives a real browser through the store like a customer would. Dry run: it never pays."""

import json
import re
import time
from urllib.parse import quote_plus, urljoin, urlparse

from . import config

DESKTOP = {"viewport": {"width": 1366, "height": 850}}
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

FORBIDDEN_RE = re.compile("|".join(re.escape(w) for w in config.FORBIDDEN_CLICK_WORDS), re.I)


class SafetyStop(Exception):
    """Raised if anything tries to click a payment-type button."""


# ---------------------------------------------------------------- findings

class Findings:
    def __init__(self):
        self.items = {}
        self.wins = []

    def add(self, title, impact, effort, status, who, page, detail, fix, shot=None):
        """impact/effort: 1 low .. 3 high. status: 'verified' or 'hypothesis'."""
        key = (title, page)
        f = self.items.get(key)
        if not f:
            f = self.items[key] = {
                "title": title, "impact": impact, "effort": effort, "status": status,
                "page": page, "detail": detail, "fix": fix, "seen_by": [], "shots": [],
            }
        if who not in f["seen_by"]:
            f["seen_by"].append(who)
        if shot and shot not in f["shots"]:
            f["shots"].append(shot)

    def win(self, text):
        if text not in self.wins:
            self.wins.append(text)

    def ranked(self):
        return sorted(self.items.values(), key=lambda f: (-f["impact"], f["effort"], f["title"]))


# ---------------------------------------------------------------- browser helpers

class Run:
    """One persona on one device."""

    def __init__(self, browser, playwright, base, persona, device, outdir, findings):
        self.base = base.rstrip("/")
        self.persona = persona
        self.device = device
        self.who = f"{persona['name']} ({device})"
        self.out = outdir
        self.f = findings
        self.steps = []
        self.shots = []
        self.log = []
        self.step = "start"
        if device == "mobile":
            opts = dict(playwright.devices["Pixel 7"])
        else:
            opts = dict(DESKTOP, user_agent=DESKTOP_UA)
        self.ctx = browser.new_context(**opts)
        self.ctx.set_default_timeout(20000)
        self.page = self.ctx.new_page()
        self._listen(self.page)

    # -- logging of errors the customer never sees but that break things
    def _listen(self, page):
        host = urlparse(self.base).hostname
        def own(url):
            h = urlparse(url).hostname or ""
            return h == host or h.endswith("shopify.com") or h.endswith("shopifycdn.com")
        page.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource") and self.log.append(
            {"step": self.step, "kind": "console", "msg": m.text[:300], "own": True}))
        page.on("pageerror", lambda e: self.log.append(
            {"step": self.step, "kind": "js-crash", "msg": str(e)[:300], "own": True}))
        page.on("response", lambda r: r.status >= 400 and not r.request.is_navigation_request() and self.log.append(
            {"step": self.step, "kind": f"http {r.status}", "msg": r.url[:300], "own": own(r.url)}))
        page.on("requestfailed", lambda r: self.log.append(
            {"step": self.step, "kind": "failed", "msg": r.url[:300], "own": own(r.url)}))

    def url(self, path):
        return urljoin(self.base + "/", path.lstrip("/"))

    def shot(self, name):
        fn = f"{self.persona['key']}_{self.device}_{len(self.shots) + 1:02d}_{name}.png"
        try:
            self.page.screenshot(path=str(self.out / "screenshots" / fn))
            self.shots.append({"file": f"screenshots/{fn}", "label": name})
            return f"screenshots/{fn}"
        except Exception:
            return None

    def record(self, step, ok, note="", ms=None):
        self.steps.append({"step": step, "ok": ok, "note": note, "ms": ms})

    def visit(self, path, label):
        self.step = label
        t0 = time.time()
        try:
            resp = self.page.goto(self.url(path), wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            self.record(label, False, f"Page did not load: {str(e).splitlines()[0][:120]}")
            return None
        try:
            self.page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        ms = int((time.time() - t0) * 1000)
        if "/password" in self.page.url:
            raise RuntimeError("The store is behind a password page. Turn it off or test while it's open.")
        status = resp.status if resp else 0
        if ms > config.SLOW_PAGE_MS:
            self.f.add(f"Slow page: {label}", 2, 2, "verified", self.who, label,
                       f"Took {ms/1000:.1f}s to fully load (target under {config.SLOW_PAGE_MS/1000:.0f}s). "
                       "Speed varies run to run, so re-run before acting.",
                       "Compress big images, remove unused apps/scripts on this page.")
        return {"status": status, "ms": ms}

    def safe_click(self, locator, why):
        text = ""
        try:
            text = (locator.inner_text(timeout=3000) or "") + " " + (locator.get_attribute("value") or "")
        except Exception:
            pass
        if FORBIDDEN_RE.search(text):
            raise SafetyStop(f"Refused to click '{text.strip()}' ({why}). Dry run never pays.")
        locator.click()

    def scroll_through(self):
        try:
            self.page.evaluate("""async () => {
                for (let y = 0; y < document.body.scrollHeight; y += 600) {
                    window.scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); }
                window.scrollTo(0, 0); }""")
            self.page.wait_for_timeout(800)
        except Exception:
            pass

    def audit(self, label):
        """Alt text, broken images, tap targets, H1s on the current page."""
        self.scroll_through()
        try:
            a = self.page.evaluate(AUDIT_JS, config.MIN_TAP_TARGET_PX)
        except Exception:
            return
        if a["noAlt"]:
            self.f.add(f"Images with no alt text: {label}", 1, 1, "verified", self.who, label,
                       f"{a['noAlt']} image(s) have no alt text. Screen readers and Google can't tell what they show. "
                       f"Example: {a['noAltSample'][:1]}",
                       "Shopify admin: open the image, click 'Add alt text'. Theme images: Online Store > Customize.")
        if a["emptyAltBig"]:
            self.f.add(f"Large images with blank alt text: {label}", 1, 1, "hypothesis", self.who, label,
                       f"{a['emptyAltBig']} large visible image(s) have blank alt text. Fine if decorative, "
                       "but product photos should be described.",
                       "Add a short description to product photos.")
        if a["broken"]:
            self.f.add(f"Broken images: {label}", 2, 1, "verified", self.who, label,
                       f"{a['broken']} image(s) failed to load. Example: {a['brokenSample'][:1]}",
                       "Re-upload or remove the image.")
        if a["h1"] != 1:
            self.f.add(f"Page has {a['h1']} main headings (H1): {label}", 1, 2, "verified", self.who, label,
                       "Search engines expect exactly one H1 per page.",
                       "Theme editor: make one heading the H1, others H2.")
        if self.device == "mobile" and a["smallTaps"]:
            self.f.add("Small tap targets on mobile", 2, 2, "verified", self.who, "Mobile pages",
                       f"{a['smallTaps']} buttons/links on the {label} are smaller than {config.MIN_TAP_TARGET_PX}px, "
                       f"easy to mis-tap with a thumb. Examples: {', '.join(a['smallSample'][:4])}",
                       "Theme editor or CSS: increase button/icon padding on mobile.")
        if self.device == "mobile" and a["hScroll"]:
            self.f.add(f"Page scrolls sideways on mobile: {label}", 2, 2, "verified", self.who, label,
                       "Content is wider than the phone screen, so the page wiggles side to side.",
                       "Find the too-wide element (often an image or table) and set max-width:100%.")

    def cart_count(self):
        try:
            return self.page.evaluate("fetch('/cart.js').then(r => r.json()).then(c => c.item_count)")
        except Exception:
            return None

    def close(self):
        self.ctx.close()

    def errors_report(self):
        own = [e for e in self.log if e["own"]]
        third = [e for e in self.log if not e["own"]]
        by_step = {}
        for e in own:
            by_step.setdefault(re.sub(r" \(.*\)$", "", e["step"]), []).append(e)
        for step, errs in by_step.items():
            crash = [e for e in errs if e["kind"] == "js-crash"]
            missing = [e for e in errs if e["kind"].startswith("http 404")]
            if crash:
                self.f.add(f"JavaScript error on {step}", 2, 2, "verified", self.who, step,
                           f"{len(crash)} script crash(es). First: {crash[0]['msg'][:200]}",
                           "Usually a theme app or custom code snippet. Disable apps one at a time to find it.")
            if missing:
                self.f.add(f"Missing files (404) on {step}", 2, 1, "verified", self.who, step,
                           f"{len(missing)} file(s) not found. First: {missing[0]['msg'][:200]}",
                           "Remove the reference or re-upload the file.")
            other = [e for e in errs if e not in crash and e not in missing]
            if other:
                self.f.add(f"Console errors on {step}", 1, 2, "hypothesis", self.who, step,
                           f"{len(other)} error(s). First: [{other[0]['kind']}] {other[0]['msg'][:200]}",
                           "Often harmless app noise. Worth a look if something on this page misbehaves.")
        if third:
            self.f.add("Third-party scripts failing", 1, 2, "hypothesis", self.who, "various",
                       f"{len(third)} outside requests failed (apps, trackers, ads). First: {third[0]['msg'][:150]}",
                       "Check installed apps; uninstall ones you no longer use.")


# ---------------------------------------------------------------- the persona journey

def journey(run, store):
    p = run.persona
    f = run.f

    # 1. Landing page
    r = run.visit("/", "Home page")
    if not r or r["status"] >= 400:
        run.record("Home page", False, f"status {r and r['status']}")
        f.add("Home page failed to load", 3, 1, "verified", run.who, "Home page", "Shoppers can't get in.", "Check the store is online.")
        return
    run.audit("Home page")
    run.shot("home")
    run.record("Home page", True, "", r["ms"])
    has_search = run.page.locator(
        "input[type=search], a[href*='/search'], [aria-label*='earch'], summary[aria-label*='earch'], details-modal.header__search"
    ).count()
    if not has_search:
        f.add("No visible search box/icon", 3, 1, "hypothesis", run.who, "Home page",
              "Couldn't find a search box or search icon in the header.",
              "Theme editor > Header: turn on search.")

    # 2. Search
    product_url = None
    for term in p["searches"]:
        r = run.visit(f"/search?q={quote_plus(term)}&type=product", f"Search: {term}")
        if not r:
            continue
        links = product_links(run.page)
        titles = [t for _, t in links[:5]]
        words = [w for w in re.split(r"\W+", term.lower()) if len(w) > 2]
        relevant = [t for t in titles if any(w in t.lower() for w in words)]
        if not links:
            run.record(f"Search '{term}'", False, "0 results", r["ms"])
            f.add(f"Search for '{term}' returns nothing", 3, 1, "verified", run.who, f"Search: {term}",
                  "A customer searching this finds no products.",
                  "Add the term to product titles/tags, or set up search synonyms (Search & Discovery app).",
                  run.shot(f"search-{slug(term)}"))
            continue
        shot = run.shot(f"search-{slug(term)}")
        if titles and not relevant:
            f.add(f"Search '{term}' top results look off-topic", 2, 1, "hypothesis", run.who, f"Search: {term}",
                  f"Top results: {'; '.join(titles[:3])}",
                  "Use the Search & Discovery app to boost the right products.", shot)
        run.record(f"Search '{term}'", True, f"{len(links)} results; top: {titles[0][:60] if titles else '?'}", r["ms"])
        f.win(f"Search '{term}' finds products ({len(links)} on first page)")
        if not product_url:
            product_url = first_in_stock(run, links)

    # 3. Collection browse + filters
    r = run.visit(p["browse"], "Collection page")
    if r and r["status"] < 400:
        n = len(product_links(run.page))
        filters = run.page.locator(
            "[id*='Facet'], [class*='facet'], [class*='filter'], [id*='filter'], select[name='sort_by'], #SortBy"
        ).count()
        run.audit("Collection page")
        shot = run.shot("collection")
        run.record("Collection page", n > 0, f"{n} products shown, filter/sort controls: {'yes' if filters else 'no'}", r["ms"])
        if not filters:
            f.add("No filter or sort options on collection page", 2, 1, "hypothesis", run.who, "Collection page",
                  "Couldn't find filter/sort controls. Shoppers with 900+ items to dig through need them.",
                  "Theme editor > Collection: enable filtering and sorting (Search & Discovery app adds filters).", shot)
        if not product_url and n:
            product_url = first_in_stock(run, product_links(run.page))
    else:
        run.record("Collection page", False, f"status {r and r['status']}")

    if not product_url and store.get("in_stock"):
        product_url = run.url("/products/" + store["in_stock"][0])
    if not product_url:
        run.record("Product page", False, "Couldn't find any product to open")
        return

    # 4. Product page
    path = urlparse(product_url).path
    r = run.visit(path, "Product page")
    if not r or r["status"] >= 400:
        run.record("Product page", False, f"status {r and r['status']}")
        return
    run.audit("Product page")
    shot = run.shot("product")
    run.record("Product page", True, run.page.title()[:70], r["ms"])
    product_checks(run, shot)

    # 5. Add to cart
    before = run.cart_count() or 0
    btn = add_button(run.page)
    run.step = "Add to cart"
    if btn is None:
        f.add("Add-to-cart button not found", 3, 2, "hypothesis", run.who, "Product page",
              "The shopper couldn't find an add-to-cart button (may be sold out, or the theme uses an unusual button).",
              "Check this product page by hand.", shot)
        run.record("Add to cart", False, "No button found")
    else:
        try:
            run.safe_click(btn, "add to cart")
            run.page.wait_for_timeout(2500)
        except SafetyStop:
            raise
        except Exception as e:
            run.record("Add to cart", False, f"Click failed: {str(e).splitlines()[0][:100]}")
    after = run.cart_count()
    if after is not None and after > before:
        run.record("Add to cart", True, f"cart now has {after} item(s)")
        run.shot("added-to-cart")
        f.win(f"Add to cart works ({run.device})")
    elif btn is not None:
        f.add("Add to cart didn't add the item", 3, 2, "verified", run.who, "Product page",
              f"Clicked the button; cart count stayed at {after}.", "Test this product by hand; check cart apps.",
              run.shot("add-failed"))
        if not any(s["step"] == "Add to cart" for s in run.steps):
            run.record("Add to cart", False, "Cart count did not go up")
        return
    else:
        return

    # 6. Cart
    r = run.visit("/cart", "Cart page")
    if r and r["status"] < 400:
        run.audit("Cart page")
        shot = run.shot("cart")
        co = checkout_button(run.page)
        run.record("Cart page", co is not None, "checkout button found" if co else "no checkout button", r["ms"])
        txt = run.page.inner_text("body").lower()
        if "shipping" not in txt:
            f.add("Cart doesn't mention shipping", 1, 1, "hypothesis", run.who, "Cart page",
                  "Surprise shipping costs are the #1 reason for abandoned carts.",
                  "Add a line like 'Shipping calculated at checkout' or a free-shipping threshold message.", shot)
    else:
        run.record("Cart page", False, f"status {r and r['status']}")
        return

    # 7. Checkout start, then STOP
    co = checkout_button(run.page)
    run.step = "Checkout start"
    if co is None:
        f.add("No checkout button in cart", 3, 1, "verified", run.who, "Cart page",
              "Shopper can't proceed to checkout from the cart.", "Theme editor > Cart.", shot)
        return
    t0 = time.time()
    try:
        run.safe_click(co, "go to checkout")
        run.page.wait_for_url(re.compile(r"/checkouts?/"), timeout=30000)
        run.page.wait_for_timeout(2500)
        ms = int((time.time() - t0) * 1000)
        run.shot("checkout-start-STOPPED-HERE")
        run.record("Checkout start (stopped before payment)", True, "Checkout loaded. Nothing typed, nothing paid.", ms)
        f.win(f"Checkout opens from the cart ({run.device})")
    except SafetyStop:
        raise
    except Exception as e:
        run.record("Checkout start", False, str(e).splitlines()[0][:120])
        f.add("Checkout didn't open from cart", 3, 2, "verified", run.who, "Cart page",
              "Clicking checkout didn't reach the checkout page within 30s.", "Test by hand; check checkout apps.",
              run.shot("checkout-failed"))


def trust_tour(run):
    """The skeptic's extra homework: policies, contact, authenticity."""
    f = run.f
    for name, path in config.TRUST_PAGES.items():
        r = run.visit(path, name)
        if not r or r["status"] >= 400:
            run.record(name, False, f"status {r and r['status']}")
            f.add(f"{name} missing", 3 if "Refund" in name or "Shipping" in name else 2, 1, "verified",
                  run.who, name, f"{path} returned {r and r['status']}.",
                  "Shopify admin > Settings > Policies (or create the page).", run.shot(slug(name)))
            continue
        words = len(run.page.inner_text("body").split())
        run.record(name, True, f"{words} words", r["ms"])
        if words < 120:
            f.add(f"{name} looks very thin", 2, 1, "hypothesis", run.who, name,
                  f"Only about {words} words on the page.", "Spell out timelines, costs and how to reach you.",
                  run.shot(slug(name)))
        else:
            f.win(f"{name} page exists")
    run.visit("/", "Home page (footer check)")
    links = run.page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, (e.innerText||'').trim().toLowerCase()])")
    hrefs = " ".join(h.lower() for h, _ in links)
    texts = " ".join(t for _, t in links)
    for label, words in [("shipping", ["shipping"]), ("returns/refunds", ["return", "refund"]),
                         ("contact", ["contact"]), ("about / our story", ["about", "story", "mission"])]:
        if not any(w in hrefs or w in texts for w in words):
            f.add(f"No '{label}' link on home page", 2, 1, "verified", run.who, "Home page",
                  f"A buyer checking you out can't spot a {label} link in the menu or footer.",
                  "Online Store > Navigation: add it to the footer menu.")
    if "mailto:" not in hrefs and "tel:" not in hrefs:
        f.add("No email or phone link on home page", 1, 1, "hypothesis", run.who, "Home page",
              "Some buyers want a direct way to reach a real person before buying collectibles.",
              "Add your shop email to the footer.")


def product_checks(run, shot):
    f = run.f
    page = run.page
    body = page.inner_text("body").lower()
    html = page.content().lower()
    if not page.locator("[class*='price'], [id*='price'], [data-product-price]").count():
        f.add("Price not clearly marked on product page", 3, 2, "hypothesis", run.who, "Product page",
              "Couldn't find a price element.", "Check theme product template.", shot)
    review_markers = ["jdgm", "judge.me", "spr-", "yotpo", "loox", "stamped", "okendo", "reviews.io", "product-reviews", "rating"]
    if not any(m in html for m in review_markers):
        f.add("No reviews or star ratings on product pages", 2, 2, "hypothesis", run.who, "Product page",
              "No review widget detected. Reviews are one of the strongest trust signals for a small shop.",
              "Install a free review app (e.g. Judge.me) and import eBay feedback if allowed.", shot)
    else:
        f.win("Product pages show a reviews/rating widget")
    if not any(w in body for w in ["shipping", "ships"]):
        f.add("Product page doesn't mention shipping", 2, 1, "hypothesis", run.who, "Product page",
              "Buyers look for 'when will it ship and how much' next to the Add to Cart button.",
              "Add a collapsible 'Shipping & returns' block in the product template.", shot)
    if not any(w in body for w in ["return", "refund"]):
        f.add("Product page doesn't mention returns", 2, 1, "hypothesis", run.who, "Product page",
              "No returns info near the buy button.", "Same collapsible block as shipping.", shot)
    if not any(w in body for w in ["authentic", "genuine", "official", "licensed"]):
        f.add("No authenticity wording on product page", 1, 1, "hypothesis", run.who, "Product page",
              "Collectors buying Sonny Angel/Re-Ment worry about fakes.",
              "Add a short 'Authentic, never a knockoff' line to the product template.", shot)
    if any(w in body for w in ["rescue", "donat"]):
        f.win("Product page mentions the dog rescue mission")
    else:
        f.add("Rescue mission not mentioned on product page", 1, 1, "hypothesis", run.who, "Product page",
              "Your mission is a reason to buy from you instead of a big seller.",
              "Add a one-line 'Every purchase helps rescue dogs' badge near Add to Cart.", shot)


def store_checks(browser, playwright, base, outdir, findings, store, discount):
    """Once per run, desktop: out-of-stock behavior, broken links, discount code."""
    run = Run(browser, playwright, base, {"key": "store", "name": "Store-wide checks"}, "desktop", outdir, findings)
    f = findings
    try:
        # Out of stock
        for handle in store.get("sold_out", [])[:3]:
            r = run.visit(f"/products/{handle}", "Sold-out product")
            if not r or r["status"] >= 400:
                run.record(f"Sold-out: {handle[:40]}", True, "product no longer on the site (skipped)")
                continue
            shot = run.shot("sold-out")
            body = run.page.inner_text("body").lower()
            btn = add_button(run.page, include_disabled=True)
            disabled = btn is not None and (btn.is_disabled() or "sold out" in (btn.inner_text() or "").lower())
            says = "sold out" in body or "out of stock" in body
            notify = any(w in body for w in ["notify me", "back in stock", "email me when"])
            run.record(f"Sold-out: {handle[:40]}", disabled or says,
                       f"says sold out: {says}, button disabled: {disabled}, notify-me: {notify}", r["ms"])
            if not (disabled or says):
                f.add("Sold-out product still looks buyable", 3, 1, "verified", run.who, "Sold-out product",
                      f"/products/{handle} doesn't say sold out and the button isn't disabled.",
                      "Check 'Continue selling when out of stock' is OFF for this product.", shot)
            else:
                f.win("Sold-out products are clearly marked")
            if not notify:
                f.add("No 'notify me when back in stock' option", 1, 2, "hypothesis", run.who, "Sold-out product",
                      "Collectors would sign up to hear when a sold-out piece is back. Free apps exist.",
                      "Install a back-in-stock app, or hide sold-out items from collections.", shot)

        # Broken internal links from home page
        run.visit("/", "Home page (link check)")
        hrefs = run.page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        host = urlparse(base).hostname
        seen = []
        for h in hrefs:
            u = h.split("#")[0]
            if urlparse(u).hostname == host and u not in seen and "/cart" not in u and "/account" not in u:
                seen.append(u)
        bad = []
        for u in seen[: config.MAX_LINKS_TO_CHECK]:
            try:
                status = run.ctx.request.get(u, timeout=20000, max_redirects=5).status
            except Exception:
                status = 0
            if status >= 400 or status == 0:
                bad.append(f"{urlparse(u).path} ({status})")
        run.record("Link check", not bad, f"{len(seen[:config.MAX_LINKS_TO_CHECK])} links tested, {len(bad)} broken")
        if bad:
            f.add("Broken links on home page", 3, 1, "verified", run.who, "Home page",
                  f"{len(bad)} link(s) go nowhere: {', '.join(bad[:6])}",
                  "Online Store > Navigation: fix or remove these menu items.")
        else:
            f.win(f"All {len(seen[:config.MAX_LINKS_TO_CHECK])} home-page links work")

        # Discount code
        if discount and store.get("in_stock"):
            run.step = "Discount code"
            run.page.evaluate("""h => fetch('/products/' + h + '.js').then(r => r.json()).then(p =>
                fetch('/cart/add.js', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: [{id: p.variants.find(v => v.available).id, quantity: 1}]})}))""",
                              store["in_stock"][0])
            run.visit(f"/discount/{quote_plus(discount)}?redirect=/cart", "Discount code")
            cart = run.page.evaluate("fetch('/cart.js').then(r => r.json())")
            applied = (cart.get("total_discount") or 0) > 0 or bool(cart.get("cart_level_discount_applications"))
            shot = run.shot("discount")
            run.record(f"Discount '{discount}'", applied, "applied in cart" if applied else "not applied in cart")
            if applied:
                f.win(f"Discount code {discount} applies in the cart")
            else:
                f.add(f"Discount code '{discount}' didn't apply", 2, 1, "verified", run.who, "Cart page",
                      "The code was sent via the shareable discount link but the cart shows no discount. "
                      "It may need a minimum spend, specific products, or may only apply at checkout.",
                      "Shopify admin > Discounts: check the code is active and its conditions.", shot)
        elif not discount:
            run.record("Discount code", True, "skipped (no code given; use --discount CODE)")
    finally:
        run.errors_report()
        run.close()
    return run


# ---------------------------------------------------------------- page helpers

def product_links(page):
    try:
        raw = page.eval_on_selector_all(
            "main a[href*='/products/'], #MainContent a[href*='/products/'], a[href*='/products/']",
            "els => els.map(e => [e.href, (e.innerText || e.getAttribute('aria-label') || '').trim()])")
    except Exception:
        return []
    out, seen = [], set()
    for href, text in raw:
        path = urlparse(href).path
        m = re.search(r"/products/([^/?#]+)", path)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append((href, text.split("\n")[0] or m.group(1).replace("-", " ")))
    return out


def first_in_stock(run, links):
    """First product link whose product can actually be bought (checked via Shopify's public .js)."""
    for href, _ in links[:8]:
        m = re.search(r"/products/([^/?#]+)", urlparse(href).path)
        try:
            ok = run.page.evaluate(
                "h => fetch('/products/' + h + '.js').then(r => r.json()).then(p => p.variants.some(v => v.available))",
                m.group(1))
        except Exception:
            ok = False
        if ok:
            return href
    return None


def add_button(page, include_disabled=False):
    sels = ["form[action*='/cart/add'] button[type=submit][name='add']",
            "form[action*='/cart/add'] button[type=submit]",
            "button[name='add']", "form[action*='/cart/add'] input[type=submit]",
            "button:has-text('Add to cart')"]
    for s in sels:
        loc = page.locator(s)
        for i in range(loc.count()):
            b = loc.nth(i)
            try:
                if b.is_visible() and (include_disabled or b.is_enabled()):
                    return b
            except Exception:
                continue
    return None


def checkout_button(page):
    for s in ["button[name='checkout']", "input[name='checkout']", "a[href*='/checkout']", "button:has-text('Check out')"]:
        loc = page.locator(s)
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    return None


def load_store_info(request_ctx, base):
    """Read the store's public product list to find in-stock and sold-out items."""
    info = {"in_stock": [], "sold_out": list(config.OUT_OF_STOCK_HANDLES)}
    for pg in (1, 2, 3):
        try:
            r = request_ctx.get(f"{base}/products.json?limit=250&page={pg}", timeout=30000)
            prods = r.json().get("products", []) if r.ok else []
        except Exception:
            break
        if not prods:
            break
        for p in prods:
            avail = any(v.get("available") for v in p.get("variants", []))
            (info["in_stock"] if avail else info["sold_out"]).append(p["handle"])
    seen = set()
    info["sold_out"] = [h for h in info["sold_out"] if not (h in seen or seen.add(h))]
    return info


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


AUDIT_JS = """(minTap) => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const imgs = [...document.images];
  const noAlt = imgs.filter(i => !i.hasAttribute('alt'));
  const emptyAltBig = imgs.filter(i => i.hasAttribute('alt') && !i.alt.trim() && vis(i) && i.getBoundingClientRect().width > 150);
  const broken = imgs.filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src) && !(i.currentSrc || i.src).startsWith('data:'));
  const taps = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, [role=button]')]
    .filter(el => vis(el) && !el.closest('p'))
    .filter(el => { const r = el.getBoundingClientRect(); return r.width < minTap || r.height < minTap; });
  return {
    noAlt: noAlt.length, noAltSample: noAlt.slice(0, 3).map(i => (i.currentSrc || i.src).split('?')[0].slice(-80)),
    emptyAltBig: emptyAltBig.length,
    broken: broken.length, brokenSample: broken.slice(0, 3).map(i => (i.currentSrc || i.src).slice(0, 120)),
    smallTaps: taps.length,
    smallSample: taps.slice(0, 6).map(el => (el.innerText || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 30) || el.tagName),
    h1: document.querySelectorAll('h1').length,
    hScroll: document.documentElement.scrollWidth > window.innerWidth + 2,
  };
}"""
