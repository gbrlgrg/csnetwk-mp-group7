"""
server/priority.py — PRIORITY_GRANT / PRIORITY_PASS, seq_num (priority
token) validation with STALE_ACTION and same-seq re-grants, and the
consecutive-pass priority window (RFC 5.4, 8.1-8.2, 11). (Gregorio)
"""

import queue
import time

from server.game_state import ClientGone, GameOver
from server.transport import KNOWN_CLIENT_TYPES

PRIORITY_TYPES = {"CAST_SPELL", "ACTIVATE_ABILITY", "PRIORITY_PASS", "PLAY_LAND"}


class PriorityMixin:
    def next_event(self, timeout=None):
        """Pop one event; raise ClientGone on disconnect notification."""
        try:
            kind, idx, pdu = self.events.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError
        if kind == "gone":
            raise ClientGone(idx)
        return idx, pdu

    def expect(self, want_idx, allowed_types, expected_seq, regrant=None,
               timeout_ms=None):
        """Wait for a PDU of one of `allowed_types` from seat `want_idx`
        whose seq_num equals `expected_seq` (the current priority token).

        Everything else is rejected with the appropriate ERROR code:
          * wrong player            -> NOT_YOUR_PRIORITY
          * unknown 'type'          -> UNKNOWN_TYPE
          * wrong type / wrong time -> ILLEGAL_ACTION
          * stale seq_num           -> STALE_ACTION (+ optional re-grant)
        CONCEDE is legal from anyone at any time (RFC 5.4).
        On timeout the offending player is treated as disconnected (RFC 4.2).
        """
        deadline = None
        if timeout_ms:
            deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            tmo = None
            if deadline is not None:
                tmo = max(0.0, deadline - time.monotonic())
            try:
                idx, pdu = self.next_event(timeout=tmo)
            except TimeoutError:
                # RFC 4.2: enforce time_limit_ms -> GAME_OVER DISCONNECT.
                raise ClientGone(want_idx)

            ptype = pdu.get("type")
            if ptype == "CONCEDE":
                raise GameOver(1 - idx, idx, "CONCEDE")
            if ptype not in KNOWN_CLIENT_TYPES:
                self.send_error(idx, "UNKNOWN_TYPE",
                                f"Unknown PDU type '{ptype}'.", pdu)
                continue
            if idx != want_idx:
                self.send_error(idx, "NOT_YOUR_PRIORITY",
                                "You do not hold priority.", pdu)
                continue
            if ptype not in allowed_types:
                self.send_error(idx, "ILLEGAL_ACTION",
                                f"{ptype} is not legal right now.", pdu)
                continue
            if expected_seq is not None and pdu.get("seq_num") != expected_seq:
                self.send_error(idx, "STALE_ACTION",
                                f"Priority token mismatch. Expected seq_num "
                                f"{expected_seq}, got {pdu.get('seq_num')}.",
                                pdu, echo_seq=pdu.get("seq_num"))
                if regrant:
                    # Re-issue the current PRIORITY_GRANT (RFC 5.4 example)
                    # and validate future PDUs against the fresh token.
                    expected_seq = regrant()
                continue
            return pdu

    def grant_priority(self, idx):
        return self.send_to(idx, {"type": "PRIORITY_GRANT",
                                  "player_id": self.pid(idx),
                                  "time_limit_ms": self.time_limit_ms})

    def resend_grant(self, idx, token):
        """RFC Section 11 item 3: after rejecting an illegal action, if the
        player still holds priority, re-issue PRIORITY_GRANT with the SAME
        seq_num so the player may try again (no counter increment)."""
        self.clients[idx].send({"type": "PRIORITY_GRANT",
                                "player_id": self.pid(idx),
                                "seq_num": token,
                                "time_limit_ms": self.time_limit_ms})

    def priority_window(self, main_phase=False):
        """One full priority window (RFC 8.1 / 8.2). Returns when both players
        pass consecutively with an empty stack (step ends)."""
        holder = self.active
        passes = 0
        while True:
            token = self.grant_priority(holder)
            acted = self.await_action(holder, token, main_phase)
            if acted == "PASS":
                passes += 1
                if passes == 2:
                    if self.stack:
                        self.resolve_top()
                        holder, passes = self.active, 0
                    else:
                        return                     # step advances
                else:
                    holder = 1 - holder
            else:
                passes = 0
                # After casting/land the same player retains priority except
                # when the action was a pass; RFC 8.1 rule 3.
                holder = acted if isinstance(acted, int) else holder

    def await_action(self, idx, token, main_phase):
        """Wait for one legal priority action. Returns 'PASS' or the seat
        index that retains priority after a successful non-pass action."""
        def regrant():
            nonlocal token
            token = self.grant_priority(idx)
            return token
        while True:
            pdu = self.expect(idx, PRIORITY_TYPES, token, regrant,
                              timeout_ms=self.time_limit_ms)
            t = pdu["type"]
            if t == "PRIORITY_PASS":
                return "PASS"
            if t == "PLAY_LAND":
                if self.try_play_land(idx, pdu, main_phase):
                    self.broadcast_state()
                    return idx        # AP retains priority (RFC 7.5)
                self.resend_grant(idx, token)
                continue
            if t == "CAST_SPELL":
                if self.try_cast(idx, pdu, main_phase):
                    return idx        # caster retains priority (RFC 8.1)
                self.resend_grant(idx, token)
                continue
            if t == "ACTIVATE_ABILITY":
                # MTGNP 1.0 catalog defines no activated non-mana abilities;
                # mana is paid implicitly inside CAST_SPELL (RFC 7.5).
                self.send_error(idx, "ILLEGAL_ACTION",
                                "No activated abilities in the card set.", pdu)
                self.resend_grant(idx, token)
                continue
