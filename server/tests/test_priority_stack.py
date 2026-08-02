"""Unit tests for server/priority.py and server/stack.py: seq_num token
validation (STALE_ACTION / NOT_YOUR_PRIORITY / UNKNOWN_TYPE), LIFO stack
resolution order, and counterspell fizzle — all with dummy connections.
Run: python3 server/tests/test_priority_stack.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

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


def sent_types(conn):
    return [p["type"] for p in conn.sent]


class TestPriorityTokens(unittest.TestCase):
    def test_seq_counter_is_monotonic_and_shared_on_broadcast(self):
        s = make_server()
        a = s.send_to(0, {"type": "PRIORITY_GRANT"})
        b = s.send_all({"type": "PHASE_TRANSITION"})
        c = s.send_to(1, {"type": "PRIORITY_GRANT"})
        self.assertEqual((a, b, c), (1, 2, 3))
        # broadcast delivered identically to both with one seq
        self.assertEqual(s.clients[0].sent[-1]["seq_num"], 2)
        self.assertEqual(s.clients[1].sent[0]["seq_num"], 2)

    def test_expect_rejects_stale_wrong_player_unknown(self):
        s = make_server()
        token = s.send_to(0, {"type": "PRIORITY_GRANT"})
        # stale seq -> STALE_ACTION, then re-granted token is accepted
        s.events.put(("pdu", 0, {"type": "PRIORITY_PASS", "seq_num": 99}))
        s.events.put(("pdu", 1, {"type": "PRIORITY_PASS", "seq_num": token}))
        s.events.put(("pdu", 0, {"type": "FROBNICATE", "seq_num": token}))
        s.events.put(("pdu", 0, {"type": "PRIORITY_PASS", "seq_num": token}))
        pdu = s.expect(0, {"PRIORITY_PASS"}, token)
        self.assertEqual(pdu["type"], "PRIORITY_PASS")
        codes = [p["code"] for p in s.clients[0].sent if p["type"] == "ERROR"]
        self.assertIn("STALE_ACTION", codes)
        self.assertIn("UNKNOWN_TYPE", codes)
        codes1 = [p["code"] for p in s.clients[1].sent if p["type"] == "ERROR"]
        self.assertIn("NOT_YOUR_PRIORITY", codes1)

    def test_expect_regrant_updates_expected_token(self):
        s = make_server()
        token = s.send_to(0, {"type": "PRIORITY_GRANT"})
        new_token = {}
        def regrant():
            # idempotent: repeated stale rejections re-send the same grant
            if "v" not in new_token:
                new_token["v"] = s.grant_priority(0)
            return new_token["v"]
        s.events.put(("pdu", 0, {"type": "PRIORITY_PASS", "seq_num": 999}))
        # after the re-grant, the OLD token must now be rejected...
        s.events.put(("pdu", 0, {"type": "PRIORITY_PASS", "seq_num": token}))
        # ...and the NEW token accepted.
        s.events.put(("pdu", 0, {"type": "PRIORITY_PASS", "seq_num": token + 1}))
        pdu = s.expect(0, {"PRIORITY_PASS"}, token, regrant=regrant)
        self.assertEqual(pdu["seq_num"], new_token["v"])


class TestStack(unittest.TestCase):
    def field(self, s, idx, *cards):
        for c in cards:
            s.players[idx]["battlefield"].append(
                s.new_perm(c, creature=not c.startswith(("mountain", "island",
                                                         "swamp", "forest",
                                                         "plains"))))

    def test_cast_pushes_and_resolves_lifo(self):
        s = make_server()
        s.active = 0
        self.field(s, 0, "mountain_001", "mountain_002")
        s.players[0]["hand"] = ["lightning_bolt_001", "shock_001"]
        ok = s.try_cast(0, {"type": "CAST_SPELL", "card_id":
                            "lightning_bolt_001", "targets": ["player_2"],
                            "mana_payment": {"R": 1}}, main_phase=True)
        self.assertTrue(ok)
        # instants may be cast with a non-empty stack
        ok = s.try_cast(0, {"type": "CAST_SPELL", "card_id": "shock_001",
                            "targets": ["player_2"],
                            "mana_payment": {"R": 1}}, main_phase=True)
        self.assertTrue(ok)
        self.assertEqual([i["source"] for i in s.stack],
                         ["lightning_bolt_001", "shock_001"])
        s.resolve_top()                                  # LIFO: Shock first
        self.assertEqual(s.players[1]["life"], 18)       # 2 damage
        s.resolve_top()
        self.assertEqual(s.players[1]["life"], 15)       # then 3 damage
        self.assertEqual(s.players[0]["graveyard"],
                         ["shock_001", "lightning_bolt_001"])

    def test_counterspell_fizzles_target(self):
        s = make_server()
        s.active = 0
        self.field(s, 0, "mountain_001")
        self.field(s, 1, "island_001", "island_002")
        s.players[0]["hand"] = ["lightning_bolt_001"]
        s.players[1]["hand"] = ["counterspell_001"]
        s.try_cast(0, {"card_id": "lightning_bolt_001",
                       "targets": ["player_2"], "mana_payment": {"R": 1}},
                   main_phase=True)
        bolt_item = s.stack[-1]["stack_item_id"]
        s.try_cast(1, {"card_id": "counterspell_001",
                       "targets": [bolt_item], "mana_payment": {"U": 2}},
                   main_phase=False)
        s.resolve_top()                                  # Counterspell
        self.assertEqual(s.stack, [])                    # bolt removed
        self.assertEqual(s.players[1]["life"], 20)       # no damage dealt
        self.assertIn("lightning_bolt_001", s.players[0]["graveyard"])
        fizzles = [p for p in s.clients[0].sent
                   if p["type"] == "STACK_RESOLVE" and p["result"] == "FIZZLE"]
        self.assertEqual(len(fizzles), 1)

    def test_sorcery_speed_rejected_off_turn(self):
        s = make_server()
        s.active = 0
        self.field(s, 1, "forest_001", "island_001")
        s.players[1]["hand"] = ["grizzly_bears_001"]
        ok = s.try_cast(1, {"card_id": "grizzly_bears_001", "targets": [],
                            "mana_payment": {"G": 1, "U": 1}},
                        main_phase=True)
        self.assertFalse(ok)                             # not their turn
        codes = [p["code"] for p in s.clients[1].sent if p["type"] == "ERROR"]
        self.assertIn("WRONG_PHASE", codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
