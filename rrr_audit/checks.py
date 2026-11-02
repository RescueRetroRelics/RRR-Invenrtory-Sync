"""Turn crawled pages into findings. Every finding is something the crawler actually saw (verified),
unless the issue type says hypothesis (a likely problem a human should eyeball)."""
import re
from collections import defaultdict
from urllib.parse import urlparse

from .crawler import link_key, same_site

# code: (title, impact 1-3, effort 1-3, label, why it matters, how to fix in Shopify)
ISSUES = {
    "broken_internal_link": (
        "Broken link to a page on your own site", 3, 1, "verified",
        "Shoppers hit a dead end and Google sees a sloppy site.",
        "Find the link on the page listed and point it at a page that exists (Online Store > Navigation for menus, "
        "or edit the page/product text). If the page moved, add a redirect: Online Store > Navigation > View URL redirects."),
    "broken_image": (
        "Broken image", 3, 1, "verified",
        "A missing picture on a vintage listing kills the sale.",
        "Open the product/page in admin and re-upload the image."),
    "page_error": (
        "Page doesn't load (error or 404)", 3, 1, "verified",
        "Anyone landing here from Google or a link gets an error page.",
        "If the page was deleted on purpose, add a redirect to the closest match "
        "(Online Store > Navigation > View URL redirects). Otherwise republish it."),
    "nav_footer_mismatch": (
        "Menu and footer disagree", 3, 1, "verified",
        "Same link name going to two different places confuses shoppers and splits your SEO.",
        "Online Store > Navigation: open the main menu and the footer menu and point both items at the same page. "
        "For policies, use the built-in Shopify policy pages (Settings > Policies)."),
    "missing_meta_description": (
        "No meta description", 2, 1, "verified",
        "Google makes up its own snippet from random page text. A written one gets more clicks.",
        "Product/collection/page in admin > scroll to 'Search engine listing' > Edit > write 1-2 sentences (120-155 characters)."),
    "meta_description_cut_off": (
        "Meta description looks cut off mid-sentence", 2, 1, "verified",
        "Shopify fills this from the first 160 characters of your description, so it stops mid-word in Google.",
        "Admin > 'Search engine listing' > Edit > write a complete sentence or two, 120-155 characters."),
    "meta_description_too_long": (
        "Meta description too long (Google will chop it)", 1, 1, "verified",
        "Google shows about 155 characters; the rest gets '...'.",
        "Admin > 'Search engine listing' > Edit > trim to 155 characters or less."),
    "meta_description_too_short": (
        "Meta description very short", 1, 1, "verified",
        "Under ~50 characters wastes the space Google gives you.",
        "Admin > 'Search engine listing' > Edit > expand to 120-155 characters."),
    "duplicate_meta_description": (
        "Same meta description on several pages", 1, 2, "verified",
        "Google treats copy-paste descriptions as low effort.",
        "Give each page its own description in 'Search engine listing'."),
    "missing_title": (
        "Page has no title", 3, 1, "verified",
        "The title is the blue headline in Google results.",
        "Admin > 'Search engine listing' > Edit > Page title."),
    "duplicate_title": (
        "Same page title on several pages", 2, 2, "verified",
        "Google can't tell the pages apart.",
        "Give each one a unique title in 'Search engine listing'."),
    "title_too_long": (
        "Page title too long (Google will chop it)", 1, 1, "verified",
        "Google shows about 60 characters of a title.",
        "Admin > 'Search engine listing' > Page title: put the important words first, 60 characters or less."),
    "missing_alt_text": (
        "Image has no alt text", 2, 1, "verified",
        "Screen readers skip it and Google Images can't index it. Alt text is free SEO for collectibles.",
        "Products: click the image in admin > 'Add alt text'. Pages/theme: Online Store > Customize > the image block > 'Image alt text'. "
        "Describe it like you'd tell a friend: 'Vintage 1965 Snoopy Hallmark ornament, red doghouse'."),
    "empty_alt_text": (
        "Icon/logo image has blank alt text", 1, 1, "hypothesis",
        "Blank alt is correct for pure decoration. If this icon is a link or button, it needs words.",
        "If it's a product or content photo, add alt text (same steps as above). If it's decoration, leave it."),
    "no_h1": (
        "No main heading (H1)", 2, 2, "verified",
        "The H1 tells Google what the page is about.",
        "Usually a theme setting: Online Store > Customize > that page's section > make sure the title/heading is shown. "
        "For pages with only images, add a 'Rich text' block with a heading."),
    "multiple_h1": (
        "More than one main heading (H1)", 1, 2, "hypothesis",
        "Not an error, but one clear H1 is a stronger signal. Often caused by the theme.",
        "Check the page in Customize. If a logo or banner is an H1, change the extra one to a smaller heading."),
    "heading_skip": (
        "Heading levels skip (e.g. H2 then H4)", 1, 2, "verified",
        "Screen readers use headings like a table of contents; gaps make it confusing.",
        "In the page text editor, change the skipped heading to the next level down (H2 > H3 > H4)."),
    "jsonld_invalid": (
        "Structured data is broken (bad JSON)", 3, 2, "verified",
        "Google ignores it completely, so no price/stock stars in search results.",
        "Usually from an app or a custom code snippet. Online Store > Themes > Edit code, or turn off the app that adds it. "
        "Test fixes at https://search.google.com/test/rich-results"),
    "product_schema_missing": (
        "Product page has no Product structured data", 3, 2, "verified",
        "Without it Google can't show price and 'In stock' in search results.",
        "Your theme normally adds this. If it's missing site-wide, the theme or an SEO app is overriding it. "
        "Check with https://search.google.com/test/rich-results"),
    "product_schema_incomplete": (
        "Product structured data is missing details", 2, 2, "verified",
        "Google wants name, image, price, currency and availability to show a rich result. Brand helps too.",
        "Missing brand = the Vendor field says 'Rescue Retro Relics' or is blank (the Vendor fixer tool handles this). "
        "Missing price/availability usually means a theme or app problem."),
    "org_schema_missing": (
        "Home page has no Organization/Store structured data", 1, 2, "hypothesis",
        "Helps Google show your logo and social links. Nice to have, not urgent.",
        "Add your logo and social links in Online Store > Customize > Theme settings. Many themes then output it."),
    "noindex": (
        "Page is hidden from Google (noindex)", 2, 1, "verified",
        "This page will never show up in search. Fine for thank-you pages, bad for products.",
        "If it should be found, check SEO apps or theme code for a 'noindex' setting on it."),
    "missing_canonical": (
        "No canonical link", 1, 2, "verified",
        "Shopify normally adds this; missing means a theme edit removed it.",
        "Online Store > Themes > Edit code > theme.liquid should contain {{ canonical_url }}."),
    "slow_page": (
        "Slow page (over 3 seconds to respond)", 2, 3, "hypothesis",
        "One slow run can be bad Wi-Fi. If it shows up every run, it's real.",
        "Re-run to confirm. If it repeats: compress big images, remove unused apps."),
    "broken_external_link": (
        "Link to another website doesn't work", 1, 1, "hypothesis",
        "Some sites (Facebook, Instagram, eBay) block robots, so check by clicking it yourself.",
        "Click it. If it's really dead, update or remove it."),
    "redirected_link": (
        "Link goes through a redirect", 1, 1, "verified",
        "Works, but slower and leaks a little SEO value.",
        "Update the link to point straight at the final address."),
}

KEY_PAGES = {
    "shipping": ("shipping",), "returns": ("return", "refund"), "contact": ("contact",),
    "privacy": ("privacy",), "terms": ("terms",), "about": ("about", "our story", "mission"),
}


def _is_product(url):
    return urlparse(url).path.startswith("/products/")


def _looks_cut_off(desc):
    d = desc.rstrip()
    if d.endswith(("...", "…")):
        return True
    return len(d) >= 145 and not re.search(r"[.!?)\"'”’]$", d)


def check_page(page):
    out = []
    add = lambda code, detail="": out.append({"code": code, "url": page.url, "detail": detail})
    if page.status != 200:
        add("page_error", page.error or f"HTTP {page.status}")
        return out
    if page.seconds > 3:
        add("slow_page", f"{page.seconds}s")
    robots = page.meta_robots
    if "noindex" in robots:
        add("noindex", robots)
    if not page.title:
        add("missing_title")
    elif len(page.title) > 70:
        add("title_too_long", f"{len(page.title)} chars: {page.title}")
    desc = page.meta_description
    if not desc:
        add("missing_meta_description")
    else:
        if _looks_cut_off(desc):
            add("meta_description_cut_off", f"…{desc[-60:]}")
        elif len(desc) > 160:
            add("meta_description_too_long", f"{len(desc)} chars")
        if len(desc) < 50:
            add("meta_description_too_short", f"{len(desc)} chars: {desc}")
    if not page.canonical:
        add("missing_canonical")
    h1s = [t for lvl, t in page.headings if lvl == 1]
    if not h1s:
        add("no_h1")
    elif len(h1s) > 1:
        add("multiple_h1", " | ".join(h1s[:3]))
    prev = 0
    for lvl, text in page.headings:
        if prev and lvl > prev + 1:
            add("heading_skip", f"H{prev} then H{lvl} '{text[:50]}'")
            break
        prev = lvl
    seen_imgs = set()
    for img in page.images:
        if img.tiny or not img.src or img.src in seen_imgs:
            continue
        seen_imgs.add(img.src)
        name = img.src.split("?")[0].rsplit("/", 1)[-1]
        decorative = re.search(r"(icon|logo|spacer|divider|arrow|placeholder|badge|\.svg$)", name.lower())
        if img.alt is None or (not img.alt.strip() and not decorative):
            # Shopify's text editor saves photos with alt="" by default, so blank counts as missing
            add("missing_alt_text", name + ("" if img.alt is None else " (alt is blank)"))
        elif not img.alt.strip():
            add("empty_alt_text", name)
    for err in page.jsonld_errors:
        add("jsonld_invalid", err)
    types = page.schema_types()
    if _is_product(page.url):
        products = [o for o in page.jsonld if "Product" in str(o.get("@type")) and "Group" not in str(o.get("@type"))]
        groups = [o for o in page.jsonld if "ProductGroup" in str(o.get("@type"))]
        products = products or groups
        if not products:
            add("product_schema_missing")
        else:
            add_missing = _product_gaps(products[0])
            if add_missing:
                add("product_schema_incomplete", "missing: " + ", ".join(add_missing))
    if urlparse(page.url).path in ("", "/") and not types & {"Organization", "OnlineStore", "Store", "LocalBusiness", "WebSite"}:
        add("org_schema_missing")
    return out


def _product_gaps(obj):
    missing = []
    for f in ("name", "image"):
        if not obj.get(f):
            missing.append(f)
    brand = obj.get("brand")
    brand_name = brand.get("name") if isinstance(brand, dict) else brand
    if not brand_name:
        missing.append("brand")
    elif str(brand_name).strip().lower() in ("rescue retro relics", "rescueretrorelics"):
        missing.append("brand (says 'Rescue Retro Relics', not the real maker)")
    offers = obj.get("offers") or obj.get("hasVariant")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if isinstance(offers, dict) and offers.get("offers"):  # ProductGroup > variant > offers
        offers = offers["offers"]
        offers = offers[0] if isinstance(offers, list) and offers else offers
    if not isinstance(offers, dict):
        missing.append("offers (price)")
    else:
        for f, label in (("price", "price"), ("priceCurrency", "currency"), ("availability", "availability")):
            if not offers.get(f) and not (f == "price" and offers.get("lowPrice")):
                missing.append(label)
    return missing


def check_site(pages, link_status, host):
    """Site-wide checks: broken links, duplicates, nav vs footer."""
    out = []
    ok_pages = [p for p in pages.values() if p.status == 200]

    # Duplicate titles / descriptions
    for attr, code in (("title", "duplicate_title"), ("meta_description", "duplicate_meta_description")):
        groups = defaultdict(list)
        for p in ok_pages:
            v = (getattr(p, attr) or "").strip().lower()
            if v:
                groups[v].append(p.url)
        for v, urls in groups.items():
            if len(urls) > 1:
                for u in urls:
                    out.append({"code": code, "url": u, "detail": f"shared with {len(urls) - 1} other page(s): '{v[:70]}'"})

    # Broken links and images (reported on the page that contains them;
    # menu/footer links show on every page, so those are reported once)
    sitewide = set()
    for p in ok_pages:
        reported = set()
        for link in p.links:
            key = link_key(link.href, host)
            if not key or key in reported or key not in link_status:
                continue
            if link.region != "body":
                if key in sitewide:
                    continue
                sitewide.add(key)
            status, err = link_status[key]
            if status == 200 or (300 <= status < 400):
                continue
            reported.add(key)
            internal = same_site(key, host)
            if not internal and status in (401, 403, 429, 999):
                continue  # robot-blocking sites, not real breakage
            code = "broken_internal_link" if internal else "broken_external_link"
            out.append({"code": code, "url": p.url,
                        "detail": f"'{link.text[:40] or '(no text)'}' -> {key} ({err or status})"
                                  + ("" if link.region == "body" else f" [in the {'footer' if link.region == 'footer' else 'main menu'}, on every page]")})
        for img in p.images:
            if not img.src or img.tiny or img.src in reported or img.src not in link_status:
                continue
            status, err = link_status[img.src]
            if status not in (200,) and not (300 <= status < 400):
                reported.add(img.src)
                out.append({"code": "broken_image", "url": p.url, "detail": f"{img.src} ({err or status})"})

    # Pages that redirect, and internal links pointing at them
    redirected = {u: p.redirected_to for u, p in pages.items() if p.redirected_to}
    for p in ok_pages:
        hits = sorted({link_key(l.href, host) for l in p.links} & set(redirected))
        for k in hits[:5]:
            out.append({"code": "redirected_link", "url": p.url, "detail": f"{k} -> {redirected[k]}"})

    out.extend(nav_vs_footer(pages, host))
    return out


def merge(page_findings, site_findings, sitemap_urls):
    """A dead page that's only reached by a link is already reported as a broken link; don't count it twice.
    Dead pages listed in the sitemap stay (Google is being told they exist)."""
    linked_dead = {f["detail"].split(" -> ", 1)[1].split(" (")[0] for f in site_findings
                   if f["code"] == "broken_internal_link" and " -> " in f["detail"]}
    keep = [f for f in page_findings
            if not (f["code"] == "page_error" and f["url"] in linked_dead and f["url"] not in sitemap_urls)]
    return keep + site_findings


def nav_vs_footer(pages, host):
    home = next((p for p in pages.values() if urlparse(p.url).path in ("", "/") and p.status == 200), None)
    if not home:
        return []
    out = []
    header, footer = defaultdict(set), defaultdict(set)
    for link in home.links:
        key = link_key(link.href, host)
        label = link.text.strip().lower()
        if not key or not label or len(label) > 40:
            continue
        if link.region == "footer":
            footer[label].add(key)
        elif link.region == "header":
            header[label].add(key)
    # Same name, different destination (within or across menus)
    for label in sorted(set(header) | set(footer)):
        dests = header.get(label, set()) | footer.get(label, set())
        if len(dests) > 1:
            where = []
            for d in sorted(dests):
                spots = [n for n, m in (("main menu", header), ("footer", footer)) if d in m.get(label, set())]
                where.append(f"{d} ({' + '.join(spots)})")
            out.append({"code": "nav_footer_mismatch", "url": home.url,
                        "detail": f"'{label}' goes to different places: " + "; ".join(where)})
    # Different names for the same key page (e.g. menu 'Terms' vs footer 'Terms of Service' pointing to two URLs)
    already = {frozenset(header.get(l, set()) | footer.get(l, set())) for l in set(header) | set(footer)}
    for key_name, words in KEY_PAGES.items():
        def dests(menu):
            return {d for lbl, ds in menu.items() if any(w in lbl for w in words) for d in ds}
        h, f = dests(header), dests(footer)
        if h and f and not (h & f) and frozenset(h | f) not in already:
            out.append({"code": "nav_footer_mismatch", "url": home.url,
                        "detail": f"{key_name} page: main menu -> {', '.join(sorted(h))}; footer -> {', '.join(sorted(f))}"})
    return out


def rank(findings):
    """Group findings into a fix list, most valuable/easiest first."""
    groups = defaultdict(list)
    for f in findings:
        groups[f["code"]].append(f)
    fixes = []
    for code, items in groups.items():
        title, impact, effort, label, why, how = ISSUES[code]
        score = impact * 3 - effort + (1 if label == "verified" else 0)
        fixes.append({"code": code, "title": title, "impact": impact, "effort": effort, "label": label,
                      "why": why, "how": how, "count": len(items), "pages": len({i["url"] for i in items}),
                      "items": items, "score": score, "quick_win": impact >= 2 and effort == 1})
    fixes.sort(key=lambda x: (-x["score"], -x["count"]))
    return fixes
