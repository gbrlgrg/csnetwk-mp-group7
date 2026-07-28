"""
state_view.py — Dev 3 (N4)

Builds the personalized, hidden-info-filtered dict that goes inside a
GAME_STATE_UPDATE PDU (RFC 0001 §4.2, §10.2.2). Each player must see
their own hand in full but only the opponent's hand_count.

transport.py (Dev 1) is expected to call build_view() once per recipient
before sending a GAME_STATE_UPDATE, i.e. this is called twice per
broadcast (once for each player) since the payload differs per player.
"""

from gameState import GameState


def build_lobby_view(players_ready: int, waiting_for: list[str]) -> dict:
    """RFC §6.2 lobby-phase variant of GAME_STATE_UPDATE.state"""
    return {
        "phase": "LOBBY",
        "players_ready": players_ready,
        "waiting_for": waiting_for,
    }


def build_view(state: GameState, for_player: str) -> dict:
    """
    In-game variant (MULLIGAN and IN_GAME phases, RFC §10.2.2).
    Returns the `state` object to embed in a GAME_STATE_UPDATE sent to
    `for_player`. Own hand is visible; opponent hand is count-only.
    """
    opponent = state.other_player(for_player)

    view = {
        "turn": state.turn,
        "active_player": state.active_player,
        "phase": state.phase,
        "priority_holder": state.priority_holder,  # null during UNTAP/CLEANUP
        "life_totals": {
            pid: p.life_total for pid, p in state.players.items()
        },
        "stack": [
            {
                "stack_item_id": item.stack_item_id,
                "item_type": item.item_type,
                "source": item.source_id,
                "targets": item.targets,
                "controller": item.controller_id,
            }
            for item in state.stack
        ],
        "battlefield": {
            pid: [_permanent_view(perm) for perm in p.battlefield]
            for pid, p in state.players.items()
        },
        "graveyard": {
            pid: p.graveyard for pid, p in state.players.items()
        },
        # Hidden-info filtering happens here:
        "hand": {
            for_player: state.players[for_player].hand
        },
        "hand_counts": {
            opponent: len(state.players[opponent].hand)
        },
        "library_counts": {
            pid: len(p.library) for pid, p in state.players.items()
        },
        "land_played_this_turn": state.players[state.active_player].land_played_this_turn
        if state.active_player else False,
    }
    return view


def _permanent_view(perm) -> dict:
    """Non-creatures: id + tapped only. Creatures add damage/power/toughness/summoning_sick."""
    base = {"id": perm.id, "tapped": perm.tapped}
    if perm.is_creature:
        base.update({
            "damage": perm.damage,
            "power": perm.power,
            "toughness": perm.toughness,
            "summoning_sick": perm.summoning_sick,
        })
    return base