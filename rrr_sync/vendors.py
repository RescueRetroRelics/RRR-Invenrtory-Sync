"""Set the correct Vendor on products (fixes the "Rescue Retro Relics" vendor mix-up).

Input: a CSV with a `new_vendor` column and one way to find the product:
`product_gid` (gid://shopify/Product/123), `product_id` (123) or `handle`.

Safety:
- Dry run unless --apply.
- Each product is fetched first. Products already showing the new vendor are skipped.
- The old vendor of every changed product is saved to data/vendor_backup.csv before it changes,
  so it can be undone.
- Only the Vendor field changes.
- Writes data/vendor_results.csv with every product's GID, title, old and new vendor, so it can
  be matched to eBay listings.
"""

import csv
from pathlib import Path

from .config import ROOT

DATA = ROOT / "data"

BY_ID = """
query Get($id: ID!) { product(id: $id) { id title handle vendor } }
"""
BY_HANDLE = """
query Get($q: String!) { products(first: 1, query: $q) { nodes { id title handle vendor } } }
"""
UPDATE = """
mutation Update($input: ProductInput!) {
  productUpdate(input: $input) { product { id vendor } userErrors { field message } }
}
"""


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for i, r in enumerate(rows, 2):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        vendor = r.get("new_vendor") or r.get("vendor")
        gid = r.get("product_gid") or ""
        if not gid and r.get("product_id", "").isdigit():
            gid = f"gid://shopify/Product/{r['product_id']}"
        if not vendor or not (gid or r.get("handle")):
            out.append({"row": i, "error": "needs new_vendor and product_gid/product_id/handle"})
            continue
        out.append({"row": i, "gid": gid, "handle": r.get("handle", ""), "vendor": vendor})
    return out


def fetch(shopify, row):
    if row["gid"]:
        return shopify.graphql(BY_ID, {"id": row["gid"]})["product"]
    nodes = shopify.graphql(BY_HANDLE, {"q": f"handle:'{row['handle']}'"})["products"]["nodes"]
    return nodes[0] if nodes and nodes[0]["handle"] == row["handle"] else None


def run(shopify, csv_path, do_apply=False, log=print, out_dir=DATA):
    rows = read_rows(csv_path)
    mode = "APPLYING" if do_apply else "DRY RUN (add --apply to make changes)"
    log(f"Vendor update: {len(rows)} rows from {csv_path}. {mode}")
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True)
    results, backup, errors, changed = [], [], [], 0
    for row in rows:
        if "error" in row:
            errors.append(f"row {row['row']}: {row['error']}")
            continue
        p = fetch(shopify, row)
        if not p:
            errors.append(f"row {row['row']}: product not found ({row['gid'] or row['handle']})")
            continue
        status = "unchanged" if p["vendor"] == row["vendor"] else ("updated" if do_apply else "would update")
        if p["vendor"] != row["vendor"]:
            log(f"  {p['title'][:60]}: '{p['vendor']}' -> '{row['vendor']}'")
            if do_apply:
                backup.append({"product_gid": p["id"], "handle": p["handle"], "vendor": p["vendor"]})
                with open(out_dir / "vendor_backup.csv", "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=["product_gid", "handle", "vendor"])
                    w.writeheader()
                    w.writerows(backup)
                errs = shopify.graphql(UPDATE, {"input": {"id": p["id"], "vendor": row["vendor"]}})["productUpdate"]["userErrors"]
                if errs:
                    status = "error"
                    errors.extend(f"{p['title']}: {e['message']}" for e in errs)
            if status != "error":
                changed += 1
        results.append({"product_gid": p["id"], "handle": p["handle"], "title": p["title"],
                         "old_vendor": p["vendor"], "new_vendor": row["vendor"], "status": status})
    with open(out_dir / "vendor_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["product_gid", "handle", "title", "old_vendor", "new_vendor", "status"])
        w.writeheader()
        w.writerows(results)
    log(f"{'Updated' if do_apply else 'Would update'} {changed} products. Results: {out_dir / 'vendor_results.csv'}")
    for e in errors:
        log("  ERROR " + e)
    return changed, errors
