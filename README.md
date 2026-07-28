# MTGNP — Machine Problem (CSNETWK)

Implementation of the Magic: The Gathering Multiplayer Network Protocol
(MTGNP v1.0) per RFC 0001, for CSNETWK.

## Directory Structure

```
project/
├── server/
│   ├── main.py                 # entry point, socket accept loop (Dev 1)
│   ├── transport.py            # framing, PDU codec, dispatch table (Dev 1)
│   ├── heartbeat.py            # PING/PONG (Dev 1)
│   ├── lobby.py                 # PLAYER_READY handling, LOBBY state (Dev 2)
│   ├── phase_engine.py         # phase/step machine, PHASE_TRANSITION (Dev 2)
│   ├── combat.py                # attackers/blockers/damage (Dev 4)
│   │
│   ├── card_catalog.py         # loads/parses shared JSON card catalog,
│   │                           #   deck_list validation -> ILLEGAL_DECK (Dev 3)
│   ├── game_state.py           # GameState/PlayerState/Permanent/StackItem —
│   │                           #   the authoritative data model (Dev 3)
│   ├── state_view.py           # builds personalized GAME_STATE_UPDATE views,
│   │                           #   hides opponent hand (Dev 3)
│   ├── priority.py             # PRIORITY_GRANT/PASS, seq_num validation,
│   │                           #   STALE_ACTION (Dev 3, N7)
│   ├── stack.py                # LIFO stack, STACK_PUSH/STACK_RESOLVE,
│   │                           #   state-based actions (Dev 3, N7)
│   ├── effects.py              # card effect implementations, mana payment,
│   │                           #   INSUFFICIENT_MANA (Dev 3, N8)
│   │
│   └── tests/
│       ├── test_card_catalog.py
│       ├── test_game_state.py
│       ├── test_priority_stack.py
│       └── test_effects.py
│
├── shared/
│   └── cards.json               # shared card catalog (out-of-band card data, RFC §1)
│
├── client/
│   └── ...                     # client implementation & rendering (Dev 4)
│
├── requirements.txt
└── README.pdf                  # build/run instructions, work matrix, AI usage,
                                 #   known limitations (submitted deliverable)
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # venv\Scripts\activate on Windows
pip install -r requirements.txt
```

`requirements.txt`:
```
pytest
```

## Running Tests

```bash
pytest server/tests/
```

## Verbose Mode

Both client and server must support a runtime-toggleable verbose mode
that prints every PDU sent/received on both sides (see RFC §5, MP
prerequisite). This gates all grading — the MP is not checked without it.

## Notes

- Card IDs in PDUs are keys into `data/cards.json` — both server and
  client load this file independently (RFC §1, NOTE).
- `game_state.py`'s `GameState.to_dict()` is the full internal
  representation and must never be sent over the wire directly —
  always go through `state_view.py` first to strip hidden
  information per player.
