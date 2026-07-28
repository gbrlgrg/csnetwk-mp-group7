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

## Module Ownership — All Devs

### Dev 1 — Transport, Framing, Connections
| File | Responsibility | Timetable slot |
|---|---|---|
| `main.py` | Entry point; TCP socket setup, bind, listen on :4444 | N1 |
| `transport.py` | 4-byte length-prefix framing, exact-N reads, 65,535-byte cap; two-client accept (refuse 3rd); type/seq_num envelope codec; dispatch table for all 25 PDU types; ERROR envelope | N2–N3 |
| `heartbeat.py` | PING/PONG (30s/10s), disconnect/reconnect handling, `time_limit_ms` enforcement | N4–N5 |
| — | Verbose mode (server + client side PDU logging, runtime toggle) | N2 (gates all grading) |
| — | `GAME_OVER` → return to `LOBBY` on same TCP connection | N10 |

### Dev 2 — Lobby, Lifecycle, Turn/Phase Engine
| File | Responsibility | Timetable slot |
|---|---|---|
| `lobby.py` | `PLAYER_READY` validation (non-empty id, `DUPLICATE_ID`, deck 1–50, `ILLEGAL_DECK`), lobby-variant `GAME_STATE_UPDATE`, deck re-submission | N4 |
| — | `GAME_SETUP`: life 20, shuffle, deal seven, random first player; London Mulligan incl. `cards_to_bottom == N` check | N5 |
| `phase_engine.py` | Phase/step machine across all 14 phase names, `PHASE_TRANSITION` broadcasts; untap/upkeep/draw incl. first-player-no-draw; end step + cleanup discard loop | N6 |
| — | Wire phase transitions to priority windows; combat step transitions; `WRONG_PHASE` enforcement | N7–N8 |
| — | Win-condition detection (life ≤ 0, empty-library draw, concede) | N10 |

### Dev 3 — Card Data, Game State, Priority/Stack, Effects
| File | Responsibility | Timetable slot |
|---|---|---|
| `card_catalog.py` | Load shared JSON card catalog; expose card lookups; validate `deck_list` (1–50 cards, all legal) → `ILLEGAL_DECK` | N2 |
| `game_state.py` | Authoritative `GameState` data model (life, hand, battlefield, graveyard, library, stack) | N2 |
| `state_view.py` | Per-player `GAME_STATE_UPDATE` view with hidden-hand filtering | N4 (done early) |
| — | Priority scaffolding ready to hang off the phase machine | N6 |
| `priority.py` | `PRIORITY_GRANT`/`PRIORITY_PASS`, `seq_num` token validation, `STALE_ACTION` | N7 |
| `stack.py` | LIFO stack, `STACK_PUSH`/`STACK_RESOLVE`, both-pass rules, state-based actions applied before any grant | N7 |
| `effects.py` | 5+ card effects (mechanically simplest, different code paths), `PLAY_LAND`, mana payment, `INSUFFICIENT_MANA`; hidden-info regression check | N8 |
| — | State-based actions during combat | N9 |

### Dev 4 — Client, Combat, Rendering
| File | Responsibility | Timetable slot |
|---|---|---|
| `client/` skeleton | Client skeleton, console rendering against a mock server | N2 |
| `client/` | Client renders lobby, sends `PLAYER_READY`; mulligan flow | N4–N5 |
| `client/` | Client phase display | N6 |
| `client/` | Client priority prompts | N7 |
| `client/` | Client casting flow | N8 |
| `combat.py` | Declare attackers (summoning-sickness/tapped rejection), declare blockers + damage order, combat damage incl. optional first/double strike, `COMBAT_DAMAGE_RESULT` | N9 |
| `client/` | Client end-to-end: sends every required PDU, treats server state as authoritative | N10 |
| — | Known limitations and deviations from the RFC (README.pdf section) | N11–N12 |

### All Devs (shared/cross-cutting)
| Task | Timetable slot |
|---|---|
| Read RFC §5 & §10.1; fill Work Distribution Matrix; agree language/repo/shared JSON card catalog | N1 |
| Full-game rehearsal (two complete games in verbose mode); trigger own layer's error codes (12 codes split four ways); fix + log known limitations | N11 |
| README.pdf: build instructions, Work Distribution Matrix, AI Usage section, known limitations; readability pass; cross-explain dry-run; zip and submit | N12 |

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
