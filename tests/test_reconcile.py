import tempfile
import unittest
from pathlib import Path

from rrr_sync import reconcile

SHOPIFY = """Handle,Title,Vendor,Option1 Name,Option1 Value,Variant SKU,Variant Inventory Qty,Variant Price,Image Src,Cost per item,Status
ketchup,Re-Ment Snoopy Ketchup Figure,Re-Ment,Title,Default Title,SNP-KETCHUP,1,14.00,,4.50,active
ketchup,,,,,,,,http://img/2.jpg,,
belle,Snoopy Hippers Belle Pink Bow Sonny Angel,Sonny Angel,Title,Default Title,,3,12.00,,,active
lunchbox,Vintage Garfield Lunchbox 1978,Thermos,Title,Default Title,,2,45.00,,,active
shirt,Snoopy Joe Cool Tee,Peanuts,Size,Small,,0,20.00,,,active
shirt,,,,Large,,4,20.00,,,
old,Archived Thing,X,Title,Default Title,,5,1.00,,,archived
"""

EBAY = """Some banner line from Seller Hub

Item number,Title,Variation details,Custom label (SKU),Available quantity,Current price,Sold quantity
1,Totally different title,,snp-ketchup,3,15.99,2
2,Snoopy Hippers Belle Pink Bow Sonny Angel,,,1,13.99,1
3,Snoopy Hippers Belle Pink Bow Sonny Angel,,,1,13.99,0
4,Care Bears Funshine Plush 1983,,,1,30.00,0
5,Snoopy Joe Cool Tee Large Size,,,1,22.00,4
"""


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "products_export_1.csv").write_text(SHOPIFY, encoding="utf-8")
        (self.tmp / "ebay-active-listings.csv").write_text(EBAY, encoding="utf-8")
        found = reconcile.find_exports(self.tmp)
        self.shop = reconcile.load_shopify_products_csv(found["shopify"])
        ebay = reconcile.load_ebay_csv(found["ebay"])
        self.report = reconcile.Report(shop=self.shop, ebay=reconcile.match(ebay, self.shop))
        reconcile.add_sales(self.report)
        self.f = reconcile.findings(self.report)

    def test_reads_variants_and_skips_image_rows(self):
        titles = [s.title for s in self.shop]
        self.assertEqual(len(self.shop), 6)
        self.assertIn("Snoopy Joe Cool Tee Large", titles)
        self.assertEqual(self.shop[0].cost, 4.5)

    def test_matching_methods(self):
        by_id = {e.item_id: e for e in self.report.ebay}
        self.assertEqual(by_id["1"].method, "sku")
        self.assertEqual(by_id["2"].method, "exact_title")
        self.assertEqual(by_id["4"].method, "unmatched")
        self.assertEqual(by_id["5"].match.title, "Snoopy Joe Cool Tee Large")

    def test_findings(self):
        self.assertEqual([s.handle for s in self.f["only_shopify"]], ["lunchbox"])
        self.assertEqual([e.item_id for e in self.f["only_ebay"]], ["4"])
        oversell = [r for r in self.f["qty"] if r[0].startswith("OVERSELL")]
        self.assertEqual([r[1].handle for r in oversell], ["ketchup"])  # eBay 3 > Shopify 1
        self.assertEqual([(s.handle, len(g)) for s, g in self.f["dupes"]], [("belle", 2)])
        # sold on eBay, no cost on Shopify -> flagged, biggest seller first
        self.assertEqual([s.title for s in self.f["no_cost"]], ["Snoopy Joe Cool Tee Large", "Snoopy Hippers Belle Pink Bow Sonny Angel"])

    def test_writes_report_files(self):
        out = self.tmp / "out"
        summary = reconcile.write_reports(self.report, out)
        self.assertIn("OVERSELL RISK", summary)
        for name in ("master.csv", "only_on_shopify.csv", "only_on_ebay.csv", "quantity_mismatches.csv",
                     "ebay_duplicates.csv", "missing_unit_costs.csv", "check_these_matches.csv", "SUMMARY.txt"):
            self.assertTrue((out / name).exists(), name)


if __name__ == "__main__":
    unittest.main()


class LinkReportTest(unittest.TestCase):
    def test_strict_matching_and_six_files(self):
        from rrr_sync import link_report as lr
        tmp = Path(tempfile.mkdtemp())
        (tmp / "p.csv").write_text(SHOPIFY, encoding="utf-8")
        shop = reconcile.load_shopify_products_csv(tmp / "p.csv")
        ebay = [
            reconcile.EbayItem("1", "Totally different", sku="SNP-KETCHUP", qty=1),
            reconcile.EbayItem("2", "Snoopy Hippers Belle Pink Bow Sonny Angel 🎀", qty=1),
            reconcile.EbayItem("3", "Snoopy Hippers Belle Pink Bow Sonny Angel ✨", qty=1),
            reconcile.EbayItem("4", "Vintage Garfield Lunchbox 1978 Thermos Rare", qty=1),
            reconcile.EbayItem("5", "Care Bears Funshine Plush", sku="15420259205416", qty=1),
        ]
        c, u, m = lr.link(ebay, shop, {"belle": "gid://shopify/Product/9"})
        self.assertEqual(sorted(r["ebay"].item_id for r in c), ["1", "2", "3"])  # emojis ignored
        self.assertEqual([r["ebay"].item_id for r in u], ["4"])  # close, not certain: never auto-picked
        self.assertEqual([r["ebay"].item_id for r in m], ["5"])
        self.assertEqual(lr.sku_kind("15420259205416"), "Shopify-style number")
        self.assertEqual(lr.sku_kind("RR-TJBND-05"), "old custom SKU")
        self.assertEqual(lr.clean("Pokémon ⚡"), "pokemon")
        lr.write(tmp / "out", shop, c, u, m)
        for n in range(1, 7):
            self.assertEqual(len(list((tmp / "out").glob(f"{n}_*.csv"))), 1)
        dupes = (tmp / "out" / "2_duplicate_groups.csv").read_text(encoding="utf-8-sig")
        self.assertIn("gid://shopify/Product/9", dupes)
