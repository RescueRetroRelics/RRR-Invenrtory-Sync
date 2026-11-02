import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bookkeeping"))
import monthly_summary as ms  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "bookkeeping" / "sample_exports"


class BookkeepingTest(unittest.TestCase):
    def test_money_parsing(self):
        self.assertEqual(ms.money("$1,234.50"), 1234.5)
        self.assertEqual(ms.money("(3.00)"), -3.0)
        self.assertIsNone(ms.money("--"))

    def test_dates(self):
        self.assertEqual(ms.month_of("Oct 05, 2026"), "2026-10")
        self.assertEqual(ms.month_of("2026-09-14 13:02:11 -0400"), "2026-09")
        self.assertEqual(ms.month_of("10/31/2026"), "2026-10")

    def test_sample_totals_match_hand_math(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(ms.main(["--exports", str(SAMPLES), "--out", out]), 0)
            led_csv = next(Path(out).glob("*.csv")).read_text()
        # Sep: 42 + 6.50 + 35 + 5 = 88.50 income; 40 + 5.85 + 5.78 + 1.78 = 53.41 expenses
        self.assertIn("2026-09,TOTAL,Total income,88.50", led_csv)
        self.assertIn("2026-09,TOTAL,Total expenses,53.41", led_csv)
        self.assertIn("2026-10,TOTAL,Net profit (income minus expenses),28.79", led_csv)


if __name__ == "__main__":
    unittest.main()
