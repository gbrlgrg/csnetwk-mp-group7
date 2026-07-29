import socket
import struct
import threading

import pytest

from transport import (
    MAX_FRAME_SIZE,
    ConnectionClosedError,
    FrameTooLargeError,
    recv_frame,
    send_frame,
)


def _socket_pair():
    a, b = socket.socketpair()
    return a, b


def test_round_trip_small_payload():
    a, b = _socket_pair()
    try:
        send_frame(a, b"hello world")
        assert recv_frame(b) == b"hello world"
    finally:
        a.close()
        b.close()


def test_round_trip_empty_payload():
    a, b = _socket_pair()
    try:
        send_frame(a, b"")
        assert recv_frame(b) == b""
    finally:
        a.close()
        b.close()


class _ChunkedRecvSocket:
    """Wraps a real socket but forces recv() to return small chunks,
    so recv_frame's exact-N read loop is actually exercised."""

    def __init__(self, sock, chunk_size):
        self._sock = sock
        self._chunk_size = chunk_size

    def recv(self, n):
        return self._sock.recv(min(n, self._chunk_size))


def test_partial_recv_is_reassembled():
    a, b = _socket_pair()
    try:
        payload = b"x" * 10_000
        chunked_b = _ChunkedRecvSocket(b, chunk_size=37)

        t = threading.Thread(target=send_frame, args=(a, payload))
        t.start()
        assert recv_frame(chunked_b) == payload
        t.join(timeout=5)
    finally:
        a.close()
        b.close()


def test_oversized_send_rejected():
    a, b = _socket_pair()
    try:
        with pytest.raises(FrameTooLargeError):
            send_frame(a, b"x" * (MAX_FRAME_SIZE + 1))
    finally:
        a.close()
        b.close()


def test_oversized_declared_length_rejected():
    a, b = _socket_pair()
    try:
        header = struct.pack(">I", MAX_FRAME_SIZE + 1)
        t = threading.Thread(target=a.sendall, args=(header,))
        t.start()
        with pytest.raises(FrameTooLargeError):
            recv_frame(b)
        t.join(timeout=5)
    finally:
        a.close()
        b.close()


def test_connection_closed_mid_header():
    a, b = _socket_pair()
    try:
        a.sendall(b"\x00\x00")  # only 2 of 4 header bytes
        a.close()
        with pytest.raises(ConnectionClosedError):
            recv_frame(b)
    finally:
        b.close()


def test_connection_closed_mid_payload():
    a, b = _socket_pair()
    try:
        header = struct.pack(">I", 100)
        a.sendall(header + b"short")
        a.close()
        with pytest.raises(ConnectionClosedError):
            recv_frame(b)
    finally:
        b.close()


def test_max_size_payload_round_trips():
    a, b = _socket_pair()
    try:
        payload = b"y" * MAX_FRAME_SIZE
        result = {}

        def receiver():
            result["data"] = recv_frame(b)

        t = threading.Thread(target=receiver)
        t.start()
        send_frame(a, payload)
        t.join(timeout=5)
        assert result["data"] == payload
    finally:
        a.close()
        b.close()
