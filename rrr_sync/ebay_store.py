"""eBay store categories (the shelves in Stephanie's eBay store, not eBay's own categories).

Why this exists: listings made through the Inventory API land in the store's "Other"
shelf unless the offer names a store category. This module:
  * reads the store's real category list (Trading API GetStore),
  * finds live listings sitting in "Other" (or anywhere) via GetSellerList,
  * writes a review sheet (CSV) with a suggested shelf per listing,
  * applies a reviewed sheet: Inventory-API listings through their offer
    (storeCategoryNames + re-publish), Seller Hub listings through ReviseItem.
    eBay refuses ReviseItem on Inventory-API listings, hence the two paths.
Nothing moves until a sheet is applied with confirm=True.
"""

import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ebay_inventory as inv
from .ebay import NS, _text, _trading_call, _xml_escape

OTHER_ID = "1"   # eBay's built-in "Other" store category
SHEET_COLUMNS = ("item_id", "sku", "title", "current_category", "new_category", "suggested_by")


def store_categories(cfg):
    """Every store category as {"id", "name", "path", "leaf"}. path = "/Parent/Child"."""
    root = _trading_call(cfg, "GetStore", "<CategoryStructureOnly>true</CategoryStructureOnly>")
    return parse_store_categories(root)


def parse_store_categories(root):
    out = []

    def walk(el, parent_path):
        for cat in el.findall("e:CustomCategory", NS) + el.findall("e:ChildCategory", NS):
            name = _text(cat, "e:Name")
            path = f"{parent_path}/{name}"
            children = cat.findall("e:ChildCategory", NS)
            out.append({"id": _text(cat, "e:CategoryID"), "name": name, "path": path, "leaf": not children})
            walk(cat, path)

    store = root.find("e:Store/e:CustomCategories", NS)
    if store is not None:
        walk(store, "")
    return out


def _resolve(categories, wanted):
    """Store category by ID, full path, or (unique) name, case-insensitive. Leaf only."""
    w = str(wanted).strip()
    wl = w.lower().strip("/")
    hits = [c for c in categories if c["id"] == w or c["path"].lower().strip("/") == wl]
    if not hits:
        hits = [c for c in categories if c["name"].lower() == wl]
    if len(hits) != 1:
        names = ", ".join(c["path"] for c in categories if c["leaf"])
        why = "matches more than one" if hits else "isn't one of your store categories"
        raise ValueError(f'"{wanted}" {why}. Your store categories: {names}')
    if not hits[0]["leaf"]:
        raise ValueError(f'"{hits[0]["path"]}" has sub-categories; pick one of those')
    return hits[0]


def resolve_store_category(cfg, wanted, categories=None):
    return _resolve(categories if categories is not None else store_categories(cfg), wanted)


def live_listings_with_store_category(cfg):
    """[{item_id, sku, title, store_category_id}] for every live listing (GetSellerList)."""
    now = datetime.now(timezone.utc)
    window = (f"<EndTimeFrom>{now:%Y-%m-%dT%H:%M:%S.000Z}</EndTimeFrom>"
              f"<EndTimeTo>{now + timedelta(days=119):%Y-%m-%dT%H:%M:%S.000Z}</EndTimeTo>")
    selectors = "".join(f"<OutputSelector>{s}</OutputSelector>" for s in (
        "ItemArray.Item.ItemID", "ItemArray.Item.Title", "ItemArray.Item.SKU",
        "ItemArray.Item.Storefront", "ItemArray.Item.SellingStatus.ListingStatus",
        "PaginationResult", "HasMoreItems"))
    items, page = [], 1
    while True:
        root = _trading_call(cfg, "GetSellerList", (
            f"{window}<DetailLevel>ReturnAll</DetailLevel>"
            f"<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>"
            f"{selectors}"))
        items.extend(parse_seller_list(root))
        if _text(root, "e:HasMoreItems").lower() != "true":
            return items
        page += 1


def parse_seller_list(root):
    out = []
    for item in root.findall("e:ItemArray/e:Item", NS):
        status = _text(item, "e:SellingStatus/e:ListingStatus", "Active")
        if status != "Active":
            continue
        out.append({
            "item_id": _text(item, "e:ItemID"),
            "sku": _text(item, "e:SKU"),
            "title": _text(item, "e:Title"),
            "store_category_id": _text(item, "e:Storefront/e:StoreCategoryID", OTHER_ID) or OTHER_ID,
        })
    return out


def suggest(title, categories):
    """Best store category for a title by whole-word matches on category names. None if unsure."""
    words = set(re.findall(r"[a-z0-9']+", title.lower()))
    scored = []
    for c in categories:
        if not c["leaf"] or c["id"] == OTHER_ID or c["name"].lower() == "other":
            continue
        cat_words = [w for w in re.findall(r"[a-z0-9']+", c["path"].lower()) if len(w) > 2]
        hits = sum(1 for w in cat_words if w in words or w.rstrip("s") in words)
        if hits:
            scored.append((hits, len(c["path"]), c))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1]))
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1] == scored[1][1]:
        return None   # tie: let Stephanie pick
    return scored[0][2]


def build_sheet(cfg, out_dir, only_other=True):
    """Write the review sheet. Returns (csv_path, counts)."""
    categories = store_categories(cfg)
    by_id = {c["id"]: c for c in categories}
    rows = []
    for l in live_listings_with_store_category(cfg):
        if only_other and l["store_category_id"] != OTHER_ID:
            continue
        current = by_id.get(l["store_category_id"])
        s = suggest(l["title"], categories)
        rows.append({
            "item_id": l["item_id"], "sku": l["sku"], "title": l["title"],
            "current_category": current["path"] if current else ("/Other" if l["store_category_id"] == OTHER_ID else l["store_category_id"]),
            "new_category": s["path"] if s else "",
            "suggested_by": "title keyword match - CHECK IT" if s else "",
        })
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / f"store_categories_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    counts = {"listings": len(rows), "suggested": sum(1 for r in rows if r["new_category"]),
              "blank": sum(1 for r in rows if not r["new_category"]),
              "store_categories": [c["path"] for c in categories if c["leaf"]]}
    return str(path), counts


def read_sheet(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _inventory_offer_for(cfg, row):
    """The Inventory-API offer behind a live listing, or None (Seller Hub listing)."""
    if not row.get("sku"):
        return None
    for o in inv.offers_for_sku(cfg, row["sku"]):
        if str(o.get("listing", {}).get("listingId", "")) == str(row["item_id"]):
            return o
    return None


def apply_sheet(cfg, path, confirm=False):
    """Move listings to the new_category in a reviewed sheet. Blank rows are skipped.

    confirm=False (default) is a dry run: checks every row, changes nothing.
    """
    categories = store_categories(cfg)
    results = []
    for row in read_sheet(path):
        wanted = (row.get("new_category") or "").strip()
        base = {"item_id": row.get("item_id"), "title": row.get("title", "")[:60]}
        if not wanted:
            results.append({**base, "result": "SKIPPED (blank)"})
            continue
        try:
            cat = _resolve(categories, wanted)
        except ValueError as e:
            results.append({**base, "result": "BAD CATEGORY", "error": str(e)[:300]})
            continue
        base["new_category"] = cat["path"]
        if not confirm:
            results.append({**base, "result": "WOULD MOVE"})
            continue
        try:
            offer = _inventory_offer_for(cfg, row)
            if offer:
                offer["storeCategoryNames"] = [cat["path"]]
                inv.update_offer(cfg, offer["offerId"], offer)
                inv.publish_offer(cfg, offer["offerId"])   # re-publish revises the live listing
                how = "offer"
            else:
                _trading_call(cfg, "ReviseItem", (
                    f"<Item><ItemID>{_xml_escape(row['item_id'])}</ItemID>"
                    f"<Storefront><StoreCategoryID>{cat['id']}</StoreCategoryID></Storefront></Item>"))
                how = "ReviseItem"
            results.append({**base, "result": "MOVED", "via": how})
        except Exception as e:
            results.append({**base, "result": "FAILED", "error": str(e)[:300]})
    summary = {}
    for r in results:
        summary[r["result"]] = summary.get(r["result"], 0) + 1
    return {"dry_run": not confirm, "summary": summary, "rows": results}
