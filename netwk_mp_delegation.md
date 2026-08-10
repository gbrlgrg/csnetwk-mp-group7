# MTGNP Machine Problem - Task Delegation (4 Developers)

## How the split was chosen (not just even division)
Natural fault line - by protocol layer, not by feature or file count. RFC 0001 already hands us four horizontal layers that touch almost entirely different code paths: (1) the transport/socket/codec layer, (2) the lifecycle + turn/phase state machine, (3) priority/stack/card-effect resolution, (4) combat + the client. Splitting this way - instead of e.g. "each dev implements one full turn phase end-to-end" - keeps each person in a different file/module for 90%+ of the project, so two people are rarely editing the same function. The one place all four layers do meet (the dispatch table that routes 25 PDU types to handlers) is deliberately owned by one person (Dev 1) so there's a single source of truth for "how a PDU gets from socket to handler."

## What blocks everyone else, and is front-loaded
Nothing above the socket layer can be tested against a real connection until framing (4-byte length prefix, exact-N reads) and the `type`, `seq_num` PDU codec exist. This is Dev 1's work, and it's scheduled first (finishes 28 Jul) specifically so the other three aren't blocked past day 3. Until then, Devs 2-4 build against local stubs rather than sitting idle.

## When non-dev/writing work can realistically start
There are no separate report-writer roles here - README and AI-disclosure work is dev-authored - but the same logic applies: the README's build/run instructions and known-limitations section can't be written honestly until there's a working end-to-end game, not a partial one. That work is placed last (N12), after the full-game rehearsal (N11) has actually produced bugs and gaps to document. Writing the README earlier would mean documenting a plan instead of a working system.

## Anchor for timing
The dates and session numbers below (N1-N12) come directly from the existing (CSNETWKmtgnpscheduleproposal.xlsx) schedule (26 Jul - 6 Aug 2026), not invented for this delegation.

---

## Dev 1 - Transport & Session (30 rubric pts)
**Owns:** the TCP socket layer, message framing, the PDU codec + dispatch table, verbose mode, heartbeat, and connection lifecycle.

### Files/sections owned:
* TCP listener/connection-accept logic (port 4444)
* Framing layer (4-byte big-endian length prefix, exact-N read, 65,535-byte cap)
* PDU codec: JSON encode/decode for the `type` + `seq_num` envelope, dispatch table for all 25 PDU types
* Verbose-mode logging (both client and server sides of it)
* `ERROR` envelope construction
* PING/PONG heartbeat handling
* Disconnect/reconnect detection and the `GAME_OVER` → LOBBY restart on the same TCP connection

### Concrete tasks:
- [ ] Bind/listen on 4444; accept exactly two clients; refuse a third connection attempt
- [ ] Implement the 4-byte length-prefix framing on both send and receive paths
- [ ] Build the dispatch table routing all 25 `type` values to stub/real handlers
- [ ] Implement verbose mode as a runtime-toggleable flag, printing every PDU sent/received, both sides
- [ ] Implement PING (client)/PONG (server) with timeout-based disconnect
- [ ] Implement `GAME_OVER` reason handling at the transport level (all 4 reasons) and confirm the connection is retained, not closed, afterward
- [ ] Pair with Dev 3 on `seq_num` validation and `STALE_ACTION` rejection (shared boundary see below)

### Hard boundaries
**must NOT touch:**
* Game-state logic (life totals, stack contents, battlefield state) that's Dev 2/3 territory
* Combat resolution or client rendering code
* Should not hand-roll card-effect logic even temporarily "to test framing" use a stub PDU instead

### Handoff points:
* **28 Jul (N3)** - critical handoff: framing + codec must be real and working. Devs 2-4 port their stub-based work onto this transport starting the same day. If this slips, the whole team's schedule slips.
* **29 Jul (N4)**: hands PING/PONG to be tested against Dev 2's lobby flow
* **4 Aug (N10)**: joins Dev 4 to wire `GAME_OVER` into the first full playable game

---

## Dev 2 - Lifecycle & Turn Engine (25 rubric pts)
**Owns:** LOBBY, `PLAYER_READY` validation, `GAME_SETUP`, the London Mulligan, and the full phase/step state machine.

### Files/sections owned:
* Lobby state handling and `PLAYER_READY` validation (`DUPLICATE_ID`, `ILLEGAL_DECK` deck size 1-50)
* Lobby-variant `GAME_STATE_UPDATE`
* `GAME_SETUP` (life totals, shuffle, deal 7, coin flip)
* Mulligan logic, including the cards_to_bottom == N validation
* The phase/step state machine across all 14 phase names and their `PHASE_TRANSITION` broadcasts
* Phase-legality enforcement (e.g., sorcery-speed-only casting in Main Phase)
* Cleanup-step discard loop (hand size > 7)

### Concrete tasks:
- [ ] Validate `PLAYER_READY`: non-empty unique `player_id`, deck of 1-50 legal cards
- [ ] Implement lobby re-submission (a player may resend `PLAYER_READY` before both are ready)
- [ ] Implement life = 20, shuffle, 7-card deal, random first-player coin flip
- [ ] Implement London Mulligan including the bottom-count check
- [ ] Build the phase/step machine (Untap → Upkeep → Draw → Precombat Main → Combat → Postcombat Main → End Step → Cleanup) broadcasting `PHASE_TRANSITION` at each step
- [ ] Enforce the first-player-no-draw rule on turn 1
- [ ] Implement `WRONG_PHASE` rejection for actions attempted outside their legal phase
- [ ] Implement the cleanup discard loop, awaiting `DISCARD` until hand size <= 7

### Hard boundaries
**must NOT touch:**
* The stack/priority mechanics themselves (Dev 3 owns push/resolve) - Dev 2 only owns when priority windows open, not what happens inside them
* Combat sub-steps beyond broadcasting the phase transitions into/out of Combat
* Socket/framing code

### Handoff points:
* **29 Jul (N4)**: hands lobby completion to Dev 3, who layers hidden-hand filtering onto `GAME_STATE_UPDATE` from day one
* **31 Jul (N6)**: the phase machine must exist and be stable before Dev 3 can hang priority windows off it (N7) - this is the key downstream dependency
* **1 Aug (N7)**: wires phase transitions into Dev 3's priority windows
* **3 Aug (N9)**: provides combat step transitions for Dev 4's combat sequence

---

## Dev 3 - Priority, Stack & State (20 rubric pts)
**Owns:** priority windows, the LIFO stack, state-based actions, at least 5 card effects, mana payment, and hidden-hand filtering.

### Files/sections owned:
* `PRIORITY_GRANT`/`PRIORITY_PASS` handling and the `seq_num` priority token
* The stack: `STACK_PUSH`, `STACK_RESOLVE`, both-pass resolution logic
* State-based action checks (life <= 0, lethal damage → graveyard), applied repeatedly before any priority grant
* At least 5 card effects (chosen to exercise different code paths)
* `PLAY_LAND` and mana payment, including `INSUFFICIENT_MANA`
* Personalized `GAME_STATE_UPDATE` construction - specifically the hidden-hand filtering

### Concrete tasks:
- [ ] Implement `PRIORITY_GRANT`, `PRIORITY_PASS` with correct `seq_num` token issuance
- [ ] Implement the LIFO stack with push/resolve and the consecutive-pass resolution rule
- [ ] Implement state-based action sweep (repeat until none remain) before granting priority
- [ ] Implement 5 card effects covering distinct mechanics (e.g., direct damage, life gain, destroy, counter, draw)
- [ ] Implement mana payment validation and `INSUFFICIENT_MANA`
- [ ] Build/verify that every `GAME_STATE_UPDATE` filters the opponent's hand from day one do not retroactively patch this in later
- [ ] Pair with Dev 1 on `STALE_ACTION` validation (shared boundary)

### Hard boundaries
**must NOT touch:**
* Phase-transition broadcasting logic itself (consumes Dev 2's transitions, doesn't generate them)
* Combat damage calculation (Dev 4 owns combat damage; Dev 3 only supplies the state-based-action sweep combat damage triggers)
* Framing/dispatch code

### Handoff points:
* **1 Aug (N7)**: priority/stack must exist and be stable before Dev 4 can rely on state-based actions killing creatures correctly in combat (N9) - key downstream dependency
* **2 Aug (N8)**: hands the 5 implemented card effects to Dev 4 for casting-flow client testing
* **5 Aug (N11)**: triggers own layer's error codes (`STALE_ACTION`, `ILLEGAL_TARGET`, `INSUFFICIENT_MANA`, `TRIGGER_ORDER_INVALID`, `TRIGGER_CHOICE_INVALID`) as part of the team-wide error sweep

---

## Dev 4 - Combat & Client (20 rubric pts)
**Owns:** the full combat sequence and the entire client implementation.

### Files/sections owned:
* Declare Attackers / Declare Blockers / Assign Damage Order / First Strike / Combat Damage / End of Combat
* Summoning-sickness and tapped-creature attack validation
* `COMBAT_DAMAGE_RESULT` construction
* The client: connection, PDU sending, state rendering, client-side `ERROR` handling

### Concrete tasks:
- [ ] Implement Declare Attackers with summoning-sickness/tapped rejection (`ILLEGAL_ACTION`)
- [ ] Implement Declare Blockers and `ASSIGN_DAMAGE_ORDER` (skip this step when no attacker is multiply-blocked)
- [ ] Implement combat damage, including the optional first/double-strike step
- [ ] Build `COMBAT_DAMAGE_RESULT` broadcast with correct simultaneous damage values
- [ ] Build the client: send every required PDU type, render `GAME_STATE_UPDATE` as authoritative, discard conflicting local state
- [ ] Implement client-side handling of all `ERROR` PDUs without crashing
- [ ] Client mulligan flow, casting flow, phase display, priority prompts (built against mocks/stubs as early as N2, since this role is the least blocked)

### Hard boundaries
**must NOT touch:**
* Server-side stack/priority resolution logic (consumes Dev 3's `STACK_PUSH`, `STACK_RESOLVE`, doesn't generate them)
* Lifecycle/phase-machine internals (consumes Dev 2's `PHASE_TRANSITION`, doesn't generate them)
* Framing/dispatch code

### Handoff points:
* **27 Jul (N2) onward**: client can be built against a mock server immediately start early since this role has the fewest hard blockers
* **1 Aug (N7)**: depends on Dev 3's stack/state-based actions existing before combat damage can correctly kill creatures - key upstream dependency
* **4 Aug (N10)**: pairs with Dev 1 to assemble the first full playable game end-to-end
* **5 Aug (N11)**: triggers own layer's error codes (`ILLEGAL_ACTION` for combat, plus general client-side error resilience) as part of the team-wide sweep

---

## Shared - All Four (5 rubric pts)
**Owns:** readability/comments, testing & interoperability, the README, and the cross-explain demo rehearsal.

### Concrete tasks (split by original layer ownership):
* **Dev 1** - writes build/run instructions, including the verbose-mode flag
* **Dev 2** - fills the Work Distribution Matrix honestly
* **Dev 3** - writes the AI Usage section, naming every tool and how it was used
* **Dev 4** - writes known limitations and deviations from the RFC
* **All** - readability pass (comments specifically on sockets, framing, and state transitions)
* **All** - dry-run where each dev explains a layer they did not write (this is the rubric's highest-leverage check: it can zero out a grade if someone can't explain a section, including AI-assisted code)
* **All** - zip, verify from a clean clone, submit

### Hard boundary:
This is the one phase where no one "owns" a file exclusively - but each person still writes their assigned README section alone first, then the group edits together, to avoid four people fighting over one document simultaneously.

### Handoff point:
This entire block is sequenced last (6 Aug/N12), and only after N11's full-game rehearsal has produced real bugs/gaps - writing the README before that would mean documenting intentions instead of an actual working system.

---

## Cross-cutting notes
* Two shared boundaries exist on purpose (Dev 1 + Dev 3 on `seq_num` `STALE_ACTION`; all four on error-code triggering at N11) - these are the only points where ownership deliberately overlaps, because the alternative (one person owning validation across all 25 PDU types) would make that person a bottleneck for everyone else.
* Dev 1 is the single largest risk to the schedule - everything downstream depends on the 28 Jul handoff landing on time. If it slips, treat it as a team-wide problem, not a Dev 1 problem.
