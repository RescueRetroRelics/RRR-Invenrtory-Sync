import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sale_analyzer import analyze, settings, sources

TODAY = date(2026, 11, 2)


def product(pid, title, created, qty=2, price=20.0, cost=None, ptype="Pins", status="ACTIVE"):
    return {"id": pid, "title": title, "type": ptype, "status": status, "created": created,
            "qty": qty, "price": price, "cost": cost, "sku": ""}


def sale(title, day, channel="eBay", pid=None, unit=None, list_price=None, qty=1, sku="", item_id=""):
    return {"channel": channel, "date": day, "title": title, "qty": qty, "unit_price": unit,
            "list_price": list_price, "sku": sku, "item_id": item_id, "product_id": pid}


def cfg(**over):
    s = settings.load(Path("/nonexistent"))
    s.update(over)
    return s


class MatchingTest(unittest.TestCase):
    def test_order_of_matching(self):
        products = [
            product("1", "Garfield Paws Inc Licensed 5-Piece Gold-Outlined Enamel Pin Set", date(2026, 6, 1)),
            product("2", "Batman 3 Piece Enamel Pin Set Bat Signal Mask Licensed", date(2026, 6, 1)),
            product("3", "Re-Ment Snoopy Ketchup", date(2026, 6, 1)),
        ]
        products[2]["sku"] = "SNP-KETCHUP"
        sales = [
            sale("anything", TODAY, sku="snp-ketchup"),
            sale("Garfield Paws Inc 5 Piece Gold Enamel Pin Set Odie Pookie King Licensed", TODAY),
            sale("Batman 2 Piece Enamel Pin Set Bat Signal Mask Licensed DC NEW", TODAY),
            sale("Completely unrelated Hello Kitty mug", TODAY),
            sale("Other eBay title", TODAY, item_id="999"),
        ]
        sources.match_sales(sales, products, item_titles={"999": "Re-Ment Snoopy Ketchup"})
        self.assertEqual([s["product_id"] for s in sales], ["3", "1", "2", None, "3"])
        self.assertEqual(sales[4]["match"], "sync match")


class TierTest(unittest.TestCase):
    def test_defaults_when_few_sales(self):
        info = analyze.learn_tier_days([], cfg())
        self.assertEqual(info["tier_days"], [60, 120, 180])

    def test_learned_tiers_are_increasing(self):
        sales = [{"days_to_sell": d} for d in range(1, 41)]
        days = analyze.learn_tier_days(sales, cfg())["tier_days"]
        self.assertEqual(days, sorted(days))
        self.assertGreaterEqual(days[0], 30)
        self.assertGreaterEqual(days[1] - days[0], 21)

    def test_clock_restarts_at_last_sale_and_floor_holds(self):
        products = [
            product("old", "Old unsold", date(2026, 3, 1), price=20.0),
            product("cheap", "Old cheap", date(2026, 3, 1), price=6.0, cost=4.0),
            product("resold", "Sold last week", date(2026, 3, 1), price=20.0),
            product("new", "New", date(2026, 10, 20), price=20.0),
        ]
        sales = [sale("Sold last week", date(2026, 10, 26), pid="resold")]
        analyze.attach_days(sales, products)
        rows, _, _, _ = analyze.suggest_tiers(products, sales, cfg(), TODAY)
        by = {r["product_id"]: r for r in rows}
        self.assertEqual(by["old"]["tier"], 3)
        self.assertEqual(by["old"]["suggested_price"], 13.99)   # 30% off, ends in .99
        self.assertEqual(by["cheap"]["suggested_price"], 6.0)   # cost 4 + $3 floor > price -> unchanged
        self.assertEqual(by["resold"]["tier"], 0)
        self.assertEqual(by["new"]["tier"], 0)

    def test_hot_category_waits_one_tier(self):
        products = [product(str(i), f"Pin {i}", date(2026, 3, 1), qty=1, ptype="Pins") for i in range(6)]
        products += [product(f"s{i}", f"Sticker {i}", date(2026, 3, 1), qty=10, ptype="Stickers") for i in range(5)]
        sales = [sale(f"Pin {i}", date(2026, 4, 1), pid=str(i)) for i in range(6)]
        analyze.attach_days(sales, products)
        rows, _, hot, _ = analyze.suggest_tiers(products, sales, cfg(tier_days=[60, 120, 180]), TODAY)
        self.assertIn("Pins", hot)
        by = {r["product_id"]: r for r in rows}
        self.assertEqual(by["0"]["tier"], 2)       # 215 idle days would be tier 3
        self.assertEqual(by["s0"]["tier"], 3)


class FileTest(unittest.TestCase):
    def test_shopify_orders_csv_skips_cancelled_and_applies_discount(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.csv"
            with path.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Name", "Created at", "Financial Status", "Cancelled at", "Lineitem quantity",
                            "Lineitem name", "Lineitem price", "Lineitem discount", "Lineitem sku"])
                w.writerow(["#1", "2026-08-24 08:30:10 -0400", "paid", "", "2", "Washi", "14.00", "2.00", ""])
                w.writerow(["#2", "2026-08-25 08:30:10 -0400", "refunded", "", "1", "Gone", "9.00", "0", ""])
            self.assertEqual(sources.sniff_csv(path), "shopify_orders")
            rows = sources.shopify_orders_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["date"], rows[0]["unit_price"], rows[0]["qty"]), (date(2026, 8, 24), 13.0, 2))

    def test_run_end_to_end_on_sample(self):
        sample = Path(__file__).resolve().parent.parent / "samples" / "sale_analyzer" / "inputs"
        products = sources.products_json(sample / "shopify_products.json")
        sales = (sources.shopify_orders_csv(sample / "shopify_orders_export.csv")
                 + sources.ebay_orders_csv(sample / "ebay_orders_since_2026-01-01.csv"))
        sources.match_sales(sales, products)
        result = analyze.run(products, sales, cfg(), TODAY)
        self.assertEqual(result["matched"], len(sales))
        self.assertTrue(all(r["suggested_price"] <= r["price"] for r in result["tiers"]))


if __name__ == "__main__":
    unittest.main()
