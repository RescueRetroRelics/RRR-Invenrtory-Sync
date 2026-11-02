"""python -m sale_analyzer  ->  data/sale_analyzer/sale_report.html + CSVs

Read-only. Pulls what it can from Shopify and eBay with the same .env as the sync tool,
and also reads any export CSVs you drop into data/sale_analyzer_inputs/.
"""

import argparse
import glob
import sys
from datetime import date
from pathlib import Path

from rrr_sync.config import DATA_DIR, Config

from . import analyze, report, settings as settings_mod, sources

INPUTS_DIR = DATA_DIR / "sale_analyzer_inputs"
OUT_DIR = DATA_DIR / "sale_analyzer"


def load_all(args, notes):
    cfg = None if args.offline else Config.load()
    products, shopify_sales, ebay_sales = [], [], []
    inputs = Path(args.inputs)
    inputs.mkdir(parents=True, exist_ok=True)

    # Files first: anything in the inputs folder.
    for path in sorted(inputs.glob("*.json")):
        if "product" in path.name.lower():
            products = sources.products_json(path)
            print(f"  products:       {len(products)} from {path.name}")
    for path in sorted(inputs.glob("*.csv")):
        kind = sources.sniff_csv(path)
        if kind == "shopify_orders":
            rows = sources.shopify_orders_csv(path)
            shopify_sales += rows
            print(f"  Shopify sales:  {len(rows)} lines from {path.name}")
        elif kind == "shopify_products" and not products:
            products = sources.products_csv(path)
            print(f"  products:       {len(products)} from {path.name}")
            notes.append("Products came from a Shopify CSV export, which has no 'created' date, so item ages are unknown. "
                         "Fill in Shopify in .env so the tool can read ages straight from Shopify.")
        elif kind == "ebay_orders":
            rows = sources.ebay_orders_csv(path)
            ebay_sales += rows
            print(f"  eBay sales:     {len(rows)} lines from {path.name}")
        else:
            print(f"  skipped {path.name} (not a Shopify orders/products or eBay orders export)")

    if cfg is not None:
        shop = None
        try:
            from rrr_sync.shopify import Shopify
            cfg.require("shopify_domain")
            shop = Shopify(cfg)
        except Exception as e:
            print(f"  Shopify: not connected ({e}). Using files only.")
        if shop and not products:
            try:
                products = sources.shopify_products_api(shop)
                print(f"  products:       {len(products)} from Shopify")
            except Exception as e:
                print(f"  Shopify products: FAILED ({e})")
        if shop and not shopify_sales:
            try:
                shopify_sales = sources.shopify_orders_api(shop, args.since)
                print(f"  Shopify sales:  {len(shopify_sales)} lines from Shopify")
            except Exception as e:
                print("  Shopify orders: can't read them through the app (it probably lacks the read_orders\n"
                      "                  permission). Export them instead: Shopify admin > Orders > Export >\n"
                      f"                  All orders, CSV for Excel, and save it into {inputs}")
                notes.append("Shopify orders were not included (the app can't read orders; see README).")
        if not ebay_sales:
            try:
                from rrr_sync import ebay
                raw = ebay.orders_since(cfg, f"{args.since}T00:00:00.000Z")
                ebay_sales = sources.ebay_orders_raw(raw)
                print(f"  eBay sales:     {len(ebay_sales)} lines from eBay")
            except Exception as e:
                newest = sorted(glob.glob(str(DATA_DIR / "ebay_orders_since_*.csv")))
                if newest:
                    ebay_sales = sources.ebay_orders_csv(newest[-1])
                    print(f"  eBay sales:     {len(ebay_sales)} lines from {Path(newest[-1]).name} (live eBay failed: {e})")
                else:
                    print(f"  eBay: not connected ({e})")
    return products, shopify_sales, ebay_sales


def main(argv=None):
    p = argparse.ArgumentParser(prog="sale_analyzer", description="Suggest sale tiers from sales history (read-only)")
    p.add_argument("--since", default="2026-01-01", help="first sale date to include, YYYY-MM-DD")
    p.add_argument("--offline", action="store_true", help="use only the files in the inputs folder")
    p.add_argument("--inputs", default=str(INPUTS_DIR), help="folder with export files")
    p.add_argument("--out", default=str(OUT_DIR), help="where to write the report")
    p.add_argument("--today", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()

    print("Reading data (read-only)...")
    notes = []
    products, shopify_sales, ebay_sales = load_all(args, notes)
    if not products:
        print("\nNo products found. Fill in Shopify in .env, or put a Shopify products export in "
              f"{args.inputs}. See SALE_ANALYZER.md.")
        return 1
    since = date.fromisoformat(args.since)
    sales = [s for s in shopify_sales + ebay_sales if s["date"] and s["date"] >= since]
    sources.match_sales(sales, products, sources.approved_ebay_titles())

    unmatched = sum(1 for s in sales if not s.get("product_id"))
    if unmatched:
        notes.append(f"{unmatched} of {len(sales)} sale lines couldn't be tied to a current Shopify product "
                     "(usually sold-out items that were deleted, or titles that differ a lot). They count in the "
                     "monthly totals but not in sell-through or tiers.")
    notes.append("'Days to sell' is counted from the day the product was added to Shopify. Items listed on eBay "
                 "before they were added to Shopify really took longer than shown, and sales from before they "
                 "were on Shopify have no days-to-sell at all.")
    notes.append("eBay order prices can include shipping, so eBay revenue is approximate. Sell-through and "
                 "days-to-sell use unit counts and dates only, so they're not affected.")

    st = settings_mod.load()
    result = analyze.run(products, sales, st, today)
    out = Path(args.out)
    tiers_csv, sales_csv = report.write_csvs(result, sales, out)
    page = report.write_html(result, st, out, notes)

    t1, t2, t3 = result["tier_info"]["tier_days"]
    print(f"\n{result['units_total']} units sold since {args.since} ({result['matched']} of {len(sales)} lines matched to a product)")
    print(f"Tier start days: {t1} / {t2} / {t3}  ({result['tier_info']['source']})")
    for t, s in result["tier_summary"].items():
        print(f"  {report.TIER_NAMES[t]:<20} {s['items']:>4} items")
    print(f"\nReport:      {page}\nSuggestions: {tiers_csv}\nSales list:  {sales_csv}")
    print("Nothing was changed on Shopify or eBay.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
