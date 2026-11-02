"""Pull the SEO-relevant bits out of one HTML page (standard library only)."""
import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
REGIONS = ("header", "nav", "footer")


@dataclass
class Link:
    href: str
    text: str
    region: str  # "header", "footer" or "body"


@dataclass
class Image:
    src: str
    alt: str | None  # None = no alt attribute at all
    tiny: bool = False


@dataclass
class Page:
    url: str
    status: int = 0
    seconds: float = 0.0
    error: str = ""
    redirected_to: str = ""
    title: str = ""
    meta_description: str | None = None
    meta_robots: str = ""
    canonical: str = ""
    headings: list = field(default_factory=list)  # [(level, text)]
    images: list = field(default_factory=list)
    links: list = field(default_factory=list)
    jsonld_raw: list = field(default_factory=list)
    jsonld: list = field(default_factory=list)  # parsed objects (flattened)
    jsonld_errors: list = field(default_factory=list)

    def schema_types(self):
        types = set()
        for obj in self.jsonld:
            t = obj.get("@type")
            for one in (t if isinstance(t, list) else [t]):
                if one:
                    types.add(str(one))
        return types


class _Parser(HTMLParser):
    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.page = page
        self.region_depth = {r: 0 for r in REGIONS}
        self.in_title = False
        self.heading = None  # [level, text parts]
        self.link = None  # [href, text parts, region]
        self.script_jsonld = None
        self.skip_text = 0  # inside <script>/<style>
        self.stack = []  # open elements: (tag, region it opened or None)

    def region(self):
        if self.region_depth["footer"]:
            return "footer"
        if self.region_depth["header"] or self.region_depth["nav"]:
            return "header"
        return "body"

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag not in VOID:
            ident = (a.get("id", "") + " " + a.get("class", "")).lower()
            reg = tag if tag in REGIONS else None
            # Shopify themes also mark regions with section ids/classes
            if reg is None and ("shopify-section-footer" in ident or "footer-group" in ident):
                reg = "footer"
            elif reg is None and ("shopify-section-header" in ident or "header-group" in ident):
                reg = "header"
            self.stack.append((tag, reg))
            if reg:
                self.region_depth[reg] += 1
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "description" and self.page.meta_description is None:
                self.page.meta_description = a.get("content", "").strip()
            elif name == "robots":
                self.page.meta_robots = a.get("content", "").lower()
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.page.canonical = urljoin(self.page.url, a.get("href", ""))
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading = [int(tag[1]), []]
        elif tag == "a" and a.get("href"):
            self.link = [a["href"], [a.get("aria-label", "")] if a.get("aria-label") else [], self.region()]
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or (a.get("srcset", "").split(" ")[0])
            tiny = a.get("width") in ("1", "0") or a.get("height") in ("1", "0")
            self.page.images.append(Image(urljoin(self.page.url, src) if src else "", a.get("alt") if "alt" in a else None, tiny))
        elif tag == "script":
            self.skip_text += 1
            if "ld+json" in a.get("type", "").lower():
                self.script_jsonld = []
        elif tag == "style":
            self.skip_text += 1

    def handle_endtag(self, tag):
        if tag not in VOID and any(t == tag for t, _ in self.stack):
            while self.stack:
                t, reg = self.stack.pop()
                if reg:
                    self.region_depth[reg] -= 1
                if t == tag:
                    break
        if tag == "title":
            self.in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.heading:
            text = " ".join(" ".join(self.heading[1]).split())
            self.page.headings.append((self.heading[0], text))
            self.heading = None
        elif tag == "a" and self.link:
            href, parts, region = self.link
            text = " ".join(" ".join(parts).split())
            self.page.links.append(Link(urljoin(self.page.url, href), text, region))
            self.link = None
        elif tag in ("script", "style"):
            self.skip_text = max(0, self.skip_text - 1)
            if tag == "script" and self.script_jsonld is not None:
                self.page.jsonld_raw.append("".join(self.script_jsonld))
                self.script_jsonld = None

    def handle_data(self, data):
        if self.script_jsonld is not None:
            self.script_jsonld.append(data)
            return
        if self.skip_text:
            return
        if self.in_title:
            self.page.title += data
        if self.heading is not None:
            self.heading[1].append(data)
        if self.link is not None:
            self.link[1].append(data)


def _flatten(obj, out):
    if isinstance(obj, list):
        for o in obj:
            _flatten(o, out)
    elif isinstance(obj, dict):
        out.append(obj)
        if "@graph" in obj:
            _flatten(obj["@graph"], out)


def parse_page(page, html):
    p = _Parser(page)
    try:
        p.feed(html)
        p.close()
    except Exception as exc:  # malformed HTML should never stop the audit
        page.error = page.error or f"HTML parse problem: {exc}"
    page.title = " ".join(page.title.split())
    for raw in page.jsonld_raw:
        try:
            _flatten(json.loads(raw), page.jsonld)
        except ValueError as exc:
            page.jsonld_errors.append(str(exc)[:120])
    return page
