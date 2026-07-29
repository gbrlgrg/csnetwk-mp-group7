"""
transport.py — Dev 1

Message framing per RFC 0001 §5.1-5.2: every PDU is a 4-byte big-endian
length prefix followed by exactly that many bytes of JSON payload. This
module only deals in raw bytes; JSON parsing lives in pduCodec.py.
"""

import socket
import struct

LENGTH_PREFIX_SIZE = 4
MAX_FRAME_SIZE = 65_535


class FrameTooLargeError(Exception):
    """Raised when a frame's declared length exceeds MAX_FRAME_SIZE."""


class ConnectionClosedError(Exception):
    """Raised when the peer closes the connection mid-read."""


def _recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """Read exactly num_bytes from sock, looping over partial recv() calls."""
    chunks = []
    remaining = num_bytes
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionClosedError("Peer closed connection during read")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, payload: bytes) -> None:
    """Write payload as a length-prefixed frame. Raises FrameTooLargeError
    if payload exceeds MAX_FRAME_SIZE (RFC §5.2)."""
    if len(payload) > MAX_FRAME_SIZE:
        raise FrameTooLargeError(
            f"Payload is {len(payload)} bytes; max is {MAX_FRAME_SIZE}"
        )
    header = struct.pack(">I", len(payload))
    sock.sendall(header + payload)


def recv_frame(sock: socket.socket) -> bytes:
    """Read one length-prefixed frame and return the raw payload bytes.
    Raises ConnectionClosedError if the peer disconnects mid-frame, or
    FrameTooLargeError if the declared length exceeds MAX_FRAME_SIZE."""
    header = _recv_exact(sock, LENGTH_PREFIX_SIZE)
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_SIZE:
        raise FrameTooLargeError(f"Declared frame length {length} exceeds max {MAX_FRAME_SIZE}")
    return _recv_exact(sock, length)
