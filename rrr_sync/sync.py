"""Keep eBay at quantity 1 per item, with Shopify holding the real count.

Each run, for every approved eBay <-> Shopify pair:
  1. A new paid eBay order takes the quantity sold off Shopify.
  2. eBay then shows 1 if Shopify has any left, or 0 if Shopify is sold out.

So an eBay sale with 4 on Shopify -> Shopify 3, eBay back to 1.
Shopify sells its last one -> eBay goes to 0 (hidden, not deleted).
Restock on Shopify -> eBay comes back at 1 by itself.

eBay listings are read before orders on purpose: if a sale lands mid-run, the
worst case is a listing that waits one extra run to come back, never a relist
of something that's gone.

Needs eBay's "Out-of-stock control" turned on, so a listing at 0 is hidden
instead of ended and keeps its item number, watchers and sales history.
"""

import json
import time
import uuid
from dataclasses import dataclass, field

from . import ebay
from .config import DATA_DIR
from .matching import approved_pairs

STATE_FILE = DATA_DIR / "sync_state.json"
ORDER_LOOKBACK_DAYS = 14
COUNTED_PAYMENT = ("PAID", "PARTIALLY_REFUNDED")


def ebay_target(shopify_qty):
    return 1 if shopify_qty > 0 else 0


@dataclass
class SyncReport:
    events: list = field(default_factory=list)    # lines worth a notification
    changes: list = field(default_factory=list)   # everything done (or, in a dry run, to do)
    skipped: list = field(default_factory=list)   # (what, why)
    errors: list = field(default_factory=list)


def _load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _iso_days_ago(days):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days * 86400))


def sales_to_count(orders, processed):
    """Yield (order, line_item) for paid, uncancelled line items not yet counted."""
    for o in orders:
        if o.get("orderPaymentStatus") not in COUNTED_PAYMENT:
            continue
        if o.get("cancelStatus", {}).get("cancelState") == "CANCELED":
            continue
        for li in o.get("lineItems", []):
            if li.get("lineItemId") and li["lineItemId"] not in processed:
                yield o, li


def pair_for_line_item(li, pairs):
    item_id, sku = li.get("legacyItemId", ""), li.get("sku", "")
    return pairs.get(f"{item_id}:{sku}") or pairs.get(item_id)


def run(cfg, shop, apply=False):
    pairs = approved_pairs()
    listings = {l.key: l for l in ebay.active_listings(cfg)}
    variants = {v.variant_id: v for v in shop.variants()}
    orders = ebay.orders_since(cfg, _iso_days_ago(ORDER_LOOKBACK_DAYS))
    state = _load_state()
    processed = state.setdefault("processed_line_items", {})
    report = SyncReport()
    say = report.events.append

    # First run ever: whatever eBay sold before today is already reflected in
    # Shopify (you've been doing it by hand), so just remember those sales.
    if not state.get("baseline_done"):
        for o, li in sales_to_count(orders, processed):
            processed[li["lineItemId"]] = o.get("creationDate", "")
        state["baseline_done"] = True
        report.changes.append("First run: marked existing eBay orders as already counted.")

    # 1. eBay sales -> take them off Shopify.
    for o, li in sales_to_count(orders, processed):
        qty = int(li.get("quantity", 1))
        title = li.get("title", "")[:60]
        vid = pair_for_line_item(li, pairs)
        variant = variants.get(vid) if vid else None
        if variant is None or variant.available is None:
            say(f"NOT MATCHED: eBay sold {qty}x {title} (order {o['orderId']}). "
                "Take it off Shopify by hand, then mark the match Y in matches.csv.")
            if apply:
                processed[li["lineItemId"]] = o.get("creationDate", "")
            continue
        before = variant.available
        if apply:
            try:
                errs, after = shop.adjust_available(
                    variant, -qty,
                    reference=f"gid://rrr-inventory-sync/EbayOrder/{o['orderId']}",
                    key=str(uuid.uuid5(uuid.NAMESPACE_URL, "ebay-line-item:" + li["lineItemId"])),
                )
                if errs:
                    raise RuntimeError("; ".join(errs))
            except Exception as e:
                report.errors.append(f"{title}: {e}")
                say(f"ERROR: couldn't take eBay sale of {title} off Shopify: {e}")
                continue
            processed[li["lineItemId"]] = o.get("creationDate", "")
            variant.available = after if after is not None else before - qty
        else:
            variant.available = before - qty
        if variant.available < 0:
            say(f"OVERSOLD: {title} sold on eBay but Shopify was already at {before}. Check stock!")
        else:
            say(f"eBay sale: {title}. Shopify {before} -> {variant.available}.")

    # 2. eBay shows 1 while Shopify has stock, 0 when it doesn't.
    ended = state.setdefault("ended_warned", {})
    used = {}
    for key, vid in pairs.items():
        variant = variants.get(vid)
        title = (variant.title if variant else key)[:60]
        if vid in used:
            report.skipped.append((title, f"Shopify product already paired with eBay {used[vid]}"))
            continue
        used[vid] = key
        if variant is None:
            report.skipped.append((key, "Shopify product not found (deleted or archived)"))
            continue
        if not variant.tracked or variant.available is None:
            report.skipped.append((title, "Shopify inventory not tracked at the sync location"))
            continue
        listing = listings.get(key)
        if listing is None:
            if variant.available > 0 and key not in ended:
                say(f"ENDED: eBay listing {key} for {title} has ended but Shopify still has "
                    f"{variant.available}. Relist it (and turn on Out-of-stock control).")
                ended[key] = time.strftime("%Y-%m-%d")
            continue
        ended.pop(key, None)

        target = ebay_target(variant.available)
        if listing.available == target:
            continue
        if apply:
            try:
                ebay.set_quantity(cfg, listing, target)
            except Exception as e:
                report.errors.append(f"{title}: {e}")
                say(f"ERROR: couldn't set eBay to {target} for {title}: {e}")
                continue
        if target == 0:
            say(f"Sold out: {title}. eBay listing set to 0 (hidden until restocked).")
        elif listing.available == 0:
            say(f"Relisted: {title} back on eBay at 1 (Shopify has {variant.available}).")
        else:
            report.changes.append(f"{title}: eBay {listing.available} -> 1")

    # Forget sales older than the lookback window; eBay won't send them again.
    cutoff = _iso_days_ago(ORDER_LOOKBACK_DAYS + 7)
    for li_id, created in list(processed.items()):
        if created and created < cutoff:
            del processed[li_id]

    if apply:
        _save_state(state)
    return report
