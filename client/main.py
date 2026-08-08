"""
client/main.py — MTGNP v1.0 Player Client (RFC 0001, CSNETWK). (Barreo)

Usage:
    python3 -m client.main --id player_1 --deck decks/burn.txt [--host H] [--port 4444] [--verbose]

The client is intentionally thin (RFC 4.3): it renders the Visible State the
server sends and translates keyboard commands into PDUs. It never computes
game outcomes locally; every GAME_STATE_UPDATE overwrites its local view.

Three threads:
  * reader    — receives PDUs, updates the local view, tracks the current
                priority token / request tokens, prints prompts
  * heartbeat — PING every 30 s; disconnects if no PONG within 10 s (RFC 4.3)
  * main      — reads commands from stdin and sends PDUs

Commands (type 'help' in-game):
  ready                 send PLAYER_READY with your deck
  keep [c1 c2 ...]      keep hand (list cards to bottom after mulligans)
  mull                  take a mulligan
  play <card>           play a land
  cast <card> [target]  cast a spell (mana payment auto-computed)
  pass                  pass priority
  attack [c1 c2 ...]    declare attackers (none = no attack)
  block c:a [c:a ...]   declare blockers, e.g. wall_of_stone_004:goblin_guide_001
  order <atk> b1 b2 ..  assign damage order for a multi-blocked attacker
  discard c1 [c2 ...]   discard down to 7 at cleanup
  yes / no              accept or decline an optional trigger
  torder t1 t2 ...      order your simultaneous triggers (last resolves first)
  hand / state          re-print your hand / the full visible state
  concede               concede the game
"""

import argparse
import os
import re
import socket
import sys
import threading
import time

if __package__ in (None, ""):                      # `python3 client/main.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The transport codec and card catalog are protocol-shared with the server
# package (same framing, same out-of-band card data; RFC Sections 1 and 5).
from server.card_catalog import base_name, card_def, load_catalog
from server.transport import (DEFAULT_PORT, FramingError, recv_pdu, send_pdu,
                              set_verbose)

# ---------------------------------------------------------------------------
# ANSI colors (best-effort; degrades to plain text on non-color terminals).
# Windows 10+ consoles need VT processing enabled once at startup.
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        import ctypes
        _h = ctypes.windll.kernel32.GetStdHandle(-11)
        _m = ctypes.c_uint()
        if ctypes.windll.kernel32.GetConsoleMode(_h, ctypes.byref(_m)):
            ctypes.windll.kernel32.SetConsoleMode(
                _h, _m.value | 0x0004)      # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREY = "\033[90m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_KIND_COLORS = {"land": _GREEN, "creature": _YELLOW, "instant": _BLUE,
                "sorcery": _MAGENTA}
_WHITE = "\033[97m"
_MANA_COLORS = {"W": _WHITE, "U": _BLUE, "B": _GREY, "R": _RED, "G": _GREEN}


def _c(code, text):
    """Wrap text in an ANSI color code and reset it afterwards."""
    return f"{code}{text}{_RESET}"


def _table(headers, rows):
    """Align rows into table lines. Each row is (cells, colorize) where
    colorize is None or a callable fn(col_index, padded_text) -> str."""
    n = len(headers)
    widths = [len(h) for h in headers]
    for cells, _ in rows:
        for i in range(n):
            widths[i] = max(widths[i], len(cells[i]))
    out = ["  " + "  ".join(_c(_CYAN + _BOLD, headers[i].ljust(widths[i]))
                            for i in range(n))]
    for cells, colorize in rows:
        padded = [cells[i].ljust(widths[i]) for i in range(n)]
        if colorize:
            padded = [colorize(i, padded[i]) for i in range(n)]
        out.append("  " + "  ".join(padded))
    return out


def _fmt_cost(cost):
    """Magic-style cost, e.g. {R:2, X:2} -> '2RR', {U:1} -> 'U'."""
    if not cost:
        return "-"
    gen = cost.get("X", 0)
    rest = "".join(k * v for k, v in cost.items() if k != "X")
    return (str(gen) if gen else "") + rest


def _color_cost(cost):
    """Color each mana symbol in a cost string (R red, U blue, ...)."""
    out = ""
    for ch in cost:
        if ch in _MANA_COLORS:
            out += _c(_MANA_COLORS[ch] + _BOLD, ch)
        else:
            out += ch
    return out


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _vis_len(s):
    return len(_ANSI_RE.sub("", s))


def _suggestion_box(lines, title="what you can do"):
    """Draw a small bordered hint box. `lines` are plain-text suggestions."""
    if not lines:
        return
    content = [_c(_GREEN + _BOLD, title)] + [f"{i + 1}. {l}"
                                             for i, l in enumerate(lines)]
    w = max(_vis_len(x) for x in content) + 2
    edge = "-" * w
    print("  " + _c(_CYAN, "+") + edge + _c(_CYAN, "+"))
    for x in content:
        pad = w - _vis_len(x) - 2
        print("  " + _c(_CYAN, "|") + " " + x + " " * pad + " " + _c(_CYAN, "|"))
    print("  " + _c(_CYAN, "+") + edge + _c(_CYAN, "+"))


class Client:
    def __init__(self, host, port, player_id, deck):
        self.player_id = player_id
        self.deck = deck
        self.catalog = load_catalog()
        self.sock = socket.create_connection((host, port))
        self.send_lock = threading.Lock()
        self.lock = threading.Lock()          # guards the fields below
        self.state = {}                       # last visible state
        self.priority_token = None            # seq of last PRIORITY_GRANT
        self.request_token = None             # seq for mulligan/discard/etc.
        self.trigger = None                   # pending TRIGGER_CHOICE
        self.trigger_order = None             # pending TRIGGER_ORDER
        self.last_server_seq = 0              # for CONCEDE (RFC 5.4)
        self.ping_seq = 0
        self.ready_seq = 0
        self.pong_deadline = None
        self._rendered = False
        self.running = True

    # ---------------- sending ----------------
    def send(self, pdu):
        send_pdu(self.sock, pdu, who="server", lock=self.send_lock)

    # ---------------- reader thread ----------------
    def reader(self):
        while self.running:
            try:
                pdu = recv_pdu(self.sock, who="server")
            except (ConnectionError, OSError, FramingError) as e:
                print(f"\n[client] connection lost: {e}")
                self.running = False
                return
            self.handle(pdu)

    def handle(self, pdu):
        t = pdu.get("type")
        with self.lock:
            if t != "PONG":
                self.last_server_seq = pdu.get("seq_num", self.last_server_seq)
        if t == "GAME_STATE_UPDATE":
            with self.lock:
                self.state = pdu.get("state", {})
                # A state update doubles as the request PDU for
                # MULLIGAN_CHOICE and DISCARD (RFC 5.4).
                if self.state.get("phase") in ("MULLIGAN", "CLEANUP"):
                    self.request_token = pdu["seq_num"]
            # Redraw only when the player is being asked something (lobby,
            # mulligan, cleanup discard) or on the first update. Otherwise
            # the PRIORITY_GRANT refresh keeps the view current without
            # reprinting the whole board after every single event.
            if (self.state.get("phase") in ("LOBBY", "GAME_SETUP", "MULLIGAN",
                                            "CLEANUP")
                    or not self._rendered):
                self.render()
                self._rendered = True
                if self.state.get("phase") in ("LOBBY", "GAME_SETUP",
                                                "MULLIGAN", "CLEANUP"):
                    _suggestion_box(self._suggestions())
        elif t == "PRIORITY_GRANT":
            with self.lock:
                self.priority_token = pdu["seq_num"]
            if self._rendered:
                self.render()
            print(f"\n{_c(_GREEN + _BOLD, '>>> You have priority')} "
                  f"(seq {pdu['seq_num']}, "
                  f"{pdu.get('time_limit_ms', 0)//1000}s). "
                  f"Commands: cast / play / pass / attack ... "
                  f"(help for full list)")
            _suggestion_box(self._suggestions())
        elif t == "PHASE_TRANSITION":
            with self.lock:
                # DECLARE_ATTACKERS / DECLARE_BLOCKERS / ASSIGN_DAMAGE_ORDER
                # PDUs echo the PHASE_TRANSITION's seq_num (RFC 5.4).
                self.request_token = pdu["seq_num"]
                self.state["phase"] = pdu.get("to_phase")
            to = pdu.get("to_phase")
            if to == "UPKEEP":          # one compact line per turn
                act = pdu.get("active_player")
                who = "Your turn" if act == self.player_id else f"{act}'s turn"
                line = f"--- Turn {pdu.get('turn')}: {who} ---"
                print(f"\n{_c(_CYAN + _BOLD, line)}")
            elif to in ("PRECOMBAT_MAIN", "POSTCOMBAT_MAIN",
                        "DECLARE_ATTACKERS", "DECLARE_BLOCKERS",
                        "ASSIGN_DAMAGE_ORDER"):
                print(f"\n{_c(_CYAN, f'--- {pdu.get("from_phase")} -> {to} ---')}")
                # Combat declarations have no PRIORITY_GRANT, so refresh the
                # board here so the player can see the battlefield.
                if to in ("DECLARE_ATTACKERS", "DECLARE_BLOCKERS",
                          "ASSIGN_DAMAGE_ORDER"):
                    if self._rendered:
                        self.render()
                    _suggestion_box(self._suggestions())
        elif t == "TRIGGER_ORDER":
            with self.lock:
                self.trigger_order = pdu
            print(f"\n{_c(_YELLOW + _BOLD, '??? You have simultaneous triggers:')} "
                  f"{pdu.get('trigger_ids')}\n"
                  f"    Order them with: torder <id> <id> ... "
                  f"(last listed resolves first)")
        elif t == "TRIGGER_CHOICE":
            with self.lock:
                self.trigger = pdu
            print(f"\n{_c(_YELLOW + _BOLD, '??? Optional trigger')} from "
                  f"{self._card_name(pdu.get('source_id'))}: "
                  f"{pdu.get('effect_summary')}  -> answer 'yes' or 'no'")
        elif t == "STACK_PUSH":
            targets = pdu.get("targets") or []
            tgt = f" targeting {', '.join(targets)}" if targets else ""
            if pdu.get("item_type") == "TRIGGER_ABILITY":
                what = f"trigger ({self._card_name(pdu.get('source'))}) on the stack"
            else:
                what = f"casts {self._card_name(pdu.get('source'))}"
            print(f"\n{_c(_MAGENTA + _BOLD, '[stack]')} "
                  f"{pdu.get('controller')} {what}{tgt} "
                  f"({pdu.get('stack_item_id')})")
        elif t == "STACK_RESOLVE":
            if pdu.get("result") == "FIZZLE":
                detail = "fizzles (all targets illegal)"
            else:
                detail = self._describe_changes(pdu.get("state_changes"))
            print(f"\n{_c(_MAGENTA + _BOLD, '[stack]')} "
                  f"{pdu.get('stack_item_id')} {pdu.get('result')}: {detail}")
        elif t == "COMBAT_DAMAGE_RESULT":
            hits = [f"{e.get('source')} -> {e.get('target')} "
                    f"({e.get('amount')})"
                    for e in (pdu.get("damage_events") or [])
                    if e.get("amount")]
            lt = pdu.get("life_totals") or {}
            me = self.player_id
            life = " | ".join(_c(_GREEN if k == me else _YELLOW, f"{k} {v}")
                              for k, v in lt.items())
            died = pdu.get("creatures_died") or []
            print(f"\n{_c(_YELLOW + _BOLD, '[combat]')} "
                  f"{'; '.join(hits) if hits else _c(_GREY, 'no damage')} | "
                  f"life: {life} | "
                  f"died: {', '.join(died) if died else 'none'}")
        elif t == "GAME_OVER":
            print(f"\n{_c(_RED + _BOLD, '===== GAME OVER:')} "
                  f"{pdu.get('winner_id')} wins "
                  f"({pdu.get('reason')}) "
                  f"{_c(_RED + _BOLD, '=====')}\n"
                  f"Type 'ready' to play again on this connection.")
        elif t == "ERROR":
            print(f"\n{_c(_RED + _BOLD, f'[ERROR {pdu.get("code")}]')} "
                  f"{pdu.get('message')}")
        elif t == "PONG":
            with self.lock:
                self.pong_deadline = None

    def _describe_changes(self, changes):
        parts = []
        for c in changes or []:
            ct = c.get("change_type")
            tgt = c.get("target")
            if ct == "DAMAGE":
                parts.append(_c(_RED, f"{c.get('amount')} damage to {tgt}"))
            elif ct == "LIFE_GAIN":
                parts.append(_c(_GREEN, f"{tgt} gains {c.get('amount')} life"))
            elif ct == "PUMP":
                parts.append(_c(_BLUE, f"+{c.get('power')}/"
                                       f"+{c.get('toughness')} to {tgt}"))
            elif ct == "COUNTERED":
                parts.append(_c(_MAGENTA, f"counters {tgt}"))
            elif ct == "PERMANENT_ENTERS":
                parts.append(_c(_YELLOW, f"{c.get('card_id')} enters under "
                                         f"{c.get('controller')}'s control"))
            elif ct == "DRAW":
                parts.append(_c(_CYAN, f"{tgt} draws {c.get('amount')}"))
            else:
                parts.append(f"{ct} {c}")
        return "; ".join(parts) if parts else _c(_GREY, "no effect")

    # ---------------- rendering (RFC 4.3) ----------------
    def _card_name(self, cid):
        d = card_def(self.catalog, cid)
        return d["name"] if d else cid

    def _kind_cell_color(self, kind):
        code = _KIND_COLORS.get(kind, _RESET)
        def fn(i, text):
            if i == 1:                       # instance id
                return _c(_GREY, text)
            if i == 2:                       # card name
                return _c(code + _BOLD, text)
            if i == 3:                       # mana cost
                return _color_cost(text)
            if i == 4:                       # type column
                return _c(code, text)
            return text
        return fn

    def _available_mana(self, pid):
        """Untapped mana sources (lands) of a player, by color letter."""
        counts = {}
        for p in self.state.get("battlefield", {}).get(pid, []):
            d = card_def(self.catalog, p["id"])
            if d and d.get("kind") == "land" and not p.get("tapped"):
                c = d.get("produces")
                if c:
                    counts[c] = counts.get(c, 0) + 1
        return counts

    # ---------------- beginner suggestions ----------------
    def _card_kind(self, cid):
        d = card_def(self.catalog, cid)
        return d.get("kind") if d else None

    def _can_pay(self, cost):
        cost = cost or {}
        avail = self._available_mana(self.player_id)
        if sum(avail.values()) < sum(cost.values()):
            return False
        for c, n in cost.items():
            if c != "X" and avail.get(c, 0) < n:
                return False
        return True

    def _target_hint(self, d):
        if d.get("targets_stack"):
            return "<stack item id>"
        if d.get("targets_creature"):
            return "<creature>"
        if d.get("needs_target"):
            return "<player>" if d.get("effect") == "lifegain" \
                else "<player or creature>"
        return ""

    def _cast_hint(self, cid):
        d = card_def(self.catalog, cid)
        if not d:
            return f"cast {cid}"
        tgt = self._target_hint(d)
        tgt_s = f" {tgt}" if tgt else ""
        return f"cast {cid}{tgt_s} — costs {_fmt_cost(d.get('cost'))}"

    def _combat_suggestions(self, phase, bf, opp_bf):
        out = []
        if phase == "DECLARE_ATTACKERS":
            legal = [p["id"] for p in bf
                     if p.get("creature") and not p.get("tapped")
                     and not p.get("summoning_sick")]
            if legal:
                out.append(f"type 'attack {legal[0]}' to attack with "
                           f"{legal[0]}")
                if len(legal) > 1:
                    out.append("more: 'attack "
                               + " ".join(legal[:2]) + "'")
                out.append("type 'attack' alone to skip attacking")
            else:
                out.append("no legal attackers — type 'attack' to skip")
            return out
        if phase == "DECLARE_BLOCKERS":
            blockers = [p["id"] for p in bf
                        if p.get("creature") and not p.get("tapped")]
            atks = [p["id"] for p in opp_bf
                    if p.get("creature") and p.get("tapped")]
            if not blockers:
                out.append("no untapped creatures to block with — "
                           "type 'block'")
            elif not atks:
                out.append("opponent has no attackers — type 'block' to skip")
            else:
                out.append(f"type 'block {blockers[0]}:{atks[0]}' to block "
                           f"{atks[0]} with {blockers[0]}")
                out.append("or type 'block' alone to not block")
            return out
        out.append("order your attacker's blockers: "
                   "'order <attacker> <b1> <b2> ...'")
        out.append("the first blocker in the list takes damage first")
        return out

    def _suggestions(self):
        s = self.state
        me = self.player_id
        phase = s.get("phase")
        hand = s.get("hand", {}).get(me, [])
        act = s.get("active_player")
        stack = s.get("stack") or []
        bf_all = s.get("battlefield") or {}
        bf = bf_all.get(me, [])
        opp_id = next((k for k in bf_all if k != me), None)
        opp_bf = bf_all.get(opp_id, []) if opp_id else []
        out = []

        if phase == "LOBBY":
            out.append("type 'ready' to join the game with your deck")
            return out
        if phase == "GAME_SETUP":
            out.append("both players ready — the server is dealing your hand")
            return out
        if phase == "MULLIGAN":
            n = s.get("mulligans", 0)
            if n == 0:
                out.append("type 'keep' to keep this hand")
            else:
                out.append(f"already mulliganed {n} time(s) - keeping now "
                           f"requires bottoming {n} card(s):")
                out.append(f"type 'keep <card1> ... <card{n}>' "
                           f"with the ones you want on the bottom")
                out.append("hint: bottom your weakest/most expensive cards, "
                           "keep lands + cheap threats")
            out.append("type 'mull' to shuffle back and draw 7 new cards")
            return out
        if phase == "CLEANUP":
            if len(hand) > 7:
                out.append(f"discard {len(hand) - 7} card(s): "
                           f"'discard <card> [card2 ...]'")
                for c in hand:
                    if self._card_kind(c) == "land":
                        out.append(f"hint: keep spells, discard {c} (a land)")
                        break
            else:
                out.append("hand is 7 or fewer — nothing to discard")
            return out
        if phase in ("DECLARE_ATTACKERS", "DECLARE_BLOCKERS",
                     "ASSIGN_DAMAGE_ORDER"):
            return self._combat_suggestions(phase, bf, opp_bf)

        # Priority windows: lands + sorcery-speed in your main phase,
        # instants any time, then passing.
        my_turn = act == me
        in_main = phase in ("PRECOMBAT_MAIN", "POSTCOMBAT_MAIN")
        can_sorcery = my_turn and in_main and not stack
        if can_sorcery and not s.get("land_played_this_turn"):
            for c in hand:
                if self._card_kind(c) == "land":
                    out.append(f"play {c} — a land (one per turn)")
                    break
        for c in hand:
            kind = self._card_kind(c)
            if kind not in ("creature", "instant", "sorcery"):
                continue
            d = card_def(self.catalog, c)
            if not self._can_pay(d.get("cost")):
                continue
            if kind in ("creature", "sorcery"):
                if can_sorcery:
                    out.append(self._cast_hint(c))
            elif kind == "instant":
                out.append(self._cast_hint(c) + " (instant)")
        if not out:
            out.append("nothing affordable to play right now")
        out.append("type 'pass' to give priority to the other player")
        return out

    def _creature_cell_color(self, tapped):
        card_code = _GREY if tapped else _YELLOW + _BOLD
        def fn(i, text):
            if i == 1:
                return _c(card_code, text)
            if i == 4 and "dmg" in text:
                return _c(_RED, text)
            return text
        return fn

    def _land_cell_color(self, i, text):
        if i in (1, 2):
            return _c(_GREEN, text)
        if i == 4 and text == "T":
            return _c(_GREY, text)
        return text

    def _hand_table(self, hand):
        rows = []
        for n, cid in enumerate(hand, 1):
            d = card_def(self.catalog, cid)
            name = d["name"] if d else cid
            kind = d["kind"] if d else "?"
            cost = _fmt_cost(d.get("cost")) if d else "-"
            rows.append(([str(n), cid, name, cost, kind],
                         self._kind_cell_color(kind)))
        for ln in _table(["#", "CARD", "NAME", "COST", "TYPE"], rows):
            print(ln)

    def render(self):
        s = self.state
        if s.get("phase") == "LOBBY":
            print(f"\n{_c(_CYAN, '[lobby]')} ready: {s.get('players_ready')} "
                  f"waiting for: {s.get('waiting_for')}")
            return
        if s.get("phase") == "GAME_SETUP":
            print(f"\n{_c(_CYAN, '[setup]')} both players ready — "
                  f"server is dealing.")
            return
        me = self.player_id
        hand = s.get("hand", {}).get(me, [])
        lt = s.get("life_totals") or {}
        opp = next((k for k in lt if k != me), None)
        my_life = lt.get(me)
        opp_life = lt.get(opp) if opp is not None else None
        life_bits = []
        if my_life is not None:
            life_bits.append(_c(_GREEN + _BOLD, f"you {my_life}"))
            if opp_life is not None:
                oc = _RED if opp_life < my_life else _YELLOW
                life_bits.append(_c(oc, f"{opp} {opp_life}"))
        else:
            life_bits = [_c(_GREEN + _BOLD, f"{k} {v}") for k, v in lt.items()]
        header = (f"=== turn {s.get('turn')} | {s.get('phase')} | "
                  f"active: {s.get('active_player')} ===")
        print(f"\n{_c(_CYAN + _BOLD, header)}")
        print(f"  {_c(_CYAN + _BOLD, 'life:')} {' | '.join(life_bits)}")
        pids = [me] + ([opp] if opp is not None else [])
        mana_bits = []
        for pid in pids:
            counts = self._available_mana(pid)
            parts = []
            for c in "WUBRG":
                if counts.get(c):
                    parts.append(_c(_MANA_COLORS[c] + _BOLD, c)
                                 + f" x{counts[c]}")
            label = "you" if pid == me else pid
            mana_bits.append(f"{label}: {' '.join(parts) if parts else _c(_GREY, '0')}")
        print(f"  {_c(_CYAN + _BOLD, 'mana:')} {' | '.join(mana_bits)}")
        # -- battlefield table --
        bf_rows = []
        for pid, perms in (s.get("battlefield") or {}).items():
            for p in perms:
                if "power" in p:
                    status = []
                    if p.get("tapped"):
                        status.append("T")
                    if p.get("damage"):
                        status.append(f"dmg{p['damage']}")
                    if p.get("summoning_sick"):
                        status.append("sick")
                    bf_rows.append(([pid, p["id"], "creature",
                                     f"{p['power']}/{p['toughness']}",
                                     " ".join(status) or "-"],
                                    self._creature_cell_color(
                                        bool(p.get("tapped")))))
                else:
                    bf_rows.append(([pid, p["id"], "land", "-",
                                     "T" if p.get("tapped") else "untapped"],
                                    self._land_cell_color))
        print(f"\n{_c(_CYAN + _BOLD, 'BATTLEFIELD')}")
        if bf_rows:
            for ln in _table(["PLAYER", "CARD", "TYPE", "P/T", "STATUS"],
                             bf_rows):
                print(ln)
        else:
            print("  (empty)")
        if s.get("stack"):
            names = ", ".join(self._card_name(i["source"]) for i in s["stack"])
            print(f"\n{_c(_CYAN + _BOLD, 'STACK')}: {_c(_MAGENTA, names)}")
        # -- hand table --
        print(f"\n{_c(_CYAN + _BOLD, f'YOUR HAND ({len(hand)})')}")
        if hand:
            self._hand_table(hand)
        else:
            print("  (empty)")
        hc = s.get("hand_counts") or {}
        lc = s.get("library_counts") or {}
        opp_hand = next((v for k, v in hc.items() if k != me), "?")
        libs = " | ".join(f"{k} {v}" for k, v in lc.items())
        print(f"\n{_c(_GREY, f'opp hand: {opp_hand}  |  libraries: {libs}')}")

    # ---------------- heartbeat thread (RFC 4.3) ----------------
    def heartbeat(self):
        while self.running:
            time.sleep(30)
            if not self.running:
                return
            with self.lock:
                self.ping_seq += 1
                self.pong_deadline = time.monotonic() + 10
                seq = self.ping_seq
            try:
                self.send({"type": "PING", "seq_num": seq,
                           "timestamp": int(time.time() * 1000)})
            except OSError:
                self.running = False
                return
            time.sleep(10)
            with self.lock:
                dead = (self.pong_deadline is not None
                        and time.monotonic() > self.pong_deadline)
            if dead:
                print("\n[client] no PONG within 10s — disconnecting.")
                self.running = False
                self.sock.close()
                return

    # ---------------- mana auto-payment helper ----------------
    def auto_mana(self, card_id):
        """Build a mana_payment dict for a card from the untapped lands the
        server last showed us. Colored requirements first, then generic (X)
        from whatever colors remain."""
        d = card_def(self.catalog, card_id)
        cost = dict(d.get("cost") or {})
        avail = {}
        for p in (self.state.get("battlefield", {})
                  .get(self.player_id, [])):
            pd = card_def(self.catalog, p["id"])
            if pd and pd.get("kind") == "land" and not p.get("tapped"):
                avail[pd["produces"]] = avail.get(pd["produces"], 0) + 1
        pay = {}
        for color, n in cost.items():
            if color == "X":
                continue
            pay[color] = pay.get(color, 0) + n
            avail[color] = avail.get(color, 0) - n
        for _ in range(cost.get("X", 0)):
            c = max(avail, key=lambda k: avail[k], default=None)
            if c is None or avail[c] <= 0:
                break                      # let the server reject it
            pay[c] = pay.get(c, 0) + 1
            avail[c] -= 1
        return pay

    # ---------------- command loop ----------------
    def run(self):
        threading.Thread(target=self.reader, daemon=True).start()
        threading.Thread(target=self.heartbeat, daemon=True).start()
        print("Connected. Type 'ready' to join the game, 'help' for commands.")
        while self.running:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if not line.strip():
                continue
            try:
                self.command(line.strip())
            except Exception as e:            # never crash on bad input
                print(f"[client] command failed: {e}")
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def command(self, line):
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        with self.lock:
            ptok, rtok = self.priority_token, self.request_token
            last = self.last_server_seq
            trig = self.trigger

        if cmd == "help":
            print(__doc__)
        elif cmd == "ready":
            self.ready_seq += 1
            self.send({"type": "PLAYER_READY", "seq_num": self.ready_seq,
                       "player_id": self.player_id, "deck_list": self.deck})
        elif cmd == "keep":
            need = self.state.get("mulligans", 0)
            hand = self.state.get("hand", {}).get(self.player_id, [])
            bad = [c for c in args if c not in hand]
            if need and (len(args) != need or bad):
                print(f"[client] keep after {need} mulligan(s) requires "
                      f"exactly {need} cards from your hand to bottom: "
                      f"keep <card1> ... <card{need}>"
                      + (f"  (not in hand: {bad})" if bad else ""))
            else:
                self.send({"type": "MULLIGAN_CHOICE", "seq_num": rtok,
                           "keep": True, "cards_to_bottom": args})
        elif cmd == "mull":
            self.send({"type": "MULLIGAN_CHOICE", "seq_num": rtok,
                       "keep": False, "cards_to_bottom": []})
        elif cmd == "play":
            self.send({"type": "PLAY_LAND", "seq_num": ptok,
                       "card_id": args[0]})
        elif cmd == "cast":
            card = args[0]
            targets = args[1:2]
            self.send({"type": "CAST_SPELL", "seq_num": ptok,
                       "card_id": card, "targets": targets,
                       "mana_payment": self.auto_mana(card)})
        elif cmd == "pass":
            self.send({"type": "PRIORITY_PASS", "seq_num": ptok})
        elif cmd == "attack":
            opp = [p for p in (self.state.get("life_totals") or {})
                   if p != self.player_id]
            tgt = opp[0] if opp else "?"
            self.send({"type": "DECLARE_ATTACKERS", "seq_num": rtok,
                       "attackers": [{"creature_id": c, "target": tgt}
                                     for c in args]})
        elif cmd == "block":
            blockers = []
            for pair in args:
                c, a = pair.split(":")
                blockers.append({"creature_id": c, "blocking_id": a})
            self.send({"type": "DECLARE_BLOCKERS", "seq_num": rtok,
                       "blockers": blockers})
        elif cmd == "order":
            self.send({"type": "ASSIGN_DAMAGE_ORDER", "seq_num": rtok,
                       "attacker_id": args[0], "blocker_order": args[1:]})
        elif cmd == "discard":
            self.send({"type": "DISCARD", "seq_num": rtok,
                       "card_ids": args})
        elif cmd == "torder":
            with self.lock:
                to = self.trigger_order
            if not to:
                print("[client] no pending trigger ordering")
                return
            self.send({"type": "TRIGGER_ORDER_RESPONSE",
                       "seq_num": to["seq_num"],
                       "ordered_trigger_ids": args})
            with self.lock:
                self.trigger_order = None
        elif cmd in ("yes", "no"):
            if not trig:
                print("[client] no pending trigger choice")
                return
            self.send({"type": "TRIGGER_CHOICE_RESPONSE",
                       "seq_num": trig["seq_num"],
                       "trigger_id": trig["trigger_id"],
                       "accept": cmd == "yes", "chosen_target": None})
            with self.lock:
                self.trigger = None
        elif cmd == "concede":
            self.send({"type": "CONCEDE", "seq_num": last,
                       "player_id": self.player_id})
        elif cmd == "hand":
            hand = self.state.get("hand", {}).get(self.player_id, [])
            if hand:
                self._hand_table(hand)
            else:
                print("hand: empty")
        elif cmd == "state":
            self.render()
            _suggestion_box(self._suggestions())
        elif cmd == "quit":
            self.running = False
        else:
            print(f"[client] unknown command '{cmd}' — try 'help'")


def load_deck(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()
                and not ln.startswith("#")]


def main():
    ap = argparse.ArgumentParser(description="MTGNP v1.0 Player Client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--id", required=True, help="your player_id")
    ap.add_argument("--deck", required=True,
                    help="deck file: one card instance ID per line")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print every PDU sent and received")
    args = ap.parse_args()
    set_verbose(args.verbose)
    Client(args.host, args.port, args.id, load_deck(args.deck)).run()


if __name__ == "__main__":
    main()
