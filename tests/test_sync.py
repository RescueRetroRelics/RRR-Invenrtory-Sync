"""The qty-1 rule, run against fake eBay and Shopify."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rrr_sync import sync
from rrr_sync.ebay import EbayListing
from rrr_sync.shopify import ShopifyVariant


class FakeShop:
    def __init__(self, variants):
        self._variants = variants
        self.adjusted = []

    def variants(self):
        return list(self._variants)

    def adjust_available(self, variant, delta, reference, key):
        self.adjusted.append((variant.variant_id, delta, key))
        return [], variant.available + delta


def order(order_id, line_id, item_id, qty=1, paid="PAID", cancel="NONE_REQUESTED"):
    return {
        "orderId": order_id, "creationDate": "2099-01-01T00:00:00.000Z",
        "orderPaymentStatus": paid, "cancelStatus": {"cancelState": cancel},
        "lineItems": [{"lineItemId": line_id, "legacyItemId": item_id, "sku": "", "title": "Snoopy Belle", "quantity": qty}],
    }


class QtyOneRuleTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(sync, "STATE_FILE", tmp / "state.json"),
            mock.patch.object(sync, "approved_pairs", lambda: {"111": "v1"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.ebay_sets = []

    def run_sync(self, ebay_qty, shop_qty, orders, listed=True):
        listings = [EbayListing("111", "", "Snoopy Belle", ebay_qty)] if listed else []
        variant = ShopifyVariant("v1", "p1", True, "inv1", "", "Snoopy Belle", shop_qty, True, "ACTIVE")
        shop = FakeShop([variant])
        with mock.patch.object(sync.ebay, "active_listings", lambda cfg: listings), \
             mock.patch.object(sync.ebay, "orders_since", lambda cfg, since: orders), \
             mock.patch.object(sync.ebay, "set_quantity", lambda cfg, l, q: self.ebay_sets.append(q)):
            report = sync.run(cfg=None, shop=shop, apply=True)
        return report, shop

    def baseline(self, existing_orders=()):
        self.run_sync(1, 4, list(existing_orders))
        self.ebay_sets.clear()

    def test_first_run_does_not_recount_old_sales(self):
        old = order("O0", "L0", "111")
        report, shop = self.run_sync(1, 4, [old])
        self.assertEqual(shop.adjusted, [])
        report, shop = self.run_sync(1, 4, [old])   # and never later either
        self.assertEqual(shop.adjusted, [])

    def test_ebay_sale_comes_off_shopify_and_relists_one(self):
        self.baseline()
        report, shop = self.run_sync(0, 4, [order("O1", "L1", "111")])
        self.assertEqual([a[:2] for a in shop.adjusted], [("v1", -1)])
        self.assertEqual(self.ebay_sets, [1])
        self.assertTrue(any("Shopify 4 -> 3" in e for e in report.events))

    def test_sale_is_only_counted_once(self):
        self.baseline()
        o = order("O1", "L1", "111")
        self.run_sync(0, 4, [o])
        report, shop = self.run_sync(1, 3, [o])
        self.assertEqual(shop.adjusted, [])

    def test_last_one_sold_on_ebay_stays_at_zero(self):
        self.baseline()
        report, shop = self.run_sync(0, 1, [order("O1", "L1", "111")])
        self.assertEqual(self.ebay_sets, [])          # already 0 on eBay, Shopify now 0
        self.assertEqual([a[:2] for a in shop.adjusted], [("v1", -1)])

    def test_shopify_sells_out_hides_ebay_listing(self):
        self.baseline()
        report, _ = self.run_sync(1, 0, [])
        self.assertEqual(self.ebay_sets, [0])
        self.assertTrue(any(e.startswith("Sold out") for e in report.events))

    def test_shopify_restock_brings_ebay_back(self):
        self.baseline()
        report, _ = self.run_sync(0, 5, [])
        self.assertEqual(self.ebay_sets, [1])

    def test_extra_ebay_quantity_trimmed_to_one(self):
        self.baseline()
        self.run_sync(3, 4, [])
        self.assertEqual(self.ebay_sets, [1])

    def test_unpaid_and_cancelled_orders_are_ignored(self):
        self.baseline()
        _, shop = self.run_sync(1, 4, [order("O1", "L1", "111", paid="PENDING"),
                                       order("O2", "L2", "111", cancel="CANCELED")])
        self.assertEqual(shop.adjusted, [])

    def test_oversell_is_flagged(self):
        self.baseline()
        report, _ = self.run_sync(0, 0, [order("O1", "L1", "111")])
        self.assertTrue(any(e.startswith("OVERSOLD") for e in report.events))

    def test_unmatched_sale_is_flagged_once(self):
        self.baseline()
        report, _ = self.run_sync(1, 4, [order("O1", "L1", "999")])
        self.assertTrue(any(e.startswith("NOT MATCHED") for e in report.events))
        report, _ = self.run_sync(1, 4, [order("O1", "L1", "999")])
        self.assertFalse(any(e.startswith("NOT MATCHED") for e in report.events))

    def test_ended_listing_warned_once(self):
        self.baseline()
        report, _ = self.run_sync(0, 4, [], listed=False)
        self.assertTrue(any(e.startswith("ENDED") for e in report.events))
        report, _ = self.run_sync(0, 4, [], listed=False)
        self.assertFalse(any(e.startswith("ENDED") for e in report.events))


if __name__ == "__main__":
    unittest.main()
