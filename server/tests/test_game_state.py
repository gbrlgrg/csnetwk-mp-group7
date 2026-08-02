"""Unit tests for server/game_state.py and server/state_view.py using a
socketless Server (dummy connections). Run: python3 server/tests/test_game_state.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from server.main import Server


class DummyConn:
    """Captures PDUs the server 'sends' without any real socket."""
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


class TestGameState(unittest.TestCase):
    def test_reset_initialises_model(self):
        s = make_server()
        for p in s.players:
            self.assertEqual(p["life"], 20)
            self.assertEqual(p["battlefield"], [])
            self.assertEqual(p["graveyard"], [])
        self.assertEqual(s.stack, [])
        self.assertEqual(s.phase, "LOBBY")

    def test_pid_and_idx_of(self):
        s = make_server()
        self.assertEqual(s.pid(0), "player_1")
        self.assertEqual(s.idx_of("player_2"), 1)
        self.assertIsNone(s.idx_of("nobody"))

    def test_new_perm_summoning_sickness_and_haste(self):
        s = make_server()
        bears = s.new_perm("grizzly_bears_001", creature=True)
        goblin = s.new_perm("goblin_guide_001", creature=True)
        land = s.new_perm("mountain_001", creature=False)
        self.assertTrue(bears["summoning_sick"])
        self.assertFalse(goblin["summoning_sick"])      # haste
        self.assertFalse(land["creature"])
        self.assertFalse(land["tapped"])

    def test_power_toughness_with_pump(self):
        s = make_server()
        p = s.new_perm("grizzly_bears_001", creature=True)
        self.assertEqual((s.power(p), s.toughness(p)), (2, 2))
        p["pump_p"] += 3
        p["pump_t"] += 3                                # Giant Growth
        self.assertEqual((s.power(p), s.toughness(p)), (5, 5))

    def test_find_perm(self):
        s = make_server()
        perm = s.new_perm("wall_of_stone_001", creature=True)
        s.players[1]["battlefield"].append(perm)
        owner, found = s.find_perm("wall_of_stone_001")
        self.assertEqual(owner, 1)
        self.assertIs(found, perm)
        self.assertEqual(s.find_perm("missing_001"), (None, None))

    def test_visible_state_hides_opponent_hand(self):
        s = make_server()
        s.players[0]["hand"] = ["lightning_bolt_001", "mountain_001"]
        s.players[1]["hand"] = ["counterspell_001"]
        v0 = s.visible_state(0)
        self.assertEqual(v0["hand"]["player_1"],
                         ["lightning_bolt_001", "mountain_001"])
        self.assertNotIn("player_2", v0["hand"])         # hidden info
        self.assertEqual(v0["hand_counts"]["player_2"], 1)
        v1 = s.visible_state(1)
        self.assertNotIn("player_1", v1["hand"])
        self.assertEqual(v1["hand_counts"]["player_1"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
