"""Match eBay listings to Shopify variants and keep the result in a reviewable CSV.

Matches are found in this order:
  1. same SKU on both sides                  -> auto-approved
  2. identical title after normalising       -> auto-approved
  3. close title (fuzzy score >= FUZZY_MIN)  -> left for you to approve

Edit the `approved` column of data/matches.csv (Y or N). Only rows marked Y
are synced. Re-running `match` keeps every Y/N decision you already made.
"""

import csv
import re
from difflib import SequenceMatcher

from .config import DATA_DIR

MATCHES_FILE = DATA_DIR / "matches.csv"
FUZZY_MIN = 0.80
COLUMNS = [
    "approved", "method", "score",
    "ebay_key", "ebay_item_id", "ebay_sku", "ebay_title", "ebay_qty",
    "shopify_variant_id", "shopify_sku", "shopify_title", "shopify_qty",
]


def normalize(title):
    t = title.lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _score(a, b, a_tokens, b_tokens):
    if not a_tokens or not b_tokens:
        return 0.0
    jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def find_matches(listings, variants, previous=None):
    """Return CSV rows (dicts), one per eBay listing."""
    previous = previous or {}
    by_id = {v.variant_id: v for v in variants}
    by_sku, by_title = {}, {}
    for v in variants:
        if v.sku:
            by_sku.setdefault(v.sku.lower(), []).append(v)
        by_title.setdefault(normalize(v.title), []).append(v)
    tokens = {v.variant_id: set(normalize(v.title).split()) for v in variants}

    rows, taken = [], set()

    # Keep earlier human decisions first so auto-matching works around them.
    for listing in listings:
        prev = previous.get(listing.key)
        if prev and prev["approved"].strip().upper() in ("Y", "N") and prev["shopify_variant_id"] in by_id:
            if prev["approved"].strip().upper() == "Y":
                taken.add(prev["shopify_variant_id"])

    for listing in listings:
        prev = previous.get(listing.key)
        if prev and prev["approved"].strip().upper() in ("Y", "N") and prev["shopify_variant_id"] in by_id:
            rows.append(_row(listing, by_id[prev["shopify_variant_id"]],
                             prev["method"], prev["score"], prev["approved"].strip().upper()))
            continue

        match, method, score = None, "", 0.0
        sku_hits = by_sku.get(listing.sku.lower(), []) if listing.sku else []
        title_hits = by_title.get(normalize(listing.title), [])
        if len(sku_hits) == 1:
            match, method, score = sku_hits[0], "sku", 1.0
        elif len(title_hits) == 1:
            match, method, score = title_hits[0], "exact_title", 1.0
        else:
            n = normalize(listing.title)
            n_tokens = set(n.split())
            best = max(
                ((_score(n, normalize(v.title), n_tokens, tokens[v.variant_id]), v) for v in variants),
                key=lambda pair: pair[0],
                default=(0.0, None),
            )
            if best[1] is not None and best[0] >= FUZZY_MIN:
                score, match, method = round(best[0], 3), best[1], "fuzzy"

        if match is None:
            rows.append(_row(listing, None, "unmatched", "", ""))
            continue
        auto_ok = method in ("sku", "exact_title") and match.variant_id not in taken
        if auto_ok:
            taken.add(match.variant_id)
        rows.append(_row(listing, match, method, score, "Y" if auto_ok else ""))
    return rows


def _row(listing, variant, method, score, approved):
    return {
        "approved": approved,
        "method": method,
        "score": score,
        "ebay_key": listing.key,
        "ebay_item_id": listing.item_id,
        "ebay_sku": listing.sku,
        "ebay_title": listing.title,
        "ebay_qty": listing.available,
        "shopify_variant_id": variant.variant_id if variant else "",
        "shopify_sku": variant.sku if variant else "",
        "shopify_title": variant.title if variant else "",
        "shopify_qty": "" if variant is None or variant.available is None else variant.available,
    }


def load_matches(path=MATCHES_FILE):
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["ebay_key"]: row for row in csv.DictReader(f)}


def save_matches(rows, path=MATCHES_FILE):
    path.parent.mkdir(exist_ok=True)
    rows = sorted(rows, key=lambda r: ({"Y": 1, "N": 3}.get(r["approved"], 0 if r["method"] == "fuzzy" else 2), r["ebay_title"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def approved_pairs(path=MATCHES_FILE):
    """{ebay_key: shopify_variant_id} for rows marked Y."""
    return {
        key: row["shopify_variant_id"]
        for key, row in load_matches(path).items()
        if row["approved"].strip().upper() == "Y" and row["shopify_variant_id"]
    }
