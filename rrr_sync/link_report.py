"""Link every eBay listing to its Shopify product: six read-only spreadsheets.

Strict rules (Stephanie's): auto-match only when certain, meaning the same SKU
on both sides, or the exact same title once emojis, symbols, accents and case
are ignored. Anything less certain goes to "can't match" with a best guess
noted, and is never picked automatically. Several eBay listings pointing at one
Shopify product is normal (quantity-one copies) and is reported as a group.

Nothing on Shopify or eBay is changed.
"""

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .matching import _score

GUESS_MIN = 0.55   # below this, an unmatched eBay listing counts as "not on Shopify"
BIG_GROUP = 5


def clean(title):
    """Lowercase, accents folded (Pokémon -> pokemon), emojis and symbols dropped."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def sku_kind(sku):
    if not sku:
        return ""
    return "Shopify-style number" if re.fullmatch(r"\d{12,}(-\w+)?", sku) else "old custom SKU"


def product_number(sku):
    m = re.fullmatch(r"(\d{12,})(-\w+)?", sku or "")
    return m.group(1) if m else ""


def link(ebay_items, shop_items, gid_by_handle=None):
    """Return (confirmed, uncertain, not_on_shopify) lists of dicts."""
    gid_by_handle = gid_by_handle or {}
    for s in shop_items:
        if not s.product_gid and s.handle in gid_by_handle:
            s.product_gid = gid_by_handle[s.handle]
    handle_by_number = {gid.rsplit("/", 1)[-1]: h for h, gid in gid_by_handle.items()}

    by_sku, by_title, by_handle = defaultdict(list), defaultdict(list), defaultdict(list)
    for s in shop_items:
        if s.sku:
            by_sku[s.sku.lower()].append(s)
        by_title[clean(s.title)].append(s)
        by_handle[s.handle].append(s)
    tokens = {id(s): set(clean(s.title).split()) for s in shop_items}
    index = defaultdict(set)
    for s in shop_items:
        for t in tokens[id(s)]:
            index[t].add(id(s))
    by_id = {id(s): s for s in shop_items}

    def best_guess(e):
        n = clean(e.title)
        nt = set(n.split())
        shared = Counter(sid for t in nt for sid in index.get(t, ()))
        cands = [sid for sid, c in shared.most_common(60)]
        scored = [(_score(n, clean(by_id[sid].title), nt, tokens[sid]), by_id[sid]) for sid in cands]
        return max(scored, key=lambda p: p[0], default=(0.0, None))

    confirmed, uncertain, missing = [], [], []
    for e in ebay_items:
        hit, method, why = None, "", ""
        sku_hits = by_sku.get(e.sku.lower(), []) if e.sku else []
        number = product_number(e.sku)
        title_hits = by_title.get(clean(e.title), [])
        if len(sku_hits) == 1:
            hit, method = sku_hits[0], "same SKU"
        elif number and number in handle_by_number and len(by_handle[handle_by_number[number]]) == 1:
            hit, method = by_handle[handle_by_number[number]][0], "eBay SKU = Shopify product number"
        elif len(title_hits) == 1:
            hit, method = title_hits[0], "exact title (emojis/symbols ignored)"
        elif len(sku_hits) > 1:
            why = f"SKU is on {len(sku_hits)} Shopify items"
        elif len(title_hits) > 1:
            why = f"title matches {len(title_hits)} Shopify items/variants exactly"

        if hit:
            confirmed.append({"ebay": e, "shop": hit, "method": method})
            continue
        score, guess = best_guess(e)
        if len(title_hits) > 1:
            guess, score = title_hits[0], 1.0
        if why or (guess is not None and score >= GUESS_MIN):
            uncertain.append({"ebay": e, "guess": guess, "score": round(score, 2),
                              "why": why or "titles are close but not identical"})
        else:
            missing.append({"ebay": e, "guess": guess, "score": round(score, 2)})
    return confirmed, uncertain, missing


def _w(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _closeness(score):
    return "very close" if score >= 0.9 else "close" if score >= 0.75 else "loose"


def _m(x):
    return "" if x is None else f"{x:.2f}"


def write(out_dir, shop_items, confirmed, uncertain, missing, last_sale=None):
    """Write the six spreadsheets plus a summary. Returns the summary text."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    last_sale = last_sale or {}
    gid = lambda s: s.product_gid or ""

    # 1. confirmed matches
    rows = sorted(confirmed, key=lambda r: (r["shop"].title.lower(), r["ebay"].item_id))
    _w(out / "1_confirmed_matches.csv",
       ["ebay_item_id", "ebay_title", "ebay_sku", "ebay_qty", "shopify_product_gid", "shopify_handle",
        "shopify_title", "shopify_qty", "shopify_status", "matched_by"],
       [[r["ebay"].item_id, r["ebay"].title, r["ebay"].sku, r["ebay"].qty, gid(r["shop"]), r["shop"].handle,
         r["shop"].title, r["shop"].qty, r["shop"].status, r["method"]] for r in rows])

    # 2. duplicate groups
    groups = defaultdict(list)
    for r in confirmed:
        groups[id(r["shop"])].append(r)
    dupes = []
    for g in groups.values():
        if len(g) < 2:
            continue
        s = g[0]["shop"]
        ebay_qty = sum(r["ebay"].qty for r in g)
        flags = []
        if s.qty is not None and ebay_qty > s.qty:
            flags.append("EBAY QTY > SHOPIFY")
        if len(g) >= BIG_GROUP:
            flags.append(f"{BIG_GROUP}+ COPIES")
        dupes.append((flags, s, g, ebay_qty))
    dupes.sort(key=lambda d: (not d[0] or "EBAY" not in d[0][0], -len(d[2]), d[1].title.lower()))
    _w(out / "2_duplicate_groups.csv",
       ["flags", "shopify_title", "shopify_handle", "shopify_product_gid", "shopify_qty", "ebay_copies",
        "ebay_total_qty", "ebay_item_ids"],
       [["; ".join(f), s.title, s.handle, gid(s), s.qty, len(g), q, " ".join(r["ebay"].item_id for r in g)]
        for f, s, g, q in dupes])

    # 3. can't match (uncertain) - best guess shown, never applied
    rows = sorted(uncertain, key=lambda r: -r["score"])
    _w(out / "3_cant_match.csv",
       ["ebay_item_id", "ebay_title", "ebay_sku", "ebay_qty", "why_not_certain", "best_guess_shopify_title",
        "best_guess_handle", "best_guess_gid", "similarity_0_to_1", "how_close", "your_pick_(fill_in)"],
       [[r["ebay"].item_id, r["ebay"].title, r["ebay"].sku, r["ebay"].qty, r["why"],
         r["guess"].title if r["guess"] else "", r["guess"].handle if r["guess"] else "",
         gid(r["guess"]) if r["guess"] else "", r["score"], _closeness(r["score"]), ""] for r in rows])

    # 4. existing SKUs on eBay
    shop_for = {r["ebay"].key: r for r in confirmed}
    sku_rows = []
    for r in confirmed + uncertain + missing:
        e = r["ebay"]
        if not e.sku:
            continue
        c = shop_for.get(e.key)
        sku_rows.append([e.item_id, e.title, e.sku, sku_kind(e.sku),
                         gid(c["shop"]) if c else "", c["shop"].handle if c else "",
                         c["method"] if c else "not matched (see list 3 or 5)",
                         f"gid://shopify/Product/{product_number(e.sku)}" if product_number(e.sku) else ""])
    sku_rows.sort(key=lambda x: (x[3], x[1].lower()))
    _w(out / "4_existing_skus.csv",
       ["ebay_item_id", "ebay_title", "current_sku", "sku_type", "matched_shopify_gid", "matched_shopify_handle",
        "matched_by", "gid_the_sku_points_to (unverified)"], sku_rows)

    # 5. on eBay, not on Shopify (one row per unique item)
    uniq = defaultdict(list)
    for r in missing:
        uniq[clean(r["ebay"].title)].append(r)
    rows5 = sorted(uniq.values(), key=lambda g: g[0]["ebay"].title.lower())
    _w(out / "5_on_ebay_not_shopify.csv",
       ["ebay_title", "copies", "ebay_total_qty", "price", "ebay_item_ids", "closest_shopify_title (weak)",
        "similarity"],
       [[g[0]["ebay"].title, len(g), sum(r["ebay"].qty for r in g),
         " / ".join(sorted({_m(r["ebay"].price) for r in g if r["ebay"].price is not None})),
         " ".join(r["ebay"].item_id for r in g),
         g[0]["guess"].title if g[0]["guess"] else "", g[0]["score"]] for g in rows5])

    # 6. on Shopify, not on eBay
    linked = {id(r["shop"]) for r in confirmed}
    maybe = {id(r["guess"]) for r in uncertain if r["guess"]}
    rows6 = [s for s in shop_items if id(s) not in linked and s.status != "archived"]
    rows6.sort(key=lambda s: (-(s.qty or 0), s.title.lower()))
    _w(out / "6_on_shopify_not_ebay.csv",
       ["shopify_title", "shopify_handle", "shopify_product_gid", "shopify_qty", "status", "price",
        "last_sale_date", "note"],
       [[s.title, s.handle, gid(s), s.qty, s.status, _m(s.price),
         last_sale.get(s.handle, "") or last_sale.get(clean(s.title), ""),
         "might be on eBay: see list 3" if id(s) in maybe else ""] for s in rows6])

    in_stock6 = sum(1 for s in rows6 if (s.qty or 0) > 0)
    over = sum(1 for d in dupes if "EBAY QTY > SHOPIFY" in d[0])
    big = sum(1 for d in dupes if f"{BIG_GROUP}+ COPIES" in d[0])
    long_ = sum(1 for x in sku_rows if x[3].startswith("Shopify"))
    lines = [
        f"eBay listings checked: {len(confirmed) + len(uncertain) + len(missing)}",
        f"1  Confirmed matches:               {len(confirmed)} eBay listings -> {len(groups)} Shopify items",
        f"2  Duplicate groups (2+ eBay copies): {len(dupes)}   (eBay qty > Shopify: {over}, 5+ copies: {big})",
        f"3  Can't match (your call):          {len(uncertain)}",
        f"4  eBay listings that have a SKU:    {len(sku_rows)}   (Shopify-style numbers {long_}, "
        f"old custom {len(sku_rows) - long_})",
        f"5  On eBay, not on Shopify:          {len(rows5)} unique items ({len(missing)} listings)",
        f"6  On Shopify, not on eBay:          {len(rows6)} items ({in_stock6} with stock)",
        "",
        "Read-only. Nothing on Shopify or eBay was changed.",
    ]
    text = "\n".join(lines) + "\n"
    (out / "SUMMARY.txt").write_text(text, encoding="utf-8")
    return text


def last_sale_from_orders_csv(path):
    """{handle or cleaned title: latest order date} from Shopify's orders export."""
    from .reconcile import _get, _read_csv
    out = {}
    for row in _read_csv(path, "Lineitem name"):
        when = (_get(row, "Paid at") or _get(row, "Created at"))[:10]
        name = re.sub(r"\s+-\s+Default Title$", "", _get(row, "Lineitem name"))
        key = clean(name)
        if when and when > out.get(key, ""):
            out[key] = when
    return out


def gids_from_shopify(cfg):
    """{handle: product GID} straight from Shopify (needs the Shopify login in .env)."""
    from .shopify import Shopify
    shop, out, cursor = Shopify(cfg), {}, None
    q = "query($c:String){products(first:250,after:$c){pageInfo{hasNextPage endCursor} nodes{id handle}}}"
    while True:
        conn = shop.graphql(q, {"c": cursor})["products"]
        out.update({n["handle"]: n["id"] for n in conn["nodes"]})
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        cursor = conn["pageInfo"]["endCursor"]
