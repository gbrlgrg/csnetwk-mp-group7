"""
server/transport.py — MTGNP framing, PDU codec and verbose logging
(RFC 0001 Section 5). Also hosts the per-connection reader (ClientConn),
the dispatch table of legal client PDU types, and the TransportMixin with
the server's seq_num-stamped send helpers. (Suñga)

Implements Section 5 of the RFC:
  * 4-byte big-endian unsigned length prefix (Section 5.2)
  * UTF-8 JSON payload, max 65,535 bytes (Sections 5.2 / 5.3)
plus the shared card catalog loader and the verbose PDU logger required by
the machine-problem rubric.
"""

import json
import os
import struct
import sys
import threading
import time

MAX_PDU_BYTES = 65535        # RFC 5.2: "A PDU MUST NOT exceed 65,535 bytes."
DEFAULT_PORT = 4444          # RFC 5.1: default server port.

# ---------------------------------------------------------------------------
# Verbose mode (rubric prerequisite): toggled at startup with --verbose / -v.
# When on, every PDU sent or received is printed, clearly labelled.
# ---------------------------------------------------------------------------
VERBOSE = False
_log_lock = threading.Lock()


def set_verbose(flag: bool) -> None:
    global VERBOSE
    VERBOSE = flag


def log_pdu(direction: str, who: str, pdu: dict) -> None:
    """Print a PDU in a readable, labelled format when verbose mode is on.

    direction: 'SEND' or 'RECV'
    who:       peer label, e.g. 'player_1' or 'server'
    """
    if not VERBOSE:
        return
    stamp = time.strftime("%H:%M:%S")
    with _log_lock:
        print(f"[{stamp}] {direction:4s} {who:>9s} | "
              f"{json.dumps(pdu, separators=(',', ':'))}")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Message framing (RFC Section 5.2)
# ---------------------------------------------------------------------------
class FramingError(Exception):
    pass


def send_pdu(sock, pdu: dict, who: str = "peer", lock: threading.Lock = None) -> None:
    """Encode a PDU as UTF-8 JSON and send it with a 4-byte BE length prefix."""
    payload = json.dumps(pdu).encode("utf-8")
    if len(payload) > MAX_PDU_BYTES:
        raise FramingError(f"PDU exceeds {MAX_PDU_BYTES} bytes")
    frame = struct.pack("!I", len(payload)) + payload
    if lock:
        with lock:
            sock.sendall(frame)
    else:
        sock.sendall(frame)
    log_pdu("SEND", who, pdu)


def _recv_exact(sock, n: int) -> bytes:
    """Read exactly n bytes from the socket (RFC 5.2: 'read exactly that many
    bytes before attempting JSON parsing'). Raises ConnectionError on EOF."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed connection")
        buf += chunk
    return buf


def recv_pdu(sock, who: str = "peer") -> dict:
    """Receive one length-prefixed PDU. Returns the parsed JSON object.

    Raises FramingError with reason 'INVALID_JSON' semantics if the payload is
    not valid UTF-8 JSON — callers decide whether to send an ERROR PDU.
    """
    header = _recv_exact(sock, 4)
    (length,) = struct.unpack("!I", header)
    if length > MAX_PDU_BYTES:
        raise FramingError(f"frame length {length} exceeds {MAX_PDU_BYTES}")
    payload = _recv_exact(sock, length)
    try:
        pdu = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FramingError(f"INVALID_JSON: {e}")
    if not isinstance(pdu, dict):
        raise FramingError("INVALID_JSON: top-level value is not an object")
    log_pdu("RECV", who, pdu)
    return pdu




# ---------------------------------------------------------------------------
# Legal client->server PDU types (dispatch table; RFC Section 10.1)
# ---------------------------------------------------------------------------
KNOWN_CLIENT_TYPES = {
    "PLAYER_READY", "MULLIGAN_CHOICE", "PRIORITY_PASS", "CAST_SPELL",
    "ACTIVATE_ABILITY", "TRIGGER_ORDER_RESPONSE", "TRIGGER_CHOICE_RESPONSE",
    "DECLARE_ATTACKERS", "DECLARE_BLOCKERS", "ASSIGN_DAMAGE_ORDER",
    "PLAY_LAND", "DISCARD", "CONCEDE", "PING",
}


# ---------------------------------------------------------------------------
# Per-connection wrapper: socket + reader thread + send lock
# ---------------------------------------------------------------------------
class ClientConn:
    def __init__(self, sock, addr, idx, event_q):
        self.sock = sock
        self.addr = addr
        self.idx = idx                 # seat index 0 or 1
        self.player_id = None          # set by PLAYER_READY
        self.send_lock = threading.Lock()
        self.alive = True
        self._q = event_q
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def label(self):
        return self.player_id or f"seat_{self.idx}"

    def send(self, pdu):
        try:
            send_pdu(self.sock, pdu, who=self.label(), lock=self.send_lock)
        except OSError:
            self.alive = False

    def _read_loop(self):
        """Reader thread: frame-decode PDUs and push them to the event queue.
        PING is answered inline via the heartbeat module so heartbeats keep
        flowing even while the main thread waits on a specific player."""
        from server import heartbeat
        while self.alive:
            try:
                pdu = recv_pdu(self.sock, who=self.label())
            except FramingError as e:
                # RFC 11: INVALID_JSON — report and keep the connection.
                self.send({"type": "ERROR", "seq_num": 0, "code": "INVALID_JSON",
                           "message": str(e), "rejected_action": None})
                continue
            except (ConnectionError, OSError):
                self.alive = False
                self._q.put(("gone", self.idx, None))
                return
            if pdu.get("type") == "PING":
                heartbeat.answer_ping(self, pdu)
                continue
            self._q.put(("pdu", self.idx, pdu))


# ---------------------------------------------------------------------------
# TransportMixin: the Server's seq_num-stamped send helpers (RFC 5.4)
# ---------------------------------------------------------------------------
class TransportMixin:
    def next_seq(self):
        self.seq += 1
        return self.seq

    def send_to(self, idx, pdu):
        pdu["seq_num"] = self.next_seq()
        self.clients[idx].send(pdu)
        return pdu["seq_num"]

    def send_all(self, pdu):
        """Broadcast one logical PDU. Per the RFC examples a broadcast
        consumes a single seq_num, delivered identically to both clients."""
        pdu["seq_num"] = self.next_seq()
        for c in self.clients:
            if c and c.alive:
                c.send(pdu)
        return pdu["seq_num"]

    def send_error(self, idx, code, message, rejected=None, echo_seq=None):
        self.clients[idx].send({
            "type": "ERROR",
            "seq_num": echo_seq if echo_seq is not None else self.seq,
            "code": code, "message": message,
            "rejected_action": rejected,
        })
