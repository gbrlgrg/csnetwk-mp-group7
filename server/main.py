"""
server/main.py — MTGNP v1.0 Game Server entry point (RFC 0001). (Suñga)

Run from the project root:
    python3 -m server.main [--port 4444] [--verbose] [--time-limit 60000]
(or `python3 server/main.py ...`, which bootstraps the import path itself)

The Server class composes one mixin per concern — see the module docstrings:
transport (framing/sends), game_state (data model), state_view (personalized
views), lobby (LOBBY/SETUP/MULLIGAN), priority (grants + seq_num tokens),
phase_engine (turn machine), stack (LIFO stack + triggers), effects (card
effects + mana), combat (combat sub-machine). Method implementations are
identical to the reference single-file build; only the file layout differs.
"""

import argparse
import os
import queue
import random
import socket
import sys
import threading

if __package__ in (None, ""):                      # `python3 server/main.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.card_catalog import load_catalog
from server.combat import CombatMixin
from server.effects import EffectsMixin
from server.game_state import ClientGone, GameOver, GameStateMixin
from server.lobby import LobbyMixin
from server.phase_engine import PhaseEngineMixin
from server.priority import PriorityMixin
from server.stack import StackMixin
from server.state_view import StateViewMixin
from server.transport import (DEFAULT_PORT, ClientConn, TransportMixin,
                              set_verbose)


class Server(TransportMixin, GameStateMixin, StateViewMixin, LobbyMixin,
             PriorityMixin, PhaseEngineMixin, StackMixin, EffectsMixin,
             CombatMixin):
    def __init__(self, port, time_limit_ms, seed=None, force_first=None):
        self.port = port
        self.time_limit_ms = time_limit_ms
        self.force_first = force_first          # test hook: 0, 1, or None
        if seed is not None:
            random.seed(seed)                   # test hook: reproducible runs
        self.catalog = load_catalog()
        self.events = queue.Queue()
        self.clients = [None, None]    # seat 0, seat 1
        self.seq = 0                   # server PDU counter (RFC 5.4)
        self.stk_counter = 0
        self.trg_counter = 0
        # Only one thread may ever call self.listener.accept() (see
        # _accept_loop below); reconnect waiters block on these instead of
        # racing each other for the next incoming connection.
        self._seats_lock = threading.Lock()
        self._seat_waiters = {}        # seat idx -> threading.Event

    # ------------------------------------------------------------------
    # Connection acceptance (RFC 5.1): exactly two seats, refuse extras.
    # A single background thread owns accept() for the process lifetime,
    # so a reconnecting client can never race the "refuse extras" logic.
    # ------------------------------------------------------------------
    def accept_players(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(5)
        print(f"[server] listening on port {self.port}")
        self.listener = srv
        threading.Thread(target=self._accept_loop, daemon=True).start()
        self.await_seat(0)
        self.await_seat(1)

    def _accept_loop(self):
        while True:
            try:
                sock, addr = self.listener.accept()
            except OSError:
                return
            with self._seats_lock:
                seat = self.clients.index(None) if None in self.clients else None
                if seat is None:
                    print(f"[server] refusing extra connection from {addr}")
                    sock.close()
                    continue
                self.clients[seat] = ClientConn(sock, addr, seat, self.events)
                print(f"[server] seat {seat} connected from {addr}")
                waiter = self._seat_waiters.pop(seat, None)
            if waiter:
                waiter.set()

    def await_seat(self, seat):
        """Block until `seat` is filled (by _accept_loop) after being
        cleared to None. Caller must clear self.clients[seat] first."""
        with self._seats_lock:
            if self.clients[seat] is not None:
                return
            event = self._seat_waiters.setdefault(seat, threading.Event())
        event.wait()

    # ------------------------------------------------------------------
    # Session lifecycle (RFC Section 6): LOBBY -> ... -> GAME_OVER -> LOBBY
    # ------------------------------------------------------------------
    def run_forever(self):
        self.accept_players()
        while True:
            self.reset_game_state()
            try:
                self.run_lobby()
                seqs = self.run_setup()
                self.run_mulligan(seqs)
                self.phase = "IN_GAME"
                self.run_game()
            except GameOver as g:
                self.announce_game_over(g)
            except ClientGone as e:
                g = GameOver(1 - e.idx, e.idx, "DISCONNECT")
                self.announce_game_over(g)
                self.replace_dead_seats()
            # Loop: back to LOBBY on the same TCP connections (RFC 6.6).

    def announce_game_over(self, g):
        win = self.pid(g.winner_idx) or f"seat_{g.winner_idx}"
        lose = self.pid(g.loser_idx) or f"seat_{g.loser_idx}"
        print(f"[server] GAME_OVER: {win} beats {lose} ({g.reason})")
        # Drain stale events BEFORE broadcasting: anything already queued
        # belongs to the game that just ended, while anything a client sends
        # after seeing GAME_OVER (e.g. a fresh PLAYER_READY) must survive
        # for the next LOBBY.
        while not self.events.empty():
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self.send_all({"type": "GAME_OVER", "winner_id": win,
                       "loser_id": lose, "reason": g.reason})

    def replace_dead_seats(self):
        for i in (0, 1):
            if not self.clients[i].alive:
                try:
                    self.clients[i].sock.close()
                except OSError:
                    pass
                print(f"[server] waiting for a new client on seat {i} ...")
                with self._seats_lock:
                    self.clients[i] = None
                self.await_seat(i)
                print(f"[server] seat {i} reconnected from {self.clients[i].addr}")


def main():
    ap = argparse.ArgumentParser(description="MTGNP v1.0 Game Server")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print every PDU sent and received")
    ap.add_argument("--time-limit", type=int, default=60000,
                    help="priority response deadline in ms (RFC 4.2)")
    ap.add_argument("--seed", type=int, default=None,
                    help="debug: seed the RNG for reproducible shuffles")
    ap.add_argument("--first", type=int, default=None, choices=(0, 1),
                    help="debug: force which seat goes first")
    args = ap.parse_args()
    set_verbose(args.verbose)
    Server(args.port, args.time_limit, args.seed, args.first).run_forever()


if __name__ == "__main__":
    main()
