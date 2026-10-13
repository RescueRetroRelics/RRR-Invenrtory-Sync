import json
import unittest
from unittest import mock

from rrr_sync import ebay_inventory as inv
from rrr_sync import ebay_listings as el
from rrr_sync import http
from rrr_sync.config import Config


def cfg():
    return Config(*[""] * 12)


class FakeEbay:
    """Stands in for http.request: records calls, answers like eBay does."""

    def __init__(self):
        self.calls = []
        self.items = {
            "15420259402024": {"sku": "15420259402024", "locale": "en_US", "condition": "NEW",
                               "availability": {"shipToLocationAvailability": {"quantity": 1}},
                               "product": {"title": "Funko Classic Mickey 1/6", "description": "desc"}},
            "999999": {"sku": "999999", "product": {"title": "No offers"}},
        }
        self.offers = {
            "308888822011": {"offerId": "308888822011", "sku": "15420259402024", "status": "UNPUBLISHED",
                             "marketplaceId": "EBAY_US", "format": "FIXED_PRICE", "categoryId": "149372",
                             "merchantLocationKey": "RRR", "availableQuantity": 1,
                             "pricingSummary": {"price": {"value": "9.99", "currency": "USD"}},
                             "listingPolicies": {"eBayPlusIfEligible": False}},
        }

    def __call__(self, method, url, headers=None, body=None, **kw):
        self.calls.append((method, url, headers, body))
        path = url.split("/sell/inventory/v1", 1)[1]
        route, _, query = path.partition("?")
        if method == "GET" and route == "/inventory_item":
            items = list(self.items.values())
            return 200, json.dumps({"inventoryItems": items, "total": len(items)})
        if method == "GET" and route == "/offer":
            sku = dict(p.split("=") for p in query.split("&"))["sku"]
            found = [o for o in self.offers.values() if o["sku"] == sku]
            if not found:
                raise http.HttpError(404, json.dumps({"errors": [{"errorId": 25713, "message": "This Offer is not available."}]}), url)
            return 200, json.dumps({"offers": found, "total": len(found)})
        if method == "GET" and route.startswith("/offer/"):
            return 200, json.dumps(self.offers[route.split("/")[2]])
        if method == "GET" and route.startswith("/inventory_item/"):
            return 200, json.dumps(self.items[route.split("/")[2]])
        if method == "PUT":
            return 204, ""
        if method == "POST" and route.endswith("/publish"):
            return 200, json.dumps({"listingId": "110555"})
        raise AssertionError(f"unexpected {method} {url}")

    def bodies(self, method, fragment):
        return [json.loads(b) for m, u, h, b in self.calls if m == method and fragment in u]


SHOPIFY = {
    "id": "gid://shopify/Product/15420259402024", "number": "15420259402024",
    "title": "Funko Mystery Mini Classic Disney Classic Mickey Mouse, 1/6",
    "description": "<p>The OG mouse</p>", "price": "8.99", "quantity": 1,
    "images": ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"],
}


class InventoryApiTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeEbay()
        patches = [mock.patch.object(http, "request", self.fake),
                   mock.patch.object(inv, "get_access_token", lambda c: "TOKEN")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_every_request_sends_content_language(self):
        inv.all_offers(cfg())
        inv.publish_offer(cfg(), "308888822011")
        self.assertTrue(self.fake.calls)
        for _, _, headers, _ in self.fake.calls:
            self.assertEqual(headers["Content-Language"], "en-US")
            self.assertEqual(headers["Authorization"], "Bearer TOKEN")

    def test_offers_are_fetched_per_sku_and_404_means_none(self):
        offers, errors = inv.all_offers(cfg())
        self.assertEqual([o["offerId"] for o in offers], ["308888822011"])
        self.assertEqual(offers[0]["_title"], "Funko Classic Mickey 1/6")
        self.assertEqual(errors, [])
        offer_gets = [u for m, u, _, _ in self.fake.calls if m == "GET" and "/offer?" in u]
        self.assertEqual(len(offer_gets), 2)
        self.assertTrue(all("sku=" in u for u in offer_gets))

    def test_publish_sends_empty_body(self):
        inv.publish_offer(cfg(), "308888822011")
        method, url, _, body = self.fake.calls[-1]
        self.assertEqual((method, body), ("POST", ""))

    def test_publish_attaches_photos_policies_and_price(self):
        with mock.patch.object(el, "shopify_product", return_value=SHOPIFY):
            result = el.publish(cfg(), "308888822011")
        self.assertEqual(result["listing_id"], "110555")
        self.assertEqual(result["images"], 2)

        item = self.fake.bodies("PUT", "/inventory_item/15420259402024")[0]
        self.assertEqual(item["product"]["imageUrls"], SHOPIFY["images"])
        self.assertNotIn("sku", item)       # read-only fields stripped
        self.assertNotIn("locale", item)

        offer = self.fake.bodies("PUT", "/offer/308888822011")[0]
        self.assertEqual(offer["pricingSummary"]["price"]["value"], "10.99")
        pol = offer["listingPolicies"]
        self.assertEqual(pol["fulfillmentPolicyId"], "284028239015")
        self.assertEqual(pol["paymentPolicyId"], "283965435015")
        self.assertEqual(pol["returnPolicyId"], "291780816015")
        self.assertEqual(pol["bestOfferTerms"], {"bestOfferEnabled": True})
        self.assertFalse(pol["eBayPlusIfEligible"])   # unrelated fields kept
        for read_only in ("offerId", "sku", "status", "marketplaceId", "format"):
            self.assertNotIn(read_only, offer)
        self.assertEqual(offer["listingDescription"], "desc")

    def test_explicit_image_urls_win_over_shopify(self):
        with mock.patch.object(el, "shopify_product", return_value=SHOPIFY):
            el.publish(cfg(), "308888822011", image_urls=["http://x.com/1.jpg", "https://x.com/2.jpg"])
        item = self.fake.bodies("PUT", "/inventory_item/")[0]
        self.assertEqual(item["product"]["imageUrls"], ["https://x.com/1.jpg", "https://x.com/2.jpg"])

    def test_publish_refuses_without_any_photos(self):
        with mock.patch.object(el, "shopify_product", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "No photos"):
                el.publish(cfg(), "308888822011")
        self.assertFalse(self.fake.bodies("PUT", "/offer/"))


class CategoryAndConfigTest(unittest.TestCase):
    def test_category_suggestions_returns_ids_and_paths(self):
        body = {"categorySuggestions": [{
            "category": {"categoryId": "31802", "categoryName": "Lip Gloss"},
            "categoryTreeNodeAncestors": [{"categoryName": "Lips"}, {"categoryName": "Makeup"},
                                          {"categoryName": "Health & Beauty"}],
        }]}
        calls = []
        def fake(method, url, headers=None, body_=None, **kw):
            calls.append((url, headers))
            return 200, json.dumps(body)
        with mock.patch.object(http, "request", fake), \
             mock.patch.object(inv, "get_access_token", lambda c: "TOKEN"):
            got = inv.category_suggestions(cfg(), "Chucky lip gloss")
        self.assertEqual(got, [{"category_id": "31802", "path": "Health & Beauty > Makeup > Lips > Lip Gloss"}])
        self.assertIn("q=Chucky+lip+gloss", calls[0][0])

    def test_missing_settings_use_real_env_names(self):
        from rrr_sync.config import ConfigError
        with self.assertRaisesRegex(ConfigError, "SHOPIFY_STORE_DOMAIN, SHOPIFY_LOCATION_ID"):
            cfg().require("shopify_domain", "shopify_location_id")

    def test_dotenv_reads_notepad_txt_and_bom(self):
        import os, tempfile
        from pathlib import Path
        from rrr_sync.config import load_dotenv
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            Path(d, ".env.txt").write_text("\ufeffEBAY_CLIENT_ID=abc\nSHOPIFY_STORE_DOMAIN=x.myshopify.com\n", encoding="utf-8")
            load_dotenv(Path(d, ".env"))
            self.assertEqual(os.environ["EBAY_CLIENT_ID"], "abc")
            self.assertEqual(os.environ["SHOPIFY_STORE_DOMAIN"], "x.myshopify.com")


class HelpersTest(unittest.TestCase):
    def test_price_is_shopify_plus_two(self):
        self.assertEqual(el.ebay_price("8.99"), "10.99")
        self.assertEqual(el.ebay_price("43.99"), "45.99")
        self.assertEqual(el.ebay_price(15), "17.00")

    def test_sku_from_gid(self):
        self.assertEqual(el.sku_from_shopify_id("gid://shopify/Product/15420259402024"), "15420259402024")
        self.assertEqual(el.sku_from_shopify_id("15420259402024"), "15420259402024")

    def test_shopify_id_from_sku(self):
        self.assertEqual(el.shopify_id_from_sku("15420259205416"), "15420259205416")
        self.assertEqual(el.shopify_id_from_sku("15420259205416-B"), "15420259205416")
        self.assertIsNone(el.shopify_id_from_sku("FNKO-DMINI-MICK-STD-001"))

    def test_image_urls_accept_string_and_dedupe(self):
        self.assertEqual(el.clean_image_urls("https://a/1.jpg, https://a/1.jpg https://a/2.jpg"),
                         ["https://a/1.jpg", "https://a/2.jpg"])
        self.assertEqual(len(el.clean_image_urls([f"https://a/{i}.jpg" for i in range(30)])), 24)


if __name__ == "__main__":
    unittest.main()
