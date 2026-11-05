from __future__ import annotations

import errno
import json
import socket
import sys
import threading
import traceback
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlsplit

from .guard import ClientGuard
from .state import Encoded, encode

_DISCONNECTS = {
    errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT, errno.ENOTCONN
}
_ROUTES = {
    "/healthz", "/openapi.json", "/api/v1/info", "/api/v1/status",
    "/api/v1/players", "/api/v1/history", "/api/v1/chat",
}
_ENDPOINTS = {"/api/v1/" + name: name for name in ("status", "players", "history", "chat")}


class APIHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False
    request_queue_size = 128

    def __init__(self, address: tuple[str, int], plugin: object) -> None:
        http = plugin.settings.http
        self.plugin = plugin
        self.stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(http.max_connections)
        self.guard = ClientGuard(
            rate_per_second=http.rate_limit_per_second,
            burst=http.rate_limit_burst,
            per_ip_connections=http.per_ip_connections,
            temporary_block_seconds=30,
            max_tracked_clients=4096,
        )
        super().__init__(address, APIHandler)

    def process_request(self, request: socket.socket, client_address: tuple) -> None:
        if not self._slots.acquire(blocking=False):
            self._reject(request, HTTPStatus.SERVICE_UNAVAILABLE, 1)
            return
        ip = str(client_address[0])
        accepted, retry = self.guard.acquire(ip)
        if not accepted:
            self._slots.release()
            self._reject(request, HTTPStatus.TOO_MANY_REQUESTS, retry)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.guard.release(ip)
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.guard.release(str(client_address[0]))
            self._slots.release()

    def _reject(self, request: socket.socket, code: HTTPStatus, retry: int) -> None:
        body = encode({
            "type": "about:blank", "title": code.phrase, "status": int(code)
        }, "application/problem+json").body
        cors = ("Access-Control-Allow-Origin: *\r\n"
                if "*" in self.plugin.settings.http.cors_origins else "")
        response = (
            f"HTTP/1.0 {code.value} {code.phrase}\r\n"
            "Content-Type: application/problem+json\r\n"
            f"Content-Length: {len(body)}\r\nRetry-After: {max(1, retry)}\r\n"
            f"{cors}Connection: close\r\n\r\n"
        ).encode("ascii") + body
        with suppress(OSError):
            request.settimeout(0.2)
            request.sendall(response)
        with suppress(OSError):
            request.shutdown(socket.SHUT_RDWR)
        self.close_request(request)

    def handle_error(self, request: socket.socket, client_address: tuple) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, OSError) and (exc.errno in _DISCONNECTS or self.stopping.is_set()):
            return
        self.plugin.logger.error(
            "Status API HTTP request error:\n" +
            "".join(traceback.format_exception(*sys.exc_info())).rstrip()
        )


class APIHandler(BaseHTTPRequestHandler):
    server: APIHTTPServer
    server_version = "EndstoneStatusAPI/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def do_GET(self) -> None:
        self._dispatch(head=False)

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_OPTIONS(self) -> None:
        try:
            path = urlsplit(self.path).path
        except ValueError:
            return self._problem(HTTPStatus.BAD_REQUEST, head=False)
        if not self._enabled_route(path):
            return self._problem(HTTPStatus.NOT_FOUND, head=False)
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain", head=True,
                   extra={"Allow": "GET, HEAD, OPTIONS",
                          "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                          "Access-Control-Allow-Headers": "If-None-Match",
                          "Access-Control-Max-Age": "600"})

    def do_POST(self) -> None:
        self._problem(HTTPStatus.METHOD_NOT_ALLOWED, head=False, extra={"Allow": "GET, HEAD, OPTIONS"})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_TRACE = do_POST
    do_CONNECT = do_POST

    def _enabled_route(self, path: str) -> bool:
        if path not in _ROUTES:
            return False
        name = _ENDPOINTS.get(path)
        return name is None or getattr(self.server.plugin.settings.endpoints, name)

    def _dispatch(self, *, head: bool) -> None:
        if len(self.path) > 2048:
            return self._problem(HTTPStatus.REQUEST_URI_TOO_LONG, head=head)
        if self.headers.get("Content-Length") not in (None, "", "0"):
            return self._problem(HTTPStatus.BAD_REQUEST, head=head)
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
        except ValueError:
            return self._problem(HTTPStatus.BAD_REQUEST, head=head)
        if not self._enabled_route(path):
            return self._problem(HTTPStatus.NOT_FOUND, head=head)
        api = self.server.plugin
        if path == "/healthz":
            healthy = api.running and api.state.is_fresh(api.max_stale_seconds)
            self._json(HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
                       encode({"ok": healthy}), head=head, cache="no-store")
            return
        if path == "/openapi.json":
            self._json(HTTPStatus.OK, api.openapi, head=head)
            return
        if path == "/api/v1/info":
            self._json(HTTPStatus.OK, api.info, head=head)
            return
        if not api.running or not api.state.is_fresh(api.max_stale_seconds):
            return self._problem(HTTPStatus.SERVICE_UNAVAILABLE, head=head,
                                 detail="No fresh server snapshot is available")
        if path == "/api/v1/chat":
            try:
                params = parse_qs(parsed.query, keep_blank_values=True)
                if set(params) - {"limit"} or len(params.get("limit", [])) > 1:
                    raise ValueError("unsupported query parameters")
                raw = params.get("limit", ["20"])[0]
                if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 10000:
                    raise ValueError("limit must be a positive decimal integer")
                limit = min(int(raw), api.settings.chat.max_messages)
            except ValueError:
                return self._problem(HTTPStatus.BAD_REQUEST, head=head,
                                     detail="Invalid chat limit (expected positive integer)")
            self._json(HTTPStatus.OK, api.state.chat(limit), head=head)
            return
        result = api.state.get(path)
        if result is None:
            return self._problem(HTTPStatus.SERVICE_UNAVAILABLE, head=head)
        self._json(HTTPStatus.OK, result, head=head)

    def _json(self, code: HTTPStatus, result: Encoded, *, head: bool, cache: str = "no-cache") -> None:
        if code == HTTPStatus.OK and self.headers.get("If-None-Match") == result.etag:
            self._send(HTTPStatus.NOT_MODIFIED, b"", result.content_type, head=True,
                       cache=cache, extra={"ETag": result.etag})
        else:
            self._send(code, result.body, result.content_type, head=head,
                       cache=cache, extra={"ETag": result.etag})

    def _problem(self, code: HTTPStatus, *, head: bool, detail: str | None = None,
                 extra: dict[str, str] | None = None) -> None:
        payload: dict[str, object] = {
            "type": "about:blank", "title": code.phrase, "status": code.value
        }
        if detail:
            payload["detail"] = detail
        result = encode(payload, "application/problem+json")
        self._send(code, result.body, result.content_type, head=head, cache="no-store", extra=extra)

    def _send(self, code: HTTPStatus, body: bytes, content_type: str, *, head: bool,
              cache: str = "no-cache", extra: dict[str, str] | None = None) -> None:
        try:
            self.send_response(code.value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            origin = self.headers.get("Origin")
            allowed = self.server.plugin.settings.http.cors_origins
            if "*" in allowed:
                self.send_header("Access-Control-Allow-Origin", "*")
            elif origin and origin in allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if extra:
                for name, value in extra.items():
                    self.send_header(name, value)
            self.send_header("Access-Control-Expose-Headers", "ETag, Retry-After")
            self.end_headers()
            if not head and body:
                self.wfile.write(body)
        except OSError as exc:
            if exc.errno in _DISCONNECTS:
                self.close_connection = True
            else:
                raise

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._problem(HTTPStatus(code) if code in HTTPStatus._value2member_map_ else HTTPStatus.BAD_REQUEST,
                      head=False)

    def log_message(self, format: str, *args: object) -> None:
        return
