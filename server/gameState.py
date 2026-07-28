"""
game_state.py — Dev 3

The single authoritative GameState the server maintains (RFC 0001 §3,
"Game State" / "Visible State"). Dev 2 (phases), Dev 4 (combat), and
Dev 3's own priority.py/stack.py/effects.py all read and mutate this
same object — treat its field names as a contract with the rest of team.

state_view.py is responsible for turning this into the personalized,
hidden-info-filtered dict that GAME_STATE_UPDATE actually sends.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Permanent:
    id: str                       # matches the card instance id from deck_list
    controller: str                # player_id
    tapped: bool = False
    # Creature-only fields (None/absent for non-creatures per §10.2.2)
    is_creature: bool = False
    power: Optional[int] = None
    toughness: Optional[int] = None
    damage: int = 0
    summoning_sick: bool = True
    keywords: list = field(default_factory=list)


@dataclass
class StackItem:
    stack_item_id: str
    item_type: str                 # SPELL | ABILITY | TRIGGER_ABILITY
    source_id: str
    controller_id: str
    targets: list = field(default_factory=list)


@dataclass
class PlayerState:
    player_id: str
    life_total: int = 20
    hand: list = field(default_factory=list)          # card_ids, owner-visible only
    library: list = field(default_factory=list)        # ordered; top = index 0 (agree with team)
    graveyard: list = field(default_factory=list)       # index 0 = first card placed
    battlefield: list = field(default_factory=list)     # list[Permanent]
    land_played_this_turn: bool = False
    deck_list: list = field(default_factory=list)       # original submitted deck


class GameState:
    """Server-side authoritative state for one game session (two players)."""

    def __init__(self, player_ids: list[str]):
        self.players: dict[str, PlayerState] = {
            pid: PlayerState(player_id=pid) for pid in player_ids
        }
        self.turn: int = 0
        self.phase: str = "LOBBY"            # LOBBY|MULLIGAN|IN_GAME phase names, see RFC §10.2.4
        self.active_player: Optional[str] = None
        self.priority_holder: Optional[str] = None
        self.stack: list[StackItem] = []
        self.mulligan_count: dict[str, int] = {pid: 0 for pid in player_ids}
        self._seq_num: int = 0               # server-issued PDU counter (§5.4)

    def other_player(self, player_id: str) -> str:
        return next(pid for pid in self.players if pid != player_id)

    def next_seq_num(self) -> int:
        self._seq_num += 1
        return self._seq_num

    def find_permanent(self, permanent_id: str) -> Optional[Permanent]:
        for p_state in self.players.values():
            for perm in p_state.battlefield:
                if perm.id == permanent_id:
                    return perm
        return None

    def apply_state_based_actions(self) -> list[str]:
        """
        RFC §8.4 — check SBAs repeatedly until none remain.
        Returns list of description strings for anything that changed
        (useful for verbose mode / STACK_RESOLVE state_changes).
        Real logic (lethal damage, 0 toughness, life <= 0) belongs to
        stack.py, which will call into this — kept as a stub here so the
        data model is in place before N7.
        """
        changes = []
        # TODO(N7, stack.py): lethal damage / toughness <= 0 / life <= 0
        return changes

    def to_dict(self) -> dict:
        """
        Full internal representation. state_view.py wraps this and
        strips hidden information per-player before it goes into a
        GAME_STATE_UPDATE PDU. Do NOT send this raw over the wire.
        """
        return {
            "turn": self.turn,
            "phase": self.phase,
            "active_player": self.active_player,
            "priority_holder": self.priority_holder,
            "life_totals": {pid: p.life_total for pid, p in self.players.items()},
            "stack": [vars(item) for item in self.stack],
            "battlefield": {
                pid: [vars(perm) for perm in p.battlefield]
                for pid, p in self.players.items()
            },
            "graveyard": {pid: p.graveyard for pid, p in self.players.items()},
            "hand": {pid: p.hand for pid, p in self.players.items()},
            "library_counts": {pid: len(p.library) for pid, p in self.players.items()},
        }