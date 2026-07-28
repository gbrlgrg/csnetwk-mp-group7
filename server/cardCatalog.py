"""
card_catalog.py — Dev 3

Loads the shared out-of-band card catalog (JSON) referenced by RFC 0001 §1.
Card IDs exchanged in PDUs are keys into this catalog. This module is the
single source of truth for card data on the server (and can be reused
as-is on the client side, since the RFC says both sides load the same file).
"""

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CardDef:
    card_id: str            # matches the key used in deck_list / battlefield ids
    name: str
    card_type: str          # e.g. "CREATURE", "INSTANT", "SORCERY", "LAND"
    mana_cost: dict         # e.g. {"R": 1} or {} for lands
    power: Optional[int] = None
    toughness: Optional[int] = None
    keywords: list = field(default_factory=list)   # e.g. ["HASTE", "FIRST_STRIKE"]
    effect_text: str = ""
    effect_id: Optional[str] = None  # links to a handler in effects.py


class CardCatalog:
    """Holds every legal card definition, loaded once at startup."""

    def __init__(self):
        self._cards: dict[str, CardDef] = {}

    @classmethod
    def load(cls, path: str) -> "CardCatalog":
        catalog = cls()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Expecting raw to be a list of card dicts. Adjust key names below
        # to match whatever schema the team agrees on for cards.json.
        for entry in raw:
            card = CardDef(
                card_id=entry["card_id"],
                name=entry["name"],
                card_type=entry["card_type"],
                mana_cost=entry.get("mana_cost", {}),
                power=entry.get("power"),
                toughness=entry.get("toughness"),
                keywords=entry.get("keywords", []),
                effect_text=entry.get("effect_text", ""),
                effect_id=entry.get("effect_id"),
            )
            catalog._cards[card.card_id] = card

        return catalog

    def get(self, card_id: str) -> Optional[CardDef]:
        return self._cards.get(card_id)

    def is_legal(self, card_id: str) -> bool:
        return card_id in self._cards

    def validate_deck(self, deck_list: list[str]) -> Optional[str]:
        """
        Returns an ILLEGAL_DECK reason string if invalid, else None.
        Mirrors RFC §6.2 / §6.3 validation rules.
        """
        if not deck_list:
            return "Deck is empty."
        if len(deck_list) > 50:
            return f"Deck contains {len(deck_list)} cards; maximum is 50."
        for cid in deck_list:
            if not self.is_legal(cid):
                return f"Unknown card_id '{cid}' not in catalog."
        return None

    def __len__(self):
        return len(self._cards)


if __name__ == "__main__":
    # quick manual smoke test
    catalog = CardCatalog.load("shared/cards.json")
    print(f"Loaded {len(catalog)} cards")
    print(catalog.validate_deck(["nonexistent_card"]))