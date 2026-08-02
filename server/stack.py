"""
server/stack.py — the LIFO stack: casting, STACK_PUSH / STACK_RESOLVE,
fizzle-on-illegal-target, state-based actions, and triggered-ability
placement incl. TRIGGER_ORDER and TRIGGER_CHOICE (RFC Section 8). (Gregorio)
"""

from server.card_catalog import card_def
from server.game_state import GameOver

class StackMixin:
    def try_cast(self, idx, pdu, main_phase):
        card = pdu.get("card_id")
        d = card_def(self.catalog, card) if card else None
        pl = self.players[idx]
        if card not in pl["hand"] or d is None or d["kind"] == "land":
            self.send_error(idx, "ILLEGAL_ACTION",
                            "Card is not a castable card in your hand.", pdu)
            return False
        sorcery_speed = d["kind"] in ("creature", "sorcery", "enchantment",
                                      "artifact")
        if sorcery_speed and not (main_phase and idx == self.active
                                  and not self.stack):
            self.send_error(idx, "WRONG_PHASE",
                            f"{d['name']} may only be cast in your own Main "
                            f"Phase with an empty stack.", pdu)
            return False
        targets = pdu.get("targets", [])
        if d.get("needs_target"):
            if len(targets) != 1 or not self.target_legal(d, targets[0]):
                self.send_error(idx, "ILLEGAL_TARGET",
                                "Missing or illegal target.", pdu)
                return False
        if not self.check_and_pay_mana(idx, d.get("cost"),
                                       pdu.get("mana_payment"), pdu):
            return False
        pl["hand"].remove(card)
        self.stk_counter += 1
        item = {"stack_item_id": f"stk_{self.stk_counter:02d}",
                "item_type": "SPELL", "source": card,
                "targets": targets, "controller": self.pid(idx)}
        self.stack.append(item)
        self.send_all({"type": "STACK_PUSH", **item})
        return True

    def target_legal(self, spell_def, target):
        if spell_def.get("targets_stack"):
            return any(s["stack_item_id"] == target for s in self.stack)
        if spell_def.get("targets_creature"):
            _, perm = self.find_perm(target)
            return perm is not None and perm["creature"]
        # 'any target' style (bolt/shock) or player-only (healing salve):
        if self.idx_of(target) is not None:
            return True
        if spell_def.get("effect") == "lifegain":
            return False
        _, perm = self.find_perm(target)
        return perm is not None and perm["creature"]

    def resolve_top(self):
        item = self.stack.pop()
        d = card_def(self.catalog, item["source"])
        changes = []
        # Re-check target legality; fizzle if all targets are now illegal.
        if d.get("needs_target"):
            if not all(self.target_legal(d, t) for t in item["targets"]):
                self.send_all({"type": "STACK_RESOLVE",
                               "stack_item_id": item["stack_item_id"],
                               "result": "FIZZLE", "state_changes": []})
                d_owner = self.idx_of(item["controller"])
                if item["item_type"] == "SPELL":
                    self.players[d_owner]["graveyard"].append(item["source"])
                self.after_event()
                return
        controller = self.idx_of(item["controller"])
        etb_perm = None
        if item["item_type"] == "TRIGGER_ABILITY":
            changes += self.apply_trigger_effect(item, controller)
        elif d["kind"] == "creature":
            perm = self.new_perm(item["source"], creature=True)
            self.players[controller]["battlefield"].append(perm)
            changes.append({"change_type": "PERMANENT_ENTERS",
                            "card_id": item["source"],
                            "controller": item["controller"]})
            etb_perm = perm
        else:
            changes += self.apply_spell_effect(d, item, controller)
            self.players[controller]["graveyard"].append(item["source"])
        self.send_all({"type": "STACK_RESOLVE",
                       "stack_item_id": item["stack_item_id"],
                       "result": "RESOLVED", "state_changes": changes})
        self.after_event(etb_source=etb_perm,
                         etb_controller=controller if etb_perm else None)

    def check_sbas(self):
        """Apply state-based actions repeatedly until none remain. Returns a
        list of (owner_idx, card_id) for creatures that died, so callers can
        collect death triggers (RFC 8.6.1)."""
        deaths = []
        while True:
            acted = False
            # Simultaneous death: AP loses ties (RFC 8.4).
            l0, l1 = self.players[0]["life"], self.players[1]["life"]
            if l0 <= 0 and l1 <= 0:
                raise GameOver(1 - self.active, self.active, "LIFE_ZERO")
            if l0 <= 0:
                raise GameOver(1, 0, "LIFE_ZERO")
            if l1 <= 0:
                raise GameOver(0, 1, "LIFE_ZERO")
            for i in (0, 1):
                for perm in list(self.players[i]["battlefield"]):
                    if perm["creature"] and (
                            self.toughness(perm) <= 0
                            or perm["damage"] >= self.toughness(perm)):
                        self.players[i]["battlefield"].remove(perm)
                        self.players[i]["graveyard"].append(perm["id"])
                        deaths.append((i, perm["id"]))
                        acted = True
            if not acted:
                return deaths

    def after_event(self, etb_source=None, etb_controller=None):
        """SBA check, then trigger detection/placement, then fresh state to
        both players (RFC 8.4 step 3 + 8.6.1). Called after every event.

        Triggers are collected from the event (ETB) and from the SBA sweep
        (death triggers), then placed on the stack in APNAP order. If one
        player has several simultaneous triggers, the TRIGGER_ORDER /
        TRIGGER_ORDER_RESPONSE flow of RFC 8.6.2 chooses their order."""
        deaths = self.check_sbas()
        pending = []               # (controller_idx, source_card_id, trig)
        if etb_source is not None:
            d = card_def(self.catalog, etb_source["id"])
            if d.get("etb_trigger"):
                pending.append((etb_controller, etb_source["id"],
                                d["etb_trigger"]))
        for owner, cid in deaths:
            d = card_def(self.catalog, cid)
            if d.get("death_trigger"):
                pending.append((owner, cid, d["death_trigger"]))
        self.dispatch_triggers(pending)
        self.broadcast_state()

    def dispatch_triggers(self, pending):
        """Place simultaneous triggers in APNAP order: the active player's
        triggers go on the stack first (RFC 8.6.2)."""
        for idx in (self.active, 1 - self.active):
            mine = [(src, trig) for (o, src, trig) in pending if o == idx]
            if not mine:
                continue
            if len(mine) == 1:
                self.place_trigger(idx, *mine[0])
                continue
            # Multiple simultaneous triggers for one player: the player
            # chooses their stack order via TRIGGER_ORDER (RFC 8.6.2 /
            # 10.2.10-11).
            by_id = {}
            for src, trig in mine:
                self.trg_counter += 1
                by_id[f"trg_{self.trg_counter:02d}"] = (src, trig)
            req = self.send_to(idx, {"type": "TRIGGER_ORDER",
                                     "player_id": self.pid(idx),
                                     "trigger_ids": list(by_id)})
            while True:
                pdu = self.expect(idx, {"TRIGGER_ORDER_RESPONSE"}, req,
                                  timeout_ms=self.time_limit_ms)
                order = pdu.get("ordered_trigger_ids", [])
                if sorted(order) != sorted(by_id):
                    self.send_error(idx, "TRIGGER_ORDER_INVALID",
                                    "ordered_trigger_ids must be a "
                                    "permutation of the offered trigger_ids.",
                                    pdu)
                    continue
                break
            # Triggers are pushed in the listed order, so the LAST listed
            # trigger ends up on top of the stack and resolves first.
            for tid in order:
                src, trig = by_id[tid]
                self.place_trigger(idx, src, trig, trig_id=tid)

    def place_trigger(self, controller, source_id, trig, trig_id=None):
        """Put one trigger on the stack. Optional ('you may') triggers first
        go through the TRIGGER_CHOICE flow (RFC 8.6.3); declining discards
        the trigger with no effect."""
        if trig_id is None:
            self.trg_counter += 1
            trig_id = f"trg_{self.trg_counter:02d}"
        if trig.get("optional"):
            req = self.send_to(controller, {
                "type": "TRIGGER_CHOICE", "trigger_id": trig_id,
                "source_id": source_id, "effect_summary": trig["summary"],
                "requires_target": False, "legal_targets": []})
            pdu = self.expect(controller, {"TRIGGER_CHOICE_RESPONSE"}, req,
                              timeout_ms=self.time_limit_ms)
            if pdu.get("trigger_id") != trig_id:
                self.send_error(controller, "TRIGGER_CHOICE_INVALID",
                                "Unknown trigger_id.", pdu)
                return
            if not pdu.get("accept"):
                return                   # declined: silently discarded
        self.stk_counter += 1
        item = {"stack_item_id": f"stk_{self.stk_counter:02d}",
                "item_type": "TRIGGER_ABILITY", "source": source_id,
                "targets": [], "controller": self.pid(controller),
                "trigger_effect": trig}
        self.stack.append(item)
        pub = {k: v for k, v in item.items() if k != "trigger_effect"}
        self.send_all({"type": "STACK_PUSH", **pub})
