"""
server/card_catalog.py — shared JSON card catalog loading and deck-list
validation (RFC Section 1 NOTE, Section 6.2). (Gregorio)

Card instance IDs in PDUs are '<base>_<nnn>'; the base is the catalog key.
The catalog itself lives out-of-band in shared/cards.json and is loaded by
both the server and the client.
"""

import json
import os


def load_catalog(path: str = None) -> dict:
    if path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "shared", "cards.json")
    with open(path, "r", encoding="utf-8") as f:
        cat = json.load(f)
    cat.pop("_comment", None)
    return cat


def base_name(card_instance_id: str) -> str:
    """'lightning_bolt_001' -> 'lightning_bolt'."""
    parts = card_instance_id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return card_instance_id


def card_def(catalog: dict, card_instance_id: str):
    return catalog.get(base_name(card_instance_id))


def validate_deck(catalog: dict, deck) -> str:
    """Return None if the deck is legal, else a human-readable reason for an
    ILLEGAL_DECK error (1-50 cards, all from the fixed set)."""
    if not isinstance(deck, list):
        return "deck_list must be an array of card instance IDs."
    if not (1 <= len(deck) <= 50):
        return f"Deck has {len(deck)} cards; must be between 1 and 50."
    for c in deck:
        if not isinstance(c, str) or card_def(catalog, c) is None:
            return f"Unknown card ID '{c}' (not in the fixed set)."
    return None
