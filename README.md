# MTGNP v1.0 — Magic: The Gathering Network Protocol

CSNETWK Machine Problem — implementation of RFC 0001 (MTGNP v1.0).

## Project structure

```
project/
├── server/                     # Game Server package — run: python3 -m server.main
│   ├── main.py                 #   entry point, socket accept loop, lifecycle (Suñga)
│   ├── transport.py            #   framing, PDU codec, ClientConn, dispatch table (Suñga)
│   ├── heartbeat.py            #   PING/PONG (Suñga)
│   ├── lobby.py                #   PLAYER_READY handling, LOBBY/SETUP/MULLIGAN (Rebudiao)
│   ├── phase_engine.py         #   phase/step machine, PHASE_TRANSITION, land drops (Rebudiao)
│   ├── combat.py               #   attackers/blockers/damage order/first strike (Barreo)
│   ├── card_catalog.py         #   loads shared JSON catalog; ILLEGAL_DECK validation (Gregorio)
│   ├── game_state.py           #   authoritative data model + GameOver/ClientGone (Gregorio)
│   ├── state_view.py           #   personalized GAME_STATE_UPDATE views, hidden hands (Gregorio)
│   ├── priority.py             #   PRIORITY_GRANT/PASS, seq_num tokens, STALE_ACTION (Gregorio)
│   ├── stack.py                #   LIFO stack, STACK_PUSH/RESOLVE, SBAs, triggers (Gregorio)
│   ├── effects.py              #   card effects, mana payment, INSUFFICIENT_MANA (Gregorio)
│   └── tests/
│       ├── test_card_catalog.py       # unit: catalog + deck validation
│       ├── test_game_state.py         # unit: data model + hidden info
│       ├── test_priority_stack.py     # unit: tokens, LIFO stack, counterspell
│       ├── test_effects.py            # unit: mana, effects, state-based actions
│       ├── test_integration_game.py   # end-to-end protocol game   (10 assertions)
│       ├── test_integration_combat.py # combat/effect mechanics     (3 assertions)
│       └── test_integration_edge.py   # edge cases + TRIGGER_ORDER (18 assertions)
├── shared/
│   └── cards.json              # shared card catalog (out-of-band card data, RFC §1)
├── client/
│   └── main.py                 # Player Client & rendering — run: python3 -m client.main (Barreo)
├── decks/                      # sample deck lists (one card instance ID per line)
├── requirements.txt            # states that only the Python stdlib is required
├── README.md                   # this document (Markdown source)
└── README.pdf                  # this document (submitted deliverable)
```

The server is a Python package: one module per protocol concern, composed
into the `Server` class in `server/main.py` via mixins. Method
implementations are identical to the reference single-file build; only the
file layout differs.

Requirements: **Python 3.10+**, standard library only. No third-party packages.

## Running — step by step

**Prerequisites:** Python 3.10 or newer. No installation of packages is
needed (standard library only).

> **Windows note:** the command is `python` (or `py`), not `python3` —
> substitute it in every command below, e.g. `python server.py --verbose`.
> If Windows says *"Python was not found"*, install Python from
> python.org/downloads and tick **"Add python.exe to PATH"** in the
> installer, then reopen the terminal. Check with `python --version`
> (Linux/macOS: `python3 --version`).

1. **Unzip the project** and open a terminal in the project folder (the one containing the `server/` package).
> **Note:** You could add `--verbose` for steps 3-5 to enable verbose mode. 
3. **Start the server** (Terminal 1). It listens on port 4444 (RFC 5.1):
   ```
   python -m server.main
   ```
   You should see `[server] listening on port 4444`. Use `--port <n>` if
   4444 is taken (then add `--port <n>` to the clients too).
4. **Start the first client** (Terminal 2):
   ```
   python -m client.main --id player_1 --deck decks/burn.txt 
   ```
   If the server is on another machine, add `--host <server-ip>`.
5. **Start the second client** (Terminal 3):
   ```
   python -m client.main --id player_2 --deck decks/control.txt
   ```
   A third connection attempt will be refused by the server (RFC 5.1).
6. **Join the game:** type `ready` in each client and press Enter. When both
   are ready the server deals hands and the mulligan begins.
7. **Mulligan:** type `keep` to keep your hand, or `mull` to redraw. After
   N mulligans, keep with `keep <card_1> ... <card_N>` to put N cards on
   the bottom of your library (London mulligan).
8. **Play:** when you see `>>> You have priority`, you may act:
   * `play mountain_003` — play a land (your main phase only, 1/turn)
   * `cast lightning_bolt_001 player_2` — cast a spell (mana is paid
     automatically from your untapped lands)
   * `pass` — pass priority
   * `attack goblin_guide_001` — declare attackers (in DECLARE_ATTACKERS)
   * `block wall_of_stone_001:goblin_guide_001` — declare blockers
   * `order <attacker> <blocker1> <blocker2>` — damage order (multi-blocks)
   * `discard <card>` — discard to 7 at cleanup; `yes` / `no` — answer an
     optional trigger; `hand` / `state` — re-print; `concede` — give up;
     `help` — full list
9. **Game over:** the winner and reason are announced; both clients stay
   connected and can type `ready` to start a new game (RFC 6.6).

### Verbose mode (rubric prerequisite)

`--verbose` / `-v` on **either program** prints **every PDU sent and
received**, labelled with direction, peer, timestamp, and the full JSON:

```
[14:03:22] SEND  player_1 | {"type": "PRIORITY_GRANT", "player_id": "player_1", ...}
[14:03:23] RECV  player_1 | {"type": "CAST_SPELL", "seq_num": 12, ...}
```

Without the flag both programs run quietly. This satisfies the "Verbose Mode
Requirement" toggle in the project specification.

### Debug flags (server)

* `--time-limit <ms>` — priority deadline (default 60000; RFC 4.2)
* `--seed <n>` — seed the RNG for reproducible shuffles/coin flips (demos)
* `--first {0,1}` — force which seat goes first (demos/tests)
* `--port <n>` — listen port

### Tests

```
python server/tests/test_card_catalog.py     # unit tests (catalog/decks)
python server/tests/test_game_state.py       # unit tests (data model)
python server/tests/test_priority_stack.py   # unit tests (tokens/stack)
python server/tests/test_effects.py          # unit tests (effects/mana/SBAs)
python server/tests/test_integration_game.py    # 10 protocol assertions
python server/tests/test_integration_combat.py  #  3 combat assertions
python server/tests/test_integration_edge.py    # 18 edge-case assertions
```

All are run from the project root. `test_integration_edge.py` derives a deterministic RNG seed by replicating the server's
seeded shuffle locally, so its scripted 10-turn game is fully reproducible.

## Design summary

* **Transport (RFC 5):** every PDU is a 4-byte big-endian length prefix +
  UTF-8 JSON, max 65,535 bytes. `common.recv_pdu` reads exactly the framed
  bytes before parsing; malformed payloads yield `ERROR INVALID_JSON`.
* **Threads:** the server runs one reader thread per client feeding a single
  event queue; the main thread runs the RFC Section 6 lifecycle
  (`LOBBY → GAME_SETUP → MULLIGAN → IN_GAME → GAME_OVER → LOBBY`, same TCP
  connections). `PING` is answered with `PONG` directly in the reader thread
  (independent heartbeat counter, RFC 5.4). The client runs reader,
  heartbeat (PING/30 s, 10 s PONG deadline) and stdin threads.
* **Authority (RFC 4.2/4.3):** all rules live in the server. The client never
  simulates outcomes; each personalized `GAME_STATE_UPDATE` (own hand only,
  opponent hand as a count) overwrites its view.
* **seq_num discipline (RFC 5.4):** the server keeps one monotonically
  increasing counter; a broadcast consumes one number. The client echoes the
  seq of the latest `PRIORITY_GRANT` for priority actions, the relevant
  request PDU for `MULLIGAN_CHOICE`/`DISCARD` (the `GAME_STATE_UPDATE`) and
  combat declarations (the `PHASE_TRANSITION`), and any last server seq for
  `CONCEDE`. Mismatches get `ERROR STALE_ACTION` and, when the player still
  holds priority, a re-granted token. After an *illegal* (non-stale) action
  the server re-issues `PRIORITY_GRANT` with the **same** seq_num
  (RFC Sec. 11 item 3).
* **Rules implemented:** London mulligan; full phase sequence with
  first-turn draw skip; one land per turn at sorcery speed; implicit mana
  (declared `mana_payment` validated against untapped lands, which are then
  tapped); LIFO stack with priority windows and caster-retains-priority;
  fizzle on illegal targets at resolution; state-based actions after every
  event (lethal damage, 0 toughness, life ≤ 0, AP loses simultaneous-death
  ties); summoning sickness; combat with attack tapping, single-attacker
  blocks, multi-block damage ordering (`ASSIGN_DAMAGE_ORDER`), first-strike
  step when relevant, no trample; cleanup discard-to-7 loop; win by
  `LIFE_ZERO`, `DECK_EMPTY`, `CONCEDE`, `DISCONNECT` (incl. priority
  timeout); extra connections refused; lobby deck resubmission; duplicate ID
  rejection.
* **Card effects (≥ 5, RFC Appendix set):** Lightning Bolt / Shock (damage),
  Counterspell (counters a stack item), Giant Growth (+3/+3 until EOT),
  Healing Salve (lifegain), Divination (draw 2), Gray Merchant of Asphodel
  (ETB drain-2 optional **triggered ability** exercised through
  `TRIGGER_CHOICE` / `TRIGGER_CHOICE_RESPONSE`), Festering Imp (death
  trigger; two dying simultaneously exercises `TRIGGER_ORDER` /
  `TRIGGER_ORDER_RESPONSE`), Goblin Guide (haste),
  Youthful Knight (first strike), plus vanilla creatures and five basic
  lands.

## Known deviations / interpretations

* A countered spell is announced with `STACK_RESOLVE` `result: "FIZZLE"`
  (the RFC only defines `RESOLVED | FIZZLE`, with no dedicated "countered"
  result).
* Gray Merchant's drain is modelled as an *optional* ("you may") trigger so
  the `TRIGGER_CHOICE` flow of RFC 8.6.3 is exercised; declining discards it.
* `ACTIVATE_ABILITY` is answered with `ILLEGAL_ACTION`: the fixed card set
  defines no activated non-mana abilities, and mana is implicit (RFC 7.5).
  As a result the interactive client has no command that sends it (the
  server-side rejection path is still fully implemented).
* Summoning sickness is cleared for the active player's creatures during
  their Cleanup (equivalent, for this card set, to "since your last turn
  began").
* `--seed` / `--first` are non-RFC debug conveniences.

## Work Distribution Matrix

Member 1 = John Lloyd Suñga · Member 2 = TODO Daniel Rebudiao ·
Member 3 = Gaibril Gregorio · Member 4 = TODO Carlo Barreo

| Task / Feature | Member 1 | Member 2 | Member 3 | Member 4 |
|---|---|---|---|---|
| TCP Server: connection handling, framing, dispatch | `main.py` accept loop, lifecycle; `transport.py` `ClientConn` + dispatch table | | | |
| Game lifecycle: LOBBY, GAME_SETUP, MULLIGAN logic | | `lobby.py` — `PLAYER_READY`, LOBBY/SETUP, London Mulligan | `card_catalog.py` — deck-list validation, `ILLEGAL_DECK` | |
| Turn & phase engine (all phases/steps, transitions) | | `phase_engine.py` — phase/step machine, `PHASE_TRANSITION`, land drops | | |
| Priority & Stack logic, spell/ability resolution | | | `priority.py`, `stack.py`, `effects.py` — `PRIORITY_GRANT`/`PASS`, seq_num tokens, LIFO stack, SBAs, triggers, card effects | |
| Combat system (attackers, blockers, damage) | | | | `combat.py` — attackers/blockers, damage order, first strike |
| Client implementation & state rendering | | | `state_view.py` — personalized `GAME_STATE_UPDATE` views, hidden hands | `client/main.py` — full client & console rendering |
| PDU serialisation/deserialisation (all 25 PDU types) | `transport.py` — PDU codec, 4-byte length framing, seq_num-stamped send helpers | | | |
| Error handling, PING/PONG heartbeat, disconnect logic | `heartbeat.py` PING/PONG; error paths in `transport.py` / `main.py` | | `game_state.py` — `GameOver` / `ClientGone` handling | |
| Verbose mode (client + server PDU logging, toggle on/off) | server-side verbose logging + `--verbose` flag (`transport.py`, `main.py`) | | | client-side `--verbose` PDU logging (`client/main.py`) |
| Testing & interoperability | ✓ | ✓ | ✓ | ✓ |
| README / documentation / AI disclosure | ✓ | ✓ | ✓ | ✓ |

## AI Usage Disclosure

### 1. Summary statement

> AI assistance (Claude, ChatGPT, GitHub Copilot) was used during the development of this
> machine problem, primarily for extracting all requirements for the project,
> generating a task delegation plan for four developers that follow the Work
> Distribution Matrix, generating an initial
> implementation of the server package and client, drafting test scripts, and
> explaining other nuances in the specifications. All AI output was reviewed,
> tested, and modified by the group. Every member has read
> and can explain the modules attributed to them in the Work Distribution
> Matrix above, and the group jointly reviewed all remaining modules.

### 2. Tools used

| AI tool | Version / model | Used by | Access (free / paid / school) | What it was used for |
|---|---|---|---|---|
| Claude | Opus 5, Fable 5 | All members| Free, Paid | Extracting an overview of the project requirements, Generating an organized task delegation plan, Proper directory of the file architecture, Explanation of each task and how to implement it |
| ChatGPT | GPT-5 | All members | Free | Explaining LIFO stack semantics, debugging framing bug |
| GitHub Copilot | Multi-model | All members | Free | Inline assisted autocompletion and guidance while writing files |

### 3. Per-file AI involvement

Legend — **G** = AI generated the first draft, member reviewed/tested/edited ·
**A** = member wrote it, AI used only for debugging, refactoring, or
explanation · **H** = hand-written, no AI involvement.

| File | Level (See Legend) | Owner | What the AI did | What the member changed by hand |
|---|---|---|---|---|
| `server/main.py` | G | Suñga | TODO | TODO |
| `server/transport.py` | G | Suñga | TODO | TODO |
| `server/heartbeat.py` | A | Suñga | TODO | TODO |
| `server/lobby.py` | TODO | Rebudiao | TODO | TODO |
| `server/phase_engine.py` | TODO | Rebudiao | TODO | TODO |
| `server/combat.py` | TODO | Barreo | TODO | TODO |
| `server/card_catalog.py` | G | Gregorio | Needed functions/methods for the file | Implementation of the methods / how to call them |
| `server/game_state.py` | A | Gregorio | The networking, Lobby, Combat, Stack resolution, Phase engine, Effects, Priority, Transport, and Client | Player state, Permanent management, Stack tracking, and Game-over/Disconnect Exceptions |
| `server/state_view.py` | A | Gregorio | The data formatting and cleaning the broadcast_state helper | I did everything |
| `server/priority.py` | A | Gregorio | Error-handling edge cases | I did everything |
| `server/stack.py` | G | Gregorio | The initial structure and logic, which handles the LIFO stack — spell casting, target validation, stack resolution with fizzle checks, state-based actions, and APNAP trigger ordering |  you then fixed and debugged the edge cases like mana payment validation, sorcery-speed timing checks, and the trigger choice/order flows |
| `server/effects.py` | G | Gregorio | Debugged Edge Cases like generic mana coverage checks and the countered-spell graveyard handling | I did all of the card effect logic |
| `client/main.py` | TODO | Barreo | TODO | TODO |
| `server/tests/*.py` | TODO | TODO | TODO | TODO |
| `shared/cards.json`, `decks/*.txt` | TODO | TODO | TODO | TODO |
| `README.md` | G | all | General formatting of the whole README file | Fixed the TODO markings and grammar. |

### 4. What was **not** AI-generated

TODO — list the work that is entirely the group's. Suggested items:

* TODO e.g. interpretation of RFC 0001 and the deviations in *Known
  deviations / interpretations* above
* TODO e.g. the module split and mixin composition in `server/main.py`
* TODO e.g. the seq_num echo discipline decided after testing stale actions
* TODO e.g. deck construction in `decks/` and card balance in `cards.json`
* TODO e.g. all bugs found and fixed during interoperability testing

### 5. Verification performed

State how AI output was checked before it was accepted — this is what
separates disclosed assistance from dishonesty.

| Check | Done by | Result |
|---|---|---|
| Ran all 4 unit test files | TODO | TODO e.g. all pass |
| Ran all 3 integration test files (31 assertions) | TODO | TODO |
| Manual 2-client game to `GAME_OVER`, both win conditions | TODO | TODO |
| Verbose PDU logs checked against RFC 0001 §5 framing | TODO | TODO |
| Error paths exercised (`INVALID_JSON`, `STALE_ACTION`, `ILLEGAL_DECK`, `INSUFFICIENT_MANA`) | TODO | TODO |
| Line-by-line walkthrough of AI-drafted code by its owner | TODO | TODO |

### 6. Member attestation

Each member signs for the modules they own **and** confirms they can
explain the rest of the codebase at the demo.

| Member | Modules I can explain line-by-line | AI tools I personally used | Signature / date |
|---|---|---|---|
| John Lloyd Suñga | `main.py`, `transport.py`, `heartbeat.py` | TODO | TODO |
| Daniel Rebudiao | `lobby.py`, `phase_engine.py` | TODO | TODO |
| Gaibril Gregorio | `card_catalog.py`, `game_state.py`, `state_view.py`, `priority.py`, `stack.py`, `effects.py` | Claude, ChatGPT | ![Gregorio Signature](signatures/Gregorio%20Signature%20and%20Date.jpg) |
| Carlo Barreo | `combat.py`, `client/main.py` | TODO | TODO |
