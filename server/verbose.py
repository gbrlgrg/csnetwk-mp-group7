"""
verbose.py — Dev 1

Runtime-toggleable verbose logging (RFC 0001 §4.2/§4.3 prerequisite).
When enabled, every PDU sent or received must be printed on both the
client and server sides. This module holds the flag and the print
routine shared by transport.py/pduCodec.py on the server, and by the
client's own logging hook.
"""

import threading

_lock = threading.Lock()
_verbose_enabled = False


def set_verbose(enabled: bool) -> None:
    global _verbose_enabled
    with _lock:
        _verbose_enabled = enabled


def is_verbose() -> bool:
    with _lock:
        return _verbose_enabled


def log_verbose(side: str, message: str) -> None:
    """Print message if verbose mode is enabled. side is a short label
    such as "SERVER", "CLIENT", "SEND", or "RECV"."""
    if is_verbose():
        print(f"[{side}] {message}", flush=True)


def log_pdu(side: str, direction: str, pdu: dict) -> None:
    """Convenience wrapper for logging a full PDU dict. direction is
    "SEND" or "RECV". Format: [SIDE] DIRECTION type seq=N <json>"""
    if not is_verbose():
        return
    pdu_type = pdu.get("type", "?")
    seq_num = pdu.get("seq_num", "?")
    print(f"[{side}] {direction} {pdu_type} seq={seq_num} {pdu}", flush=True)
