"""Read the CSV exports Stephanie drops into exports/ (and the eBay orders the sync tool saves in data/).

Every file is recognised by its column headers, so file names don't matter.
Each loader returns plain dicts so the report code doesn't care where a sale came from.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

try:  # Windows Python often has no time-zone database; fall back to Eastern Standard Time.
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover
    LOCAL_TZ = timezone(timedelta(hours=-5))


@dataclass
class Sale:
    channel: str      # "Shopify" or "eBay"
    order_id: str
    day: date
    title: str
    sku: str
    qty: int
    amount: float     # item revenue (price x qty, after line discounts), no shipping/tax


@dataclass
class Product:
    handle: str
    title: str
    vendor: str
    product_type: str
    sku: str
    price: float
    qty: int | None   # None when the export has no inventory column
    status: str


@dataclass
class Listing:
    item_id: str
    title: str
    sku: str
    qty: int | None
    price: float
    start: date | None
    watchers: int | None


def money(value):
    if value is None:
        return 0.0
    s = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def to_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def parse_day(value):
    """Turn the date formats Shopify and eBay use into a local calendar date."""
    v = (value or "").strip()
    if not v:
        return None
    # ISO with Z / offset (eBay API, Shopify API)
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(LOCAL_TZ)
        return dt.date()
    except ValueError:
        pass
    # Shopify export: "2026-10-28 22:01:27 -0400" -> the date as written is already store-local
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    if m:
        return date.fromisoformat(m.group(1))
    # eBay Seller Hub: "Oct-28-26", "Oct 28, 2026", "10/28/2026", "28-Oct-26"
    cleaned = re.sub(r"\s+(PDT|PST|EDT|EST|MST|MDT|CST|CDT|GMT|UTC).*$", "", v)
    for fmt in ("%b-%d-%y", "%b-%d-%Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%y",
                "%b-%d-%y %H:%M:%S", "%b %d, %Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


# ---------- reading any CSV and working out what it is ----------

def _rows(path):
    """Yield (headers, rows) after skipping eBay's blank/preamble lines above the header."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        lines = list(csv.reader(f))
    for i, row in enumerate(lines[:15]):
        cells = [c.strip().lower() for c in row]
        if len([c for c in cells if c]) >= 3 and kind_of(cells):
            headers = [c.strip() for c in row]
            out = []
            for r in lines[i + 1:]:
                if not any(c.strip() for c in r):
                    continue
                out.append({h: (r[j] if j < len(r) else "") for j, h in enumerate(headers)})
            return headers, out
    return [], []


def kind_of(lower_headers):
    h = set(lower_headers)
    if "name" in h and "lineitem name" in h:
        return "shopify_orders"
    if "handle" in h and "variant price" in h:
        return "shopify_products"
    if "order_id" in h and "line_total" in h:
        return "ebay_orders_api"
    if "order number" in h and "item title" in h:
        return "ebay_orders_sellerhub"
    if "item number" in h and "title" in h and ({"available quantity", "current price", "start price"} & h):
        return "ebay_listings"
    return None


def _get(row, *names):
    lower = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return ""


def load_folder(paths):
    """Read every CSV given; return what was found plus notes about each file."""
    found = {"sales": [], "products": [], "listings": [], "files": []}
    seen_lines = set()
    for path in paths:
        headers, rows = _rows(path)
        kind = kind_of([h.lower() for h in headers]) if headers else None
        if not kind:
            found["files"].append((path.name, "skipped (not a file this tool knows)"))
            continue
        before = (len(found["sales"]), len(found["products"]), len(found["listings"]))
        if kind == "shopify_orders":
            _shopify_orders(rows, found, seen_lines)
        elif kind == "shopify_products":
            found["products"].extend(_shopify_products(rows))
        elif kind == "ebay_orders_api":
            _ebay_orders_api(rows, found, seen_lines)
        elif kind == "ebay_orders_sellerhub":
            _ebay_orders_sellerhub(rows, found, seen_lines)
        elif kind == "ebay_listings":
            found["listings"].extend(_ebay_listings(rows))
        after = (len(found["sales"]), len(found["products"]), len(found["listings"]))
        added = [a - b for a, b in zip(after, before)]
        label = {"shopify_orders": "Shopify orders", "shopify_products": "Shopify products",
                 "ebay_orders_api": "eBay orders (sync tool)", "ebay_orders_sellerhub": "eBay orders (Seller Hub)",
                 "ebay_listings": "eBay active listings"}[kind]
        found["files"].append((path.name, f"{label}: {sum(added)} rows read"))
    return found


def _add(sale, found, seen):
    key = (sale.channel, sale.order_id, sale.title.lower(), sale.sku)
    if key in seen:      # same sale in two overlapping exports
        return
    seen.add(key)
    found["sales"].append(sale)


def _shopify_orders(rows, found, seen):
    # Order-level fields (dates, status) are only on the first row of each order.
    orders = {}
    for r in rows:
        name = _get(r, "Name")
        o = orders.setdefault(name, {})
        for field in ("Created at", "Financial Status", "Cancelled at"):
            if _get(r, field) and not o.get(field):
                o[field] = _get(r, field)
    for r in rows:
        name = _get(r, "Name")
        o = orders.get(name, {})
        status = (o.get("Financial Status") or "").lower()
        if o.get("Cancelled at") or status in ("voided", "refunded", "expired"):
            continue
        day = parse_day(o.get("Created at"))
        title = _get(r, "Lineitem name")
        if not day or not title:
            continue
        qty = to_int(_get(r, "Lineitem quantity"), 1)
        amount = money(_get(r, "Lineitem price")) * qty - money(_get(r, "Lineitem discount"))
        _add(Sale("Shopify", name, day, title, _get(r, "Lineitem sku"), qty, round(amount, 2)), found, seen)


def _ebay_orders_api(rows, found, seen):
    for r in rows:
        if (_get(r, "payment_status") or "").upper() in ("FULLY_REFUNDED", "FAILED"):
            continue
        day = parse_day(_get(r, "created"))
        if not day:
            continue
        qty = to_int(_get(r, "quantity"), 1)
        item_cost = _get(r, "item_cost")
        amount = money(item_cost) if item_cost != "" else money(_get(r, "line_total"))
        _add(Sale("eBay", _get(r, "order_id"), day, _get(r, "title"), _get(r, "sku"), qty, amount), found, seen)


def _ebay_orders_sellerhub(rows, found, seen):
    for r in rows:
        title = _get(r, "Item Title")
        day = parse_day(_get(r, "Sale Date", "Paid On Date"))
        if not title or not day:
            continue
        qty = to_int(_get(r, "Quantity"), 1)
        amount = money(_get(r, "Sold For")) * qty
        order = _get(r, "Order Number", "Sales Record Number")
        _add(Sale("eBay", order, day, title, _get(r, "Custom Label"), qty, round(amount, 2)), found, seen)


def _shopify_products(rows):
    """One Product per handle. Variant rows repeat the handle with a blank title."""
    by_handle = {}
    has_qty = any("variant inventory qty" in (k.lower() for k in r) for r in rows[:1])
    for r in rows:
        handle = _get(r, "Handle")
        if not handle:
            continue
        p = by_handle.get(handle)
        if p is None:
            p = Product(handle, _get(r, "Title"), _get(r, "Vendor"), _get(r, "Type"),
                        _get(r, "Variant SKU"), money(_get(r, "Variant Price")),
                        0 if has_qty else None, (_get(r, "Status") or "active").lower())
            by_handle[handle] = p
        elif not p.title and _get(r, "Title"):
            p.title = _get(r, "Title")
        if has_qty and (_get(r, "Variant Price") or _get(r, "Variant SKU") or _get(r, "Variant Inventory Qty")):
            p.qty += max(0, to_int(_get(r, "Variant Inventory Qty"), 0))
    return [p for p in by_handle.values() if p.title]


def _ebay_listings(rows):
    out = []
    for r in rows:
        title = _get(r, "Title")
        if not title:
            continue
        qty_raw = _get(r, "Available quantity", "Quantity available")
        watchers = _get(r, "Watchers")
        out.append(Listing(
            _get(r, "Item number"), title, _get(r, "Custom label (SKU)", "Custom label"),
            to_int(qty_raw) if qty_raw != "" else None,
            money(_get(r, "Current price", "Start price")),
            parse_day(_get(r, "Start date")),
            to_int(watchers) if watchers != "" else None,
        ))
    return out
