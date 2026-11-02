"""Polite, read-only crawler. Only ever sends GET/HEAD requests. Never logs in, never adds to cart."""
import gzip
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, urlunparse

from .parse import Page, parse_page

USER_AGENT = "RRR-SiteAudit/1.0 (read-only SEO check for Rescue Retro Relics)"

# Never crawl these: they're private, cart/checkout, or endless filter combinations.
SKIP_PATHS = re.compile(
    r"^/(cart|checkout|checkouts|account|orders|admin|password|challenge|apps/|tools/|services/|"
    r"wpm@|cdn/|search|recommendations|.*\.(json|xml|js|css|atom|oembed)$)"
)
# Order pages are crawled in when there's a page limit: important pages first, products last.
PRIORITY = [("", 0), ("/pages/", 1), ("/policies/", 1), ("/blogs/", 2), ("/collections", 3), ("/products/", 4)]


def priority(url):
    path = urlparse(url).path
    if path in ("", "/"):
        return 0
    best = 5
    for prefix, rank in PRIORITY[1:]:
        if path.startswith(prefix):
            best = min(best, rank)
    return best


class Fetcher:
    def __init__(self, delay=0.4, timeout=25, log=print):
        self.delay = delay
        self.timeout = timeout
        self.log = log
        self.lock = threading.Lock()
        self.next_ok = 0.0

    def _wait_turn(self):
        with self.lock:
            now = time.monotonic()
            wait = self.next_ok - now
            self.next_ok = max(now, self.next_ok) + self.delay
        if wait > 0:
            time.sleep(wait)

    def get(self, url, method="GET"):
        """Returns (status, final_url, body_text, seconds, error)."""
        for attempt in range(4):
            self._wait_turn()
            req = urllib.request.Request(url, method=method, headers={
                "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Encoding": "gzip"})
            start = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read() if method == "GET" else b""
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    ctype = resp.headers.get("Content-Type", "")
                    body = raw.decode(resp.headers.get_content_charset() or "utf-8", "replace") \
                        if ("html" in ctype or "xml" in ctype or "text" in ctype) else ""
                    return resp.status, resp.geturl(), body, time.monotonic() - start, ""
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < 3:
                    pause = min(int(exc.headers.get("Retry-After", "0") or 0) or 2 * (attempt + 1), 30)
                    self.log(f"   store says slow down, waiting {pause}s...")
                    time.sleep(pause)
                    continue
                if exc.code in (405, 403) and method == "HEAD":
                    method = "GET"  # some servers refuse HEAD; ask properly
                    continue
                return exc.code, url, "", time.monotonic() - start, f"HTTP {exc.code}"
            except Exception as exc:  # timeouts, DNS, SSL
                if attempt < 1:
                    continue
                return 0, url, "", time.monotonic() - start, f"{type(exc).__name__}: {str(exc)[:100]}"
        return 0, url, "", 0.0, "gave up after retries"


def normalize(url, base_host):
    """Same-site page URL without query/fragment, or None if it shouldn't be crawled."""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return None
    host = u.netloc.lower().removeprefix("www.")
    if host != base_host:
        return None
    path = re.sub(r"/{2,}", "/", u.path or "/")
    # /collections/x/products/y is the same product page as /products/y
    m = re.match(r"^/collections/[^/]+(/products/.+)$", path)
    if m:
        path = m.group(1)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if SKIP_PATHS.match(path) or "+" in path:
        return None
    return urlunparse((u.scheme, u.netloc.lower(), path, "", "", ""))


def same_site(url, base_host):
    return urlparse(url).netloc.lower().removeprefix("www.") == base_host


def link_key(href, base_host):
    """The URL we check for a link, or None if it isn't checkable (mailto:, cart, etc.)."""
    href = href.split("#")[0]
    u = urlparse(href)
    if u.scheme not in ("http", "https"):
        return None
    if same_site(href, base_host):
        norm = normalize(href, base_host)
        if norm:
            return norm
        if SKIP_PATHS.match(re.sub(r"/{2,}", "/", u.path or "/")) or "+" in u.path:
            return None
        return urlunparse((u.scheme, u.netloc.lower(), u.path, "", "", ""))
    return href


class Crawler:
    def __init__(self, start_url, max_pages=500, workers=3, delay=0.4, log=print):
        self.start = start_url.rstrip("/")
        self.host = urlparse(self.start).netloc.lower().removeprefix("www.")
        self.max_pages = max_pages
        self.workers = workers
        self.log = log
        self.fetcher = Fetcher(delay=delay, log=log)
        self.robots = urllib.robotparser.RobotFileParser()
        self.pages = {}
        self.sitemap_urls = set()
        self.skipped_for_limit = 0

    def allowed(self, url):
        try:
            return self.robots.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def load_robots(self):
        status, _, body, _, _ = self.fetcher.get(self.start + "/robots.txt")
        if status == 200:
            self.robots.parse(body.splitlines())
        else:
            self.robots.parse([])
        return status

    def load_sitemap(self, url=None, depth=0):
        url = url or self.start + "/sitemap.xml"
        status, _, body, _, _ = self.fetcher.get(url)
        if status != 200 or not body:
            return status
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            return status
        for loc in root.iter():
            if not loc.tag.endswith("loc") or not loc.text:
                continue
            target = loc.text.strip()
            if root.tag.endswith("sitemapindex") and depth < 2:
                self.load_sitemap(target, depth + 1)
            else:
                norm = normalize(target, self.host)
                if norm:
                    self.sitemap_urls.add(norm)
        return status

    def fetch_page(self, url):
        status, final, body, secs, err = self.fetcher.get(url)
        page = Page(url=url, status=status, seconds=round(secs, 2), error=err)
        if final and final.rstrip("/") != url.rstrip("/"):
            page.redirected_to = final
        if body and status == 200:
            parse_page(page, body)
        return page

    def crawl(self):
        self.log("Reading robots.txt and sitemap...")
        self.load_robots()
        self.load_sitemap()
        self.log(f"   sitemap lists {len(self.sitemap_urls)} pages")
        queue = {normalize(self.start + "/", self.host)} | self.sitemap_urls
        queue = {u for u in queue if u and self.allowed(u)}
        seen = set(queue)
        pending = sorted(queue, key=lambda u: (priority(u), u))
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            while pending and len(self.pages) < self.max_pages:
                room = self.max_pages - len(self.pages)
                batch, pending = pending[:min(room, 30)], pending[min(room, 30):]
                for page in pool.map(self.fetch_page, batch):
                    self.pages[page.url] = page
                    for link in page.links:
                        norm = normalize(link.href, self.host)
                        if norm and norm not in seen and self.allowed(norm):
                            seen.add(norm)
                            pending.append(norm)
                pending.sort(key=lambda u: (priority(u), u))
                self.log(f"   checked {len(self.pages)} pages...")
        self.skipped_for_limit = len(pending)
        return self.pages

    def check_links(self, check_external=True, max_checks=3000):
        """Status of every link and image found that wasn't already crawled. Returns {url: (status, error)}."""
        results = {u: (p.status, p.error) for u, p in self.pages.items()}
        targets = set()
        for page in self.pages.values():
            for link in page.links:
                targets.add(link.href)
            for img in page.images:
                if img.src and not img.tiny and not img.src.startswith("data:"):
                    targets.add(img.src)
        todo = []
        for t in sorted(targets):
            key = link_key(t, self.host)
            if not key or key in results:
                continue
            if same_site(key, self.host) or check_external:
                todo.append(key)
        todo = list(dict.fromkeys(todo))[:max_checks]
        self.log(f"Checking {len(todo)} more links and images for breakage...")

        def head(url):
            status, _, _, _, err = self.fetcher.get(url, method="HEAD")
            return url, status, err

        with ThreadPoolExecutor(max_workers=self.workers + 2) as pool:
            for i, (url, status, err) in enumerate(pool.map(head, todo), 1):
                results[url] = (status, err)
                if i % 100 == 0:
                    self.log(f"   {i}/{len(todo)} links checked...")
        return results
