"""Vendor field fixer for Rescue Retro Relics.

Reads a Shopify product export CSV, proposes the real brand for each product's
Vendor field, and writes a preview spreadsheet plus Shopify import files in
batches of 50. It never talks to the store. Nothing changes until you import a
batch file yourself (or Claude applies an approved batch).

Run:  python vendor_fixer/vendor_fixer.py products_export.csv
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(HERE, "brands.csv")
STORE_NAME = "rescue retro relics"
BATCH_SIZE = 50
REVIEW_FLAG = {"medium": "Quick check", "low": "NEEDS MY REVIEW", "none": "NEEDS MY REVIEW"}


def load_rules(path):
    rules = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        kw = (row.get("keyword") or "").strip().lower()
        brand = (row.get("brand") or "").strip()
        if not kw or not brand:
            continue
        # Whole word or phrase; a plural "s" or "'s" on the end is fine.
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?:'?s)?(?![a-z0-9])")
        rules.append({
            "keyword": kw,
            "brand": brand,
            "confidence": (row.get("confidence") or "medium").strip().lower(),
            "note": (row.get("note") or "").strip(),
            "pattern": pattern,
        })
    return rules


def propose(title, product_type, tags, rules):
    """Return (brand, confidence, reason).

    Stronger rules win first (high, then medium, then low). Within the same
    strength, the title beats the product type, which beats the tags. Tags are
    often search keywords, so a maker found ONLY in the tags drops one level.
    """
    fields = [("title", title), ("type", product_type), ("tags", tags)]
    for tier in ("high", "medium", "low"):
        for name, text in fields:
            if not text:
                continue
            low_text = text.lower()
            for rule in rules:
                if rule["confidence"] != tier or not rule["pattern"].search(low_text):
                    continue
                conf = tier
                reason = f'"{rule["keyword"]}" in {name}'
                if name == "tags" and tier == "high":
                    conf = "medium"
                    reason += " only (tags can be search keywords)"
                if rule["note"]:
                    reason += f" ({rule['note']})"
                return rule["brand"], conf, reason
    return "Unbranded", "none", "No brand or character found. Best guess is Unbranded; type the real brand if you know it."


def read_products(path):
    """Shopify exports one row per variant/image. Keep the first row per Handle."""
    products, seen = [], set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {"Handle", "Title", "Vendor"} - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"This doesn't look like a Shopify product export. Missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            handle = (row.get("Handle") or "").strip()
            if not handle or handle in seen or not (row.get("Title") or "").strip():
                continue
            seen.add(handle)
            products.append({
                "handle": handle,
                "title": row["Title"].strip(),
                "vendor": (row.get("Vendor") or "").strip(),
                "type": (row.get("Type") or "").strip(),
                "tags": (row.get("Tags") or "").strip(),
                "status": (row.get("Status") or "").strip(),
            })
    return products


def build(products, rules, only_store_vendor=True):
    rows = []
    for p in products:
        if only_store_vendor and p["vendor"].lower() not in (STORE_NAME, ""):
            continue  # already has a real brand; leave it alone
        brand, conf, reason = propose(p["title"], p["type"], p["tags"], rules)
        rows.append({
            "Handle": p["handle"],
            "Title": p["title"],
            "Status": p["status"],
            "Current Vendor": p["vendor"],
            "Proposed Vendor": brand,
            "Confidence": conf,
            "Why": reason,
            "Needs Review": REVIEW_FLAG.get(conf, ""),
            "Approve (Y/N)": "Y" if conf == "high" else "",
        })
    order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    rows.sort(key=lambda r: (order.get(r["Confidence"], 9), r["Proposed Vendor"], r["Title"]))
    return rows


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_batches(out_dir, approved):
    """Shopify import files: Handle, Title, Vendor. 50 per file."""
    paths = []
    for i in range(0, len(approved), BATCH_SIZE):
        n = i // BATCH_SIZE + 1
        path = os.path.join(out_dir, f"import_batch_{n:02d}.csv")
        write_csv(path, [{"Handle": r["Handle"], "Title": r["Title"], "Vendor": r["Proposed Vendor"]}
                         for r in approved[i:i + BATCH_SIZE]], ["Handle", "Title", "Vendor"])
        paths.append(path)
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="Propose real brands for the Shopify Vendor field.")
    ap.add_argument("export_csv", help="Shopify product export (Products > Export > CSV)")
    ap.add_argument("--approved", help="Your edited preview file. Only rows marked Y become import batches.")
    ap.add_argument("--rules", default=DEFAULT_RULES, help="Brand rules file (default: brands.csv)")
    ap.add_argument("--out", default=os.path.join("data", "vendor_fixer"), help="Output folder")
    ap.add_argument("--all", action="store_true", help="Also look at products that already have a real vendor")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    products = read_products(args.export_csv)

    # Backup of every product's current vendor, before anything else happens.
    backup = os.path.join(args.out, f"backup_vendors_{stamp}.csv")
    write_csv(backup, [{"Handle": p["handle"], "Title": p["title"], "Vendor": p["vendor"]} for p in products],
              ["Handle", "Title", "Vendor"])

    if args.approved:
        with open(args.approved, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        approved = [r for r in rows if (r.get("Approve (Y/N)") or "").strip().upper().startswith("Y")
                    and (r.get("Proposed Vendor") or "").strip()]
        batches = write_batches(args.out, approved)
        print(f"Backup saved: {backup}")
        print(f"{len(approved)} approved changes -> {len(batches)} import file(s) of up to {BATCH_SIZE}:")
        for b in batches:
            print("  " + b)
        print("DRY RUN: nothing was changed in Shopify. Import one batch at a time (see README).")
        return 0

    rules = load_rules(args.rules)
    rows = build(products, rules, only_store_vendor=not args.all)
    preview = os.path.join(args.out, f"vendor_preview_{stamp}.csv")
    write_csv(preview, rows, list(rows[0].keys()) if rows else ["Handle"])

    counts = {}
    for r in rows:
        counts[r["Confidence"]] = counts.get(r["Confidence"], 0) + 1
    brands = {}
    for r in rows:
        if r["Proposed Vendor"]:
            brands[r["Proposed Vendor"]] = brands.get(r["Proposed Vendor"], 0) + 1

    print(f"Read {len(products)} products. {len(rows)} still say 'Rescue Retro Relics' (or blank).")
    print(f"  high confidence:   {counts.get('high', 0)}  (pre-marked Y)")
    print(f"  medium confidence: {counts.get('medium', 0)}  (quick look)")
    print(f"  low (franchise):   {counts.get('low', 0)}  (no maker named)")
    print(f"  no brand found:    {counts.get('none', 0)}  (suggested Unbranded)")
    print(f"NEEDS MY REVIEW:     {counts.get('low', 0) + counts.get('none', 0)}")
    print("Top proposed brands:")
    for b, n in sorted(brands.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n:4d}  {b}")
    print(f"Preview: {preview}")
    print(f"Backup:  {backup}")
    print("DRY RUN: nothing was changed in Shopify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
