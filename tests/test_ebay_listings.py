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
            offer_id = route.split("/")[2]
            if offer_id not in self.offers:
                raise http.HttpError(404, json.dumps({"errors": [{"errorId": 25713, "message": "This Offer is not available."}]}), url)
            return 200, json.dumps(self.offers[offer_id])
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


class LiveFakeEbay(FakeEbay):
    """FakeEbay with the Mickey offer already live, plus withdraw/delete/Account API."""

    def __init__(self):
        super().__init__()
        mickey = self.offers["308888822011"]
        mickey.update(status="PUBLISHED", listing={"listingId": "227566100694"})
        mickey["listingPolicies"] = {
            "fulfillmentPolicyId": "284028239015", "paymentPolicyId": "283965435015",
            "returnPolicyId": "291780816015", "bestOfferTerms": {"bestOfferEnabled": True},
            "shippingCostOverrides": [{"priority": 1, "shippingServiceType": "DOMESTIC",
                                       "shippingCost": {"value": "5.00", "currency": "USD"}}],
        }
        self.items["15420259402024"]["product"].update(
            imageUrls=["https://cdn/a.jpg"], aspects={"Brand": ["Funko"], "Type": ["Mini"]})
        self.items["15420259402024"]["availability"]["shipToLocationAvailability"]["quantity"] = 3
        mickey["availableQuantity"] = 3
        self.offers["308888324011"] = {"offerId": "308888324011", "sku": "999999", "status": "UNPUBLISHED"}

    def __call__(self, method, url, headers=None, body=None, **kw):
        if "/sell/account/v1/" in url:
            self.calls.append((method, url, headers, body))
            route = url.split("/sell/account/v1", 1)[1].partition("?")[0]
            return 200, json.dumps({
                "/fulfillment_policy": {"fulfillmentPolicies": [{
                    "fulfillmentPolicyId": "284028239015", "name": "USPS GA 8.99",
                    "handlingTime": {"value": 1, "unit": "DAY"},
                    "shippingOptions": [{"optionType": "DOMESTIC", "costType": "FLAT_RATE",
                                         "shippingServices": [{"sortOrder": 1, "shippingServiceCode": "USPSGroundAdvantage",
                                                               "shippingCost": {"value": "8.99"}}]}]}]},
                "/payment_policy": {"paymentPolicies": [{"paymentPolicyId": "283965435015", "name": "Managed"}]},
                "/return_policy": {"returnPolicies": [{"returnPolicyId": "291780816015", "name": "No returns",
                                                       "returnsAccepted": False}]},
            }[route])
        if method == "POST" and url.endswith("/withdraw"):
            self.calls.append((method, url, headers, body))
            return 200, json.dumps({"listingId": "227566100694"})
        if method == "DELETE":
            self.calls.append((method, url, headers, body))
            return 204, ""
        return super().__call__(method, url, headers, body, **kw)


class EditListingTest(unittest.TestCase):
    def setUp(self):
        self.fake = LiveFakeEbay()
        for p in (mock.patch.object(http, "request", self.fake),
                  mock.patch.object(inv, "get_access_token", lambda c: "TOKEN")):
            p.start()
            self.addCleanup(p.stop)

    def offer_put(self):
        return self.fake.bodies("PUT", "/offer/308888822011")

    def item_put(self):
        return self.fake.bodies("PUT", "/inventory_item/15420259402024")

    def test_get_listing_by_item_number_or_link(self):
        for ref in ("227566100694", "https://www.ebay.com/itm/227566100694", "308888822011"):
            got = el.get_listing(cfg(), ref)
            self.assertEqual(got["offer_id"], "308888822011")
        self.assertEqual(got["status"], "PUBLISHED")
        self.assertEqual(got["fulfillment_policy_id"], "284028239015")
        self.assertEqual(got["quantity"], 3)
        self.assertEqual(got["aspects"]["Brand"], ["Funko"])

    def test_unknown_listing_explains_seller_hub(self):
        with self.assertRaisesRegex(RuntimeError, "Seller Hub"):
            el.get_listing(cfg(), "111111111111")

    def test_title_only_touches_item(self):
        r = el.update_listing(cfg(), "308888822011", title="New title")
        self.assertTrue(r["live_listing_updated"])
        self.assertEqual(self.item_put()[0]["product"]["title"], "New title")
        self.assertEqual(self.offer_put(), [])

    def test_description_goes_to_item_and_offer(self):
        el.update_listing(cfg(), "308888822011", description="<p>Hi</p>")
        self.assertEqual(self.item_put()[0]["product"]["description"], "<p>Hi</p>")
        self.assertEqual(self.offer_put()[0]["listingDescription"], "<p>Hi</p>")

    def test_price_and_shipping_override(self):
        el.update_listing(cfg(), "308888822011", price=12.5, shipping_cost=0)
        offer = self.offer_put()[0]
        self.assertEqual(offer["pricingSummary"]["price"]["value"], "12.50")
        self.assertEqual(offer["listingPolicies"]["shippingCostOverrides"][0]["shippingCost"]["value"], "0.00")
        self.assertNotIn("status", offer)   # read-only fields stripped

    def test_shipping_default_removes_override(self):
        el.update_listing(cfg(), "308888822011", shipping_cost="default")
        self.assertNotIn("shippingCostOverrides", self.offer_put()[0]["listingPolicies"])

    def test_new_shipping_policy_drops_old_override(self):
        r = el.update_listing(cfg(), "308888822011", fulfillment_policy_id="999")
        pol = self.offer_put()[0]["listingPolicies"]
        self.assertEqual(pol["fulfillmentPolicyId"], "999")
        self.assertNotIn("shippingCostOverrides", pol)
        self.assertEqual(pol["paymentPolicyId"], "283965435015")
        self.assertTrue(any("override" in n for n in r["notes"]))

    def test_quantity_lowered_updates_offer_before_item(self):
        el.update_listing(cfg(), "308888822011", quantity=1)
        puts = [u for m, u, h, b in self.fake.calls if m == "PUT"]
        self.assertIn("/offer/", puts[0])
        self.assertIn("/inventory_item/", puts[1])
        self.assertEqual(self.offer_put()[0]["availableQuantity"], 1)
        self.assertEqual(self.item_put()[0]["availability"]["shipToLocationAvailability"]["quantity"], 1)

    def test_quantity_raised_updates_item_first(self):
        el.update_listing(cfg(), "308888822011", quantity=5)
        puts = [u for m, u, h, b in self.fake.calls if m == "PUT"]
        self.assertIn("/inventory_item/", puts[0])

    def test_aspects_merge_and_remove(self):
        el.update_listing(cfg(), "308888822011", aspects={"Shade": "Red"}, remove_aspects="Type")
        aspects = self.item_put()[0]["product"]["aspects"]
        self.assertEqual(aspects, {"Brand": ["Funko"], "Shade": ["Red"]})

    def test_best_offer_thresholds(self):
        el.update_listing(cfg(), "308888822011", auto_accept_price=40, auto_decline_price=30)
        terms = self.offer_put()[0]["listingPolicies"]["bestOfferTerms"]
        self.assertEqual(terms["autoAcceptPrice"]["value"], "40.00")
        self.assertEqual(terms["autoDeclinePrice"]["value"], "30.00")
        el.update_listing(cfg(), "308888822011", best_offer=False)
        self.assertEqual(self.offer_put()[1]["listingPolicies"]["bestOfferTerms"], {"bestOfferEnabled": False})

    def test_condition_and_package(self):
        el.update_listing(cfg(), "308888822011", condition="used good", weight_oz=6,
                          length_in=4, width_in=3, height_in=2)
        item = self.item_put()[0]
        self.assertEqual(item["condition"], "USED_GOOD")
        self.assertEqual(item["packageWeightAndSize"]["weight"], {"value": 6.0, "unit": "OUNCE"})
        self.assertEqual(item["packageWeightAndSize"]["dimensions"]["unit"], "INCH")
        with self.assertRaisesRegex(RuntimeError, "Unknown condition"):
            el.update_listing(cfg(), "308888822011", condition="mint")
        with self.assertRaisesRegex(RuntimeError, "length, width and height"):
            el.update_listing(cfg(), "308888822011", length_in=4)

    def test_raw_fields_escape_hatch(self):
        el.update_listing(cfg(), "308888822011", offer_fields={"lotSize": 2},
                          item_fields={"product": {"subtitle": "Rare"}})
        self.assertEqual(self.offer_put()[0]["lotSize"], 2)
        product = self.item_put()[0]["product"]
        self.assertEqual(product["subtitle"], "Rare")
        self.assertEqual(product["title"], "Funko Classic Mickey 1/6")   # merged, not replaced

    def test_nothing_to_change(self):
        with self.assertRaisesRegex(RuntimeError, "Nothing to change"):
            el.update_listing(cfg(), "308888822011")

    def test_sync_price_from_shopify(self):
        with mock.patch.object(el, "shopify_product", return_value=SHOPIFY):
            el.update_listing(cfg(), "308888822011", sync_price_from_shopify=True)
        self.assertEqual(self.offer_put()[0]["pricingSummary"]["price"]["value"], "10.99")

    def test_end_listing_and_delete_draft(self):
        r = el.end_listing(cfg(), "227566100694")
        self.assertEqual(r["ended_listing_id"], "227566100694")
        with self.assertRaisesRegex(RuntimeError, "is live"):
            el.delete_draft(cfg(), "308888822011")
        el.delete_draft(cfg(), "308888324011")
        self.assertTrue(any(m == "DELETE" and u.endswith("/offer/308888324011") for m, u, h, b in self.fake.calls))

    def test_publish_keeps_chosen_policies(self):
        self.fake.offers["308888822011"]["listingPolicies"] = {
            "fulfillmentPolicyId": "555", "bestOfferTerms": {"bestOfferEnabled": True,
                                                             "autoAcceptPrice": {"value": "9.00", "currency": "USD"}}}
        with mock.patch.object(el, "shopify_product", return_value=SHOPIFY):
            el.publish(cfg(), "308888822011")
        pol = self.offer_put()[0]["listingPolicies"]
        self.assertEqual(pol["fulfillmentPolicyId"], "555")              # kept
        self.assertEqual(pol["paymentPolicyId"], "283965435015")         # default filled in
        self.assertEqual(pol["bestOfferTerms"]["autoAcceptPrice"]["value"], "9.00")

    def test_business_policies_summary(self):
        got = inv.business_policies(cfg())
        self.assertEqual(got["shipping"][0]["services"][0]["cost"], "8.99")
        self.assertEqual(got["shipping"][0]["handling_days"], 1)
        self.assertEqual(got["returns"][0]["returns_accepted"], False)
        account_calls = [u for m, u, h, b in self.fake.calls if "/sell/account/v1/" in u]
        self.assertTrue(all("marketplace_id=EBAY_US" in u for u in account_calls))


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
