"""Two-way quantity sync for approved eBay <-> Shopify pairs.

For each pair we remember the shared quantity after the last sync (L) and the
value each side was left at (Le, Ls; normally both equal L). On the next run,
with eBay now at E and Shopify at S, each side's change since then is a sale
(or a restock) that the other side has not seen:

    new = L - (Le - E) - (Ls - S)      (never below 0)

So one sale on eBay and one on Shopify both come off the shared count.
The first time a pair is synced there is no L, and the --initial policy
decides the starting point (default: the lower of the two, so nothing oversells).
"""

import json
import time
from dataclasses import dataclass, field

from . import ebay
from .config import DATA_DIR
from .matching import approved_pairs

STATE_FILE = DATA_DIR / "sync_state.json"


def reconcile(ebay_qty, shop_qty, last, initial="min"):
    """last: {"qty": L, "ebay": Le, "shopify": Ls} from the previous sync, or None."""
    if last is None:
        return {"min": min, "ebay": lambda e, s: e, "shopify": lambda e, s: s}[initial](ebay_qty, shop_qty)
    return max(last["qty"] - (last["ebay"] - ebay_qty) - (last["shopify"] - shop_qty), 0)


@dataclass
class SyncReport:
    changes: list = field(default_factory=list)   # (title, ebay_old, shop_old, new)
    skipped: list = field(default_factory=list)   # (title/key, reason)
    errors: list = field(default_factory=list)    # (title, message)


def _load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def run(cfg, shop, apply=False, initial="min"):
    pairs = approved_pairs()
    listings = {l.key: l for l in ebay.active_listings(cfg)}
    variants = {v.variant_id: v for v in shop.variants()}
    state = _load_state()
    report = SyncReport()
    reference = f"gid://rrr-inventory-sync/SyncJob/{time.strftime('%Y%m%dT%H%M%S')}"

    used = {}
    for key, vid in pairs.items():
        if vid in used:
            report.skipped.append((key, f"Shopify variant already paired with eBay {used[vid]}"))
            continue
        used[vid] = key
        listing, variant = listings.get(key), variants.get(vid)
        if listing is None:
            report.skipped.append((key, "eBay listing no longer active"))
            continue
        if variant is None:
            report.skipped.append((listing.title, "Shopify variant not found (deleted or archived)"))
            continue
        if not variant.tracked or variant.available is None:
            report.skipped.append((listing.title, "Shopify inventory not tracked at the sync location"))
            continue

        state_key = f"{key}|{vid}"
        target = reconcile(listing.available, variant.available, state.get(state_key), initial)
        if target == listing.available == variant.available:
            state[state_key] = {"qty": target, "ebay": target, "shopify": target}
            continue

        report.changes.append((listing.title, listing.available, variant.available, target))
        if not apply:
            continue
        # Record what each side actually ends up at, so a half-finished update
        # (say eBay worked and Shopify didn't) is finished correctly next run.
        ebay_now, shop_now = listing.available, variant.available
        try:
            if ebay_now != target:
                ebay.set_quantity(cfg, listing, target)
                ebay_now = target
            if shop_now != target:
                errs = shop.set_available(variant, target, reference)
                if errs:
                    raise RuntimeError("; ".join(errs))
                shop_now = target
        except Exception as e:  # keep going; one bad listing shouldn't stop the rest
            report.errors.append((listing.title, str(e)))
        state[state_key] = {"qty": target, "ebay": ebay_now, "shopify": shop_now}

    if apply:
        _save_state(state)
    return report
