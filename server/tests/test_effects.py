"""Unit tests for server/effects.py (mana payment, damage, pump, life gain,
draw, drain) and the state-based actions in server/stack.py.
Run: python3 server/tests/test_effects.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from server.game_state import GameOver
from server.main import Server


class DummyConn:
    def __init__(self):
        self.alive = True
        self.player_id = None
        self.sent = []

    def send(self, pdu):
        self.sent.append(pdu)


def make_server():
    srv = Server(port=0, time_limit_ms=1000)
    srv.clients = [DummyConn(), DummyConn()]
    srv.reset_game_state()
    srv.players[0]["id"] = "player_1"
    srv.players[1]["id"] = "player_2"
    return srv


def lands(s, idx, *cards):
    for c in cards:
        s.players[idx]["battlefield"].append(s.new_perm(c, creature=False))


class TestManaPayment(unittest.TestCase):
    def test_valid_payment_taps_lands(self):
        s = make_server()
        lands(s, 0, "mountain_001", "mountain_002", "plains_001")
        ok = s.check_and_pay_mana(0, {"R": 2, "X": 1},
                                  {"R": 2, "W": 1}, {"type": "CAST_SPELL"})
        self.assertTrue(ok)
        self.assertTrue(all(p["tapped"] for p in s.players[0]["battlefield"]))

    def test_colored_requirement_enforced(self):
        s = make_server()
        lands(s, 0, "forest_001", "forest_002")
        # Counterspell needs UU; paying GG is INSUFFICIENT_MANA
        ok = s.check_and_pay_mana(0, {"U": 2}, {"G": 2}, {})
        self.assertFalse(ok)

    def test_unpayable_pool_rejected_and_untapped(self):
        s = make_server()
        lands(s, 0, "mountain_001")
        ok = s.check_and_pay_mana(0, {"R": 2}, {"R": 2}, {})
        self.assertFalse(ok)
        self.assertFalse(s.players[0]["battlefield"][0]["tapped"])
        codes = [p["code"] for p in s.clients[0].sent if p["type"] == "ERROR"]
        self.assertIn("INSUFFICIENT_MANA", codes)


class TestEffects(unittest.TestCase):
    def test_damage_to_player_and_creature(self):
        s = make_server()
        s.deal_damage("lightning_bolt_001", "player_2", 3)
        self.assertEqual(s.players[1]["life"], 17)
        perm = s.new_perm("wall_of_stone_001", creature=True)
        s.players[1]["battlefield"].append(perm)
        s.deal_damage("shock_001", "wall_of_stone_001", 2)
        self.assertEqual(perm["damage"], 2)

    def test_pump_lifegain_draw(self):
        s = make_server()
        perm = s.new_perm("grizzly_bears_001", creature=True)
        s.players[0]["battlefield"].append(perm)
        d = s.catalog["giant_growth"]
        s.apply_spell_effect(d, {"targets": ["grizzly_bears_001"]}, 0)
        self.assertEqual((s.power(perm), s.toughness(perm)), (5, 5))
        d = s.catalog["healing_salve"]
        s.apply_spell_effect(d, {"targets": ["player_1"]}, 0)
        self.assertEqual(s.players[0]["life"], 23)
        s.players[0]["library"] = ["mountain_001", "mountain_002",
                                   "mountain_003"]
        d = s.catalog["divination"]
        s.apply_spell_effect(d, {"targets": []}, 0)
        self.assertEqual(len(s.players[0]["hand"]), 2)

    def test_drain_and_opp_lose_triggers(self):
        s = make_server()
        s.apply_trigger_effect(
            {"trigger_effect": s.catalog["gray_merchant"]["etb_trigger"]}, 1)
        self.assertEqual(s.players[0]["life"], 18)
        self.assertEqual(s.players[1]["life"], 22)
        s.apply_trigger_effect(
            {"trigger_effect": s.catalog["festering_imp"]["death_trigger"]}, 1)
        self.assertEqual(s.players[0]["life"], 17)
        self.assertEqual(s.players[1]["life"], 22)      # no gain on imp


class TestStateBasedActions(unittest.TestCase):
    def test_lethal_damage_moves_creature_to_graveyard(self):
        s = make_server()
        perm = s.new_perm("grizzly_bears_001", creature=True)
        perm["damage"] = 2
        s.players[1]["battlefield"].append(perm)
        deaths = s.check_sbas()
        self.assertEqual(deaths, [(1, "grizzly_bears_001")])
        self.assertEqual(s.players[1]["battlefield"], [])
        self.assertIn("grizzly_bears_001", s.players[1]["graveyard"])

    def test_zero_toughness_dies(self):
        s = make_server()
        perm = s.new_perm("youthful_knight_001", creature=True)
        perm["pump_t"] = -1                              # 2/0
        s.players[0]["battlefield"].append(perm)
        deaths = s.check_sbas()
        self.assertEqual(deaths, [(0, "youthful_knight_001")])

    def test_life_zero_raises_game_over_ap_loses_ties(self):
        s = make_server()
        s.active = 0
        s.players[0]["life"] = 0
        s.players[1]["life"] = 0
        with self.assertRaises(GameOver) as ctx:
            s.check_sbas()
        # Simultaneous death: the ACTIVE player loses the tie (RFC 8.4).
        self.assertEqual(ctx.exception.loser_idx, 0)
        self.assertEqual(ctx.exception.reason, "LIFE_ZERO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
