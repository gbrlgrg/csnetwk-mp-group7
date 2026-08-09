"""
server/phase_engine.py — the turn/phase/step state machine: PHASE_TRANSITION
broadcasts, first-turn draw skip, land drops, cleanup discard-to-7
(RFC Section 7). (Rebudiao)
"""

from server.card_catalog import card_def
from server.game_state import GameOver

class PhaseEngineMixin:
    def run_game(self):
        self.turn = 1
        first = True
        prev = "MULLIGAN"
        while True:
            ap = self.players[self.active]
            # ---- Untap (7.2): automatic, no priority ----
            self.phase_transition(prev, "UNTAP")
            for perm in ap["battlefield"]:
                perm["tapped"] = False
            ap["land_played"] = False
            self.broadcast_state()
            # ---- Upkeep (7.3): no triggers exist in MTGNP 1.0, so no
            # priority window here (lean turn) ----
            self.phase_transition("UNTAP", "UPKEEP")
            # ---- Draw (7.4): first player skips draw on turn 1; drawing is
            # automatic with no draw triggers, so no window ----
            self.phase_transition("UPKEEP", "DRAW")
            if not first:
                self.draw_cards(self.active, 1)
                self.broadcast_state()
            first = False
            # ---- Precombat Main (7.5) ----
            self.phase_transition("DRAW", "PRECOMBAT_MAIN")
            self.priority_window(main_phase=True)
            # ---- Combat (Section 9) ----
            last = self.run_combat()
            # ---- Postcombat Main ----
            self.phase_transition(last, "POSTCOMBAT_MAIN")
            self.priority_window(main_phase=True)
            # ---- End Step (7.7): nothing cares about the end step in
            # MTGNP 1.0 (pump clears in Cleanup), so no window ----
            self.phase_transition("POSTCOMBAT_MAIN", "END_STEP")
            # ---- Cleanup (7.8) ----
            self.phase_transition("END_STEP", "CLEANUP")
            self.run_cleanup()
            self.turn += 1
            self.active = 1 - self.active
            prev = "CLEANUP"

    def phase_transition(self, frm, to):
        self.phase = to
        self.send_all({"type": "PHASE_TRANSITION", "from_phase": frm,
                       "to_phase": to, "active_player": self.pid(self.active),
                       "turn": self.turn})
        return self.seq    # seq of the PHASE_TRANSITION (combat request token)

    def draw_cards(self, idx, n):
        for _ in range(n):
            if not self.players[idx]["library"]:
                # RFC 6.5: drawing from an empty library loses the game.
                raise GameOver(1 - idx, idx, "DECK_EMPTY")
            self.players[idx]["hand"].append(
                self.players[idx]["library"].pop(0))

    def run_cleanup(self):
        ap_idx = self.active
        ap = self.players[ap_idx]
        while len(ap["hand"]) > 7:
            req = self.broadcast_state()[ap_idx]   # request PDU (RFC 7.8)
            def regrant():
                pass
            pdu = self.expect(ap_idx, {"DISCARD"}, req,
                              timeout_ms=self.time_limit_ms)
            cards = pdu.get("card_ids", [])
            if any(c not in ap["hand"] for c in cards) or not cards:
                self.send_error(ap_idx, "ILLEGAL_ACTION",
                                "DISCARD lists cards not in your hand.", pdu)
                continue
            for c in cards:
                ap["hand"].remove(c)
                ap["graveyard"].append(c)
        # Clear damage and until-end-of-turn effects; no priority (RFC 7.8).
        for i in (0, 1):
            for p in self.players[i]["battlefield"]:
                if p["creature"]:
                    p["damage"] = 0
                    p["pump_p"] = p["pump_t"] = 0
                    if i == ap_idx:
                        pass
        # Summoning sickness wears off for the player whose turn is starting;
        # simplest faithful model: clear it for the AP's creatures now, since
        # they have been under AP's control since before their next turn.
        for p in ap["battlefield"]:
            if p["creature"]:
                p["summoning_sick"] = False
        self.broadcast_state()

    def try_play_land(self, idx, pdu, main_phase):
        card = pdu.get("card_id")
        d = card_def(self.catalog, card) if card else None
        pl = self.players[idx]
        if not main_phase or idx != self.active or self.stack:
            self.send_error(idx, "WRONG_PHASE",
                            "Lands may only be played in your own Main Phase "
                            "with an empty stack.", pdu)
            return False
        if pl["land_played"]:
            self.send_error(idx, "ILLEGAL_ACTION",
                            "Already played a land this turn.", pdu)
            return False
        if card not in pl["hand"] or not d or d["kind"] != "land":
            self.send_error(idx, "ILLEGAL_ACTION",
                            "That is not a land card in your hand.", pdu)
            return False
        pl["hand"].remove(card)
        pl["battlefield"].append(self.new_perm(card, creature=False))
        pl["land_played"] = True
        return True
