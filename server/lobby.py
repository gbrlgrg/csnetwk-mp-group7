"""
server/lobby.py — LOBBY and pre-game lifecycle: PLAYER_READY validation
(DUPLICATE_ID / ILLEGAL_DECK), lobby reconnects, GAME_SETUP dealing and the
London Mulligan (RFC 6.2-6.4). (Rebudiao)
"""

import random

from server.card_catalog import card_def, validate_deck
from server.game_state import ClientGone, GameOver

class LobbyMixin:
    def run_lobby(self):
        self.phase = "LOBBY"
        for p in self.players:
            p["id"], p["deck"] = None, None   # player IDs reset each lobby
        print("[server] LOBBY: waiting for PLAYER_READY from both seats")
        while not all(p["deck"] for p in self.players):
            try:
                idx, pdu = self.next_event()
            except ClientGone as e:
                self.handle_lobby_disconnect(e.idx)
                continue
            if pdu.get("type") != "PLAYER_READY":
                self.send_error(idx, "ILLEGAL_ACTION",
                                "Only PLAYER_READY is accepted in LOBBY.", pdu)
                continue
            self.handle_player_ready(idx, pdu)

    def handle_player_ready(self, idx, pdu):
        player_id = pdu.get("player_id")
        deck = pdu.get("deck_list")
        other = self.players[1 - idx]["id"]
        if not isinstance(player_id, str) or not player_id:
            self.send_error(idx, "ILLEGAL_ACTION",
                            "player_id must be a non-empty string.", pdu)
            return
        if player_id == other:
            self.send_error(idx, "DUPLICATE_ID",
                            f"player_id '{player_id}' already claimed.", pdu)
            return
        if (not isinstance(deck, list) or not (1 <= len(deck) <= 50)
                or any(card_def(self.catalog, c) is None for c in deck)):
            n = len(deck) if isinstance(deck, list) else "?"
            self.send_error(idx, "ILLEGAL_DECK",
                            f"Deck invalid: {n} cards or unknown card IDs "
                            f"(1-50 cards from the fixed set required).", pdu)
            return
        # Re-submission before both ready replaces the earlier deck (RFC 6.2).
        self.players[idx]["id"] = player_id
        self.players[idx]["deck"] = list(deck)
        self.clients[idx].player_id = player_id
        ready = sum(1 for p in self.players if p["deck"])
        waiting = [] if ready == 2 else \
            [self.players[1 - idx]["id"] or f"seat_{1 - idx}"]
        self.send_to(idx, {"type": "GAME_STATE_UPDATE",
                           "state": {"phase": "LOBBY", "players_ready": ready,
                                     "waiting_for": waiting}})

    def handle_lobby_disconnect(self, idx):
        print(f"[server] seat {idx} disconnected in LOBBY; awaiting new client")
        try:
            self.clients[idx].sock.close()
        except OSError:
            pass
        sock, addr = self.listener.accept()
        self.clients[idx] = ClientConn(sock, addr, idx, self.events)
        self.players[idx]["id"], self.players[idx]["deck"] = None, None
        print(f"[server] seat {idx} reconnected from {addr}")

    def run_setup(self):
        self.phase = "GAME_SETUP"
        self.send_all({"type": "GAME_STATE_UPDATE",
                       "state": {"phase": "GAME_SETUP", "players_ready": 2,
                                 "waiting_for": []}})
        for p in self.players:
            p["life"] = 20
            p["library"] = list(p["deck"])
            random.shuffle(p["library"])
            p["hand"] = [p["library"].pop(0) for _ in range(min(7, len(p["library"])))]
        # Coin flip for first player (RFC 6.3 step 5); --first overrides it
        # for reproducible tests/demos.
        self.active = (self.force_first if self.force_first is not None
                       else random.randint(0, 1))
        print(f"[server] coin flip: {self.pid(self.active)} goes first")
        self.phase = "MULLIGAN"
        return self.broadcast_state()        # seq map -> mulligan request seqs

    def run_mulligan(self, request_seqs):
        pending = {0, 1}
        while pending:
            try:
                idx, pdu = self.next_event()
            except ClientGone as e:
                raise GameOver(1 - e.idx, e.idx, "DISCONNECT")
            if pdu.get("type") == "CONCEDE":
                raise GameOver(1 - idx, idx, "CONCEDE")
            if idx not in pending or pdu.get("type") != "MULLIGAN_CHOICE":
                self.send_error(idx, "ILLEGAL_ACTION",
                                "Expecting MULLIGAN_CHOICE.", pdu)
                continue
            if pdu.get("seq_num") != request_seqs[idx]:
                self.send_error(idx, "STALE_ACTION",
                                f"Expected seq_num {request_seqs[idx]}, got "
                                f"{pdu.get('seq_num')}.", pdu,
                                echo_seq=pdu.get("seq_num"))
                continue
            pl = self.players[idx]
            if pdu.get("keep"):
                bottoms = pdu.get("cards_to_bottom", [])
                if (len(bottoms) != pl["mulligans"]
                        or any(c not in pl["hand"] for c in bottoms)):
                    self.send_error(idx, "ILLEGAL_ACTION",
                                    f"cards_to_bottom must contain exactly "
                                    f"{pl['mulligans']} cards from your hand.",
                                    pdu)
                    continue
                for c in bottoms:                 # bottom N cards (London rule)
                    pl["hand"].remove(c)
                    pl["library"].append(c)
                pending.discard(idx)
            else:
                # Redraw a fresh 7 and send a new state (= new request PDU).
                pl["mulligans"] += 1
                pl["library"].extend(pl["hand"])
                pl["hand"] = []
                random.shuffle(pl["library"])
                pl["hand"] = [pl["library"].pop(0)
                              for _ in range(min(7, len(pl["library"])))]
                request_seqs[idx] = self.send_to(
                    idx, {"type": "GAME_STATE_UPDATE",
                          "state": self.visible_state(idx)})
