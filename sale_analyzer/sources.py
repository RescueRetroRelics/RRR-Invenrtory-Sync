"""Load products and sales into two simple shapes.

product: {id, title, type, status, created (date|None), qty, price, cost, sku}
sale:    {channel, date, title, qty, unit_price (paid, or None), list_price (or None),
          sku, item_id, product_id (filled in by match_sales), match}

Everything here only READS. Nothing is ever written to Shopify or eBay.
"""

import csv
import json
from datetime import date, datetime
from pathlib import Path

from rrr_sync.matching import normalize

PRODUCTS_QUERY = """
query Products($cursor: String) {
  products(first: 250, after: $cursor, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId title productType vendor status createdAt totalInventory tracksInventory
      priceRangeV2 { minVariantPrice { amount } }
      variants(first: 1) { nodes { sku inventoryItem { unitCost { amount } } } }
    }
  }
}
"""

ORDERS_QUERY = """
query Orders($cursor: String, $q: String) {
  orders(first: 100, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      name createdAt cancelledAt displayFinancialStatus
      lineItems(first: 50) { nodes {
        title quantity sku
        originalUnitPriceSet { shopMoney { amount } }
        discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
        product { legacyResourceId }
      } }
    }
  }
}
"""

SKIP_FINANCIAL = {"REFUNDED", "VOIDED", "refunded", "voided"}


def _num(value):
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _day(value):
    """'2026-05-18T02:37:30Z' / '2026-05-18 02:37:30 -0400' -> date(2026, 5, 18)."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%b-%d-%y", "%b %d, %Y"):
        try:
            return datetime.strptime(text.split(" ")[0] if fmt == "%m/%d/%Y" else text, fmt).date()
        except ValueError:
            continue
    return None


# ---------- products ----------

def product_from_node(n):
    variants = (n.get("variants") or {}).get("nodes") or [{}]
    unit_cost = ((variants[0].get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
    return {
        "id": str(n.get("legacyResourceId") or n.get("id", "")).rsplit("/", 1)[-1],
        "title": n.get("title", ""),
        "type": (n.get("productType") or "").strip() or "(no type)",
        "status": (n.get("status") or "").upper(),
        "created": _day(n.get("createdAt")),
        "qty": int(n.get("totalInventory") or 0),
        "price": _num(((n.get("priceRangeV2") or {}).get("minVariantPrice") or {}).get("amount")),
        "cost": _num(unit_cost),
        "sku": (variants[0].get("sku") or "").strip(),
    }


def shopify_products_api(shop):
    out, cursor = [], None
    while True:
        conn = shop.graphql(PRODUCTS_QUERY, {"cursor": cursor})["products"]
        out.extend(product_from_node(n) for n in conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def products_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = data if isinstance(data, list) else data["data"]["products"]["nodes"]
    return [product_from_node(n) for n in nodes]


def products_csv(path):
    """Shopify admin Products > Export CSV. It has no 'created' date, so ages are unknown."""
    by_handle = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            handle = row.get("Handle", "")
            p = by_handle.get(handle)
            if p is None:
                if not row.get("Title"):
                    continue
                p = by_handle[handle] = {
                    "id": handle, "title": row["Title"],
                    "type": (row.get("Type") or "").strip() or "(no type)",
                    "status": (row.get("Status") or "ACTIVE").upper(),
                    "created": None, "qty": 0,
                    "price": _num(row.get("Variant Price")),
                    "cost": _num(row.get("Cost per item")),
                    "sku": (row.get("Variant SKU") or "").strip(),
                }
            p["qty"] += int(_num(row.get("Variant Inventory Qty")) or 0)
    return list(by_handle.values())


# ---------- Shopify sales ----------

def shopify_orders_api(shop, since):
    out, cursor = [], None
    while True:
        conn = shop.graphql(ORDERS_QUERY, {"cursor": cursor, "q": f"created_at:>={since}"})["orders"]
        for o in conn["nodes"]:
            if o.get("cancelledAt") or o.get("displayFinancialStatus") in SKIP_FINANCIAL:
                continue
            for li in o["lineItems"]["nodes"]:
                out.append({
                    "channel": "Shopify", "date": _day(o["createdAt"]), "title": li["title"],
                    "qty": int(li["quantity"] or 1),
                    "unit_price": _num(li["discountedUnitPriceAfterAllDiscountsSet"]["shopMoney"]["amount"]),
                    "list_price": _num(li["originalUnitPriceSet"]["shopMoney"]["amount"]),
                    "sku": li.get("sku") or "", "item_id": "",
                    "product_id": (li.get("product") or {}).get("legacyResourceId"),
                })
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]


def shopify_orders_csv(path):
    """Shopify admin Orders > Export CSV (one row per line item)."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("Cancelled at") or (row.get("Financial Status") or "") in SKIP_FINANCIAL:
                continue
            if not row.get("Lineitem name"):
                continue
            qty = int(_num(row.get("Lineitem quantity")) or 1)
            price = _num(row.get("Lineitem price"))
            discount = _num(row.get("Lineitem discount")) or 0.0
            out.append({
                "channel": "Shopify", "date": _day(row.get("Created at")),
                "title": row["Lineitem name"], "qty": qty,
                "unit_price": None if price is None else round(price - discount / qty, 2),
                "list_price": price, "sku": (row.get("Lineitem sku") or "").strip(),
                "item_id": "", "product_id": None,
            })
    return out


# ---------- eBay sales ----------

def ebay_orders_raw(orders):
    """Orders as the eBay Fulfillment API returns them (rrr_sync.ebay.orders_since)."""
    out = []
    for o in orders:
        if (o.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
            continue
        for li in o.get("lineItems", []):
            qty = int(li.get("quantity") or 1)
            cost = _num((li.get("lineItemCost") or {}).get("value"))
            out.append({
                "channel": "eBay", "date": _day(o.get("creationDate")), "title": li.get("title", ""),
                "qty": qty, "unit_price": None if cost is None else round(cost / qty, 2),
                "list_price": None, "sku": li.get("sku") or "",
                "item_id": li.get("legacyItemId") or "", "product_id": None,
            })
    return out


def ebay_orders_csv(path):
    """data/ebay_orders_since_*.csv from `python -m rrr_sync orders` (line_total may include shipping)."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if "CANCEL" in (row.get("payment_status", "") + row.get("fulfillment_status", "")).upper():
                continue
            qty = int(_num(row.get("quantity")) or 1)
            total = _num(row.get("line_total"))
            out.append({
                "channel": "eBay", "date": _day(row.get("created")), "title": row.get("title", ""),
                "qty": qty, "unit_price": None if total is None else round(total / qty, 2),
                "list_price": None, "sku": row.get("sku", ""), "item_id": row.get("item_id", ""),
                "product_id": None,
            })
    return out


def sniff_csv(path):
    """Which kind of export is this CSV? -> 'shopify_orders' | 'shopify_products' | 'ebay_orders' | None"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        header = next(csv.reader(f), [])
    if "Lineitem name" in header:
        return "shopify_orders"
    if "Handle" in header and "Variant Price" in header:
        return "shopify_products"
    if "line_item_id" in header and "line_total" in header:
        return "ebay_orders"
    return None


# ---------- matching sales to products ----------

def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def approved_ebay_titles(path=None):
    """{eBay item id: Shopify title} from the sync tool's reviewed data/matches.csv (rows marked Y)."""
    from rrr_sync.matching import MATCHES_FILE, load_matches
    out = {}
    for row in load_matches(path or MATCHES_FILE).values():
        if row.get("approved", "").strip().upper() == "Y" and row.get("shopify_title"):
            out.setdefault(row["ebay_item_id"], row["shopify_title"])
    return out


def match_sales(sales, products, item_titles=None, fuzzy_min=0.75, unique_min=0.6, unique_gap=0.2):
    """Fill sale['product_id'] and sale['match'].

    Order: Shopify product id, SKU, the sync tool's approved eBay match, exact title, then a
    close title (word overlap >= 0.75, or >= 0.6 when no other product comes close).
    """
    item_titles = item_titles or {}
    by_id = {p["id"]: p for p in products}
    by_sku = {}
    for p in products:
        if p["sku"]:
            by_sku.setdefault(p["sku"].lower(), []).append(p)
    by_title = {}
    for p in products:
        by_title.setdefault(normalize(p["title"]), []).append(p)
    tokens = [(p, set(normalize(p["title"]).split())) for p in products]
    # Shopify titles often lack the eBay "NEW 🎃" endings; ignore those words.
    filler = {"new", "nwt", "sealed", "free", "shipping", "licensed", "official"}

    for s in sales:
        if s.get("product_id") and s["product_id"] in by_id:
            s["match"] = "product_id"
            continue
        s["product_id"], s["match"] = None, "unmatched"
        sku = (s.get("sku") or "").lower()
        if sku and len(by_sku.get(sku, [])) == 1:
            s["product_id"], s["match"] = by_sku[sku][0]["id"], "sku"
            continue
        if s.get("sku") in by_id:  # eBay listing tool uses the Shopify product number as SKU
            s["product_id"], s["match"] = s["sku"], "sku=product"
            continue
        synced = by_title.get(normalize(item_titles.get(s.get("item_id"), "")), [])
        if s.get("item_id") and len(synced) == 1:
            s["product_id"], s["match"] = synced[0]["id"], "sync match"
            continue
        hits = by_title.get(normalize(s["title"]), [])
        if len(hits) == 1:
            s["product_id"], s["match"] = hits[0]["id"], "title"
            continue
        want = set(normalize(s["title"]).split()) - filler
        best, best_score, second = None, 0.0, 0.0
        for p, toks in tokens:
            score = _jaccard(want, toks - filler)
            if score > best_score:
                best, best_score, second = p, score, best_score
            elif score > second:
                second = score
        if best is not None and (best_score >= fuzzy_min
                                 or (best_score >= unique_min and best_score - second >= unique_gap)):
            s["product_id"], s["match"] = best["id"], f"close title ({best_score:.2f})"
    return sales
