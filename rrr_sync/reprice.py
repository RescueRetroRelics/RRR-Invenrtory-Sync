"""eBay repricer: lower prices on listings that aren't selling, never below break-even.

Read-only. It never changes eBay. It writes files you look at and upload yourself:

  data/reprice/<date_time>/
    preview.html            old vs new for every listing, easy to read
    preview.csv             same thing as a spreadsheet
    upload_batch_01.csv ... up to 50 price changes each, for Seller Hub > Reports > Upload
    undo_batch_01.csv ...   the old prices, in case you want to put a batch back
    listings_backup.csv     every listing and its price before any change
    summary.txt

How a new price is chosen (all numbers live in reprice_rules.json):
  1. How long has it been listed? Older listings get a bigger cut (age tiers).
  2. Watchers mean someone is interested, so those get the next-smaller cut.
  3. The cut is taken from the ORIGINAL price (remembered in data/reprice/original_prices.csv),
     so running the tool twice never stacks cuts.
  4. The price never goes below the floor: your cost + minimum profit + eBay's fees.
  5. Prices end in .49 or .99 (whichever is closest).
"""

import csv
import datetime as dt
import html
import json
import math
import re
from pathlib import Path

from .config import DATA_DIR, ROOT
from .matching import normalize

RULES_FILE = ROOT / "reprice_rules.json"
REPRICE_DIR = DATA_DIR / "reprice"
ORIGINALS_FILE = REPRICE_DIR / "original_prices.csv"
BATCH_SIZE = 50

DEFAULT_RULES = {
    "fees": {
        "final_value_fee_percent": 13.6,
        "per_order_fee_over_10": 0.40,
        "per_order_fee_10_or_less": 0.30,
        "promoted_listing_percent": 0.0,
        "sales_tax_percent_fee_is_charged_on": 6.0,
        "shipping_you_pay_per_item": 0.0,
    },
    "min_profit": 1.00,
    "min_price": 4.99,
    "cost_when_unknown": 0.0,
    "age_tiers": [
        {"days": 30, "cut_percent": 10},
        {"days": 60, "cut_percent": 20},
        {"days": 90, "cut_percent": 25},
        {"days": 180, "cut_percent": 30},
        {"days": 365, "cut_percent": 40},
    ],
    "watchers_soften_at": 3,
    "min_change_dollars": 0.50,
}

UPLOAD_HEADER = ["Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)", "ItemID", "StartPrice"]

PREVIEW_COLUMNS = [
    "status", "item_id", "title", "old_price", "new_price", "change", "change_percent",
    "days_listed", "watchers", "original_price", "floor_price", "cost", "reason",
]


# ---------------------------------------------------------------- rules

def load_rules(path=None):
    path = Path(path) if path else RULES_FILE
    rules = json.loads(json.dumps(DEFAULT_RULES))
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for key, value in user.items():
            if key.startswith("_"):
                continue
            if isinstance(value, dict) and isinstance(rules.get(key), dict):
                rules[key].update({k: v for k, v in value.items() if not k.startswith("_")})
            else:
                rules[key] = value
    rules["age_tiers"] = sorted(rules["age_tiers"], key=lambda t: t["days"])
    return rules


# ---------------------------------------------------------------- money

def _round_ending(price):
    """Nearest price ending in .49 or .99 (so a 10% cut stays about 10%)."""
    whole = math.floor(price)
    cands = [c for c in (whole - 0.01, whole + 0.49, whole + 0.99) if c > 0]
    return round(min(cands, key=lambda c: (abs(c - price), c)), 2)


def _round_up_ending(price):
    """Smallest price ending in .49 or .99 that is >= price."""
    whole = math.floor(price)
    for cand in (whole + 0.49, whole + 0.99, whole + 1.49):
        if cand >= price - 1e-9:
            return round(cand, 2)
    return round(whole + 1.49, 2)


def net_payout(price, cost, rules):
    """What you keep from a sale at `price` after eBay's cut, shipping you pay and your cost."""
    f = rules["fees"]
    fee_pct = (f["final_value_fee_percent"] + f["promoted_listing_percent"]) / 100
    taxed = price * (1 + f["sales_tax_percent_fee_is_charged_on"] / 100)
    order_fee = f["per_order_fee_over_10"] if price > 10 else f["per_order_fee_10_or_less"]
    return price - taxed * fee_pct - order_fee - f["shipping_you_pay_per_item"] - cost


def floor_price(cost, rules):
    """Lowest .49/.99 price that still covers cost + fees + minimum profit (and min_price)."""
    f = rules["fees"]
    fee_pct = (f["final_value_fee_percent"] + f["promoted_listing_percent"]) / 100
    keep = 1 - fee_pct * (1 + f["sales_tax_percent_fee_is_charged_on"] / 100)
    need = cost + rules["min_profit"] + f["shipping_you_pay_per_item"]
    price = (need + f["per_order_fee_over_10"]) / keep
    if price <= 10:
        price = min(max((need + f["per_order_fee_10_or_less"]) / keep, 0), 10)
    price = _round_up_ending(max(price, rules["min_price"]))
    while net_payout(price, cost, rules) < rules["min_profit"] - 1e-9:  # rounding safety
        price = _round_up_ending(price + 0.01)
    return price


def _money(text):
    if text is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(text))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------- dates

_TZ_WORD = re.compile(r"\s+[A-Z]{2,5}$")
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
    "%b-%d-%y %H:%M:%S", "%b-%d-%Y %H:%M:%S", "%b-%d-%y", "%b %d, %Y %H:%M:%S", "%b %d, %Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d-%b-%y", "%d %b %Y",
]


def parse_date(text):
    if not text:
        return None
    t = _TZ_WORD.sub("", str(text).strip())
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- reading files

_ALIASES = {
    "item_id": ["item number", "item id", "itemid", "ebay item id", "item_id", "ebay_item_id"],
    "title": ["title", "item title", "listing title"],
    "sku": ["custom label (sku)", "custom label", "sku", "custom label sku"],
    "price": ["current price", "price", "start price", "buy it now price", "startprice", "old_price"],
    "start_date": ["start date", "start time", "listed on", "start_date", "listing start date"],
    "watchers": ["watchers", "watch count", "watchcount"],
    "bids": ["bids", "bid count"],
    "quantity": ["available quantity", "quantity available", "quantity", "available"],
    "format": ["format", "listing format", "listing type"],
    "variation": ["variation details", "variation"],
}


def _find_header(rows):
    for i, row in enumerate(rows[:15]):
        lowered = [c.strip().lower() for c in row]
        if any(c in lowered for c in _ALIASES["item_id"]):
            return i
    raise ValueError("Couldn't find an 'Item number' column. Is this the eBay active listings export?")


def _column(header, field):
    lowered = [c.strip().lower() for c in header]
    for alias in _ALIASES[field]:
        if alias in lowered:
            return lowered.index(alias)
    return None


def read_ebay_export(path):
    """Seller Hub > Listings > Active > Download (CSV). Returns one dict per listing."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.reader(fh))
    h = _find_header(rows)
    header = rows[h]
    cols = {f: _column(header, f) for f in _ALIASES}
    seen, out = {}, []
    for row in rows[h + 1:]:
        get = lambda f: row[cols[f]].strip() if cols[f] is not None and cols[f] < len(row) else ""
        item_id = re.sub(r"\D", "", get("item_id"))
        if not item_id:
            continue
        fmt = get("format").lower()
        rec = {
            "item_id": item_id,
            "sku": get("sku"),
            "title": get("title"),
            "price": _money(get("price")),
            "start_date": get("start_date"),
            "watchers": int(_money(get("watchers")) or 0),
            "bids": int(_money(get("bids")) or 0),
            "quantity": int(_money(get("quantity"))) if _money(get("quantity")) is not None else 1,
            "format": "auction" if "auction" in fmt or fmt == "chinese" else "fixed",
            "variation": bool(get("variation")),
        }
        if item_id in seen:  # several rows for one item = a variation listing
            seen[item_id]["variation"] = True
            continue
        seen[item_id] = rec
        out.append(rec)
    return out


def read_costs(path):
    """Costs from a Shopify product export (Cost per item) or any CSV with a cost column.

    Returns {"id:<item number>": cost, "sku:<sku>": cost, "title:<normalized>": cost}.
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        fields = {k.strip().lower(): k for k in (reader.fieldnames or [])}
        pick = lambda *names: next((fields[n] for n in names if n in fields), None)
        cost_col = pick("cost per item", "cost", "unit cost", "unit_cost")
        if not cost_col:
            raise ValueError(f"No cost column in {path} (looked for 'Cost per item' or 'cost').")
        id_col = pick("item number", "item id", "item_id", "ebay_item_id")
        sku_col = pick("variant sku", "sku", "custom label (sku)")
        title_col = pick("title", "item title")
        handle_col = pick("handle")
        costs, titles_by_handle = {}, {}
        for row in reader:
            cost = _money(row.get(cost_col))
            title = (row.get(title_col) or "").strip() if title_col else ""
            if handle_col:
                handle = row.get(handle_col, "")
                if title:
                    titles_by_handle[handle] = title
                else:
                    title = titles_by_handle.get(handle, "")
            if cost is None or cost <= 0:
                continue
            if id_col and row.get(id_col, "").strip():
                costs.setdefault("id:" + re.sub(r"\D", "", row[id_col]), cost)
            if sku_col and row.get(sku_col, "").strip():
                costs.setdefault("sku:" + row[sku_col].strip().lower(), cost)
            if title:
                costs.setdefault("title:" + normalize(title), cost)
    return costs


def _cost_for(listing, costs):
    for key in ("id:" + listing["item_id"], "sku:" + listing["sku"].lower(),
                "title:" + normalize(listing["title"])):
        if key in costs and not key.endswith(":"):
            return costs[key]
    return None


# ---------------------------------------------------------------- original prices

def load_originals(path=ORIGINALS_FILE):
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["item_id"]: float(r["original_price"]) for r in csv.DictReader(fh) if r.get("original_price")}


def save_originals(originals, path=ORIGINALS_FILE):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "original_price"])
        for item_id, price in sorted(originals.items()):
            w.writerow([item_id, f"{price:.2f}"])


# ---------------------------------------------------------------- the rule

def _cut_for(days, watchers, rules):
    tiers = [t for t in rules["age_tiers"] if days >= t["days"]]
    if not tiers:
        return 0, "listed under %d days" % rules["age_tiers"][0]["days"]
    idx = len(tiers) - 1
    reason = f"listed {days} days"
    if watchers >= rules["watchers_soften_at"]:
        idx -= 1
        reason += f", {watchers} watchers so a smaller cut (send them an offer too)"
        if idx < 0:
            return 0, reason
    return rules["age_tiers"][idx]["cut_percent"], reason


def plan(listings, rules, costs=None, originals=None, today=None):
    """Decide a new price for every listing. Returns (rows, updated_originals)."""
    today = today or dt.date.today()
    costs = costs or {}
    originals = dict(originals or {})
    rows = []
    for l in listings:
        row = {
            "item_id": l["item_id"], "title": l["title"], "old_price": l["price"],
            "new_price": l["price"], "watchers": l.get("watchers", 0),
            "days_listed": "", "original_price": "", "floor_price": "", "cost": "",
        }
        rows.append(row)

        def done(status, reason):
            row.update(status=status, reason=reason)

        if not l["price"]:
            done("SKIP", "no price in the data")
            continue
        if l.get("format") == "auction" or l.get("bids"):
            done("SKIP", "auction or has bids")
            continue
        if l.get("variation"):
            done("SKIP", "listing with variations - change by hand")
            continue
        if l.get("quantity", 1) <= 0:
            done("SKIP", "out of stock")
            continue

        price = float(l["price"])
        original = originals.get(l["item_id"])
        if original is None or price > original + 0.005:
            original = price  # first time seen, or you raised it since: start from today's price
        originals[l["item_id"]] = original
        row["original_price"] = original

        cost = _cost_for(l, costs)
        known_cost = cost is not None
        cost = cost if known_cost else rules["cost_when_unknown"]
        row["cost"] = cost if known_cost else ""
        floor = floor_price(cost, rules)
        row["floor_price"] = floor
        cost_note = "" if known_cost else " (no cost on file, floor covers fees + min profit only)"

        started = parse_date(l.get("start_date"))
        if not started:
            done("SKIP", "no start date in the data")
            continue
        days = (today - started).days
        row["days_listed"] = days

        if price < floor - 0.005:
            done("BELOW_FLOOR", f"already under break-even ${floor:.2f}{cost_note}, consider raising")
            continue

        cut, why = _cut_for(days, row["watchers"], rules)
        if not cut:
            done("KEEP", why)
            continue
        target = _round_ending(original * (1 - cut / 100))
        status = "LOWER"
        if target < floor:
            target, status = floor, "LOWER_TO_FLOOR"
            why += f", stopped at break-even floor ${floor:.2f}"
        if price - target < rules["min_change_dollars"] - 1e-9:
            done("KEEP", why + f": already at or near the {cut}% target ${target:.2f}")
            continue
        row["new_price"] = target
        done(status, f"{why}: {cut}% off original ${original:.2f}{cost_note}")

    for r in rows:
        if r["status"].startswith("LOWER"):
            r["change"] = round(r["new_price"] - r["old_price"], 2)
            r["change_percent"] = round(100 * r["change"] / r["old_price"], 1)
        else:
            r["change"], r["change_percent"] = "", ""
    return rows, originals


# ---------------------------------------------------------------- output

_ORDER = {"LOWER": 0, "LOWER_TO_FLOOR": 1, "BELOW_FLOOR": 2, "KEEP": 3, "SKIP": 4}


def _fmt(v):
    return f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v))


def write_outputs(rows, listings, out_dir, rules):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (_ORDER[r["status"]], r["change"] if r["change"] != "" else 0))

    with open(out_dir / "preview.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PREVIEW_COLUMNS)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in PREVIEW_COLUMNS])

    with open(out_dir / "listings_backup.csv", "w", newline="", encoding="utf-8") as fh:
        cols = ["item_id", "sku", "title", "price", "start_date", "watchers", "bids", "quantity", "format", "variation"]
        w = csv.writer(fh)
        w.writerow(cols)
        for l in listings:
            w.writerow([_fmt(l.get(c)) for c in cols])

    changes = [r for r in rows if r["status"].startswith("LOWER")]
    batches = [changes[i:i + BATCH_SIZE] for i in range(0, len(changes), BATCH_SIZE)]
    for n, batch in enumerate(batches, 1):
        for prefix, key in (("upload", "new_price"), ("undo", "old_price")):
            with open(out_dir / f"{prefix}_batch_{n:02d}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(UPLOAD_HEADER)
                for r in batch:
                    w.writerow(["Revise", r["item_id"], f"{r[key]:.2f}"])

    summary = summarize(rows, len(batches), rules)
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    (out_dir / "preview.html").write_text(_html(rows, summary), encoding="utf-8")
    return summary, len(batches)


def summarize(rows, n_batches, rules):
    count = lambda s: sum(1 for r in rows if r["status"] == s)
    lowered = [r for r in rows if r["status"].startswith("LOWER")]
    total_old = sum(r["old_price"] for r in lowered)
    total_new = sum(r["new_price"] for r in lowered)
    no_cost = sum(1 for r in lowered if r["cost"] == "")
    f = rules["fees"]
    lines = [
        f"Listings checked:            {len(rows)}",
        f"  Price goes down:           {count('LOWER')}",
        f"  Down, stopped at floor:    {count('LOWER_TO_FLOOR')}",
        f"  Already below break-even:  {count('BELOW_FLOOR')}  (not changed, look at these)",
        f"  No change:                 {count('KEEP')}",
        f"  Skipped:                   {count('SKIP')}",
        "",
        f"Asking prices of the lowered listings: ${total_old:,.2f} -> ${total_new:,.2f}",
        f"Upload files: {n_batches} (up to {BATCH_SIZE} changes each)",
        f"Lowered listings with no cost on file: {no_cost}",
        "",
        f"Fees used: {f['final_value_fee_percent']}% final value fee"
        f" + {f['promoted_listing_percent']}% promoted, ${f['per_order_fee_over_10']:.2f}/order"
        f" (${f['per_order_fee_10_or_less']:.2f} at $10 or less), min profit ${rules['min_profit']:.2f},"
        f" min price ${rules['min_price']:.2f}. Change them in reprice_rules.json.",
        "Nothing was changed on eBay.",
    ]
    return "\n".join(lines) + "\n"


def _html(rows, summary):
    colors = {"LOWER": "#e6f4ea", "LOWER_TO_FLOOR": "#fff4d6", "BELOW_FLOOR": "#fde2e1", "KEEP": "", "SKIP": "#f1f1f1"}
    body = []
    for r in rows:
        link = f'<a href="https://www.ebay.com/itm/{r["item_id"]}">{html.escape(r["title"] or r["item_id"])}</a>'
        body.append(
            f'<tr style="background:{colors[r["status"]]}"><td>{r["status"]}</td><td>{link}</td>'
            f'<td>${_fmt(r["old_price"])}</td><td><b>${_fmt(r["new_price"])}</b></td>'
            f'<td>{_fmt(r["change_percent"])}{"%" if r["change_percent"] != "" else ""}</td>'
            f'<td>{r["days_listed"]}</td><td>{r["watchers"]}</td><td>{_fmt(r["floor_price"])}</td>'
            f'<td>{html.escape(r["reason"])}</td></tr>'
        )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>eBay reprice preview</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;color:#222}}table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#fff}}
pre{{background:#f7f7f7;padding:12px;white-space:pre-wrap}}</style></head><body>
<h1>eBay reprice preview</h1><p>Nothing has been changed on eBay. Green = price goes down. Yellow = stopped at
your break-even floor. Red = already below break-even (left alone).</p>
<pre>{html.escape(summary)}</pre>
<table><tr><th>Status</th><th>Listing</th><th>Now</th><th>New</th><th>Change</th><th>Days listed</th>
<th>Watchers</th><th>Floor</th><th>Why</th></tr>{''.join(body)}</table></body></html>"""


def run(listings, rules, costs=None, out_root=REPRICE_DIR, today=None, save=True):
    originals_path = Path(out_root) / "original_prices.csv"
    rows, originals = plan(listings, rules, costs, load_originals(originals_path), today)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = Path(out_root) / stamp
    summary, n = write_outputs(rows, listings, out_dir, rules)
    if save:
        save_originals(originals, originals_path)
    return out_dir, summary, n
