"""Unit tests for server/card_catalog.py (loading, instance-ID resolution,
deck-list validation per RFC 6.2). Run: python3 server/tests/test_card_catalog.py"""
import os
import re
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from server.card_catalog import base_name, card_def, load_catalog, validate_deck


class TestCardCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()

    def test_catalog_loads_expected_cards(self):
        for base in ("mountain", "island", "swamp", "forest", "plains",
                     "goblin_guide", "grizzly_bears", "wall_of_stone",
                     "lightning_bolt", "shock", "counterspell",
                     "giant_growth", "healing_salve", "divination",
                     "gray_merchant", "festering_imp", "youthful_knight",
                     "reckless_wurm",
                     # engine-ready CSV additions
                     "searing_spear", "skullcrack", "incinerate", "rift_bolt",
                     "lava_spike", "flame_slash", "monastery_swiftspear",
                     "goblin_bushwhacker", "cancel", "negate", "mana_leak",
                     "phantasmal_bear", "air_elemental", "leatherback_baloth",
                     "savannah_lions", "white_knight", "serra_angel",
                     "black_knight", "ornithopter"):
            self.assertIn(base, self.cat, base)
        self.assertNotIn("_comment", self.cat)

    def test_base_name_strips_instance_suffix(self):
        self.assertEqual(base_name("lightning_bolt_001"), "lightning_bolt")
        self.assertEqual(base_name("wall_of_stone_004"), "wall_of_stone")
        # no numeric suffix -> unchanged
        self.assertEqual(base_name("mountain"), "mountain")

    def test_card_def_resolves_instances(self):
        d = card_def(self.cat, "goblin_guide_017")
        self.assertEqual(d["name"], "Goblin Guide")
        self.assertTrue(d.get("haste"))
        self.assertIsNone(card_def(self.cat, "black_lotus_001"))

    def test_validate_deck_accepts_legal_decks(self):
        self.assertIsNone(validate_deck(self.cat, ["mountain_001"]))
        self.assertIsNone(validate_deck(self.cat, ["swamp_%03d" % i
                                                   for i in range(1, 51)]))

    def test_validate_deck_rejects_illegal_decks(self):
        self.assertIsNotNone(validate_deck(self.cat, []))            # 0 cards
        self.assertIsNotNone(validate_deck(self.cat,
                                           ["mountain_001"] * 51))   # 51 cards
        self.assertIsNotNone(validate_deck(self.cat, ["not_a_card_001"]))
        self.assertIsNotNone(validate_deck(self.cat, "mountain_001"))  # not a list

    def test_sample_decks_are_legal_and_spec_consistent(self):
        decks_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "decks")
        for name in sorted(os.listdir(decks_dir)):
            if not name.endswith(".txt"):
                continue
            deck = []
            with open(os.path.join(decks_dir, name), encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        deck.append(ln)
            self.assertIsNone(validate_deck(self.cat, deck), name)
            self.assertEqual(len(set(deck)), len(deck),
                             f"{name}: duplicate instance IDs")
            counts = Counter(base_name(c) for c in deck)
            for base, n in counts.items():
                d = self.cat[base]
                limit = 20 if d["kind"] == "land" else 4
                self.assertLessEqual(n, limit,
                                     f"{name}: {base} x{n} > {limit}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
