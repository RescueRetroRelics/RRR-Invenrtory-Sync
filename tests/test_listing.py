import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from listing import make_sheet, publisher as pub, sheet as sheet_io
from rrr_sync import ebay_inventory as inv
from rrr_sync import ebay_listings as el
from rrr_sync import ebay_store, http
from rrr_sync.config import Config

ASPECTS = [
    {"name": "Brand", "required": True, "usage": "RECOMMENDED", "mode": "FREE_TEXT", "multi": False, "values": []},
    {"name": "Character", "required": False, "usage": "RECOMMENDED", "mode": "FREE_TEXT", "multi": True, "values": []},
    {"name": "Type", "required": False, "usage": "RECOMMENDED", "mode": "SELECTION_ONLY", "multi": False,
     "values": ["Figure", "Keychain"]},
    {"name": "Color", "required": False, "usage": "RECOMMENDED", "mode": "FREE_TEXT", "multi": True, "values": []},
    {"name": "Item Height", "required": False, "usage": "OPTIONAL", "mode": "FREE_TEXT", "multi": False, "values": []},
    {"name": "Franchise", "required": False, "usage": "OPTIONAL", "mode": "FREE_TEXT", "multi": False, "values": []},
]
GO_TO = "Collectibles > Animation Art & Merchandise > Animation Merchandise > Other Animation Merchandise"


def cfg():
    c = Config(*[""] * 12)
    object.__setattr__(c, "shopify_location_id", "gid://shopify/Location/1") if hasattr(c, "__dataclass_fields__") else None
    return c


GOOD = {
    "Photos folder": "001", "What it is": "Funko Snoopy figure", "Shopify price": "15.99", "eBay price": "19.99",
    "Qty": "2", "Condition": "New", "eBay store category": "Snoopy",
    "eBay category": "Other Animation Merchandise (go-to)", "Shopify category": "Action & Toy Figures",
    "Age group": "Adults", "Recommended age": "10 years or older", "Color": "White", "Color 2": "Black",
    "Toy figure theme": "Snoopy", "Brand": "Funko", "Title": "Funko Snoopy Figure",
    "eBay title": "Funko Snoopy Vinyl Figure Peanuts NEW", "SEO title": "Funko Snoopy Figure",
    "SEO description": "Funko Snoopy figure for Peanuts fans.", "Description": "Line one\nLine two",
    "Product type": "Vinyl Figure", "Tags": "Snoopy, Funko", "eBay specifics": "Character: Snoopy; Woodstock | Type: figure",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = str(self.dir / "s.xlsx")
        make_sheet.build(self.path, 5)
        (self.dir / "listing_photos" / "001").mkdir(parents=True)
        (self.dir / "listing_photos" / "001" / "b.jpg").write_bytes(b"jpgdata")
        (self.dir / "listing_photos" / "001" / "a.jpg").write_bytes(b"jpgdata")
        self.cfg = mock.Mock(shopify_location_id="gid://shopify/Location/1", shopify_domain="x.myshopify.com")
        patches = [
            mock.patch.object(inv, "category_suggestions", return_value=[{"category_id": "13516", "path": GO_TO}]),
            mock.patch.object(inv, "item_aspects", return_value=ASPECTS),
            mock.patch.object(ebay_store, "store_categories", return_value=[
                {"id": "5", "name": "Snoopy", "path": "/Snoopy", "leaf": True}]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def put(self, row_values, row=2):
        sheet_io.write(self.path, {row: row_values})


class HelpersTest(unittest.TestCase):
    def test_parse_specifics(self):
        self.assertEqual(pub.parse_specifics("Brand: Funko | Character: Snoopy; Woodstock | junk"),
                         {"Brand": "Funko", "Character": ["Snoopy", "Woodstock"]})

    def test_sheet_has_dropdowns_with_real_values(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            p = make_sheet.build(str(Path(d) / "x.xlsx"), 3)
            wb = openpyxl.load_workbook(p)
            lists = wb["Lists"]
            cols = {c.value: [lists.cell(row=r, column=c.column).value for r in range(2, 12)] for c in lists[1]}
            self.assertIn("Adults", cols["Age group"])
            self.assertNotIn("10 years or older", cols["Age group"])
            self.assertIn("10 years or older", cols["Recommended age"])
            self.assertNotIn("Other", cols["ebay_store_categories"])
            self.assertTrue(wb["Listings"].data_validations.dataValidation)


class PreflightTest(Base):
    def test_good_row_is_ready_and_maps_metafields(self):
        self.put(GOOD)
        p = pub.Publisher(self.cfg, self.path)
        plan, problems = p.preflight(sheet_io.read(self.path)[0])
        self.assertEqual(problems, [])
        self.assertEqual((plan["ebay_category"], plan["category_confirmed"], plan["shelf"]), ("13516", True, "/Snoopy"))
        self.assertEqual([f.name for f in plan["photos"]], ["a.jpg", "b.jpg"])
        mf = {m["key"]: json.loads(m["value"]) for m in plan["metafields"] if m["namespace"] == "shopify"}
        google = {m["key"]: m["value"] for m in plan["metafields"] if m["namespace"] == "mm-google-shopping"}
        self.assertEqual(google, {"custom_product": "false", "condition": "new", "age_group": "adult"})
        self.assertEqual(mf["age-group"], ["gid://shopify/Metaobject/227424174376"])
        self.assertEqual(len(mf["color-pattern"]), 2)
        self.assertEqual(plan["specifics"], {"Brand": ["Funko"], "Color": ["White", "Black"],
                                             "Character": ["Snoopy", "Woodstock"], "Type": ["Figure"]})

    def test_dimensions_from_sheet_get_units(self):
        self.put({**GOOD, "Height (in)": "3.5"})
        plan, problems = pub.Publisher(self.cfg, self.path).preflight(sheet_io.read(self.path)[0])
        self.assertEqual(plan["specifics"]["Item Height"], ["3.5 in"])

    def test_problems_are_listed_and_nothing_created(self):
        bad = {**GOOD, "Photos folder": "999", "eBay price": "", "eBay store category": "",
               "Age group": "10+ years", "Brand": "", "eBay title": "x" * 81}
        self.put(bad)
        p = pub.Publisher(self.cfg, self.path)
        _, problems = p.preflight(sheet_io.read(self.path)[0])
        text = " | ".join(problems)
        for expect in ("No photos", "eBay price missing", "store category", '"10+ years"', "eBay requires: Brand", "81 characters"):
            self.assertIn(expect, text)

    def test_unknown_ebay_category_is_refused(self):
        self.put(GOOD)
        with mock.patch.object(inv, "category_suggestions", return_value=[{"category_id": "1", "path": "Sports > Golf Clubs"}]):
            _, problems = pub.Publisher(self.cfg, self.path).preflight(sheet_io.read(self.path)[0])
        self.assertTrue(any("could not be found" in x for x in problems))


class CategoryFieldsTest(Base):
    def test_other_fields_map_to_store_values_and_unknowns_only_warn(self):
        self.put({**GOOD, "Other category fields": "Power source: Battery-powered; Solar | Mystery field: x",
                  "Weight (lb)": "0.5"})
        p = pub.Publisher(self.cfg, self.path)
        p._store_fields = {"power source": {"name": "Power source", "key": "power-source",
                                            "values": {"Battery-powered": "gid://shopify/Metaobject/655549235496"}}}
        plan, problems = p.preflight(sheet_io.read(self.path)[0])
        self.assertEqual(problems, [])
        ps = [m for m in plan["metafields"] if m["key"] == "power-source"][0]
        self.assertEqual(json.loads(ps["value"]), ["gid://shopify/Metaobject/655549235496"])
        self.assertTrue(any("Solar" in w for w in plan["warnings"]))
        self.assertTrue(any("Mystery field" in w for w in plan["warnings"]))
        self.assertEqual(plan["weight"], 0.5)


class RunTest(Base):
    def fake_shop(self):
        shop = mock.Mock()
        def gql(query, variables=None):
            if "stagedUploadsCreate" in query:
                return {"stagedUploadsCreate": {"userErrors": [], "stagedTargets": [
                    {"url": "https://up/", "resourceUrl": f"https://res/{i}", "parameters": [{"name": "k", "value": "v"}]}
                    for i in range(len(variables["input"]))]}}
            if "productSet" in query:
                shop.product_input = variables["input"]
                return {"productSet": {"userErrors": [], "product": {"id": "gid://shopify/Product/777", "handle": "h"}}}
            if "publications" in query:
                return {"publications": {"nodes": [{"id": "gid://shopify/Publication/1"}, {"id": "gid://shopify/Publication/2"}]}}
            if "publishablePublish" in query:
                shop.published = variables["input"]
                return {"publishablePublish": {"userErrors": []}}
            if "media" in query:
                return {"product": {"media": {"nodes": [{"status": "READY"}]}}}
            raise AssertionError(query)
        shop.graphql.side_effect = gql
        return shop

    def test_dry_run_marks_ready_without_creating(self):
        self.put(GOOD)
        p = pub.Publisher(self.cfg, self.path)
        p._shop = self.fake_shop()
        res = p.run(confirm=False)
        self.assertEqual(res["summary"], {"READY": 1})
        p._shop.graphql.assert_not_called()
        self.assertEqual(sheet_io.read(self.path)[0]["Status"], "READY")

    def test_confirm_creates_shopify_then_ebay_and_marks_done(self):
        self.put(GOOD)
        p = pub.Publisher(self.cfg, self.path)
        p._shop = self.fake_shop()
        with mock.patch.object(http, "request", return_value=(204, "")) as up, \
             mock.patch.object(el, "create_draft", return_value={"offer_id": "O1"}) as draft, \
             mock.patch.object(el, "publish", return_value={"url": "https://www.ebay.com/itm/555"}) as publ:
            res = p.run(confirm=True)
        self.assertEqual(res["summary"], {"DONE": 1})
        self.assertEqual(up.call_count, 2)   # two photos uploaded
        inp = p._shop.product_input
        self.assertEqual(inp["seo"], {"title": "Funko Snoopy Figure", "description": "Funko Snoopy figure for Peanuts fans."})
        self.assertEqual(inp["variants"][0]["price"], "15.99")
        self.assertEqual(inp["variants"][0]["inventoryQuantities"][0]["quantity"], 2)
        self.assertNotIn("measurement", inp["variants"][0]["inventoryItem"])   # no weight given
        self.assertIn("Bailee's Legacy", inp["tags"])
        self.assertEqual(inp["descriptionHtml"], "<p>Line one</p><p>Line two</p>")
        self.assertEqual(len(p._shop.published), 2)
        kw = draft.call_args.kwargs
        self.assertEqual((draft.call_args.args[1], draft.call_args.args[2]), ("gid://shopify/Product/777", "19.99"))
        self.assertEqual((kw["category_id"], kw["store_category"], kw["category_confirmed"], kw["title"]),
                         ("13516", "/Snoopy", True, GOOD["eBay title"]))
        row = sheet_io.read(self.path)[0]
        self.assertEqual((row["Status"], row["eBay link"]), ("DONE", "https://www.ebay.com/itm/555"))
        self.assertTrue(row["Shopify link"].endswith("/777"))
        # rerun skips DONE
        self.assertEqual(p.run(confirm=True)["summary"], {})

    def test_ebay_failure_keeps_shopify_and_retry_only_does_ebay(self):
        self.put(GOOD)
        p = pub.Publisher(self.cfg, self.path)
        p._shop = self.fake_shop()
        with mock.patch.object(http, "request", return_value=(204, "")), \
             mock.patch.object(el, "create_draft", side_effect=RuntimeError("eBay down")):
            res = p.run(confirm=True)
        self.assertEqual(res["summary"], {"SHOPIFY ONLY": 1})
        row = sheet_io.read(self.path)[0]
        self.assertEqual(row["Status"], "SHOPIFY ONLY")
        self.assertIn("eBay down", row["Problem"])
        p2 = pub.Publisher(self.cfg, self.path)
        p2._shop = self.fake_shop()
        with mock.patch.object(el, "create_draft", return_value={"offer_id": "O1"}) as draft, \
             mock.patch.object(el, "publish", return_value={"url": "https://www.ebay.com/itm/9"}):
            res = p2.run(confirm=True)
        self.assertEqual(res["summary"], {"DONE": 1})
        self.assertFalse(any("productSet" in c.args[0] for c in p2._shop.graphql.call_args_list))
        self.assertEqual(draft.call_args.args[1], "gid://shopify/Product/777")


class FillTest(Base):
    def test_fill_writes_and_rejects_too_long(self):
        self.put({k: GOOD[k] for k in sheet_io.YOURS})
        self.put({k: GOOD[k] for k in sheet_io.YOURS}, row=3)
        p = pub.Publisher(self.cfg, self.path)
        todo = p.rows_to_fill()
        self.assertEqual([r["row"] for r in todo["rows"]], [2, 3])
        needed = {n["name"]: n["need"] for n in todo["rows"][0]["ebay_specifics_needed"]}
        self.assertEqual(needed["Brand"], "REQUIRED")
        self.assertEqual(needed["Franchise"], "Stephanie fills this")
        res = p.fill([{"row": 2, "Title": "T", "eBay title": "E"}, {"row": 3, "SEO title": "x" * 71}])
        self.assertEqual(res["written"], [2])
        self.assertEqual(res["problems"][0]["row"], 3)
        self.assertEqual(sheet_io.read(self.path)[0]["eBay title"], "E")


if __name__ == "__main__":
    unittest.main()
