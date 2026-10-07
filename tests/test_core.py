import unittest
import xml.etree.ElementTree as ET

from rrr_sync import ebay_auth, matching, orders
from rrr_sync.config import Config
from rrr_sync.ebay import EbayListing, parse_active_list
from rrr_sync.shopify import ShopifyVariant
from rrr_sync.sync import reconcile


def variant(vid, title, sku="", qty=3):
    return ShopifyVariant(vid, f"inv-{vid}", sku, title, qty, True, "ACTIVE")


class ReconcileTest(unittest.TestCase):
    def test_first_sync_uses_policy(self):
        self.assertEqual(reconcile(5, 3, None), 3)
        self.assertEqual(reconcile(5, 3, None, "ebay"), 5)
        self.assertEqual(reconcile(5, 3, None, "shopify"), 3)

    def test_sales_on_both_sides_both_count(self):
        last = {"qty": 5, "ebay": 5, "shopify": 5}
        self.assertEqual(reconcile(4, 3, last), 2)   # 1 eBay sale + 2 Shopify sales

    def test_restock_on_one_side_flows_through(self):
        last = {"qty": 2, "ebay": 2, "shopify": 2}
        self.assertEqual(reconcile(2, 6, last), 6)

    def test_never_negative(self):
        last = {"qty": 1, "ebay": 1, "shopify": 1}
        self.assertEqual(reconcile(0, 0, last), 0)

    def test_half_finished_update_is_not_double_counted(self):
        # Last run: target 3, eBay updated to 3, Shopify update failed and stayed at 5.
        last = {"qty": 3, "ebay": 3, "shopify": 5}
        self.assertEqual(reconcile(3, 5, last), 3)
        self.assertEqual(reconcile(2, 4, last), 1)


class MatchingTest(unittest.TestCase):
    def test_sku_then_title_then_fuzzy(self):
        listings = [
            EbayListing("1", "SNP-REM-KETCHUP", "Something else entirely", 7),
            EbayListing("2", "", "Snoopy Hippers Belle Pink Bow Peanuts Sonny Angel Mini Figure", 5),
            EbayListing("3", "", "Re-Ment Snoopy Life in a Bottle Watering Can Gardener Figure", 6),
            EbayListing("4", "", "Completely unrelated Garfield mug", 1),
        ]
        variants = [
            variant("a", "Re-Ment Snoopy Ketchup", sku="snp-rem-ketchup"),
            variant("b", "Snoopy Hippers Belle Pink Bow Peanuts Sonny Angel Mini Figure"),
            variant("c", "Re-Ment Snoopy Life in a Bottle Watering Can Gardener Figure Mini"),
        ]
        rows = {r["ebay_item_id"]: r for r in matching.find_matches(listings, variants)}
        self.assertEqual((rows["1"]["method"], rows["1"]["approved"]), ("sku", "Y"))
        self.assertEqual((rows["2"]["method"], rows["2"]["approved"]), ("exact_title", "Y"))
        self.assertEqual((rows["3"]["method"], rows["3"]["approved"]), ("fuzzy", ""))
        self.assertEqual(rows["4"]["method"], "unmatched")

    def test_previous_decisions_are_kept(self):
        listings = [EbayListing("2", "", "Snoopy Belle", 5)]
        variants = [variant("b", "Snoopy Belle"), variant("z", "Other")]
        prev = {"2": {"approved": "N", "method": "exact_title", "score": "1.0", "shopify_variant_id": "b"}}
        self.assertEqual(matching.find_matches(listings, variants, prev)[0]["approved"], "N")

    def test_one_shopify_variant_not_auto_approved_twice(self):
        listings = [EbayListing("1", "", "Snoopy Belle", 1), EbayListing("2", "", "Snoopy Belle", 1)]
        rows = matching.find_matches(listings, [variant("b", "Snoopy Belle")])
        self.assertEqual(sorted(r["approved"] for r in rows), ["", "Y"])


class EbayParsingTest(unittest.TestCase):
    XML = """<?xml version="1.0"?>
    <GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Success</Ack>
      <ActiveList>
        <ItemArray>
          <Item><ItemID>111</ItemID><Title>Snoopy Mug</Title><SKU>MUG1</SKU>
            <Quantity>5</Quantity><QuantityAvailable>3</QuantityAvailable></Item>
          <Item><ItemID>222</ItemID><Title>Peanuts Shirt</Title><Quantity>9</Quantity>
            <Variations>
              <Variation><SKU>SH-S</SKU><Quantity>4</Quantity>
                <SellingStatus><QuantitySold>1</QuantitySold></SellingStatus>
                <VariationSpecifics><NameValueList><Name>Size</Name><Value>S</Value></NameValueList></VariationSpecifics>
              </Variation>
            </Variations>
          </Item>
        </ItemArray>
        <PaginationResult><TotalNumberOfPages>2</TotalNumberOfPages></PaginationResult>
      </ActiveList>
    </GetMyeBaySellingResponse>"""

    def test_parse(self):
        listings, pages = parse_active_list(ET.fromstring(self.XML))
        self.assertEqual(pages, 2)
        self.assertEqual((listings[0].key, listings[0].available), ("111", 3))
        self.assertEqual((listings[1].key, listings[1].title, listings[1].available),
                         ("222:SH-S", "Peanuts Shirt S", 3))


class AuthTest(unittest.TestCase):
    def test_extract_code(self):
        url = "https://auth2.ebay.com/oauth2/ThirdPartyAuthSucessFailure.isapi?isAuthSuccessful=true&code=v%5E1.1%23abc&expires_in=299"
        self.assertEqual(ebay_auth.extract_code(url), "v^1.1#abc")
        self.assertEqual(ebay_auth.extract_code("v%5E1.1%23abc"), "v^1.1#abc")

    def test_auth_url(self):
        cfg = Config.load()
        cfg.ebay_client_id, cfg.ebay_runame = "CID", "My-RuName"
        url = ebay_auth.auth_url(cfg, state="s")
        self.assertIn("client_id=CID", url)
        self.assertIn("redirect_uri=My-RuName", url)
        self.assertIn("sell.fulfillment", url)


class OrdersTest(unittest.TestCase):
    def test_one_row_per_line_item(self):
        o = {"orderId": "O1", "pricingSummary": {"total": {"value": "20.00", "currency": "USD"}},
             "lineItems": [{"sku": "A", "quantity": 1}, {"sku": "B", "quantity": 2}]}
        rows = list(orders.order_rows([o]))
        self.assertEqual([r["sku"] for r in rows], ["A", "B"])
        self.assertEqual(rows[1]["order_total"], "20.00")


if __name__ == "__main__":
    unittest.main()
