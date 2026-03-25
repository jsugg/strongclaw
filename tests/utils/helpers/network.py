"""Reusable local-network helpers for integration tests."""

from __future__ import annotations

import contextlib
import http.server
import socket
import socketserver
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager

type Endpoint = tuple[str, int]
type ListenerFactory = Callable[[], AbstractContextManager[Endpoint]]
type HttpServerFactory = Callable[[bytes], AbstractContextManager[Endpoint]]


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    response_body = b"ok\n"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        del format, args
        return None


@contextlib.contextmanager
def tcp_listener() -> Iterator[Endpoint]:
    """Yield an open local TCP listener."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    stop_event = threading.Event()

    def _accept_loop() -> None:
        while not stop_event.is_set():
            try:
                listener.settimeout(0.1)
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                pass

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        yield host, int(port)
    finally:
        stop_event.set()
        listener.close()
        thread.join(timeout=1)


@contextlib.contextmanager
def disconnecting_listener() -> Iterator[Endpoint]:
    """Yield a local TCP listener that immediately closes accepted sockets."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = listener.getsockname()
    stop_event = threading.Event()

    def _accept_loop() -> None:
        while not stop_event.is_set():
            try:
                listener.settimeout(0.1)
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.close()

    thread = threading.Thread(target=_accept_loop, daemon=True)
    thread.start()
    try:
        yield host, int(port)
    finally:
        stop_event.set()
        listener.close()
        thread.join(timeout=1)


@contextlib.contextmanager
def http_server(body: bytes) -> Iterator[Endpoint]:
    """Yield a local HTTP server that always serves the provided body."""

    class Handler(_StaticHandler):
        response_body = body

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield str(host), int(port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
