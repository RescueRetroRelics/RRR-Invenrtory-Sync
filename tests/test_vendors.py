import csv
import tempfile
import unittest
from pathlib import Path

from rrr_sync import vendors


class FakeShopify:
    def __init__(self):
        self.p = {"gid://shopify/Product/1": {"id": "gid://shopify/Product/1", "title": "Snoopy Plush", "handle": "snoopy", "vendor": "Rescue Retro Relics"},
                  "gid://shopify/Product/2": {"id": "gid://shopify/Product/2", "title": "Garfield Album", "handle": "garfield", "vendor": "Cath Kidston"}}
        self.updates = []

    def graphql(self, q, v):
        if "productUpdate" in q:
            self.updates.append(v["input"])
            self.p[v["input"]["id"]]["vendor"] = v["input"]["vendor"]
            return {"productUpdate": {"product": {}, "userErrors": []}}
        if "products(" in q:
            h = v["q"].split("'")[1]
            return {"products": {"nodes": [p for p in self.p.values() if p["handle"] == h][:1]}}
        return {"product": self.p.get(v["id"])}


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.csv = self.dir / "in.csv"
        with open(self.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Product_GID", "handle", "New_Vendor"])
            w.writerow(["gid://shopify/Product/1", "", "Peanuts Worldwide"])
            w.writerow(["", "garfield", "Cath Kidston"])
            w.writerow(["", "missing-thing", "Sanrio"])
            w.writerow(["gid://shopify/Product/9", "", ""])

    def test_dry_run(self):
        shop = FakeShopify()
        changed, errors = vendors.run(shop, self.csv, log=lambda *_: None, out_dir=self.dir)
        self.assertEqual((changed, shop.updates), (1, []))
        self.assertEqual(len(errors), 2)  # not found + missing vendor

    def test_apply_backs_up_and_reports(self):
        shop = FakeShopify()
        vendors.run(shop, self.csv, do_apply=True, log=lambda *_: None, out_dir=self.dir)
        self.assertEqual(shop.updates, [{"id": "gid://shopify/Product/1", "vendor": "Peanuts Worldwide"}])
        backup = list(csv.DictReader(open(self.dir / "vendor_backup.csv")))
        self.assertEqual(backup[0]["vendor"], "Rescue Retro Relics")
        res = {r["handle"]: r["status"] for r in csv.DictReader(open(self.dir / "vendor_results.csv"))}
        self.assertEqual(res, {"snoopy": "updated", "garfield": "unchanged"})


if __name__ == "__main__":
    unittest.main()
