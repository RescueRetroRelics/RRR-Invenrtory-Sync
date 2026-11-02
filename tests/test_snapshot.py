import unittest
from datetime import date
from pathlib import Path

from rrr_snapshot import loaders, report

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.found = loaders.load_folder(sorted(SAMPLES.glob("*.csv")))

    def test_reads_every_sample_file(self):
        kinds = " ".join(what for _, what in self.found["files"])
        for label in ("Shopify orders", "Shopify products", "eBay orders (Seller Hub)", "eBay active listings"):
            self.assertIn(label, kinds)

    def test_week_totals_match_hand_count(self):
        ctx = report.build(self.found, date(2026, 11, 2), date(2026, 11, 1))
        self.assertEqual(ctx["week"], (date(2026, 10, 26), date(2026, 11, 1)))
        self.assertEqual(ctx["this_week"]["Shopify"]["sales"], 86.00)
        self.assertEqual(ctx["this_week"]["eBay"]["sales"], 104.98)
        self.assertEqual(ctx["this_week"]["All"]["orders"], 10)

    def test_slow_movers_skip_sold_out_and_drafts(self):
        ctx = report.build(self.found, date(2026, 11, 2), date(2026, 11, 1))
        titles = {r["title"] for r in ctx["slow"]}
        self.assertNotIn("Peanuts Charlie Brown Snow Globe", titles)   # qty 0
        self.assertNotIn("Draft Item Not Ready", titles)              # draft
        self.assertIn("Vintage Pyrex Butterprint Cinderella Mixing Bowl 443", titles)

    def test_default_week_is_last_mon_to_sun(self):
        self.assertEqual(report.default_week_end(date(2026, 11, 2)), date(2026, 11, 1))   # Monday
        self.assertEqual(report.default_week_end(date(2026, 11, 1)), date(2026, 10, 25))  # Sunday
        self.assertEqual(report.default_week_end(date(2026, 11, 4)), date(2026, 11, 1))   # Wednesday

    def test_date_formats(self):
        self.assertEqual(loaders.parse_day("2026-10-28 22:01:27 -0400"), date(2026, 10, 28))
        self.assertEqual(loaders.parse_day("2026-10-29T02:01:27Z"), date(2026, 10, 28))  # evening in Michigan
        self.assertEqual(loaders.parse_day("Oct-28-26"), date(2026, 10, 28))
        self.assertEqual(loaders.money("US $1,234.50"), 1234.50)
