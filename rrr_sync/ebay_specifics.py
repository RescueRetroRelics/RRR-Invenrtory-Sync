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


def validate(rules, specifics):
    """rules: {lowercase name: aspect}. specifics: {name: "value" | "a | b" | [values]}.
    Returns (clean {eBay name: [values]}, [problems])."""
    new, bad = {}, []
    for name, value in specifics.items():
        a = rules.get(str(name).strip().lower())
        if not a:
            bad.append(f"{name}: not a specific for this category")
            continue
        vals = [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) \
            else _split(str(value), a["multi"])
        if not vals:
            continue
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
    return new, bad


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
            new, bad = validate(aspects_by_cat[cat], {r["specific"]: r["new_value"] for r in todo})
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


# --- direct fixing, batch by batch (for Claude Desktop, no spreadsheet) ---------

def _strip_html(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def listings_to_fix(cfg, offset=0, limit=20):
    """A batch of live API listings with what each one is missing.

    Each entry: sku, offer_id, listing_id, title, category, store_category,
    shopify_description, have (specifics already set), missing (required/recommended
    specifics with allowed values). Listings missing nothing are left out.
    """
    from . import ebay_listings as el

    offers, _ = inv.all_offers(cfg)
    live = sorted((o for o in offers if o.get("status") == "PUBLISHED"), key=lambda o: o.get("sku", ""))
    aspects_by_cat, names, batch, scanned = {}, {}, [], 0
    for o in live[offset:]:
        if len(batch) >= limit:
            break
        scanned += 1
        cat, sku = o.get("categoryId"), o.get("sku")
        if cat not in aspects_by_cat:
            aspects_by_cat[cat] = inv.item_aspects(cfg, cat) if cat else []
            names[cat] = (inv.category_name(cfg, cat) if cat else None) or "NO CATEGORY"
        product = inv.get_inventory_item(cfg, sku).get("product", {})
        have = product.get("aspects") or {}
        have_l = {k.lower() for k in have}
        missing = [{
            "name": a["name"],
            "need": "REQUIRED" if a["required"] else "recommended",
            "only_these": a["mode"] == "SELECTION_ONLY",
            "multiple_ok": a["multi"],
            "values": a["values"][:25],
        } for a in aspects_by_cat[cat]
            if (a["required"] or a["usage"] == "RECOMMENDED") and a["name"].lower() not in have_l]
        shelf = (o.get("storeCategoryNames") or [None])[0]
        if not missing and shelf:
            continue
        try:
            shop = el.shopify_product(cfg, sku)
        except Exception:
            shop = None
        batch.append({
            "sku": sku, "offer_id": o.get("offerId"),
            "listing_id": o.get("listing", {}).get("listingId"),
            "title": product.get("title", ""),
            "category": names[cat],
            "store_category": shelf or "NONE (in Other)",
            "shopify_description": _strip_html(shop["description"])[:600] if shop else "",
            "have": have,
            "missing": missing,
        })
    next_offset = offset + scanned
    return {"listings": batch, "next_offset": next_offset if next_offset < len(live) else None,
            "total_live": len(live)}


def fix_listings(cfg, fixes, confirm=False):
    """fixes: [{"sku", "specifics": {name: value}, "store_category": "..."}].

    Every value is checked against eBay's rules; a listing with any bad value is skipped
    whole. confirm=False is a dry run. Specifics go on the inventory item (eBay revises the
    live listing from it); the store shelf goes on the offer, which is re-published.
    """
    from . import ebay_store

    cats = None
    aspects_by_cat, results = {}, []
    for f in fixes:
        sku = str(f.get("sku", "")).strip()
        base = {"sku": sku}
        try:
            offers = [o for o in inv.offers_for_sku(cfg, sku) if o.get("status") == "PUBLISHED"]
            if not offers:
                results.append({**base, "result": "FAILED", "error": "no live eBay listing for this SKU"})
                continue
            offer = offers[0]
            cat = offer.get("categoryId")
            if cat not in aspects_by_cat:
                aspects_by_cat[cat] = {a["name"].lower(): a for a in inv.item_aspects(cfg, cat)}
            new, bad = validate(aspects_by_cat[cat], f.get("specifics") or {})
            shelf = None
            if f.get("store_category"):
                if cats is None:
                    cats = ebay_store.store_categories(cfg)
                try:
                    shelf = ebay_store.resolve_store_category(cfg, f["store_category"], cats)["path"]
                except ValueError as e:
                    bad.append(str(e)[:200])
            if bad:
                results.append({**base, "result": "BAD VALUES", "error": "; ".join(bad)})
                continue
            if not confirm:
                results.append({**base, "result": "WOULD FIX", "specifics": new, "store_category": shelf})
                continue
            if new:
                item = inv.get_inventory_item(cfg, sku)
                item.setdefault("product", {})
                item["product"]["aspects"] = {**(item["product"].get("aspects") or {}), **new}
                inv.put_inventory_item(cfg, sku, item)
            if shelf and (offer.get("storeCategoryNames") or [None])[0] != shelf:
                offer["storeCategoryNames"] = [shelf]
                inv.update_offer(cfg, offer["offerId"], offer)
                inv.publish_offer(cfg, offer["offerId"])
            results.append({**base, "result": "FIXED", "specifics": new, "store_category": shelf,
                            "url": f"https://www.ebay.com/itm/{offer.get('listing', {}).get('listingId')}"})
        except Exception as e:
            results.append({**base, "result": "FAILED", "error": str(e)[:300]})
    summary = {}
    for r in results:
        summary[r["result"]] = summary.get(r["result"], 0) + 1
    return {"dry_run": not confirm, "summary": summary, "rows": results}
