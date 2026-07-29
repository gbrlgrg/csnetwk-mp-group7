"""
main.py — Dev 1

Entry point for the MTGNP game server. Binds/listens on port 4444,
accepts exactly two clients, refuses any further connection attempts,
and spawns one thread per connection (RFC 0001 §5.1).

Per-connection PDU handling (recv_frame -> decode_pdu -> dispatch ->
encode_pdu -> send_frame) is added in Phase 2/pduCodec.py; this module
currently only proves the accept/refuse-third socket behavior.
"""

import argparse
import socket
import threading

from transport import ConnectionClosedError, recv_frame, send_frame
from verbose import log_verbose, set_verbose

DEFAULT_PORT = 4444
MAX_CLIENTS = 2


class GameServer:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._clients: list[socket.socket] = []

    def _accept_slot(self, conn: socket.socket) -> bool:
        """Atomically claim one of the two client slots. Returns False
        (and the caller must refuse the connection) if both slots are
        already taken."""
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                return False
            self._clients.append(conn)
            return True

    def _release_slot(self, conn: socket.socket) -> None:
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)

    def _handle_connection(self, conn: socket.socket, addr) -> None:
        log_verbose("SERVER", f"client connected from {addr}")
        try:
            while True:
                try:
                    payload = recv_frame(conn)
                except (ConnectionClosedError, ConnectionResetError, OSError):
                    break
                # Phase 2 wires payload through decode_pdu -> dispatch -> encode_pdu.
                # Until then, echo raw bytes back so the framing layer is testable
                # in isolation.
                try:
                    send_frame(conn, payload)
                except OSError:
                    break
        finally:
            self._release_slot(conn)
            conn.close()
            log_verbose("SERVER", f"client disconnected from {addr}")

    def serve_forever(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen()
        log_verbose("SERVER", f"listening on {self.host}:{self.port}")

        try:
            while True:
                conn, addr = listener.accept()
                if not self._accept_slot(conn):
                    log_verbose("SERVER", f"refusing extra connection from {addr}")
                    conn.close()
                    continue
                thread = threading.Thread(
                    target=self._handle_connection, args=(conn, addr), daemon=True
                )
                thread.start()
        finally:
            listener.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MTGNP game server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--verbose", action="store_true", help="Log every PDU sent/received")
    args = parser.parse_args()

    set_verbose(args.verbose)
    GameServer(port=args.port).serve_forever()


if __name__ == "__main__":
    main()
