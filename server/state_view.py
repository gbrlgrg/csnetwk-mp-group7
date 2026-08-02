"""
server/state_view.py — builds the personalized GAME_STATE_UPDATE views:
each player sees their own hand, the opponent's hand only as a count
(RFC 4.2, hidden information). (Gregorio)
"""



class StateViewMixin:
    def visible_state(self, for_idx):
        me, opp = self.players[for_idx], self.players[1 - for_idx]
        def bf(pl):
            out = []
            for p in pl["battlefield"]:
                if p["creature"]:
                    out.append({"id": p["id"], "tapped": p["tapped"],
                                "damage": p["damage"],
                                "power": self.power(p),
                                "toughness": self.toughness(p),
                                "summoning_sick": p["summoning_sick"]})
                else:
                    out.append({"id": p["id"], "tapped": p["tapped"]})
            return out
        return {
            "turn": self.turn, "phase": self.phase,
            "active_player": self.pid(self.active),
            "life_totals": {self.pid(0): self.players[0]["life"],
                            self.pid(1): self.players[1]["life"]},
            "stack": [dict(s) for s in self.stack],
            "battlefield": {self.pid(0): bf(self.players[0]),
                            self.pid(1): bf(self.players[1])},
            "graveyard": {self.pid(0): list(self.players[0]["graveyard"]),
                          self.pid(1): list(self.players[1]["graveyard"])},
            "hand": {self.pid(for_idx): list(me["hand"])},
            "hand_counts": {self.pid(1 - for_idx): len(opp["hand"])},
            "library_counts": {self.pid(0): len(self.players[0]["library"]),
                               self.pid(1): len(self.players[1]["library"])},
            "land_played_this_turn": self.players[self.active]["land_played"],
        }

    def broadcast_state(self):
        """Send a personalized GAME_STATE_UPDATE to each player. Returns a
        map seat -> seq_num sent (used when a state update doubles as a
        server request PDU, e.g. mulligan or cleanup discard)."""
        seqs = {}
        for i in (0, 1):
            seqs[i] = self.send_to(i, {"type": "GAME_STATE_UPDATE",
                                       "state": self.visible_state(i)})
        return seqs
