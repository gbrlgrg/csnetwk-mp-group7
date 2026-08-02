"""
server/game_state.py — the authoritative data model: players, permanents,
stack items, and the control-flow exceptions that end a game.
(GameState / PlayerState / Permanent / StackItem; RFC 4.2). (Gregorio)
"""

from server.card_catalog import card_def

class GameOver(Exception):
    """Raised anywhere inside IN_GAME to unwind to the GAME_OVER broadcast."""
    def __init__(self, winner_idx, loser_idx, reason):
        self.winner_idx = winner_idx
        self.loser_idx = loser_idx
        self.reason = reason


class ClientGone(Exception):
    """A TCP-level disconnect or heartbeat/priority timeout for one client."""
    def __init__(self, idx):
        self.idx = idx


class GameStateMixin:
    def reset_game_state(self):
        self.players = []
        for i in range(2):
            self.players.append({
                "id": None, "deck": None,      # filled during LOBBY
                "library": [], "hand": [], "graveyard": [],
                "battlefield": [],             # list of permanent dicts
                "life": 20, "mulligans": 0, "land_played": False,
            })
        self.turn = 0
        self.active = 0
        self.stack = []                        # index 0 = bottom (RFC 8.3)
        self.phase = "LOBBY"

    def pid(self, idx):
        return self.players[idx]["id"]

    def idx_of(self, player_id):
        for i in (0, 1):
            if self.players[i]["id"] == player_id:
                return i
        return None

    def find_perm(self, perm_id):
        for i in (0, 1):
            for p in self.players[i]["battlefield"]:
                if p["id"] == perm_id:
                    return i, p
        return None, None

    def new_perm(self, card_id, creature):
        perm = {"id": card_id, "tapped": False}
        if creature:
            d = card_def(self.catalog, card_id)
            perm.update({"damage": 0, "base_power": d["power"],
                         "base_toughness": d["toughness"],
                         "pump_p": 0, "pump_t": 0,
                         "summoning_sick": not d.get("haste", False),
                         "creature": True})
        else:
            perm["creature"] = False
        return perm

    def power(self, perm):
        return perm["base_power"] + perm["pump_p"]

    def toughness(self, perm):
        return perm["base_toughness"] + perm["pump_t"]
