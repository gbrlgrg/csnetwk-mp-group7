# MTGNP Grading Rubric

> ⚠️ **PREREQUISITE — Verbose Mode (checking will not proceed without this):** Both the client and server must support a verbose mode that can be toggled on and off (e.g., via a command-line flag or startup argument). When enabled, all PDUs sent and received — on both the client side and the server side — must be printed to the console in a readable, clearly labelled format. The MP will not be checked unless verbose mode is working and active during the demo.

## TCP Sockets & Networking

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **TCP Server Setup & Client Accept** | 10 | Correct TCP server socket creation on port 4444, binding, listening, and accepting exactly two client connections. Additional connections must be refused. Server must handle client disconnect/reconnect within the implementation-defined timeout. |
| **Message Framing** | 5 | All PDUs are correctly framed with a 4-byte big-endian length prefix followed by a valid UTF-8 JSON payload. Receiver reads exactly the indicated byte count before attempting to parse. PDUs must not exceed 65,535 bytes. |
| **PDU Structure & seq_num** | 5 | Every PDU includes the required 'type' and 'seq_num' fields. Priority-bearing client PDUs echo the correct seq_num from the latest PRIORITY_GRANT or server request PDU. Server rejects stale seq_nums with ERROR code STALE_ACTION. |

## Game Lifecycle

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **LOBBY & PLAYER_READY Handling** | 10 | Server correctly enters LOBBY on startup and after each GAME_OVER. PLAYER_READY PDUs are validated: non-empty player_id, unique IDs, deck list of 1–50 cards from the fixed set. Server rejects duplicates with DUPLICATE_ID and invalid decks with ILLEGAL_DECK. |
| **GAME_SETUP & MULLIGAN** | 5 | Server initialises life totals at 20, shuffles decks, deals seven cards, and determines first player via random coin flip. London Mulligan rule: players who mulligan redraw a full hand and place N cards on the bottom when keeping after N mulligans. |
| **IN_GAME Phase & Step Transitions** | 10 | Server broadcasts PHASE_TRANSITION messages for all turn phases and steps (Untap, Upkeep, Draw, Main 1, Combat, Main 2, End, Cleanup). Phase-specific rules are enforced — e.g., land drops only in Main Phase, no non-mana spells during Untap. |
| **GAME_OVER & Session Restart** | 5 | Server correctly detects win/loss conditions (life total ≤ 0, empty library on draw, CONCEDE) and broadcasts GAME_OVER with the appropriate reason. Returns to LOBBY state and awaits fresh PLAYER_READY PDUs on the same TCP connections. |

## Server-Side Game Logic

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **Game State Management & Hidden Info** | 10 | Server maintains the single authoritative Game State. GAME_STATE_UPDATE messages are personalised per player — each player's hand is hidden from the opponent. Library counts, battlefield, graveyard, and stack contents are correctly reflected. |
| **Priority & Stack Resolution** | 10 | Server correctly issues PRIORITY_GRANT to the appropriate player at each priority window. The stack (LIFO) is maintained correctly — spells and abilities push and resolve in order. Stack resolves when both players pass priority consecutively. STACK_PUSH and STACK_RESOLVE are broadcast. At least 5 card effects (any) should be implemented to achieve the full point. |
| **Combat System** | 10 | Server correctly manages the full combat sequence: Declare Attackers, Declare Blockers, Assign Damage Order, optional First Strike Damage, and Combat Damage. Summoning sickness is enforced. COMBAT_DAMAGE_RESULT is broadcast with correct damage values. |

## Client Implementation

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **Client Sending & State Rendering** | 5 | Client correctly sends all required PDU types (PLAYER_READY, MULLIGAN_CHOICE, CAST_SPELL, PLAY_LAND, PRIORITY_PASS, CONCEDE, etc.). Client accepts GAME_STATE_UPDATE as authoritative and renders visible state accurately, discarding any locally computed state. |
| **PING/PONG Heartbeat** | 5 | Client sends PING PDUs at regular intervals (recommended: every 30 s) and disconnects if no PONG is received within the implementation-defined timeout (recommended: 10 s). Server responds to every PING with a matching PONG. |

## Error Handling & Code Quality

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **Error PDU Handling** | 5 | Server sends ERROR PDUs with correct error codes (STALE_ACTION, ILLEGAL_ACTION, ILLEGAL_DECK, DUPLICATE_ID, etc.) in all cases specified by the RFC. Client handles ERROR PDUs gracefully without crashing. |
| **Readability & Comments** | 5 | Code is well-structured with meaningful variable and function names, and includes clear comments explaining non-obvious logic — especially socket setup, message framing, and protocol state transitions. |

## Bonus

| Criterion | Points | Description |
| :--- | :---: | :--- |
| **Full Card Effects** | 10 | Implementation of all Card Abilities and Effects. |
| **Bonus Features** | 10 | Additional features beyond the base specification: triggered-ability UI, spectator client, graphical interface, or other creative extensions. Must be demonstrated and explained during checking. |
