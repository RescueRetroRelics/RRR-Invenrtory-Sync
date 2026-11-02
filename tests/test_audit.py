import os
import tempfile
import unittest
from pathlib import Path

from rrr_audit.__main__ import main
from rrr_audit.checks import _looks_cut_off, check_page, check_site, merge, rank
from rrr_audit.crawler import Crawler, normalize
from rrr_audit.parse import Page, parse_page
from tests.mock_site import serve

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"


class ParseTest(unittest.TestCase):
    def test_regions_and_alt(self):
        html = ("<header><nav><a href='/a'>A</a></nav></header><div><img src='x.jpg'><img src='y.jpg' alt=''></div>"
                "<div class='shopify-section-group-footer-group'><div><a href='/b'>B</a></div></div><a href='/c'>C</a>")
        p = parse_page(Page("https://s.com/"), html)
        self.assertEqual([(l.text, l.region) for l in p.links], [("A", "header"), ("B", "footer"), ("C", "body")])
        self.assertEqual([i.alt for i in p.images], [None, ""])

    def test_normalize(self):
        h = "rescueretrorelics.com"
        self.assertEqual(normalize("https://www.rescueretrorelics.com/collections/snoopy/products/x?variant=1#top", h),
                         "https://www.rescueretrorelics.com/products/x")
        for bad in ("https://rescueretrorelics.com/cart", "https://rescueretrorelics.com/checkouts/abc",
                    "https://rescueretrorelics.com/collections/all/snoopy+vintage", "https://ebay.com/x", "mailto:a@b.c"):
            self.assertIsNone(normalize(bad, h), bad)

    def test_cut_off(self):
        self.assertTrue(_looks_cut_off("x " * 80 + "and"))
        self.assertTrue(_looks_cut_off("Short but ends..."))
        self.assertFalse(_looks_cut_off("A complete sentence about Snoopy."))


class MockStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = serve()
        crawler = Crawler(cls.url, delay=0)
        cls.pages = crawler.crawl()
        status = crawler.check_links(check_external=False)
        page_f = [f for p in cls.pages.values() for f in check_page(p)]
        cls.findings = merge(page_f, check_site(cls.pages, status, crawler.host), crawler.sitemap_urls)
        cls.codes = {}
        for f in cls.findings:
            cls.codes.setdefault(f["code"], []).append(f)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_known_problems_found(self):
        c = self.codes
        self.assertEqual({f["detail"] for f in c["missing_alt_text"]},
                         {"bailee.jpg", "team.jpg", "char.jpg (alt is blank)"})
        self.assertTrue(c["meta_description_cut_off"][0]["url"].endswith("/products/charizard-holo"))
        self.assertIn("terms of service", c["nav_footer_mismatch"][0]["detail"])
        self.assertEqual(len(c["nav_footer_mismatch"]), 1)
        self.assertIn("deleted-thing", c["broken_internal_link"][0]["detail"])
        self.assertNotIn("page_error", c)  # the dead page is reported once, as a broken link
        self.assertIn("missing.jpg", c["broken_image"][0]["detail"])
        self.assertIn("jsonld_invalid", c)
        self.assertIn("Rescue Retro Relics", c["product_schema_incomplete"][0]["detail"])
        self.assertIn("heading_skip", c)
        self.assertIn("missing_meta_description", c)

    def test_clean_pages_have_no_noise(self):
        belle = [f for f in self.findings if f["url"].endswith("/products/snoopy-hippers-belle")
                 and f["code"] not in ("duplicate_meta_description",)]
        self.assertEqual(belle, [])

    def test_collection_product_path_not_double_crawled(self):
        self.assertFalse(any("/collections/all/products/" in u for u in self.pages))

    def test_ranking_puts_quick_high_impact_first(self):
        fixes = rank(self.findings)
        self.assertEqual(fixes[0]["impact"], 3)
        self.assertTrue(fixes[0]["quick_win"])

    def test_full_run_writes_report_and_tracks_fixes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["--site", self.url, "--no-open", "--delay", "0", "--out", d, "--no-external"]), 0)
            run = next(Path(d).iterdir())
            for name in ("report.html", "fix_list.csv", "summary.txt", "pages.csv", "findings.json"):
                self.assertTrue((run / name).exists(), name)
            self.assertIn("Menu and footer disagree", (run / "report.html").read_text(encoding="utf-8"))
            run.rename(run.with_name("2000-01-01_0000"))
            main(["--site", self.url, "--no-open", "--delay", "0", "--out", d, "--no-external"])
            latest = sorted(Path(d).iterdir())[-1]
            self.assertIn("0 problems FIXED, 0 new", (latest / "summary.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
