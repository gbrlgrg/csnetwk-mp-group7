"""
server/effects.py — card effect implementations and implicit mana payment
validation (INSUFFICIENT_MANA): damage, life gain, pump, counter, draw,
drain and opponent-loses-life triggers (RFC 7.5, 8.4). (Gregorio)
"""

from server.card_catalog import card_def

class EffectsMixin:
    def check_and_pay_mana(self, idx, cost, payment, pdu):
        """Validate the declared payment against the cost and tap lands.
        Colored cost keys must be paid in-color; 'X' (generic) may be paid
        with any color. Returns True and taps sources on success."""
        cost = dict(cost or {})
        payment = dict(payment or {})
        pool = {}   # color -> list of untapped land perms
        for perm in self.players[idx]["battlefield"]:
            d = card_def(self.catalog, perm["id"])
            if d["kind"] == "land" and not perm["tapped"]:
                pool.setdefault(d["produces"], []).append(perm)
        # 1) payment must total the cost and cover each colored requirement
        need_total = sum(cost.values())
        pay_total = sum(payment.values())
        colored_ok = all(payment.get(c, 0) >= n
                         for c, n in cost.items() if c != "X")
        if pay_total != need_total or not colored_ok:
            self.send_error(idx, "INSUFFICIENT_MANA",
                            "Declared mana_payment does not satisfy the "
                            "spell's cost.", pdu)
            return False
        # 2) payment must be producible by untapped lands
        for color, n in payment.items():
            if len(pool.get(color, [])) < n:
                self.send_error(idx, "INSUFFICIENT_MANA",
                                f"Not enough untapped sources of {color}.", pdu)
                return False
        for color, n in payment.items():
            for perm in pool[color][:n]:
                perm["tapped"] = True
        return True

    def apply_spell_effect(self, d, item, controller):
        changes = []
        eff = d.get("effect")
        tgt = item["targets"][0] if item["targets"] else None
        if eff == "damage":
            changes += self.deal_damage(item["source"], tgt, d["amount"])
        elif eff == "lifegain":
            t = self.idx_of(tgt)
            self.players[t]["life"] += d["amount"]
            changes.append({"change_type": "LIFE_GAIN", "target": tgt,
                            "amount": d["amount"]})
        elif eff == "pump":
            _, perm = self.find_perm(tgt)
            perm["pump_p"] += d["power"]
            perm["pump_t"] += d["toughness"]
            changes.append({"change_type": "PUMP", "target": tgt,
                            "power": d["power"], "toughness": d["toughness"]})
        elif eff == "counter":
            for i, s in enumerate(self.stack):
                if s["stack_item_id"] == tgt:
                    countered = self.stack.pop(i)
                    self.send_all({"type": "STACK_RESOLVE",
                                   "stack_item_id": countered["stack_item_id"],
                                   "result": "FIZZLE", "state_changes": []})
                    own = self.idx_of(countered["controller"])
                    if countered["item_type"] == "SPELL":
                        self.players[own]["graveyard"].append(
                            countered["source"])
                    changes.append({"change_type": "COUNTERED",
                                    "target": tgt})
                    break
        elif eff == "draw":
            self.draw_cards(controller, d["amount"])
            changes.append({"change_type": "DRAW",
                            "target": self.pid(controller),
                            "amount": d["amount"]})
        return changes

    def apply_trigger_effect(self, item, controller):
        trig = item["trigger_effect"]
        changes = []
        if trig["effect"] == "drain":
            n = trig["amount"]
            self.players[1 - controller]["life"] -= n
            self.players[controller]["life"] += n
            changes.append({"change_type": "DAMAGE",
                            "target": self.pid(1 - controller), "amount": n})
            changes.append({"change_type": "LIFE_GAIN",
                            "target": self.pid(controller), "amount": n})
        elif trig["effect"] == "opp_lose":
            n = trig["amount"]
            self.players[1 - controller]["life"] -= n
            changes.append({"change_type": "DAMAGE",
                            "target": self.pid(1 - controller), "amount": n})
        return changes

    def deal_damage(self, source, target, amount):
        t_idx = self.idx_of(target)
        if t_idx is not None:
            self.players[t_idx]["life"] -= amount
        else:
            _, perm = self.find_perm(target)
            if perm:
                perm["damage"] += amount
        return [{"change_type": "DAMAGE", "target": target, "amount": amount}]
