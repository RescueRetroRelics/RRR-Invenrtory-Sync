import unittest
from types import SimpleNamespace

from rrr_sync import alt_text
from rrr_sync.alt_text import _clean, template_alt

CFG_KEY = SimpleNamespace(anthropic_api_key="sk-test", alt_text_model="claude-haiku-5-5")
CFG_NOKEY = SimpleNamespace(anthropic_api_key="", alt_text_model="claude-haiku-5-5")


def media(mid, alt):
    return {"id": f"gid://shopify/MediaImage/{mid}", "alt": alt, "image": {"url": f"https://cdn.shopify.com/{mid}.jpg?v=1"}}


class FakeShopify:
    def __init__(self):
        self.products = [
            {"id": "gid://shopify/Product/1", "title": "Garfield A7 Sticker Album",
             "media": {"nodes": [media(1, ""), media(2, "Existing great alt"), media(3, None)]}},
            {"id": "gid://shopify/Product/2", "title": "Snoopy Plush Keychain", "media": {"nodes": [media(4, "Done already")]}},
        ]
        self.updates = []
        self.queries = []

    def graphql(self, q, v):
        if "fileUpdate" in q:
            self.updates.extend(v["files"])
            return {"fileUpdate": {"files": v["files"], "userErrors": []}}
        self.queries.append(v["q"])
        return {"products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.products}}


class FakeClaude:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def describe(self, url, title, pos, count):
        self.calls.append((url, pos, count))
        if self.fail_on == pos:
            raise RuntimeError("boom")
        return f"Photo of a sticker album, view {pos}"


def quiet(*_):
    pass


class AltTextTests(unittest.TestCase):
    def test_only_blank_photos_get_alt(self):
        shop, claude = FakeShopify(), FakeClaude()
        done, _ = alt_text.run(shop, CFG_KEY, do_apply=True, log=quiet, describer=claude)
        self.assertEqual(done, 2)
        self.assertEqual([u["id"][-1] for u in shop.updates], ["1", "3"])
        self.assertEqual([c[1] for c in claude.calls], [1, 3])

    def test_existing_alt_is_never_overwritten(self):
        shop = FakeShopify()
        alt_text.run(shop, CFG_KEY, do_apply=True, log=quiet, describer=FakeClaude())
        self.assertNotIn("gid://shopify/MediaImage/2", [u["id"] for u in shop.updates])
        self.assertNotIn("gid://shopify/MediaImage/4", [u["id"] for u in shop.updates])

    def test_dry_run_saves_nothing(self):
        shop = FakeShopify()
        done, _ = alt_text.run(shop, CFG_KEY, do_apply=False, log=quiet, describer=FakeClaude())
        self.assertEqual((done, shop.updates), (2, []))

    def test_claude_output_is_cleaned(self):
        shop = FakeShopify()
        alt_text.run(shop, CFG_KEY, do_apply=True, log=quiet, describer=FakeClaude())
        self.assertEqual(shop.updates[0]["alt"], "A sticker album, view 1")

    def test_claude_failure_falls_back_to_title(self):
        shop = FakeShopify()
        done, errors = alt_text.run(shop, CFG_KEY, do_apply=True, log=quiet, describer=FakeClaude(fail_on=3))
        self.assertEqual(done, 2)
        self.assertEqual(shop.updates[1]["alt"], "Garfield A7 Sticker Album - photo 3 of 3")
        self.assertTrue(any("Claude failed" in e for e in errors))

    def test_no_api_key_uses_titles(self):
        shop = FakeShopify()
        alt_text.run(shop, CFG_NOKEY, do_apply=True, log=quiet)
        self.assertEqual(shop.updates[0]["alt"], "Garfield A7 Sticker Album - main photo")

    def test_max_limits_photos_per_run(self):
        shop = FakeShopify()
        done, _ = alt_text.run(shop, CFG_KEY, do_apply=True, max_images=1, log=quiet, describer=FakeClaude())
        self.assertEqual(len(shop.updates), 1)

    def test_recent_window_vs_all(self):
        shop = FakeShopify()
        alt_text.run(shop, CFG_KEY, log=quiet, describer=FakeClaude())
        alt_text.run(shop, CFG_KEY, scan_all=True, log=quiet, describer=FakeClaude())
        self.assertTrue(shop.queries[0].startswith("created_at:>="))
        self.assertIsNone(shop.queries[1])


class CleanTests(unittest.TestCase):
    def test_strips_prefix_quotes_and_extra_lines(self):
        self.assertEqual(_clean('"Image of a red Snoopy plush"\nExtra line'), "A red Snoopy plush")

    def test_length_cap_on_word_boundary(self):
        out = _clean("word " * 60)
        self.assertLessEqual(len(out), 125)
        self.assertFalse(out.endswith(" "))

    def test_template_single_and_long(self):
        self.assertEqual(template_alt("Snoopy Plush", 1, 1), "Snoopy Plush")
        self.assertLessEqual(len(template_alt("Long title " * 20, 2, 5)), 125)


class ClaudeRequestTests(unittest.TestCase):
    def test_request_shape(self):
        sent = {}

        def fake_request_json(method, url, headers=None, payload=None, **kw):
            sent.update(method=method, url=url, headers=headers, payload=payload)
            return {"content": [{"type": "text", "text": "Snoopy plush keychain with red jacket"}]}

        orig = alt_text.http.request_json
        alt_text.http.request_json = fake_request_json
        try:
            out = alt_text.ClaudeAlt("sk-x", "claude-haiku-5-5").describe("https://cdn.shopify.com/a.jpg?v=1", "Snoopy", 1, 3)
        finally:
            alt_text.http.request_json = orig
        self.assertEqual(out, "Snoopy plush keychain with red jacket")
        self.assertEqual(sent["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], "sk-x")
        img = sent["payload"]["messages"][0]["content"][0]
        self.assertEqual(img["source"], {"type": "url", "url": "https://cdn.shopify.com/a.jpg?v=1&width=800"})
        self.assertEqual(sent["payload"]["model"], "claude-haiku-5-5")


if __name__ == "__main__":
    unittest.main()
