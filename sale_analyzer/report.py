"""Write the results: sale_report.html (open it in a browser) plus CSVs."""

import csv
import html

from .analyze import AGE_BUCKETS, PRICE_BANDS, band_label

TIER_NAMES = {0: "Fresh (leave it)", 1: "Tier 1: nudge", 2: "Tier 2: sale", 3: "Tier 3: clearance"}

TIER_COLUMNS = ["tier", "tier_name", "discount_pct", "price", "suggested_price", "qty", "title", "type",
                "days_without_sale", "days_listed", "units_sold", "cost", "product_id", "reason"]
SALES_COLUMNS = ["date", "channel", "title", "qty", "unit_price", "list_price", "days_to_sell",
                 "match", "product_id", "item_id", "sku"]


def write_csvs(result, sales, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    tiers_path = out_dir / "sale_tier_suggestions.csv"
    with tiers_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=TIER_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in result["tiers"]:
            w.writerow({**r, "tier_name": TIER_NAMES[r["tier"]]})
    sales_path = out_dir / "sales_matched.csv"
    with sales_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=SALES_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for s in sorted(sales, key=lambda s: (s["date"] is None, s["date"]), reverse=True):
            w.writerow(s)
    return tiers_path, sales_path


def _money(x):
    return "" if x is None else f"${x:,.2f}"


def _n(x, digits=0):
    if x is None:
        return "–"
    return f"{x:,.{digits}f}"


def _table(headers, rows):
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


V = '<span class="tag v">VERIFIED</span>'
H = '<span class="tag h">HYPOTHESIS</span>'


def write_html(result, settings, out_dir, notes):
    r = result
    ti = r["tier_info"]
    t1, t2, t3 = ti["tier_days"]
    disc = settings["tier_discounts_pct"]

    tier_rows = []
    for t in (3, 2, 1, 0):
        s = r["tier_summary"].get(t, {"items": 0, "units": 0, "value_now": 0, "value_after": 0})
        when = {0: f"under {t1} days without a sale", 1: f"{t1}-{t2 - 1} days", 2: f"{t2}-{t3 - 1} days",
                3: f"{t3}+ days"}[t]
        tier_rows.append([TIER_NAMES[t], when, f"{disc.get(str(t), 0)}%" if t else "0%",
                          _n(s["items"]), _n(s["units"]), _money(s["value_now"]), _money(s["value_after"])])

    band_rows = []
    for lo, hi in PRICE_BANDS:
        b = r["price_bands"].get(band_label(lo, hi))
        if b:
            band_rows.append([band_label(lo, hi), _n(b["items_in_stock"]), _n(b["units_sold"]),
                              f"{_n(b['sell_through_pct'], 1)}%", _n(b["median_days_to_sell"]), _n(b["dated_sales"])])

    cats = sorted(r["categories"].items(), key=lambda kv: -(kv[1]["units_sold"] + kv[1]["units_in_stock"]))[:25]
    cat_rows = [[html.escape(k) + (" 🔥" if k in r["hot_categories"] else ""), _n(c["items_in_stock"]),
                 _n(c["units_in_stock"]), _n(c["units_sold"]), f"{_n(c['sell_through_pct'], 1)}%",
                 _n(c["median_days_to_sell"]), _money(c["stock_value"])] for k, c in cats]

    age_rows = [[k + " days" if k != "unknown" else "unknown", _n(a["items"]), _n(a["units"]), _money(a["value"])]
                for k, a in r["aging"].items() if a["items"] or k != "unknown"]

    channels = sorted({c for m in r["monthly"].values() for c in m})
    month_rows = [[m] + [_n(v.get(c, 0)) for c in channels] for m, v in r["monthly"].items()]

    d = r["discounts"]
    disc_rows = [[k, _n(v["sales"]), f"{v['avg_pct_off']}%" if k == "discounted" else "–",
                  _n(v["median_days_to_sell"])] for k, v in d.items()]

    top = [x for x in r["tiers"] if x["tier"] > 0][:40]
    item_rows = [[TIER_NAMES[x["tier"]].split(":")[0], html.escape(x["title"][:70]), _n(x["qty"]),
                  _money(x["price"]), _money(x["suggested_price"]), _n(x["days_without_sale"]),
                  html.escape(x["reason"])] for x in top]

    learned = ""
    if ti["n"]:
        learned = (f"Of {ti['n']} sales where we know both the listing date and the sale date, half sold within "
                   f"<b>{_n(ti['p50'])} days</b>, 75% within <b>{_n(ti['p75'])}</b>, 90% within <b>{_n(ti['p90'])}</b>. {V}")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RRR Sale Tier Report</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:0;background:#faf8f5;color:#222;line-height:1.45}}
main{{max-width:980px;margin:0 auto;padding:16px}}
h1{{margin:.2em 0}} h2{{margin-top:1.8em;border-bottom:2px solid #e5ded3;padding-bottom:4px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}}
.card{{background:#fff;border:1px solid #e5ded3;border-radius:10px;padding:12px}}
.card b{{display:block;font-size:1.6em}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:.92em;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:top}}
th{{background:#f2ede6}}
.tag{{font-size:.7em;font-weight:700;padding:2px 6px;border-radius:4px;vertical-align:middle}}
.v{{background:#dff3e3;color:#17602a}} .h{{background:#fff1d6;color:#7a4b00}}
.note{{background:#fff;border-left:4px solid #c9a96e;padding:8px 12px;margin:10px 0}}
small{{color:#666}}
</style></head><body><main>
<h1>Sale Tier Report</h1>
<small>Rescue Retro Relics · data as of {r['today'].isoformat()} · read-only, nothing on the store was changed</small>

<div class="cards">
<div class="card"><b>{_n(r['units_total'])}</b>units sold in this window</div>
<div class="card"><b>{_n(r['products_active_in_stock'])}</b>active items in stock</div>
<div class="card"><b>{_n(sum(v['items'] for k, v in r['tier_summary'].items() if k > 0))}</b>items suggested for a sale</div>
<div class="card"><b>{r['store_sell_through_pct']}%</b>store sell-through</div>
</div>

<h2>Suggested sale tiers {H}</h2>
<p>An item's clock is <b>days without a sale</b>: days since it was listed, or since its last sale if it has sold before.
Tier start days: <b>{t1} / {t2} / {t3}</b> ({html.escape(ti["source"])}). {learned}</p>
{_table(['Tier', 'Days without a sale', 'Discount', 'Items', 'Units', 'Stock value now', 'After discount'], tier_rows)}
<p class="note">These tiers are a starting rule to test, not a prediction. Try one tier for 2-3 weeks, re-run this report,
and see if those items sold. Full list: <b>sale_tier_suggestions.csv</b>. Price floor: never below cost + ${settings['min_profit_over_cost']:.2f}
(when cost is filled in) or ${settings['min_price']:.2f}.</p>

<h2>Items to put on sale first {H}</h2>
<small>Highest tier first, then most money tied up. Top 40 shown.</small>
{_table(['Tier', 'Item', 'Qty', 'Price now', 'Suggested', 'Days w/o sale', 'Why'], item_rows)}

<h2>What sells, by price {V}</h2>
<small>Sell-through = units sold ÷ (units sold + units still in stock). Higher = moves faster.</small>
{_table(['Price', 'Items in stock', 'Units sold', 'Sell-through', 'Median days to sell', 'Sales with dates'], band_rows)}

<h2>What sells, by product type {V}</h2>
<small>Top 25 by volume. 🔥 = sells at least {settings['hot_category_ratio']}x the store average, so it waits one tier longer.</small>
{_table(['Product type', 'Items in stock', 'Units in stock', 'Units sold', 'Sell-through', 'Median days', 'Stock value'], cat_rows)}

<h2>How old is the stock {V}</h2>
<small>Days since the product was added to Shopify.</small>
{_table(['Age', 'Items', 'Units', 'Value at list price'], age_rows)}

<h2>Units sold per month {V}</h2>
{_table(['Month'] + channels, month_rows)}

<h2>Did discounts speed things up? {H}</h2>
<small>Shopify sales only (eBay orders don't say the list price). This shows a pattern, not a cause.</small>
{_table(['Shopify sales', 'Count', 'Avg % off', 'Median days to sell'], disc_rows)}

<h2>Notes on the data</h2>
<ul>{''.join(f'<li>{html.escape(n)}</li>' for n in notes)}</ul>
</main></body></html>"""
    path = out_dir / "sale_report.html"
    path.write_text(page, encoding="utf-8")
    return path
