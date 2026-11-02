"""The math. Pure functions: products + sales in, plain dicts out.

Every number here is COUNTED from the data (verified). The tier thresholds and
discounts are RULES built on top of those counts (hypotheses to test, not predictions).
"""

import math
from collections import Counter, defaultdict

PRICE_BANDS = [(0, 10), (10, 15), (15, 20), (20, 30), (30, 50), (50, None)]
AGE_BUCKETS = [(0, 30), (31, 60), (61, 90), (91, 180), (181, None)]


def band_label(lo, hi):
    return f"${lo}+" if hi is None else f"${lo}-{hi}"


def price_band(price):
    if price is None:
        return "unknown"
    for lo, hi in PRICE_BANDS:
        if price >= lo and (hi is None or price < hi):
            return band_label(lo, hi)
    return "unknown"


def percentile(values, pct):
    vals = sorted(values)
    if not vals:
        return None
    k = (len(vals) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def attach_days(sales, products):
    """days_to_sell = sale date - date the product was added to Shopify (only when that makes sense)."""
    by_id = {p["id"]: p for p in products}
    for s in sales:
        p = by_id.get(s.get("product_id"))
        s["days_to_sell"] = None
        if p and p["created"] and s["date"] and s["date"] >= p["created"]:
            s["days_to_sell"] = (s["date"] - p["created"]).days
    return sales


def learn_tier_days(sales, settings):
    """Tier start days from how long sold items took: 75th, 90th percentile, then further out."""
    days = [s["days_to_sell"] for s in sales if s.get("days_to_sell") is not None]
    gap = settings["min_tier_gap_days"]
    info = {"n": len(days), "p50": percentile(days, 50), "p75": percentile(days, 75), "p90": percentile(days, 90)}
    manual = settings["tier_days"]
    if manual != "auto":
        info.update(source="your sale_tiers.json", tier_days=list(manual))
        return info
    if len(days) < settings["min_dated_sales_for_auto"]:
        info.update(source=f"defaults (only {len(days)} dated sales; need {settings['min_dated_sales_for_auto']})",
                    tier_days=list(settings["fallback_tier_days"]))
        return info
    t1 = max(30, round(info["p75"]))
    t2 = max(t1 + gap, round(info["p90"]))
    t3 = max(t2 + gap, round(info["p90"] * 1.5))
    info.update(source="learned from your sales (75th / 90th percentile of days-to-sell)", tier_days=[t1, t2, t3])
    return info


def group_stats(products, sales, key_fn, today):
    """Per group: items in stock, units in stock, $ in stock, units sold, sell-through, median days to sell."""
    by_id = {p["id"]: p for p in products}
    g = defaultdict(lambda: {"items_in_stock": 0, "units_in_stock": 0, "stock_value": 0.0,
                             "units_sold": 0, "revenue": 0.0, "days": []})
    for p in products:
        if p["status"] == "ACTIVE" and p["qty"] > 0:
            row = g[key_fn(p)]
            row["items_in_stock"] += 1
            row["units_in_stock"] += p["qty"]
            row["stock_value"] += p["qty"] * (p["price"] or 0)
    for s in sales:
        p = by_id.get(s.get("product_id"))
        if not p:
            continue
        row = g[key_fn(p)]
        row["units_sold"] += s["qty"]
        row["revenue"] += s["qty"] * (s["unit_price"] if s["unit_price"] is not None else (p["price"] or 0))
        if s.get("days_to_sell") is not None:
            row["days"].append(s["days_to_sell"])
    out = {}
    for k, row in g.items():
        total = row["units_sold"] + row["units_in_stock"]
        out[k] = {
            **{x: row[x] for x in ("items_in_stock", "units_in_stock", "units_sold")},
            "stock_value": round(row["stock_value"], 2),
            "revenue": round(row["revenue"], 2),
            "sell_through_pct": round(100 * row["units_sold"] / total, 1) if total else None,
            "median_days_to_sell": percentile(row["days"], 50),
            "dated_sales": len(row["days"]),
        }
    return out


def round_99(price):
    if price < 1:
        return round(price, 2)
    return round(max(round(price) - 0.01, 0.99), 2)


def suggest_tiers(products, sales, settings, today):
    """One row per in-stock ACTIVE product with its suggested tier and price."""
    tier_info = learn_tier_days(sales, settings)
    t1, t2, t3 = tier_info["tier_days"]
    discounts = {int(k): v for k, v in settings["tier_discounts_pct"].items()}

    cats = group_stats(products, sales, lambda p: p["type"], today)
    total_sold = sum(c["units_sold"] for c in cats.values())
    total_units = total_sold + sum(c["units_in_stock"] for c in cats.values())
    store_st = total_sold / total_units if total_units else 0
    hot = {k for k, c in cats.items()
           if c["units_sold"] >= settings["hot_category_min_sold"] and c["sell_through_pct"] is not None
           and store_st and c["sell_through_pct"] / 100 >= settings["hot_category_ratio"] * store_st}

    last_sale, sold_units = {}, Counter()
    for s in sales:
        if s.get("product_id") and s["date"]:
            sold_units[s["product_id"]] += s["qty"]
            if s["product_id"] not in last_sale or s["date"] > last_sale[s["product_id"]]:
                last_sale[s["product_id"]] = s["date"]

    rows = []
    for p in products:
        if p["status"] != "ACTIVE" or p["qty"] <= 0:
            continue
        listed_days = (today - p["created"]).days if p["created"] else None
        since = max(filter(None, [p["created"], last_sale.get(p["id"])]), default=None)
        idle = (today - since).days if since else None
        reasons = []
        if idle is None:
            tier = 0
            reasons.append("no listing date, can't age it")
        else:
            tier = 3 if idle >= t3 else 2 if idle >= t2 else 1 if idle >= t1 else 0
            reasons.append(f"{idle} days without a sale")
            if p["id"] in last_sale:
                reasons.append(f"last sold {last_sale[p['id']].isoformat()}")
        if tier > 0 and p["type"] in hot:
            tier -= 1
            reasons.append(f"'{p['type']}' sells fast, held back one tier")
        pct = discounts.get(tier, 0)
        suggested, floor_hit = p["price"], False
        if pct and p["price"]:
            suggested = p["price"] * (1 - pct / 100)
            if settings["end_in_99"]:
                suggested = round_99(suggested)
            floor = settings["min_price"]
            if p["cost"] is not None:
                floor = max(floor, p["cost"] + settings["min_profit_over_cost"])
            if suggested < floor:
                suggested, floor_hit = round(min(floor, p["price"]), 2), True
                reasons.append(f"held at floor ${floor:.2f}")
        rows.append({
            "tier": tier, "discount_pct": pct if not floor_hit else round(100 * (1 - suggested / p["price"]), 1),
            "product_id": p["id"], "title": p["title"], "type": p["type"], "qty": p["qty"],
            "price": p["price"], "suggested_price": None if suggested is None else round(suggested, 2),
            "cost": p["cost"], "days_listed": listed_days, "days_without_sale": idle,
            "units_sold": sold_units.get(p["id"], 0), "reason": "; ".join(reasons),
        })
    rows.sort(key=lambda r: (-r["tier"], -(r["qty"] * (r["price"] or 0))))
    return rows, tier_info, sorted(hot), store_st


def aging(products, today):
    out = {band_label(lo, hi).replace("$", ""): {"items": 0, "units": 0, "value": 0.0} for lo, hi in AGE_BUCKETS}
    out["unknown"] = {"items": 0, "units": 0, "value": 0.0}
    for p in products:
        if p["status"] != "ACTIVE" or p["qty"] <= 0:
            continue
        key = "unknown"
        if p["created"]:
            age = (today - p["created"]).days
            for lo, hi in AGE_BUCKETS:
                if age >= lo and (hi is None or age <= hi):
                    key = band_label(lo, hi).replace("$", "")
                    break
        out[key]["items"] += 1
        out[key]["units"] += p["qty"]
        out[key]["value"] = round(out[key]["value"] + p["qty"] * (p["price"] or 0), 2)
    return out


def monthly(sales):
    m = defaultdict(lambda: Counter())
    for s in sales:
        if s["date"]:
            m[s["date"].strftime("%Y-%m")][s["channel"]] += s["qty"]
    return dict(sorted(m.items()))


def discount_effect(sales):
    """Shopify sales with a known list price: did discounted ones sell faster? (correlation only)"""
    groups = {"discounted": [], "full price": []}
    for s in sales:
        if s["list_price"] and s["unit_price"] is not None:
            key = "discounted" if s["unit_price"] < s["list_price"] - 0.005 else "full price"
            groups[key].append(s)
    out = {}
    for k, rows in groups.items():
        days = [r["days_to_sell"] for r in rows if r.get("days_to_sell") is not None]
        offs = [100 * (1 - r["unit_price"] / r["list_price"]) for r in rows if r["list_price"]]
        out[k] = {"sales": len(rows), "median_days_to_sell": percentile(days, 50),
                  "avg_pct_off": round(sum(offs) / len(offs), 1) if offs and k == "discounted" else 0}
    return out


def run(products, sales, settings, today):
    attach_days(sales, products)
    tiers, tier_info, hot, store_st = suggest_tiers(products, sales, settings, today)
    tier_summary = defaultdict(lambda: {"items": 0, "units": 0, "value_now": 0.0, "value_after": 0.0})
    for r in tiers:
        t = tier_summary[r["tier"]]
        t["items"] += 1
        t["units"] += r["qty"]
        t["value_now"] = round(t["value_now"] + r["qty"] * (r["price"] or 0), 2)
        t["value_after"] = round(t["value_after"] + r["qty"] * (r["suggested_price"] or 0), 2)
    matched = [s for s in sales if s.get("product_id")]
    return {
        "today": today,
        "sales_total": len(sales),
        "units_total": sum(s["qty"] for s in sales),
        "by_channel": dict(Counter(s["channel"] for s in sales)),
        "matched": len(matched),
        "match_methods": dict(Counter(s.get("match", "") for s in sales)),
        "tier_info": tier_info,
        "tiers": tiers,
        "tier_summary": dict(sorted(tier_summary.items())),
        "hot_categories": hot,
        "store_sell_through_pct": round(100 * store_st, 1),
        "price_bands": group_stats(products, sales, lambda p: price_band(p["price"]), today),
        "categories": group_stats(products, sales, lambda p: p["type"], today),
        "aging": aging(products, today),
        "monthly": monthly(sales),
        "discounts": discount_effect(sales),
        "products_total": len(products),
        "products_active_in_stock": sum(1 for p in products if p["status"] == "ACTIVE" and p["qty"] > 0),
    }
