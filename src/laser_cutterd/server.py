"""HTTP server.

GET /healthz reports that the daemon is up, and its version.
GET /status returns the latest snapshot of the controller as JSON.
"""

import json
import logging
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from laser_cutterd import __version__
from laser_cutterd.metrics import Value

logger = logging.getLogger(__name__)

StatusSource = Callable[[], Mapping[str, Value]]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], status: StatusSource) -> None:
        super().__init__(address, Handler)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server: Server
    server_version = f"laser-cutterd/{__version__}"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_json(HTTPStatus.OK, {"status": "ok", "version": __version__})
        elif self.path == "/status":
            self.send_json(HTTPStatus.OK, self.server.status())
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def send_json(self, status: HTTPStatus, body: Mapping[str, Value]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        # http.server writes to stderr; route it through logging instead.
        # Health checks every 30 s would drown everything else at INFO.
        logger.debug("%s %s", self.address_string(), format % args)


def make_server(host: str, port: int, status: StatusSource) -> Server:
    """A server bound to host:port (port 0 picks a free one)."""
    return Server((host, port), status)
