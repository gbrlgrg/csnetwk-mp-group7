"""
gui_bridge.py — WebSocket-to-TCP bridge for MTGNP GUI.

Connects the browser-based GUI (WebSocket) to the MTGNP game server (TCP).
The game server uses 4-byte big-endian length-prefixed JSON PDUs over raw TCP.
Browsers cannot speak raw TCP, so this bridge translates between the two.

Usage:
    pip install websockets
    python gui_bridge.py [--host 127.0.0.1] [--port 4444] [--ws-port 8765] [-v]

Start the game server first:
    python -m server.main [--port 4444]

Then start this bridge:
    python gui_bridge.py

Then open gui/index.html in TWO browser tabs (one per player).
"""

import argparse
import asyncio
import json
import logging
import struct

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gui_bridge")

# Track connected clients
client_count = 0


async def handle_client(ws, tcp_host, tcp_port, verbose):
    """Handle one browser WebSocket connection by bridging it to a TCP socket
    connected to the game server."""
    global client_count
    client_count += 1
    client_id = client_count
    client_addr = ws.remote_address
    logger.info(f"[client {client_id}] WebSocket connected from {client_addr}")

    # Connect to the game server via TCP
    try:
        reader, writer = await asyncio.open_connection(tcp_host, tcp_port)
        logger.info(
            f"[client {client_id}] TCP connection to server "
            f"{tcp_host}:{tcp_port} established"
        )
    except Exception as e:
        logger.error(f"[client {client_id}] Failed to connect to server: {e}")
        await ws.close(1011, f"Cannot connect to game server: {e}")
        return

    async def ws_to_tcp():
        """Forward WebSocket messages to TCP with 4-byte length prefix."""
        try:
            async for message in ws:
                if verbose:
                    try:
                        pdu = json.loads(message)
                        logger.info(
                            f"[client {client_id}] WS→TCP: "
                            f"{pdu.get('type', '?')} seq={pdu.get('seq_num', '?')}"
                        )
                    except json.JSONDecodeError:
                        logger.info(f"[client {client_id}] WS→TCP: (raw)")

                payload = message.encode("utf-8")
                frame = struct.pack("!I", len(payload)) + payload
                writer.write(frame)
                await writer.drain()
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[client {client_id}] WebSocket closed")
        except Exception as e:
            logger.error(f"[client {client_id}] WS→TCP error: {e}")
        finally:
            writer.close()

    async def tcp_to_ws():
        """Forward TCP length-prefixed messages to WebSocket."""
        try:
            while True:
                # Read 4-byte length header
                header = await reader.readexactly(4)
                (length,) = struct.unpack("!I", header)

                # Read payload
                payload = await reader.readexactly(length)
                message = payload.decode("utf-8")

                if verbose:
                    try:
                        pdu = json.loads(message)
                        pdu_type = pdu.get("type", "?")
                        # Don't spam PONG in verbose
                        if pdu_type != "PONG":
                            logger.info(
                                f"[client {client_id}] TCP→WS: "
                                f"{pdu_type} seq={pdu.get('seq_num', '?')}"
                            )
                    except json.JSONDecodeError:
                        logger.info(f"[client {client_id}] TCP→WS: (raw)")

                await ws.send(message)
        except asyncio.IncompleteReadError:
            logger.info(f"[client {client_id}] TCP connection closed by server")
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[client {client_id}] WebSocket closed during TCP→WS")
        except Exception as e:
            logger.error(f"[client {client_id}] TCP→WS error: {e}")
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def heartbeat():
        """Send PING to the game server every 30 seconds to keep alive."""
        ping_seq = 0
        try:
            while True:
                await asyncio.sleep(30)
                ping_seq += 1
                ping_pdu = json.dumps({
                    "type": "PING",
                    "seq_num": ping_seq,
                    "timestamp": int(asyncio.get_event_loop().time() * 1000),
                }).encode("utf-8")
                frame = struct.pack("!I", len(ping_pdu)) + ping_pdu
                writer.write(frame)
                await writer.drain()
        except Exception:
            pass

    # Run both directions concurrently + heartbeat
    tasks = [
        asyncio.create_task(ws_to_tcp()),
        asyncio.create_task(tcp_to_ws()),
        asyncio.create_task(heartbeat()),
    ]

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()

    logger.info(f"[client {client_id}] Bridge session ended")


async def main():
    parser = argparse.ArgumentParser(
        description="MTGNP WebSocket-to-TCP Bridge for the GUI"
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="game server TCP host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=4444,
        help="game server TCP port (default: 4444)"
    )
    parser.add_argument(
        "--ws-port", type=int, default=8765,
        help="WebSocket port for browser clients (default: 8765)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="log every PDU forwarded"
    )
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info(f"WebSocket bridge starting on ws://0.0.0.0:{args.ws_port}")
    logger.info(f"Proxying to TCP game server at {args.host}:{args.port}")
    logger.info("Open gui/index.html in TWO browser tabs to play!")

    # Create a handler closure that captures the TCP host/port
    async def handler(ws, path=None):
        await handle_client(ws, args.host, args.port, args.verbose)

    async with websockets.serve(handler, "0.0.0.0", args.ws_port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bridge shutting down")
