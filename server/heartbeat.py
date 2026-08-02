"""
server/heartbeat.py — PING/PONG heartbeat handling (RFC 10.2.24-25). (Suñga)

The client sends PING on an independent counter; the server answers every
PING with a PONG echoing the client's seq_num and timestamp. Answered from
the reader thread so heartbeats are never blocked by game logic.
"""


def answer_ping(conn, pdu):
    conn.send({"type": "PONG",
               "seq_num": pdu.get("seq_num", 0),
               "timestamp": pdu.get("timestamp", 0)})
