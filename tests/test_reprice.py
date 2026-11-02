import csv
import datetime as dt
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from rrr_sync import reprice
from rrr_sync.ebay import parse_pricing_list

TODAY = dt.date(2026, 11, 2)
RULES = reprice.load_rules(Path(__file__).resolve().parent.parent / "reprice_rules.json")


def listing(item_id, price, days, watchers=0, **kw):
    start = (TODAY - dt.timedelta(days=days)).isoformat()
    base = {"item_id": item_id, "sku": "", "title": f"Item {item_id}", "price": price,
            "start_date": start, "watchers": watchers, "bids": 0, "quantity": 1,
            "format": "fixed", "variation": False}
    base.update(kw)
    return base


class FloorTest(unittest.TestCase):
    def test_floor_covers_fees_and_profit(self):
        for cost in (0, 1, 3.5, 8, 20, 55):
            floor = reprice.floor_price(cost, RULES)
            self.assertGreaterEqual(reprice.net_payout(floor, cost, RULES), RULES["min_profit"] - 1e-9)
            self.assertGreaterEqual(floor, RULES["min_price"])
            self.assertIn(round(floor % 1, 2), (0.49, 0.99))

    def test_fees_are_real_not_two_dollars(self):
        # $30 item: 13.6% of $31.80 (with tax) + $0.40 is well over $2
        fees = 30 - reprice.net_payout(30, 0, RULES)
        self.assertGreater(fees, 4.5)

    def test_endings(self):
        self.assertEqual(reprice._round_ending(12.50), 12.49)
        self.assertEqual(reprice._round_ending(12.20), 11.99)
        self.assertEqual(reprice._round_ending(5.39), 5.49)
        self.assertEqual(reprice._round_up_ending(12.50), 12.99)
        self.assertEqual(reprice._round_up_ending(12.0), 12.49)


class PlanTest(unittest.TestCase):
    def plan(self, listings, costs=None, originals=None):
        rows, orig = reprice.plan(listings, RULES, costs, originals, TODAY)
        return {r["item_id"]: r for r in rows}, orig

    def test_age_tiers(self):
        rows, _ = self.plan([listing("new", 20, 45), listing("m1", 20, 90), listing("old", 20, 400)])
        self.assertEqual(rows["new"]["status"], "KEEP")
        self.assertEqual((rows["m1"]["status"], rows["m1"]["new_price"]), ("LOWER", 17.99))  # 10% -> 18.00 -> 17.99
        self.assertEqual(rows["old"]["new_price"], 13.99)  # 30% -> 14.00 -> 13.99

    def test_floor_stops_cut(self):
        rows, _ = self.plan([listing("x", 19.99, 400, sku="ABC")], costs={"sku:abc": 9.0})
        floor = reprice.floor_price(9.0, RULES)
        self.assertEqual(rows["x"]["status"], "LOWER_TO_FLOOR")
        self.assertEqual(rows["x"]["new_price"], floor)

    def test_below_floor_is_flagged_not_raised(self):
        rows, _ = self.plan([listing("x", 6.00, 100, sku="A")], costs={"sku:a": 8.0})
        self.assertEqual(rows["x"]["status"], "BELOW_FLOOR")
        self.assertEqual(rows["x"]["new_price"], 6.00)

    def test_watchers_soften(self):
        rows, _ = self.plan([listing("w", 20, 90, watchers=5), listing("w2", 20, 120, watchers=5)])
        self.assertEqual(rows["w"]["status"], "KEEP")
        self.assertEqual(rows["w2"]["new_price"], 17.99)  # 107-day tier softened to 10%

    def test_fast_sellers_wait_a_tier(self):
        rows, _ = self.plan([listing("r", 20, 120, title="Re-Ment Snoopy Kitchen"),
                             listing("p", 20, 120, title="Snoopy Enamel Pins Set"),
                             listing("s", 20, 120, title="Spinning Top Pink")])
        self.assertEqual(rows["r"]["new_price"], 17.99)
        self.assertEqual(rows["p"]["new_price"], 17.99)
        self.assertEqual(rows["s"]["new_price"], 15.99)  # "pin" inside a word doesn't count

    def test_no_stacking_on_rerun(self):
        rows, orig = self.plan([listing("a", 20, 90)])
        rows2, _ = self.plan([listing("a", rows["a"]["new_price"], 91)], originals=orig)
        self.assertEqual(rows2["a"]["status"], "KEEP")
        rows3, _ = self.plan([listing("a", 17.99, 110)], originals=orig)
        self.assertEqual(rows3["a"]["new_price"], 15.99)  # 20% off the original $20

    def test_raised_price_becomes_new_original(self):
        rows, orig = self.plan([listing("a", 25, 90)], originals={"a": 20.0})
        self.assertEqual(orig["a"], 25)
        self.assertEqual(rows["a"]["new_price"], 22.49)

    def test_skips(self):
        rows, _ = self.plan([
            listing("auc", 20, 100, format="auction"), listing("var", 20, 100, variation=True),
            listing("bid", 20, 100, bids=2), listing("nodate", 20, 100, start_date=""),
        ])
        self.assertTrue(all(r["status"] == "SKIP" for r in rows.values()))


class FilesTest(unittest.TestCase):
    def test_seller_hub_export_and_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            export = tmp / "export.csv"
            with open(export, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["Active listings report"])
                w.writerow(["Item number", "Title", "Variation details", "Custom label (SKU)",
                            "Available quantity", "Format", "Currency", "Start price", "Current price",
                            "Watchers", "Bids", "Start date"])
                for i in range(120):
                    w.writerow([f"22700000{i:04d}", f"Snoopy thing {i}", "", "", "1", "FIXED_PRICE", "USD",
                                "24.99", "US $24.99", "0", "0", "Jan-15-26 10:22:01 PST"])
                w.writerow(["227999999999", "Size shirt", "Size: M", "", "1", "FIXED_PRICE", "USD",
                            "15", "15", "0", "0", "Jan-15-26 10:22:01 PST"])
                w.writerow(["227999999999", "Size shirt", "Size: L", "", "1", "FIXED_PRICE", "USD",
                            "15", "15", "0", "0", "Jan-15-26 10:22:01 PST"])
            listings = reprice.read_ebay_export(export)
            self.assertEqual(len(listings), 121)
            self.assertTrue(listings[-1]["variation"])
            self.assertEqual(listings[0]["price"], 24.99)
            out_dir, summary, n = reprice.run(listings, RULES, out_root=tmp / "out", today=TODAY)
            self.assertEqual(n, 3)  # 120 changes -> 50 + 50 + 20
            with open(out_dir / "upload_batch_03.csv") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0][0].split("(")[0], "Action")
            self.assertEqual(len(rows), 21)
            self.assertEqual(rows[1][0], "Revise")
            with open(out_dir / "undo_batch_01.csv") as fh:
                self.assertEqual(list(csv.reader(fh))[1][2], "24.99")
            self.assertTrue((out_dir / "preview.html").exists())
            self.assertTrue((tmp / "out" / "original_prices.csv").exists())
            self.assertIn("Nothing was changed on eBay", summary)

    def test_shopify_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "products.csv"
            with open(p, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["Handle", "Title", "Variant SKU", "Cost per item"])
                w.writerow(["snoopy-mug", "Snoopy Mug!", "", "4.50"])
                w.writerow(["snoopy-mug", "", "MUG-2", "5.00"])
            costs = reprice.read_costs(p)
            self.assertEqual(costs["title:snoopy mug"], 4.50)
            self.assertEqual(costs["sku:mug-2"], 5.00)


class TradingApiTest(unittest.TestCase):
    def test_parse_pricing_list(self):
        xml = """<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents"><ActiveList><ItemArray>
        <Item><ItemID>1</ItemID><Title>Re-Ment Snoopy</Title><ListingType>FixedPriceItem</ListingType>
        <SellingStatus><CurrentPrice currencyID="USD">19.99</CurrentPrice></SellingStatus>
        <ListingDetails><StartTime>2026-07-01T12:00:00.000Z</StartTime></ListingDetails>
        <WatchCount>4</WatchCount><QuantityAvailable>1</QuantityAvailable></Item>
        </ItemArray></ActiveList></GetMyeBaySellingResponse>"""
        row = parse_pricing_list(ET.fromstring(xml))[0]
        self.assertEqual((row["price"], row["watchers"], row["format"]), (19.99, 4, "fixed"))
        self.assertEqual(reprice.parse_date(row["start_date"]), dt.date(2026, 7, 1))


if __name__ == "__main__":
    unittest.main()
