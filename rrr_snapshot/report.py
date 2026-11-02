"""Turn sales, products and listings into the weekly numbers, then write HTML + CSV."""

import csv
import html
from collections import defaultdict
from datetime import date, timedelta

from rrr_sync.matching import normalize

TOKEN_MATCH_MIN = 0.80   # share of words two titles must have in common to count as the same item


# ---------- matching sold items to Shopify products ----------

class ProductIndex:
    def __init__(self, products):
        self.products = products
        self.by_title = {normalize(p.title): p for p in products}
        self.by_sku = {p.sku.lower(): p for p in products if p.sku}
        self.tokens = [(set(normalize(p.title).split()), p) for p in products]
        self._cache = {}

    def find(self, title, sku=""):
        key = (title, sku)
        if key in self._cache:
            return self._cache[key]
        hit = self.by_sku.get(sku.lower()) if sku else None
        hit = hit or self.by_title.get(normalize(title))
        if not hit:
            words = set(normalize(title).split())
            best, best_score = None, 0.0
            for toks, p in self.tokens:
                if not toks or not words:
                    continue
                score = len(words & toks) / len(words | toks)
                if score > best_score:
                    best, best_score = p, score
            hit = best if best_score >= TOKEN_MATCH_MIN else None
        self._cache[key] = hit
        return hit


# ---------- the numbers ----------

def week_window(end):
    """7 days ending on `end` (inclusive)."""
    return end - timedelta(days=6), end


def default_week_end(today):
    """The Sunday before today (so on a Monday you get last Mon-Sun)."""
    days_back = (today.weekday() + 1) % 7 or 7
    return today - timedelta(days=days_back)


def totals(sales, start, end):
    out = {}
    for ch in ("Shopify", "eBay", "All"):
        rows = [s for s in sales if start <= s.day <= end and (ch == "All" or s.channel == ch)]
        orders = {(s.channel, s.order_id) for s in rows}
        dollars = round(sum(s.amount for s in rows), 2)
        out[ch] = {
            "orders": len(orders),
            "units": sum(s.qty for s in rows),
            "sales": dollars,
            "aov": round(dollars / len(orders), 2) if orders else 0.0,
        }
    return out


def top_sellers(sales, index, start, end, limit=10):
    groups = defaultdict(lambda: {"title": "", "units": 0, "sales": 0.0, "channels": set()})
    for s in sales:
        if not (start <= s.day <= end):
            continue
        p = index.find(s.title, s.sku)
        key = ("p", p.handle) if p else ("t", normalize(s.title))
        g = groups[key]
        g["title"] = p.title if p else (g["title"] or s.title)
        g["units"] += s.qty
        g["sales"] += s.amount
        g["channels"].add(s.channel)
    rows = sorted(groups.values(), key=lambda g: (-g["sales"], -g["units"]))
    for g in rows:
        g["sales"] = round(g["sales"], 2)
        g["channels"] = " + ".join(sorted(g["channels"]))
    return rows[:limit] if limit else rows


def slow_movers(products, listings, sales, index, as_of, days):
    """In-stock, active products with no sale on either channel in the last `days` days."""
    since = as_of - timedelta(days=days)
    sold_handles, last_sold = set(), {}
    for s in sales:
        p = index.find(s.title, s.sku)
        if p:
            last_sold[p.handle] = max(last_sold.get(p.handle, s.day), s.day)
            if since < s.day <= as_of:
                sold_handles.add(p.handle)

    listing_by_title = {normalize(l.title): l for l in listings}
    listing_tokens = [(set(normalize(l.title).split()), l) for l in listings]

    def find_listing(title):
        n = normalize(title)
        if n in listing_by_title:
            return listing_by_title[n]
        words, best, best_score = set(n.split()), None, 0.0
        for toks, l in listing_tokens:
            if toks and words:
                score = len(words & toks) / len(words | toks)
                if score > best_score:
                    best, best_score = l, score
        return best if best_score >= TOKEN_MATCH_MIN else None

    out = []
    for p in products:
        if p.status not in ("active", ""):
            continue
        if p.qty is not None and p.qty <= 0:
            continue
        if p.handle in sold_handles:
            continue
        l = find_listing(p.title) if listings else None
        qty = p.qty if p.qty is not None else 1
        out.append({
            "title": p.title,
            "vendor": p.vendor,
            "type": p.product_type,
            "price": p.price,
            "qty": p.qty if p.qty is not None else "",
            "tied_up": round(p.price * qty, 2),
            "last_sold": last_sold.get(p.handle, ""),
            "ebay_item": l.item_id if l else "",
            "ebay_listed_days": (as_of - l.start).days if l and l.start else "",
            "ebay_watchers": l.watchers if l and l.watchers is not None else "",
        })
    out.sort(key=lambda r: -r["tied_up"])
    return out


# ---------- writing it out ----------

def pct_change(now, before):
    if not before:
        return "new" if now else "-"
    change = (now - before) / before * 100
    return f"{change:+.0f}%"


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _e(v):
    return html.escape(str(v))


def _money(v):
    return f"${v:,.2f}"


def write_html(path, ctx):
    t, prev, avg = ctx["this_week"], ctx["last_week"], ctx["four_week_avg"]
    ws, we = ctx["week"]

    def tile(label, ch, key, fmt):
        now, before = t[ch][key], prev[ch][key]
        return (f'<div class="tile"><div class="lbl">{label}</div><div class="big">{fmt(now)}</div>'
                f'<div class="sub">last week {fmt(before)} · {pct_change(now, before)}</div>'
                f'<div class="sub">4-week avg {fmt(avg[ch][key])}</div></div>')

    channel_rows = "".join(
        f"<tr><td>{ch}</td><td>{t[ch]['orders']}</td><td>{t[ch]['units']}</td>"
        f"<td>{_money(t[ch]['sales'])}</td><td>{_money(t[ch]['aov'])}</td>"
        f"<td>{_money(prev[ch]['sales'])}</td></tr>"
        for ch in ("Shopify", "eBay", "All"))

    def seller_table(rows, empty):
        if not rows:
            return f'<p class="muted">{empty}</p>'
        body = "".join(f"<tr><td>{i}</td><td>{_e(r['title'])}</td><td>{r['units']}</td>"
                       f"<td>{_money(r['sales'])}</td><td>{_e(r['channels'])}</td></tr>"
                       for i, r in enumerate(rows, 1))
        return ("<table><tr><th>#</th><th>Item</th><th>Units</th><th>Sales</th><th>Where</th></tr>"
                f"{body}</table>")

    slow = ctx["slow"]
    slow_shown = slow[:25]
    if ctx["have_products"]:
        slow_body = "".join(
            f"<tr><td>{_e(r['title'])}</td><td>{_money(r['price'])}</td><td>{_e(r['qty'])}</td>"
            f"<td>{_money(r['tied_up'])}</td><td>{_e(r['last_sold'] or 'not in data')}</td>"
            f"<td>{_e(r['ebay_listed_days'])}</td><td>{_e(r['ebay_watchers'])}</td></tr>"
            for r in slow_shown)
        slow_html = (f"<p>{len(slow)} in-stock items had no sale in the last {ctx['slow_days']} days "
                     f"({_money(sum(r['tied_up'] for r in slow))} at list price). "
                     f"Biggest money tied up first; top 25 shown, the full list is in "
                     f"<b>{_e(ctx['slow_csv'])}</b>.</p>"
                     "<table><tr><th>Item</th><th>Price</th><th>Qty</th><th>Tied up</th>"
                     "<th>Last sold</th><th>Days on eBay</th><th>Watchers</th></tr>"
                     f"{slow_body}</table>")
    else:
        slow_html = ('<p class="muted">No Shopify products export found, so slow movers are skipped. '
                     'Drop one in the exports folder (see README).</p>')

    notes = "".join(f"<li>{_e(n)}</li>" for n in ctx["notes"])
    files = "".join(f"<li>{_e(name)}: {_e(what)}</li>" for name, what in ctx["files"])

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RRR Weekly Snapshot {we}</title>
<style>
 :root {{ --ink:#1d1a1f; --muted:#6b6470; --line:#e6e0e8; --accent:#c2185b; --bg:#fffafc; }}
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; color:var(--ink); background:var(--bg);
        margin:0 auto; max-width:900px; padding:16px; line-height:1.4; }}
 h1 {{ font-size:1.5rem; margin:.2rem 0; }} h2 {{ font-size:1.15rem; margin-top:1.8rem;
        border-bottom:2px solid var(--accent); padding-bottom:4px; }}
 .muted, .sub {{ color:var(--muted); font-size:.85rem; }}
 .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }}
 .tile {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px; }}
 .lbl {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
 .big {{ font-size:1.6rem; font-weight:700; }}
 table {{ width:100%; border-collapse:collapse; background:#fff; font-size:.9rem; }}
 th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
 th {{ background:#f7eef3; }}
 .scroll {{ overflow-x:auto; }}
</style></head><body>
<h1>🐾 Rescue Retro Relics: Weekly Snapshot</h1>
<div class="muted">Week of {ws:%a %b %d} to {we:%a %b %d, %Y} · made {ctx['made']:%b %d, %Y}</div>

<h2>This week</h2>
<div class="tiles">
 {tile("Sales (items)", "All", "sales", _money)}
 {tile("Orders", "All", "orders", str)}
 {tile("Items sold", "All", "units", str)}
 {tile("Avg order", "All", "aov", _money)}
</div>

<h2>By channel</h2>
<div class="scroll"><table><tr><th>Channel</th><th>Orders</th><th>Items</th><th>Sales</th>
<th>Avg order</th><th>Last week sales</th></tr>{channel_rows}</table></div>

<h2>Top sellers this week</h2>
<div class="scroll">{seller_table(ctx['top_week'], 'No sales this week in the data.')}</div>

<h2>Top sellers, last 30 days</h2>
<div class="scroll">{seller_table(ctx['top_30'], 'No sales in the last 30 days in the data.')}</div>

<h2>Slow movers</h2>
<div class="scroll">{slow_html}</div>

<h2>How to read this</h2>
<ul class="muted">{notes}</ul>
<p class="muted">Files used:</p><ul class="muted">{files}</ul>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def build(found, today, week_end=None, slow_days=60):
    week_end = week_end or default_week_end(today)
    ws, we = week_window(week_end)
    sales, products, listings = found["sales"], found["products"], found["listings"]
    index = ProductIndex(products)

    this_week = totals(sales, ws, we)
    last_week = totals(sales, ws - timedelta(days=7), we - timedelta(days=7))
    weeks = [totals(sales, ws - timedelta(days=7 * k), we - timedelta(days=7 * k)) for k in range(1, 5)]
    four_week_avg = {ch: {k: round(sum(w[ch][k] for w in weeks) / 4, 2) for k in weeks[0][ch]}
                     for ch in weeks[0]}

    notes = [
        "Sales = item prices x quantity after item discounts. Shipping, tax and fees are not included.",
        "Cancelled, voided and fully refunded orders are left out. Partial refunds are not subtracted.",
        "'4-week avg' is the average of the 4 weeks before this one.",
        "Every number here comes straight from your export files (verified). "
        "Slow movers are a flag to look at, not a verdict (hypothesis).",
        f"Slow mover = active, in stock, and no sale on Shopify or eBay in the last {slow_days} days.",
    ]
    if sales:
        first, last = min(s.day for s in sales), max(s.day for s in sales)
        notes.insert(0, f"Sales data covers {first:%b %d, %Y} to {last:%b %d, %Y}.")
        if first > ws - timedelta(days=28):
            notes.append("Heads up: the orders export doesn't reach back 4 weeks before this week, "
                         "so the 4-week average and last week may read low. Export a longer date range.")
        if first > we - timedelta(days=slow_days):
            notes.append(f"Heads up: the orders exports start after the {slow_days}-day slow-mover window, "
                         "so some items may look slow only because their sales are older than the data.")
        if last < we:
            notes.append(f"Heads up: the newest sale in the data is {last:%b %d}. If you sold after that, "
                         "export again so this week is complete.")
    channels = {s.channel for s in sales}
    for ch in ("Shopify", "eBay"):
        if ch not in channels:
            notes.append(f"No {ch} orders were found, so {ch} shows zero. See the README for the export.")
    if products and all(p.qty is None for p in products):
        notes.append("The products export has no inventory column, so every active product is treated as in stock.")
    if products:
        notes.append("Shopify's product export has no 'date listed', so a brand-new item can show as slow. "
                     "Add the eBay active-listings export to see how long each one has been listed.")

    return {
        "week": (ws, we),
        "made": today,
        "this_week": this_week,
        "last_week": last_week,
        "four_week_avg": four_week_avg,
        "top_week": top_sellers(sales, index, ws, we),
        "top_30": top_sellers(sales, index, we - timedelta(days=29), we),
        "slow": slow_movers(products, listings, sales, index, we, slow_days) if products else [],
        "slow_days": slow_days,
        "have_products": bool(products),
        "notes": notes,
        "files": found["files"],
    }
