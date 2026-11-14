"""
Container healthcheck: send a protocol-level ping and require a pong.

Unlike a bare TCP connect, this proves the event loop is reading requests and
writing replies. Exit code 0 = healthy, 1 = unhealthy.
"""

import json
import os
import socket
import sys


def check(host: str, port: int, timeout: float = 3.0) -> bool:
    """Return True if the server answers {"request": "ping"} with a pong."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(b'{"request":"ping"}\n')
            reply = sock.makefile("rb").readline()
        response = json.loads(reply)
        return response.get("status") == "ok" and response.get("pong") is True
    except (OSError, ValueError, AttributeError):
        return False


if __name__ == "__main__":
    port = int(os.getenv("SERVER_PORT", "12345"))
    sys.exit(0 if check("localhost", port) else 1)
