"""
server/combat.py — the combat sub-state-machine: declare attackers /
blockers, multi-block damage ordering, the optional first-strike step,
simultaneous combat damage, COMBAT_DAMAGE_RESULT (RFC Section 9). (Barreo)
"""

from server.card_catalog import card_def

class CombatMixin:
    def run_combat(self):
        """Runs BEGIN_COMBAT through END_OF_COMBAT. Returns the name of the
        phase we ended in ('END_OF_COMBAT') for the next PHASE_TRANSITION."""
        ap_i, nap_i = self.active, 1 - self.active
        self.phase_transition(self.phase, "BEGIN_COMBAT")

        # ---- Declare Attackers (9.3): token = PHASE_TRANSITION seq ----
        token = self.phase_transition("BEGIN_COMBAT", "DECLARE_ATTACKERS")
        pdu = self.expect(ap_i, {"DECLARE_ATTACKERS"}, token,
                          timeout_ms=self.time_limit_ms)
        attackers = self.validate_attackers(ap_i, pdu)
        while attackers is None:
            pdu = self.expect(ap_i, {"DECLARE_ATTACKERS"}, token,
                              timeout_ms=self.time_limit_ms)
            attackers = self.validate_attackers(ap_i, pdu)
        if not attackers:
            # RFC 9.3: no attackers -> skip straight to End of Combat.
            self.phase_transition("DECLARE_ATTACKERS", "END_OF_COMBAT")
            self.clear_combat()
            return "END_OF_COMBAT"
        for a in attackers:              # attacking taps the creature (9.3)
            _, perm = self.find_perm(a)
            perm["tapped"] = True
        self.broadcast_state()
        self.priority_window()

        # ---- Declare Blockers (9.4): NAP echoes PHASE_TRANSITION seq ----
        token = self.phase_transition("DECLARE_ATTACKERS", "DECLARE_BLOCKERS")
        pdu = self.expect(nap_i, {"DECLARE_BLOCKERS"}, token,
                          timeout_ms=self.time_limit_ms)
        blocks = self.validate_blockers(nap_i, pdu, attackers)
        while blocks is None:
            pdu = self.expect(nap_i, {"DECLARE_BLOCKERS"}, token,
                              timeout_ms=self.time_limit_ms)
            blocks = self.validate_blockers(nap_i, pdu, attackers)
        self.broadcast_state()
        self.priority_window()

        # ---- Assign Damage Order (9.5): only for multi-blocked attackers ---
        order = {}   # attacker_id -> [blocker ids in damage order]
        multi = {a: bs for a, bs in blocks.items() if len(bs) > 1}
        if multi:
            token = self.phase_transition("DECLARE_BLOCKERS",
                                          "ASSIGN_DAMAGE_ORDER")
            needed = set(multi)
            while needed:
                pdu = self.expect(ap_i, {"ASSIGN_DAMAGE_ORDER"}, token,
                                  timeout_ms=self.time_limit_ms)
                atk = pdu.get("attacker_id")
                bo = pdu.get("blocker_order", [])
                if atk not in needed or sorted(bo) != sorted(multi[atk]):
                    self.send_error(ap_i, "ILLEGAL_ACTION",
                                    "blocker_order must list exactly that "
                                    "attacker's blockers.", pdu)
                    continue
                order[atk] = bo
                needed.discard(atk)
            self.priority_window()
            prev = "ASSIGN_DAMAGE_ORDER"
        else:
            prev = "DECLARE_BLOCKERS"
        for a, bs in blocks.items():
            order.setdefault(a, bs)

        # ---- First Strike Damage (9.6): only if FS/DS creatures present ----
        def has_fs(cid):
            d = card_def(self.catalog, cid)
            return d.get("first_strike") or d.get("double_strike")
        fs_present = any(has_fs(a) for a in attackers) or \
            any(has_fs(b) for bs in blocks.values() for b in bs)
        if fs_present:
            self.phase_transition(prev, "FIRST_STRIKE_DAMAGE")
            self.resolve_combat_damage(attackers, blocks, order,
                                       first_strike_step=True)
            self.priority_window()
            prev = "FIRST_STRIKE_DAMAGE"

        # ---- Combat Damage (9.7) ----
        self.phase_transition(prev, "COMBAT_DAMAGE")
        self.resolve_combat_damage(attackers, blocks, order,
                                   first_strike_step=False)
        # ---- End of Combat (9.8) ----
        self.phase_transition("COMBAT_DAMAGE", "END_OF_COMBAT")
        self.clear_combat()
        return "END_OF_COMBAT"

    def validate_attackers(self, ap_i, pdu):
        out = []
        for a in pdu.get("attackers", []):
            cid = a.get("creature_id")
            owner, perm = self.find_perm(cid)
            if (owner != ap_i or perm is None or not perm["creature"]
                    or perm["tapped"] or perm["summoning_sick"]
                    or a.get("target") != self.pid(1 - ap_i)):
                self.send_error(ap_i, "ILLEGAL_ACTION",
                                f"'{cid}' cannot attack (tapped, summoning-"
                                f"sick, missing, or bad target).", pdu)
                return None
            out.append(cid)
        return out

    def validate_blockers(self, nap_i, pdu, attackers):
        blocks = {a: [] for a in attackers}
        seen = set()
        for b in pdu.get("blockers", []):
            cid, tgt = b.get("creature_id"), b.get("blocking_id")
            owner, perm = self.find_perm(cid)
            # A creature may block only one attacker; tapped creatures cannot
            # block; blocking does not tap (RFC 9.4).
            if (owner != nap_i or perm is None or not perm["creature"]
                    or perm["tapped"] or cid in seen or tgt not in blocks):
                self.send_error(nap_i, "ILLEGAL_ACTION",
                                f"'{cid}' is not a legal block.", pdu)
                return None
            seen.add(cid)
            blocks[tgt].append(cid)
        return blocks

    def resolve_combat_damage(self, attackers, blocks, order, first_strike_step):
        """Assign and apply combat damage simultaneously (RFC 9.6 / 9.7)."""
        cat = self.catalog
        ap_i, nap_i = self.active, 1 - self.active

        def deals_now(cid):
            d = card_def(cat, cid)
            fs, ds = d.get("first_strike"), d.get("double_strike")
            if first_strike_step:
                return fs or ds
            return ds or not fs      # FS-only creatures already dealt damage

        events = []
        # Attacker damage
        for atk in attackers:
            _, aperm = self.find_perm(atk)
            if aperm is None or not deals_now(atk):
                continue
            dmg = self.power(aperm)
            bs = [b for b in order.get(atk, blocks.get(atk, []))
                  if self.find_perm(b)[1] is not None]
            if not bs:
                if not blocks.get(atk):          # unblocked -> player
                    events.append((atk, self.pid(nap_i), dmg))
                continue                          # blocked, blockers all dead
            # Damage order: lethal to each blocker in order, overflow to next
            # blocker only (no trample in MTGNP 1.0 — never to the player).
            for b in bs:
                if dmg <= 0:
                    break
                _, bperm = self.find_perm(b)
                lethal = max(0, self.toughness(bperm) - bperm["damage"])
                assign = min(dmg, lethal) if b != bs[-1] else dmg
                events.append((atk, b, assign))
                dmg -= assign
        # Blocker damage (simultaneous)
        for atk, bs in blocks.items():
            _, aperm = self.find_perm(atk)
            for b in bs:
                _, bperm = self.find_perm(b)
                if bperm is None or aperm is None or not deals_now(b):
                    continue
                events.append((b, atk, self.power(bperm)))

        for src, tgt, amt in events:
            if amt > 0:
                self.deal_damage(src, tgt, amt)
        died = []
        for i in (0, 1):
            for perm in self.players[i]["battlefield"]:
                if perm["creature"] and perm["damage"] >= self.toughness(perm):
                    died.append(perm["id"])
        self.send_all({"type": "COMBAT_DAMAGE_RESULT",
                       "damage_events": [{"source": s, "target": t,
                                          "amount": a} for s, t, a in events],
                       "life_totals": {self.pid(0): self.players[0]["life"],
                                       self.pid(1): self.players[1]["life"]},
                       "creatures_died": died})
        self.after_event()                       # SBAs move the dead, win check

    def clear_combat(self):
        # Combat damage markers persist until Cleanup (RFC 7.8); attacker /
        # blocker assignments are transient locals, so nothing else to do.
        pass
