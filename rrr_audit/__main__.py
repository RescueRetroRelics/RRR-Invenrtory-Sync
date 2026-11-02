"""python -m rrr_audit  -> crawls rescueretrorelics.com and writes a fix list. Read-only."""
import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from .checks import check_page, check_site, merge, rank
from .crawler import Crawler
from .report import compare, previous_run, write_all

DEFAULT_SITE = "https://rescueretrorelics.com"


def wins(pages, findings):
    ok = [p for p in pages.values() if p.status == 200]
    if not ok:
        return []
    codes = {f["code"] for f in findings}
    out = []
    checks = [
        ("broken_internal_link", "No broken links between your own pages"),
        ("broken_image", "No broken images"),
        ("nav_footer_mismatch", "Main menu and footer agree"),
        ("jsonld_invalid", "Structured data is valid JSON everywhere"),
        ("product_schema_missing", "Every product page has Product structured data"),
        ("missing_title", "Every page has a title"),
        ("no_h1", "Every page has a main heading"),
        ("missing_alt_text", "Every image has alt text"),
    ]
    if all(p.status == 200 for p in pages.values()):
        out.append("Every page loaded")
    for code, text in checks:
        if code not in codes:
            out.append(text)
    with_desc = sum(1 for p in ok if p.meta_description)
    out.append(f"{with_desc} of {len(ok)} pages have a meta description")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rrr_audit", description="Read-only SEO + broken link audit.")
    ap.add_argument("--site", default=DEFAULT_SITE, help="site to audit (default: %(default)s)")
    ap.add_argument("--max-pages", type=int, default=400, help="page limit (default 400; important pages go first)")
    ap.add_argument("--full", action="store_true", help="check every page, no limit (slower)")
    ap.add_argument("--no-external", action="store_true", help="don't check links to other websites")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between requests (be polite to the store)")
    ap.add_argument("--out", default="site_audit_reports", help="folder for reports")
    ap.add_argument("--no-open", action="store_true", help="don't open the report when done")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # old Windows consoles choke on emoji
        except Exception:
            pass

    site = args.site.rstrip("/")
    if not site.startswith("http"):
        site = "https://" + site
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = Path(args.out) / stamp
    print(f"🐾 Auditing {site} (read-only, nothing gets changed)")

    crawler = Crawler(site, max_pages=10**7 if args.full else args.max_pages, delay=args.delay)
    pages = crawler.crawl()
    if not any(p.status == 200 for p in pages.values()):
        print("\n❌ Couldn't load the site at all. Check your internet connection and try again.")
        first = next(iter(pages.values()), None)
        if first:
            print(f"   ({first.url}: {first.error or first.status})")
        return 1
    link_status = crawler.check_links(check_external=not args.no_external)

    page_findings = []
    for p in pages.values():
        page_findings += check_page(p)
    findings = merge(page_findings, check_site(pages, link_status, crawler.host), crawler.sitemap_urls)
    fixes = rank(findings)

    meta = {"when": datetime.now().strftime("%A %B %d, %Y %I:%M %p"), "pages": len(pages),
            "links": len(link_status), "skipped": crawler.skipped_for_limit,
            "compare": compare(previous_run(args.out, out_dir), findings), "wins": wins(pages, findings)}
    report = write_all(out_dir, site, pages, fixes, findings, meta)
    print()
    print((out_dir / "summary.txt").read_text(encoding="utf-8")[:2500])
    print(f"✅ Done. Report: {report.resolve()}")
    if not args.no_open:
        try:
            webbrowser.open(report.resolve().as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
