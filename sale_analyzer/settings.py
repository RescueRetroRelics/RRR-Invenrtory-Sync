"""Sale tier rules. Change them in sale_tiers.json (next to README.md), not here.

Any key you leave out of sale_tiers.json keeps the default below.
"""

import json

from rrr_sync.config import ROOT

SETTINGS_FILE = ROOT / "sale_tiers.json"

DEFAULTS = {
    # Percent off for each tier. She is fine with thinner margins to move stock faster.
    "tier_discounts_pct": {"1": 10, "2": 20, "3": 30},
    # Days with no sale before an item enters tier 1 / 2 / 3.
    # "auto" = learn them from how fast items actually sold (needs 20+ dated sales);
    # otherwise these numbers are used as-is, e.g. [60, 120, 180].
    "tier_days": "auto",
    "fallback_tier_days": [60, 120, 180],
    "min_tier_gap_days": 21,     # tiers at least this many days apart
    "min_dated_sales_for_auto": 20,
    # Price floors: never suggest below cost + this many dollars, or below min_price.
    "min_profit_over_cost": 3.00,
    "min_price": 4.99,
    # Categories that sell much faster than average wait one tier longer.
    "hot_category_ratio": 1.5,   # sell-through at least 1.5x the store average
    "hot_category_min_sold": 5,
    # Round suggested prices to end in .99
    "end_in_99": True,
}


def load(path=SETTINGS_FILE):
    settings = json.loads(json.dumps(DEFAULTS))
    if path.exists():
        settings.update(json.loads(path.read_text(encoding="utf-8-sig")))
    return settings
