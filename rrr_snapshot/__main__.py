"""Weekly business snapshot: python -m rrr_snapshot

Reads the CSV exports in exports/ (and eBay orders the sync tool saved in data/),
then writes reports/snapshot_<week>.html plus CSVs. Read-only: it never changes the store or eBay.
"""

import argparse
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from . import loaders, report

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = ROOT / "exports"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
EBAY_TOKENS = ROOT / ".secrets" / "ebay_tokens.json"


def pull_ebay_orders(since):
    """Save fresh eBay orders with the sync tool, if eBay is already connected. Read-only."""
    from rrr_sync import ebay, orders
    from rrr_sync.config import Config
    found = ebay.orders_since(Config.load(), f"{since.isoformat()}T00:00:00.000Z")
    csv_path, _ = orders.export(found, since.isoformat())
    return len(found), csv_path


def main(argv=None):
    p = argparse.ArgumentParser(prog="rrr_snapshot", description="RRR weekly business snapshot")
    p.add_argument("--week-ending", help="last day of the week to report, YYYY-MM-DD (default: last Sunday)")
    p.add_argument("--slow-days", type=int, default=60, help="no sale in this many days = slow mover (default 60)")
    p.add_argument("--exports", default=str(EXPORTS), help="folder with your CSV exports (default: exports)")
    p.add_argument("--no-ebay-pull", action="store_true", help="don't fetch eBay orders, only use files")
    args = p.parse_args(argv)

    today = date.today()
    week_end = date.fromisoformat(args.week_ending) if args.week_ending else report.default_week_end(today)

    exports = Path(args.exports)
    exports.mkdir(exist_ok=True)

    if not args.no_ebay_pull and EBAY_TOKENS.exists():
        since = week_end - timedelta(days=max(args.slow_days, 35) + 7)
        try:
            n, path = pull_ebay_orders(since)
            print(f"eBay: pulled {n} orders since {since} -> {path.name}")
        except Exception as e:  # keep going with whatever files exist
            print(f"eBay: couldn't pull orders ({e}). Using files only.")

    paths = sorted(exports.glob("*.csv")) + sorted(DATA.glob("ebay_orders_since_*.csv"))
    if not paths:
        print(f"No CSV files found in {exports}. See README: 'What to export'.")
        return 1

    found = loaders.load_folder(paths)
    for name, what in found["files"]:
        print(f"  {name}: {what}")
    if not found["sales"]:
        print("No sales found in those files. Check you exported Orders (not Products) for the dates you want.")
        return 1

    ctx = report.build(found, today, week_end, args.slow_days)
    ws, we = ctx["week"]

    REPORTS.mkdir(exist_ok=True)
    slow_csv = REPORTS / f"slow_movers_{we}.csv"
    ctx["slow_csv"] = f"reports/{slow_csv.name}"
    html_path = REPORTS / f"snapshot_{we}.html"
    report.write_html(html_path, ctx)
    shutil.copyfile(html_path, REPORTS / "latest.html")
    report.write_csv(REPORTS / f"top_sellers_30d_{we}.csv", report.top_sellers(
        found["sales"], report.ProductIndex(found["products"]), we - timedelta(days=29), we, limit=0),
        ["title", "units", "sales", "channels"])
    if ctx["have_products"]:
        report.write_csv(slow_csv, ctx["slow"], ["title", "vendor", "type", "price", "qty", "tied_up",
                                                 "last_sold", "ebay_item", "ebay_listed_days", "ebay_watchers"])

    t, prev = ctx["this_week"]["All"], ctx["last_week"]["All"]
    print(f"\nWeek {ws:%b %d} - {we:%b %d}: ${t['sales']:,.2f} from {t['orders']} orders, {t['units']} items "
          f"(last week ${prev['sales']:,.2f}, {report.pct_change(t['sales'], prev['sales'])})")
    for ch in ("Shopify", "eBay"):
        c = ctx["this_week"][ch]
        print(f"  {ch:8} ${c['sales']:,.2f}  {c['orders']} orders")
    if ctx["top_week"]:
        print(f"  Top seller: {ctx['top_week'][0]['title']} (${ctx['top_week'][0]['sales']:,.2f})")
    if ctx["have_products"]:
        print(f"  Slow movers: {len(ctx['slow'])} items, no sale in {args.slow_days} days")
    print(f"\nReport: {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
