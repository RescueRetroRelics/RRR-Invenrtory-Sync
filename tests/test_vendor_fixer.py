import os
import unittest

from vendor_fixer.vendor_fixer import DEFAULT_RULES, load_rules, propose

RULES = load_rules(DEFAULT_RULES)


class ProposeTests(unittest.TestCase):
    def check(self, title, brand, conf, ptype="", tags=""):
        got = propose(title, ptype, tags, RULES)
        self.assertEqual((got[0], got[1]), (brand, conf), got[2])

    def test_hippers_beats_sonny_angel_keyword(self):
        self.check("Snoopy Hippers King Snoopy Peanuts Sonny Angel Mini Figure", "Takara Tomy", "high")

    def test_rement_beats_pokemon(self):
        self.check("Charizard Pokémon Randoseru 2 Re-Ment Mini School Bag", "Re-Ment", "high")

    def test_bandai_beats_hello_kitty(self):
        self.check("Hello Kitty BanDai American Design Gashapon Full Set", "Bandai", "high")

    def test_tag_used_when_title_silent(self):
        self.check("Snoopy Bunnies Keychain Green", "Accommode", "high", tags="Accommode, Snoopy")

    def test_franchise_fallback_is_low(self):
        self.check("Cute Snoopy A5 Acrylic Shaker Notebook", "Peanuts", "low")

    def test_no_match(self):
        self.check("Vintage Brass Candle Holder Pair", "", "none")

    def test_word_start_only(self):
        # "avon" must not match inside "Savonnerie"
        self.check("Savonnerie Rug Runner", "", "none")


if __name__ == "__main__":
    unittest.main()
