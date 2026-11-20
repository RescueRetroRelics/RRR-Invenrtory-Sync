import csv
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from rrr_sync import ebay_inventory as inv
from rrr_sync import ebay_listings as el
from rrr_sync import ebay_specifics as spec
from rrr_sync import ebay_store as store
from rrr_sync.config import Config


def cfg():
    return Config(*[""] * 12)


ASPECTS = [
    {"name": "Brand", "required": True, "usage": "RECOMMENDED", "mode": "FREE_TEXT", "multi": False,
     "values": ["Funko", "Hasbro", "Mattel"]},
    {"name": "Type", "required": True, "usage": "RECOMMENDED", "mode": "SELECTION_ONLY", "multi": False,
     "values": ["Vinyl Figure", "Action Figure", "Bobblehead"]},
    {"name": "Character", "required": False, "usage": "RECOMMENDED", "mode": "FREE_TEXT", "multi": True,
     "values": ["Mickey Mouse", "Mickey", "Chucky"]},
    {"name": "Color", "required": False, "usage": "OPTIONAL", "mode": "FREE_TEXT", "multi": False, "values": []},
]


class CategoryGuardTest(unittest.TestCase):
    def test_golf_clubs_on_a_funko_is_refused(self):
        with mock.patch.object(inv, "category_name", return_value="Golf Clubs"), \
             mock.patch.object(inv, "category_suggestions", return_value=[
                 {"category_id": "149372", "path": "Toys > Action Figures"}]):
            with self.assertRaisesRegex(RuntimeError, 'WRONG CATEGORY.*"Golf Clubs".*149372'):
                el.check_category(cfg(), "115280", ["Funko Mickey Mouse 1/6"])

    def test_suggested_category_passes_and_returns_name(self):
        with mock.patch.object(inv, "category_name", return_value="Action Figures"), \
             mock.patch.object(inv, "category_suggestions", return_value=[
                 {"category_id": "149372", "path": "Toys > Action Figures"}]):
            got = el.check_category(cfg(), "149372", ["Funko Mickey"])
        self.assertEqual(got["category_name"], "Action Figures")
        self.assertTrue(got["in_ebay_suggestions"])

    def test_confirmed_override_and_unknown_id(self):
        with mock.patch.object(inv, "category_name", return_value="Golf Clubs"), \
             mock.patch.object(inv, "category_suggestions", return_value=[]):
            got = el.check_category(cfg(), "115280", ["x"], confirmed=True)
        self.assertFalse(got["in_ebay_suggestions"])
        with mock.patch.object(inv, "category_name", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "no category 999"):
                el.check_category(cfg(), "999", ["x"])

    def test_create_draft_checks_category_before_writing_anything(self):
        product = {"id": "gid://shopify/Product/1234567", "number": "1234567", "title": "Funko Mickey",
                   "description": "d", "price": "9", "quantity": 1, "images": ["https://a/1.jpg"]}
        with mock.patch.object(el, "shopify_product", return_value=product), \
             mock.patch.object(inv, "category_name", return_value="Golf Clubs"), \
             mock.patch.object(inv, "category_suggestions", return_value=[]), \
             mock.patch.object(inv, "put_inventory_item") as put:
            with self.assertRaises(RuntimeError):
                el.create_draft(cfg(), "1234567", "19.99", category_id="115280")
        put.assert_not_called()


class SpecificsTest(unittest.TestCase):
    def test_guess_takes_whole_words_and_longest(self):
        a = ASPECTS[2]
        self.assertEqual(spec.guess("Funko Mickey Mouse 1/6", a), "Mickey Mouse")
        self.assertIsNone(spec.guess("Chuckyish thing", a))
        self.assertEqual(spec.guess("FUNKO pop", ASPECTS[0]), "Funko")

    def test_parse_item_aspects_orders_required_first(self):
        data = {"aspects": [
            {"localizedAspectName": "Color", "aspectConstraint": {"aspectUsage": "OPTIONAL"}},
            {"localizedAspectName": "Brand", "aspectConstraint": {"aspectRequired": True, "aspectUsage": "RECOMMENDED"},
             "aspectValues": [{"localizedValue": "Funko"}]},
            {"localizedAspectName": "Character", "aspectConstraint": {"aspectUsage": "RECOMMENDED",
             "itemToAspectCardinality": "MULTI", "aspectMode": "FREE_TEXT"}},
        ]}
        got = inv.parse_item_aspects(data)
        self.assertEqual([a["name"] for a in got], ["Brand", "Character", "Color"])
        self.assertTrue(got[1]["multi"])

    def _offers(self):
        return [{"offerId": "1", "sku": "111111", "status": "PUBLISHED", "categoryId": "149372",
                 "listing": {"listingId": "2001"}, "_title": "Funko Mickey Mouse"},
                {"offerId": "2", "sku": "222222", "status": "UNPUBLISHED", "categoryId": "149372"}]

    def test_build_sheet_lists_missing_required_and_recommended(self):
        items = {"111111": {"product": {"title": "Funko Mickey Mouse", "aspects": {"Type": ["Vinyl Figure"]}}}}
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(inv, "all_offers", return_value=(self._offers(), [])), \
             mock.patch.object(inv, "item_aspects", return_value=ASPECTS), \
             mock.patch.object(inv, "category_name", return_value="Action Figures"), \
             mock.patch.object(inv, "get_inventory_item", side_effect=lambda c, s: items[s]):
            path, stats = spec.build_sheet(cfg(), d)
            with open(path, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual([r["specific"] for r in rows], ["Brand", "Character"])   # Type present, Color optional
        self.assertEqual(rows[0]["new_value"], "Funko")
        self.assertEqual(rows[0]["need"], "REQUIRED")
        self.assertEqual(rows[1]["new_value"], "Mickey Mouse")
        self.assertEqual(stats["listings"], 1)   # unpublished skipped
        self.assertEqual(stats["listings_missing_required"], 1)

    def _sheet(self, d, rows):
        path = Path(d) / "s.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=spec.SHEET_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in spec.SHEET_COLUMNS})
        return path

    def _apply(self, rows, confirm):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(inv, "offers_for_sku", return_value=[{"offerId": "1", "categoryId": "149372"}]), \
             mock.patch.object(inv, "item_aspects", return_value=ASPECTS), \
             mock.patch.object(inv, "get_inventory_item", return_value={"sku": "111111", "product": {
                 "title": "t", "aspects": {"Type": ["Vinyl Figure"]}}}), \
             mock.patch.object(inv, "put_inventory_item") as put:
            result = spec.apply_sheet(cfg(), self._sheet(d, rows), confirm=confirm)
        return result, put

    def test_dry_run_writes_nothing(self):
        rows = [{"sku": "111111", "specific": "Brand", "new_value": "Funko"}]
        result, put = self._apply(rows, confirm=False)
        put.assert_not_called()
        self.assertEqual(result["summary"], {"WOULD ADD": 1})

    def test_confirm_merges_specifics_and_multi_values(self):
        rows = [{"sku": "111111", "specific": "brand", "new_value": "Funko"},
                {"sku": "111111", "specific": "Character", "new_value": "Mickey Mouse | Minnie"},
                {"sku": "111111", "specific": "Color", "new_value": ""}]
        result, put = self._apply(rows, confirm=True)
        self.assertEqual(result["summary"], {"ADDED": 1})
        aspects = put.call_args[0][2]["product"]["aspects"]
        self.assertEqual(aspects, {"Type": ["Vinyl Figure"], "Brand": ["Funko"],
                                   "Character": ["Mickey Mouse", "Minnie"]})

    def test_value_not_allowed_skips_whole_listing(self):
        rows = [{"sku": "111111", "specific": "Brand", "new_value": "Funko"},
                {"sku": "111111", "specific": "Type", "new_value": "Golf Club"}]
        result, put = self._apply(rows, confirm=True)
        put.assert_not_called()
        self.assertEqual(result["summary"], {"BAD VALUES": 1})
        self.assertIn("Golf Club not allowed", result["rows"][0]["error"])


STORE_XML = """<GetStoreResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Store><CustomCategories>
<CustomCategory><CategoryID>1</CategoryID><Name>Other</Name></CustomCategory>
<CustomCategory><CategoryID>10</CategoryID><Name>Funko</Name>
  <ChildCategory><CategoryID>11</CategoryID><Name>Disney</Name></ChildCategory>
  <ChildCategory><CategoryID>12</CategoryID><Name>Horror</Name></ChildCategory>
</CustomCategory>
<CustomCategory><CategoryID>20</CategoryID><Name>Vintage Toys</Name></CustomCategory>
</CustomCategories></Store></GetStoreResponse>"""

SELLER_XML = """<GetSellerListResponse xmlns="urn:ebay:apis:eBLBaseComponents"><ItemArray>
<Item><ItemID>2001</ItemID><Title>Funko Disney Mickey</Title><SKU>111111</SKU>
  <Storefront><StoreCategoryID>1</StoreCategoryID></Storefront>
  <SellingStatus><ListingStatus>Active</ListingStatus></SellingStatus></Item>
<Item><ItemID>2002</ItemID><Title>Old thing</Title>
  <Storefront><StoreCategoryID>20</StoreCategoryID></Storefront></Item>
<Item><ItemID>2003</ItemID><Title>Ended</Title>
  <SellingStatus><ListingStatus>Completed</ListingStatus></SellingStatus></Item>
</ItemArray><HasMoreItems>false</HasMoreItems></GetSellerListResponse>"""


class StoreCategoryTest(unittest.TestCase):
    def setUp(self):
        self.cats = store.parse_store_categories(ET.fromstring(STORE_XML))

    def test_parse_paths_and_leaves(self):
        paths = {c["path"]: c["leaf"] for c in self.cats}
        self.assertEqual(paths, {"/Other": True, "/Funko": False, "/Funko/Disney": True,
                                 "/Funko/Horror": True, "/Vintage Toys": True})

    def test_resolve_by_name_path_id_and_refuses_parents(self):
        self.assertEqual(store._resolve(self.cats, "disney")["id"], "11")
        self.assertEqual(store._resolve(self.cats, "Funko/Horror")["id"], "12")
        self.assertEqual(store._resolve(self.cats, "20")["path"], "/Vintage Toys")
        with self.assertRaisesRegex(ValueError, "sub-categories"):
            store._resolve(self.cats, "Funko")
        with self.assertRaisesRegex(ValueError, "isn't one of your store categories"):
            store._resolve(self.cats, "Golf")

    def test_seller_list_skips_ended_and_defaults_to_other(self):
        got = store.parse_seller_list(ET.fromstring(SELLER_XML))
        self.assertEqual([(g["item_id"], g["store_category_id"]) for g in got], [("2001", "1"), ("2002", "20")])

    def test_suggest_never_picks_other_and_prefers_specific(self):
        self.assertEqual(store.suggest("Funko Disney Mickey", self.cats)["path"], "/Funko/Disney")
        self.assertIsNone(store.suggest("Random widget", self.cats))

    def _apply(self, rows, confirm, offers):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.csv"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=store.SHEET_COLUMNS)
                w.writeheader()
                for r in rows:
                    w.writerow({c: r.get(c, "") for c in store.SHEET_COLUMNS})
            with mock.patch.object(store, "store_categories", return_value=self.cats), \
                 mock.patch.object(inv, "offers_for_sku", return_value=offers), \
                 mock.patch.object(inv, "update_offer") as upd, \
                 mock.patch.object(inv, "publish_offer") as pub, \
                 mock.patch.object(store, "_trading_call") as trading:
                result = store.apply_sheet(cfg(), path, confirm=confirm)
        return result, upd, pub, trading

    def test_dry_run_changes_nothing(self):
        result, upd, pub, trading = self._apply(
            [{"item_id": "2001", "sku": "111111", "new_category": "Disney"}, {"item_id": "2002"}],
            False, [])
        self.assertEqual(result["summary"], {"WOULD MOVE": 1, "SKIPPED (blank)": 1})
        upd.assert_not_called(); pub.assert_not_called(); trading.assert_not_called()

    def test_api_listing_goes_through_offer_and_republish(self):
        offer = {"offerId": "9", "sku": "111111", "listing": {"listingId": "2001"}}
        result, upd, pub, trading = self._apply(
            [{"item_id": "2001", "sku": "111111", "new_category": "/Funko/Disney"}], True, [offer])
        self.assertEqual(result["rows"][0]["via"], "offer")
        self.assertEqual(upd.call_args[0][2]["storeCategoryNames"], ["/Funko/Disney"])
        pub.assert_called_once_with(mock.ANY, "9")
        trading.assert_not_called()

    def test_seller_hub_listing_uses_revise_item(self):
        result, upd, pub, trading = self._apply(
            [{"item_id": "2002", "sku": "", "new_category": "Horror"}], True, [])
        self.assertEqual(result["rows"][0]["via"], "ReviseItem")
        self.assertIn("<StoreCategoryID>12</StoreCategoryID>", trading.call_args[0][2])
        upd.assert_not_called()

    def test_bad_category_is_reported_not_applied(self):
        result, upd, *_ = self._apply([{"item_id": "2001", "new_category": "Golf"}], True, [])
        self.assertEqual(result["summary"], {"BAD CATEGORY": 1})
        upd.assert_not_called()


class ReportTest(unittest.TestCase):
    def test_report_uses_live_data_and_flags_missing(self):
        offer = {"offerId": "9", "sku": "111111", "status": "PUBLISHED", "categoryId": "149372",
                 "storeCategoryNames": ["/Funko/Disney"],
                 "pricingSummary": {"price": {"value": "24.99"}}, "availableQuantity": 1,
                 "listing": {"listingId": "2001"}}
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(inv, "get_offer", side_effect=lambda c, oid: offer if oid == "9"
                               else (_ for _ in ()).throw(inv.EbayApiError(404, [{"message": "nope"}], "u"))), \
             mock.patch.object(inv, "get_inventory_item", return_value={"product": {"title": "eBay Title"}}), \
             mock.patch.object(inv, "category_name", return_value="Action Figures"), \
             mock.patch.object(el, "shopify_product", return_value={"title": "Shop Title", "price": "19.99"}):
            rows, path = el.listing_report(cfg(), offer_ids=["9", "123"], out_dir=d)
            self.assertTrue(Path(path).exists())
        by_status = {r["status"]: r for r in rows}
        self.assertEqual(by_status["NOT FOUND"]["offer_id"], "123")
        r = by_status["PUBLISHED"]
        self.assertEqual((r["ebay_title"], r["shopify_title"], r["category_name"], r["store_category"],
                          r["ebay_price"], r["url"]),
                         ("eBay Title", "Shop Title", "Action Figures", "/Funko/Disney", "24.99",
                          "https://www.ebay.com/itm/2001"))


if __name__ == "__main__":
    unittest.main()
