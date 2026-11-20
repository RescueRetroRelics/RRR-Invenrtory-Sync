"""Item specifics for listings made through the Inventory API (which Stephanie can't edit
in Seller Hub). eBay's search (Cassini) buries listings missing specifics, so:

  build_sheet  -> CSV, one row per listing per missing required/recommended specific,
                  with eBay's allowed values and a title-based guess where one is obvious.
  apply_sheet  -> reads the filled-in sheet, checks every value against eBay's rules,
                  and (confirm=True only) writes them to the inventory item. eBay revises
                  the live listing automatically when its inventory item changes.

Nothing is written without confirm=True. Blank new_value rows are skipped.
"""

import csv
import re
from datetime import datetime
from pathlib import Path

from . import ebay_inventory as inv

SHEET_COLUMNS = ("sku", "listing_id", "title", "category", "specific", "need",
                 "current_value", "new_value", "guess_from", "allowed_values")
MAX_ALLOWED_SHOWN = 40


def _words(s):
    return " " + " ".join(re.findall(r"[a-z0-9]+", s.lower())) + " "


def guess(title, aspect):
    """An allowed value that appears word-for-word in the title (longest wins), else None."""
    t = _words(title)
    hits = [v for v in aspect["values"] if v and _words(v).strip() and _words(v) in t]
    if not hits:
        return None
    hits.sort(key=len, reverse=True)
    if len(hits) > 1 and len(hits[0]) == len(hits[1]):
        return None
    return hits[0]


def build_sheet(cfg, out_dir, include_optional=False, include_unpublished=False):
    offers, _ = inv.all_offers(cfg)
    aspects_by_cat, names, rows = {}, {}, []
    stats = {"listings": 0, "listings_missing_required": 0, "rows": 0, "guessed": 0, "no_category": 0}
    for o in offers:
        if o.get("status") != "PUBLISHED" and not include_unpublished:
            continue
        stats["listings"] += 1
        cat = o.get("categoryId")
        sku = o.get("sku")
        if not cat:
            stats["no_category"] += 1
            continue
        if cat not in aspects_by_cat:
            aspects_by_cat[cat] = inv.item_aspects(cfg, cat)
            names[cat] = inv.category_name(cfg, cat) or cat
        item = inv.get_inventory_item(cfg, sku).get("product", {})
        title = item.get("title") or o.get("_title", "")
        have = {k.lower(): v for k, v in (item.get("aspects") or {}).items()}
        missing_required = False
        for a in aspects_by_cat[cat]:
            if not a["required"] and a["usage"] != "RECOMMENDED" and not include_optional:
                continue
            current = have.get(a["name"].lower())
            if current:
                continue
            g = guess(title, a)
            missing_required |= a["required"]
            rows.append({
                "sku": sku,
                "listing_id": o.get("listing", {}).get("listingId", ""),
                "title": title,
                "category": names[cat],
                "specific": a["name"],
                "need": "REQUIRED" if a["required"] else a["usage"].lower(),
                "current_value": "",
                "new_value": g or "",
                "guess_from": "title - CHECK IT" if g else "",
                "allowed_values": ("ONLY: " if a["mode"] == "SELECTION_ONLY" else "e.g. ")
                                  + " | ".join(a["values"][:MAX_ALLOWED_SHOWN]),
            })
            stats["guessed"] += bool(g)
        stats["listings_missing_required"] += missing_required
    stats["rows"] = len(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"item_specifics_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return str(path), stats


def _split(value, multi):
    parts = [p.strip() for p in value.split("|")] if multi else [value.strip()]
    return [p for p in parts if p]


def apply_sheet(cfg, path, confirm=False):
    """Write filled-in specifics. confirm=False = dry run (checks everything, writes nothing)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by_sku = {}
    for r in rows:
        if (r.get("new_value") or "").strip():
            by_sku.setdefault(r["sku"], []).append(r)

    results, aspects_by_cat = [], {}
    for sku, todo in by_sku.items():
        base = {"sku": sku, "title": todo[0].get("title", "")[:60]}
        try:
            offers = [o for o in inv.offers_for_sku(cfg, sku) if o.get("categoryId")]
            if not offers:
                results.append({**base, "result": "FAILED", "error": "no eBay offer with a category for this SKU"})
                continue
            cat = offers[0]["categoryId"]
            if cat not in aspects_by_cat:
                aspects_by_cat[cat] = {a["name"].lower(): a for a in inv.item_aspects(cfg, cat)}
            rules = aspects_by_cat[cat]
            new, bad = {}, []
            for r in todo:
                a = rules.get(r["specific"].strip().lower())
                if not a:
                    bad.append(f'{r["specific"]}: not a specific for this category')
                    continue
                vals = _split(r["new_value"], a["multi"])
                if a["mode"] == "SELECTION_ONLY":
                    allowed = {v.lower(): v for v in a["values"]}
                    wrong = [v for v in vals if v.lower() not in allowed]
                    if wrong:
                        bad.append(f'{a["name"]}: {", ".join(wrong)} not allowed by eBay')
                        continue
                    vals = [allowed[v.lower()] for v in vals]
                if any(len(v) > 65 for v in vals):
                    bad.append(f'{a["name"]}: over 65 characters')
                    continue
                new[a["name"]] = vals
            if bad:
                results.append({**base, "result": "BAD VALUES", "error": "; ".join(bad), "ok_specifics": list(new)})
                continue
            if not confirm:
                results.append({**base, "result": "WOULD ADD", "specifics": new})
                continue
            item = inv.get_inventory_item(cfg, sku)
            item.setdefault("product", {})
            item["product"]["aspects"] = {**(item["product"].get("aspects") or {}), **new}
            inv.put_inventory_item(cfg, sku, item)   # eBay revises the live listing from this
            results.append({**base, "result": "ADDED", "specifics": new})
        except Exception as e:
            results.append({**base, "result": "FAILED", "error": str(e)[:300]})
    summary = {}
    for r in results:
        summary[r["result"]] = summary.get(r["result"], 0) + 1
    return {"dry_run": not confirm, "listings": len(by_sku), "summary": summary, "rows": results}
