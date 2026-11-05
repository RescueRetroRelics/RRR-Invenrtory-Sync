import json
import tempfile
import unittest
from pathlib import Path

from rrr_sync import pronoun_fix

FIX = {"product_id": 1, "handle": "garfield-a7", "old": "related — he too", "new": "related — she too"}
BODY = "<p>Casey took one look at Garfield and immediately related — he too enjoys naps.</p><p>Garfield thinks he is king.</p>"


class FakeShopify:
    def __init__(self, bodies):
        self.bodies = dict(bodies)
        self.updates = []

    def graphql(self, query, variables):
        if "productUpdate" in query:
            i = variables["input"]
            self.bodies[i["id"]] = i["descriptionHtml"]
            self.updates.append(i["id"])
            return {"productUpdate": {"product": {"id": i["id"]}, "userErrors": []}}
        gid = variables["id"]
        if gid not in self.bodies:
            return {"product": None}
        return {"product": {"id": gid, "title": "T", "descriptionHtml": self.bodies[gid]}}


class PronounFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fixes = self.tmp / "fixes.json"
        self.fixes.write_text(json.dumps([FIX]))
        self.backup = self.tmp / "backup.json"

    def run_fix(self, shop, apply):
        return pronoun_fix.run(shop, do_apply=apply, log=lambda *_: None, fixes_path=self.fixes, backup_path=self.backup)

    def test_dry_run_changes_nothing(self):
        shop = FakeShopify({"gid://shopify/Product/1": BODY})
        changed, _ = self.run_fix(shop, False)
        self.assertEqual(changed, 1)
        self.assertEqual(shop.updates, [])
        self.assertFalse(self.backup.exists())

    def test_apply_fixes_only_the_dog_pronoun(self):
        shop = FakeShopify({"gid://shopify/Product/1": BODY})
        self.run_fix(shop, True)
        new = shop.bodies["gid://shopify/Product/1"]
        self.assertIn("related — she too", new)
        self.assertIn("Garfield thinks he is king", new)  # character pronoun untouched
        self.assertEqual(json.loads(self.backup.read_text())["1"]["descriptionHtml"], BODY)

    def test_second_run_is_harmless(self):
        shop = FakeShopify({"gid://shopify/Product/1": BODY})
        self.run_fix(shop, True)
        changed, errors = self.run_fix(shop, True)
        self.assertEqual((changed, errors), (0, []))

    def test_edited_listing_is_skipped(self):
        shop = FakeShopify({"gid://shopify/Product/1": "<p>Totally rewritten.</p>"})
        changed, _ = self.run_fix(shop, True)
        self.assertEqual(changed, 0)
        self.assertEqual(shop.updates, [])

    def test_deleted_product_reported(self):
        changed, errors = self.run_fix(FakeShopify({}), True)
        self.assertEqual(changed, 0)
        self.assertEqual(len(errors), 1)

    def test_shipped_fix_list_is_well_formed(self):
        fixes = json.loads(pronoun_fix.FIXES_FILE.read_text(encoding="utf-8"))
        self.assertGreater(len(fixes), 0)
        for f in fixes:
            self.assertNotEqual(f["old"], f["new"])
            self.assertNotRegex(f["new"], r"\b(he|him|his|himself)\b")


if __name__ == "__main__":
    unittest.main()
