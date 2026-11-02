"""Inventory reconciliation: Shopify vs eBay, read-only.

Reads a Shopify product export (CSV) and the eBay active listings (a Seller Hub
CSV, or straight from eBay when the tool's eBay login is set up), matches them,
and writes a report. It never changes anything on Shopify or eBay.

Matching order for each eBay listing:
  1. same SKU on both sides
  2. eBay SKU is the Shopify product number (listings made by the Claude listing tool)
  3. identical title (ignoring case and punctuation)
  4. close title (score >= FUZZY_MIN), marked "check" so a human looks at it
"""

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .matching import _score, normalize

FUZZY_MIN = 0.80
STORE_URL = "https://rescueretrorelics.com/products/"


@dataclass
class ShopItem:
    key: str              # variant GID when known, otherwise handle + options
    handle: str
    title: str            # product title + variant name when not "Default Title"
    sku: str = ""
    qty: int | None = None
    cost: float | None = None
    price: float | None = None
    status: str = ""      # active / draft / archived
    vendor: str = ""
    product_gid: str = ""
    variant_gid: str = ""
    units_sold: int = 0


@dataclass
class EbayItem:
    item_id: str
    title: str
    sku: str = ""
    qty: int = 0
    price: float | None = None
    units_sold: int = 0
    variation: str = ""
    match: ShopItem | None = None
    method: str = ""
    score: float = 0.0

    @property
    def key(self):
        return f"{self.item_id}:{self.sku}" if self.variation else self.item_id


@dataclass
class Report:
    shop: list
    ebay: list
    notes: list = field(default_factory=list)
    sales_source: str = ""


# ---------------------------------------------------------------- CSV helpers

def _read_csv(path, must_have):
    """Read a CSV whose header row contains `must_have` (skips any banner lines above it)."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        lines = list(csv.reader(f))
    for i, row in enumerate(lines[:30]):
        if any(cell.strip().lower() == must_have.lower() for cell in row):
            header = [c.strip() for c in row]
            return [dict(zip(header, r)) for r in lines[i + 1:] if any(c.strip() for c in r)]
    raise ValueError(f"{Path(path).name}: couldn't find a '{must_have}' column. Is this the right file?")


def _get(row, *names):
    for n in names:
        for k, v in row.items():
            if k and k.strip().lower() == n.lower():
                return (v or "").strip()
    return ""


def _num(text):
    text = re.sub(r"[^0-9.\-]", "", text or "")
    try:
        return float(text) if text not in ("", "-", ".") else None
    except ValueError:
        return None


def _int(text):
    n = _num(text)
    return int(n) if n is not None else None


def _gid(kind, raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("gid://") else f"gid://shopify/{kind}/{raw}"


# ---------------------------------------------------------------- loaders

def load_shopify_products_csv(path, inventory_csv=None):
    """Shopify admin > Products > Export (CSV). One ShopItem per variant."""
    rows = _read_csv(path, "Handle")
    inventory = _load_shopify_inventory_csv(inventory_csv) if inventory_csv else {}
    items, product = [], {}
    for row in rows:
        handle = _get(row, "Handle")
        if _get(row, "Title"):
            product = {
                "handle": handle,
                "title": _get(row, "Title"),
                "status": _get(row, "Status").lower() or ("active" if _get(row, "Published").lower() == "true" else ""),
                "vendor": _get(row, "Vendor"),
                "gid": _gid("Product", _get(row, "ID", "Product ID")),
            }
        if handle != product.get("handle"):
            continue
        options = [_get(row, f"Option{i} Value") for i in (1, 2, 3)]
        is_variant = any(options) or _get(row, "Variant Price") or _get(row, "Variant SKU")
        if not is_variant:
            continue  # extra image row
        options = [o for o in options if o]
        title = product["title"]
        if options and options not in (["Default Title"], ["Default"]):
            title += " " + " / ".join(options)
        variant_gid = _gid("ProductVariant", _get(row, "Variant ID"))
        qty = _int(_get(row, "Variant Inventory Qty"))
        inv_key = (handle, tuple(options))
        if inv_key in inventory:
            qty = inventory[inv_key]
        items.append(ShopItem(
            key=variant_gid or f"{handle}|{'/'.join(options)}",
            handle=handle, title=title, sku=_get(row, "Variant SKU"), qty=qty,
            cost=_num(_get(row, "Cost per item")), price=_num(_get(row, "Variant Price")),
            status=product["status"], vendor=product["vendor"],
            product_gid=product["gid"], variant_gid=variant_gid,
        ))
    return items


def _load_shopify_inventory_csv(path):
    """Shopify admin > Products > Inventory > Export. {(handle, options): available}."""
    out = {}
    for row in _read_csv(path, "Handle"):
        options = tuple(o for o in (_get(row, f"Option{i} Value") for i in (1, 2, 3)) if o)
        qty = _int(_get(row, "Available", "On hand"))
        if qty is not None:
            key = (_get(row, "Handle"), options)
            out[key] = out.get(key, 0) + qty
    return out


def load_ebay_csv(path):
    """eBay Seller Hub > Reports > Downloads > Active listings (CSV)."""
    items = []
    for row in _read_csv(path, "Title"):
        item_id = _get(row, "Item number", "Item ID", "ItemID")
        title = _get(row, "Title")
        if not item_id or not title:
            continue
        variation = _get(row, "Variation details", "Variation")
        items.append(EbayItem(
            item_id=item_id,
            title=f"{title} {variation}".strip() if variation else title,
            sku=_get(row, "Custom label (SKU)", "Custom label", "SKU"),
            qty=_int(_get(row, "Available quantity", "Quantity available", "Quantity")) or 0,
            price=_num(_get(row, "Current price", "Start price", "Price")),
            units_sold=_int(_get(row, "Sold quantity", "Quantity sold")) or 0,
            variation=variation,
        ))
    return items


def load_ebay_live(cfg):
    from . import ebay
    return [EbayItem(item_id=l.item_id, title=l.title, sku=l.sku, qty=l.available,
                     variation="yes" if l.variation else "")
            for l in ebay.active_listings(cfg)]


def ebay_sales_live(cfg, days=365):
    """{legacy item id: units sold} from paid, non-cancelled eBay orders."""
    from . import ebay
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    sold = Counter()
    for order in ebay.orders_since(cfg, since):
        if (order.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
            continue
        for line in order.get("lineItems", []):
            sold[str(line.get("legacyItemId", ""))] += int(line.get("quantity", 0) or 0)
    return sold


def load_shopify_orders_csv(path):
    """Shopify admin > Orders > Export. Returns (by_sku, by_title) unit counts."""
    by_sku, by_title = Counter(), Counter()
    for row in _read_csv(path, "Lineitem name"):
        if _get(row, "Financial Status").lower() in ("refunded", "voided"):
            continue
        qty = _int(_get(row, "Lineitem quantity")) or 0
        sku = _get(row, "Lineitem sku")
        if sku:
            by_sku[sku.lower()] += qty
        by_title[normalize(re.sub(r"\s+-\s+Default Title$", "", _get(row, "Lineitem name")))] += qty
    return by_sku, by_title


# ---------------------------------------------------------------- matching

def match(ebay_items, shop_items):
    """Attach the best Shopify item to every eBay listing (several listings may
    point at the same Shopify item: that's how duplicates are found)."""
    live = [s for s in shop_items if s.status != "archived"]
    by_sku, by_title, by_product_no = defaultdict(list), defaultdict(list), {}
    for s in live:
        if s.sku:
            by_sku[s.sku.lower()].append(s)
        by_title[normalize(s.title)].append(s)
        if s.product_gid:
            by_product_no.setdefault(s.product_gid.rsplit("/", 1)[-1], s)
    tokens = {id(s): set(normalize(s.title).split()) for s in live}
    norm = {id(s): normalize(s.title) for s in live}
    index = defaultdict(set)
    for s in live:
        for t in tokens[id(s)]:
            index[t].add(id(s))
    by_id = {id(s): s for s in live}

    for e in ebay_items:
        sku_hits = by_sku.get(e.sku.lower(), []) if e.sku else []
        title_hits = by_title.get(normalize(e.title), [])
        if len(sku_hits) == 1:
            e.match, e.method, e.score = sku_hits[0], "sku", 1.0
        elif e.sku and e.sku in by_product_no:
            e.match, e.method, e.score = by_product_no[e.sku], "shopify_id", 1.0
        elif len(title_hits) == 1:
            e.match, e.method, e.score = title_hits[0], "exact_title", 1.0
        else:
            n = normalize(e.title)
            n_tokens = set(n.split())
            shared = Counter(sid for t in n_tokens for sid in index.get(t, ()))
            candidates = [sid for sid, c in shared.items() if c >= 2] or list(shared)
            best = max(((_score(n, norm[sid], n_tokens, tokens[sid]), by_id[sid]) for sid in candidates),
                       key=lambda p: p[0], default=(0.0, None))
            if best[1] is not None and best[0] >= FUZZY_MIN:
                e.match, e.method, e.score = best[1], "fuzzy", round(best[0], 3)
            else:
                e.method = "unmatched"
    return ebay_items


def add_sales(report, ebay_sold=None, shopify_orders=None):
    """Fill units_sold on Shopify items (Shopify orders + eBay sales via the match)."""
    sources = []
    if shopify_orders:
        by_sku, by_title = shopify_orders
        for s in report.shop:
            s.units_sold += by_sku.get(s.sku.lower(), 0) if s.sku else 0
            if not s.sku or not by_sku.get(s.sku.lower()):
                s.units_sold += by_title.get(normalize(s.title), 0)
        sources.append("Shopify orders export")
    if ebay_sold is not None:
        for e in report.ebay:
            e.units_sold = max(e.units_sold, ebay_sold.get(e.item_id, 0))
        sources.append("eBay orders (last 12 months)")
    elif any(e.units_sold for e in report.ebay):
        sources.append("eBay 'Sold quantity' column")
    for e in report.ebay:
        if e.match and e.units_sold:
            e.match.units_sold += e.units_sold
    report.sales_source = " + ".join(sources)


# ---------------------------------------------------------------- findings

def findings(report):
    shop, ebay = report.shop, report.ebay
    listed = defaultdict(list)
    for e in ebay:
        if e.match:
            listed[id(e.match)].append(e)

    only_shopify = [s for s in shop if s.status == "active" and (s.qty or 0) > 0 and id(s) not in listed]
    only_shopify.sort(key=lambda s: (-(s.qty or 0) * (s.price or 0), s.title))
    only_ebay = sorted((e for e in ebay if not e.match), key=lambda e: e.title.lower())

    qty_rows = []
    for s in shop:
        group = listed.get(id(s))
        if not group or s.qty is None:
            continue
        ebay_total = sum(e.qty for e in group)
        if ebay_total > s.qty:
            risk = "OVERSELL RISK: eBay offers more than Shopify has"
        elif s.qty > 0 and ebay_total == 0:
            risk = "eBay shows 0 but Shopify has stock (listing hidden)"
        elif ebay_total < s.qty:
            risk = "eBay shows fewer (fine if you keep eBay at 1)"
        else:
            continue
        qty_rows.append((risk, s, group, ebay_total))
    rank = lambda risk: 0 if risk.startswith("OVERSELL") else 1 if risk.startswith("eBay shows 0") else 2
    qty_rows.sort(key=lambda r: (rank(r[0]), -abs(r[3] - (r[1].qty or 0)), r[1].title))

    dupes = [(s, group) for s in shop if len(group := listed.get(id(s), [])) > 1]
    dupes.sort(key=lambda d: (-len(d[1]), d[0].title))
    # Same eBay title listed more than once but not matched to Shopify still counts.
    title_groups = defaultdict(list)
    for e in only_ebay:
        title_groups[normalize(e.title)].append(e)
    unmatched_dupes = [g for g in title_groups.values() if len(g) > 1]

    no_cost = [s for s in shop if s.status != "archived" and not s.cost]
    if report.sales_source:
        top = sorted((s for s in no_cost if s.units_sold > 0), key=lambda s: (-s.units_sold, s.title))
        cost_basis = "units sold"
    else:
        top = sorted((s for s in no_cost if (s.qty or 0) > 0),
                     key=lambda s: (-(s.qty or 0) * (s.price or 0), s.title))
        cost_basis = "stock value (no sales data given)"

    check = sorted((e for e in ebay if e.method == "fuzzy"), key=lambda e: e.score)
    return {
        "only_shopify": only_shopify, "only_ebay": only_ebay, "qty": qty_rows,
        "dupes": dupes, "unmatched_dupes": unmatched_dupes,
        "no_cost": top, "no_cost_all": no_cost, "cost_basis": cost_basis, "check": check,
    }


# ---------------------------------------------------------------- output

def _money(x):
    return "" if x is None else f"{x:.2f}"


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_reports(report, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = findings(report)

    issues = defaultdict(list)
    for s in f["only_shopify"]:
        issues[id(s)].append("not on eBay")
    for risk, s, _, _ in f["qty"]:
        issues[id(s)].append(risk.split(":")[0])
    for s, group in f["dupes"]:
        issues[id(s)].append(f"{len(group)} eBay listings")
    for s in f["no_cost_all"]:
        issues[id(s)].append("no unit cost")

    master = []
    seen = set()
    for e in report.ebay:
        s = e.match
        if s:
            seen.add(id(s))
        tags = list(issues.get(id(s), [])) if s else ["not on Shopify"]
        if e.method == "fuzzy":
            tags.insert(0, "CHECK MATCH")
        master.append(_master_row(s, e, tags))
    for s in report.shop:
        if id(s) not in seen and s.status != "archived":
            master.append(_master_row(s, None, issues.get(id(s), [])))
    _write(out / "master.csv", MASTER_HEADER, master)

    _write(out / "only_on_shopify.csv",
           ["shopify_title", "shopify_qty", "price", "handle", "store_link", "shopify_product_gid"],
           [[s.title, s.qty, _money(s.price), s.handle, STORE_URL + s.handle, s.product_gid]
            for s in f["only_shopify"]])
    _write(out / "only_on_ebay.csv",
           ["ebay_title", "ebay_item_id", "ebay_sku", "ebay_qty", "ebay_price", "ebay_link"],
           [[e.title, e.item_id, e.sku, e.qty, _money(e.price), f"https://www.ebay.com/itm/{e.item_id}"]
            for e in f["only_ebay"]])
    _write(out / "quantity_mismatches.csv",
           ["what", "shopify_title", "shopify_qty", "ebay_total_qty", "ebay_item_ids", "shopify_product_gid"],
           [[risk, s.title, s.qty, total, " ".join(e.item_id for e in group), s.product_gid]
            for risk, s, group, total in f["qty"]])
    _write(out / "ebay_duplicates.csv",
           ["shopify_title", "shopify_qty", "how_many_ebay_listings", "ebay_item_ids", "ebay_qtys"],
           [[s.title, s.qty, len(g), " ".join(e.item_id for e in g), " ".join(str(e.qty) for e in g)]
            for s, g in f["dupes"]]
           + [["(not on Shopify) " + g[0].title, "", len(g), " ".join(e.item_id for e in g),
               " ".join(str(e.qty) for e in g)] for g in f["unmatched_dupes"]])
    _write(out / "missing_unit_costs.csv",
           ["shopify_title", "units_sold", "shopify_qty", "price", "handle", "shopify_product_gid"],
           [[s.title, s.units_sold, s.qty, _money(s.price), s.handle, s.product_gid] for s in f["no_cost"]])
    _write(out / "check_these_matches.csv",
           ["score", "ebay_title", "shopify_title", "ebay_item_id", "shopify_product_gid"],
           [[e.score, e.title, e.match.title, e.item_id, e.match.product_gid] for e in f["check"]])

    summary = _summary(report, f)
    (out / "SUMMARY.txt").write_text(summary, encoding="utf-8")
    return summary


MASTER_HEADER = [
    "issues", "match_method", "match_score",
    "shopify_product_gid", "shopify_variant_gid", "ebay_item_id",
    "shopify_title", "ebay_title", "shopify_sku", "ebay_sku",
    "shopify_qty", "ebay_qty", "shopify_price", "ebay_price", "unit_cost",
    "units_sold", "shopify_status", "handle",
]


def _master_row(s, e, tags):
    return [
        "; ".join(tags), e.method if e else "not on eBay", e.score if e and e.match else "",
        s.product_gid if s else "", s.variant_gid if s else "", e.item_id if e else "",
        s.title if s else "", e.title if e else "", s.sku if s else "", e.sku if e else "",
        "" if not s or s.qty is None else s.qty, e.qty if e else "",
        _money(s.price) if s else "", _money(e.price) if e else "", _money(s.cost) if s else "",
        (s.units_sold if s else e.units_sold), s.status if s else "", s.handle if s else "",
    ]


def _summary(report, f):
    shop_live = [s for s in report.shop if s.status != "archived"]
    matched = [e for e in report.ebay if e.match]
    oversell = [r for r in f["qty"] if r[0].startswith("OVERSELL")]
    hidden = [r for r in f["qty"] if r[0].startswith("eBay shows 0")]
    by_method = Counter(e.method for e in report.ebay)
    lines = [
        f"RRR inventory reconciliation  -  {date.today().isoformat()}",
        "=" * 60,
        f"Shopify items (variants, not archived): {len(shop_live)}",
        f"eBay live listings:                      {len(report.ebay)}",
        f"eBay listings matched to Shopify:        {len(matched)}"
        f"  (SKU {by_method['sku'] + by_method['shopify_id']}, exact title {by_method['exact_title']},"
        f" close title {by_method['fuzzy']})",
        "",
        "WHAT NEEDS ATTENTION",
        f"  In stock on Shopify but NOT on eBay:   {len(f['only_shopify'])}   -> only_on_shopify.csv",
        f"  On eBay but NOT found on Shopify:      {len(f['only_ebay'])}   -> only_on_ebay.csv",
        f"  OVERSELL RISK (eBay > Shopify qty):    {len(oversell)}   -> quantity_mismatches.csv",
        f"  eBay at 0 while Shopify has stock:     {len(hidden)}",
        f"  Shopify items with 2+ eBay listings:   {len(f['dupes'])}   -> ebay_duplicates.csv",
        f"  Same title listed 2+ times on eBay, not on Shopify: {len(f['unmatched_dupes'])}",
        f"  Missing unit cost ({f['cost_basis']}): {len(f['no_cost'])}   -> missing_unit_costs.csv",
        f"  Close-title matches to eyeball:        {len(f['check'])}   -> check_these_matches.csv",
        "",
        "Everything side by side: master.csv",
    ]
    if report.sales_source:
        lines.append(f"Sales counts come from: {report.sales_source}")
    lines += [""] + report.notes

    def top(title, rows, fmt, n=10):
        if rows:
            lines.extend(["", title] + [f"  - {fmt(r)}" for r in rows[:n]])
            if len(rows) > n:
                lines.append(f"  ...and {len(rows) - n} more in the CSV")

    top("Oversell risk (fix these first):", oversell,
        lambda r: f"{r[1].title[:70]}  (Shopify {r[1].qty}, eBay {r[3]})")
    top("Top items in stock on Shopify but not on eBay:", f["only_shopify"],
        lambda s: f"{s.title[:70]}  (qty {s.qty}, ${_money(s.price)})")
    top("eBay listings not found on Shopify:", f["only_ebay"],
        lambda e: f"{e.title[:70]}  (item {e.item_id})")
    top("Most duplicated on eBay:", f["dupes"],
        lambda d: f"{d[0].title[:70]}  ({len(d[1])} listings)")
    top("Missing unit cost, biggest first:", f["no_cost"],
        lambda s: f"{s.title[:70]}  (sold {s.units_sold}, qty {s.qty})")
    lines.append("")
    lines.append("Read-only report. Nothing on Shopify or eBay was changed.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- finding input files

def newest(folder, *patterns):
    hits = [p for pat in patterns for p in Path(folder).glob(pat) if p.is_file()]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def find_exports(folder):
    """Guess which export is which by file name, newest first."""
    folder = Path(folder)
    return {
        "shopify": newest(folder, "products_export*.csv", "*product*export*.csv"),
        "inventory": newest(folder, "inventory_export*.csv", "*inventory*.csv"),
        "orders": newest(folder, "orders_export*.csv", "*orders*.csv"),
        "ebay": newest(folder, "*eBay*.csv", "*ebay*.csv", "*active*listing*.csv", "*Listings*.csv"),
    }
