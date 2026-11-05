import unittest
from datetime import datetime, timedelta, timezone

from rrr_sync import new_tags
from rrr_sync.new_tags import Product, plan

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def prod(pid, days_old, tags=(), status="ACTIVE"):
    return Product(f"gid://shopify/Product/{pid}", f"Item {pid}", list(tags), NOW - timedelta(days=days_old), status)


class PlanTests(unittest.TestCase):
    def actions(self, products, **kw):
        return {(c.action, c.product.id.rsplit("/", 1)[-1]) for c in plan(products, now=NOW, **kw)}

    def test_new_listing_gets_tag(self):
        self.assertEqual(self.actions([prod(1, 0)]), {("add", "1")})

    def test_already_tagged_new_listing_left_alone(self):
        self.assertEqual(self.actions([prod(1, 3, ["new item"])]), set())

    def test_tag_removed_after_14_days(self):
        self.assertEqual(self.actions([prod(1, 15, ["new item", "Snoopy"])]), {("remove", "1")})

    def test_day_14_still_new(self):
        self.assertEqual(self.actions([prod(1, 13.9, ["new item"])]), set())

    def test_old_untagged_product_ignored(self):
        self.assertEqual(self.actions([prod(1, 200, ["Snoopy"])]), set())

    def test_tag_match_ignores_case_and_spaces(self):
        self.assertEqual(self.actions([prod(1, 2, [" New Item "])]), set())
        self.assertEqual(self.actions([prod(2, 30, ["NEW ITEM"])]), {("remove", "2")})

    def test_keep_new_pin_is_respected(self):
        self.assertEqual(self.actions([prod(1, 60, ["new item", "keep-new"])]), set())

    def test_archived_new_product_not_tagged(self):
        self.assertEqual(self.actions([prod(1, 1, status="ARCHIVED")]), set())

    def test_similar_tags_untouched(self):
        self.assertEqual(self.actions([prod(1, 40, ["New York Taxi"])]), set())

    def test_duplicates_from_both_searches_counted_once(self):
        p = prod(1, 20, ["new item"])
        self.assertEqual(len(plan([p, p], now=NOW)), 1)

    def test_custom_window(self):
        self.assertEqual(self.actions([prod(1, 8, ["new item"])], days=7), {("remove", "1")})


class FakeShopify:
    def __init__(self, products):
        self.products = {p.id: p for p in products}
        self.mutations = []

    def graphql(self, query, variables):
        if "tagsAdd" in query or "tagsRemove" in query:
            self.mutations.append(("add" if "tagsAdd" in query else "remove", variables["id"], variables["tags"]))
            key = "tagsAdd" if "tagsAdd" in query else "tagsRemove"
            return {key: {"userErrors": []}}
        nodes = [{"id": p.id, "title": p.title, "tags": p.tags, "createdAt": p.created_at.isoformat(), "status": p.status}
                 for p in self.products.values()]
        return {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}


class RunTests(unittest.TestCase):
    def setUp(self):
        self.shop = FakeShopify([prod(1, 1), prod(2, 20, ["new item"]), prod(3, 5, ["new item"])])

    def test_dry_run_changes_nothing(self):
        changes, _ = new_tags.run(self.shop, do_apply=False, log=lambda *_: None)
        self.assertEqual(len(changes), 2)
        self.assertEqual(self.shop.mutations, [])

    def test_apply_sends_only_the_one_tag(self):
        new_tags.run(self.shop, do_apply=True, log=lambda *_: None)
        self.assertEqual(sorted(self.shop.mutations), [
            ("add", "gid://shopify/Product/1", ["new item"]),
            ("remove", "gid://shopify/Product/2", ["new item"]),
        ])


if __name__ == "__main__":
    unittest.main()
