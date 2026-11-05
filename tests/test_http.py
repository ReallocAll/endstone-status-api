from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from endstone_status_api.config import APIConfig, EndpointsConfig
from endstone_status_api.http import APIHTTPServer
from endstone_status_api.state import APIState, encode


class HTTPTest(unittest.TestCase):
    def setUp(self):
        state = APIState(3)
        state.publish({
            "/api/v1/status": {
                "api_version": "v1",
                "sampled_at": "2026-11-05T00:00:00Z",
                "server": {"online": True},
            }
        })
        for index in range(4):
            state.add_chat({"timestamp": "2026-11-05T00:00:00Z", "player": "Test",
                            "message": f"msg{index}"})
        self.api = SimpleNamespace(
            settings=APIConfig(endpoints=EndpointsConfig(chat=True)),
            state=state,
            running=True,
            max_stale_seconds=60.0,
            info=encode({"api_version": "v1", "endpoints": {"chat": True}}),
            openapi=encode({"openapi": "3.1.0"}),
            logger=SimpleNamespace(error=lambda *_: None),
        )
        self.http = APIHTTPServer(("127.0.0.1", 0), self.api)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.http.server_port)

    def tearDown(self):
        self.api.running = False
        self.http.stopping.set()
        self.http.shutdown()
        self.thread.join(timeout=3)
        self.http.server_close()

    def fetch(self, route, method="GET", headers=None):
        request = Request(self.url + route, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_status_cors_etag_and_head(self):
        status, headers, body = self.fetch("/api/v1/status", headers={"Origin": "https://site.example"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertTrue(json.loads(body)["server"]["online"])
        etag = headers["ETag"]
        self.assertEqual(self.fetch("/api/v1/status", headers={"If-None-Match": etag})[0], 304)
        status, headers, body = self.fetch("/api/v1/status", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_disabled_endpoint_and_errors(self):
        code, headers, data = self.fetch("/api/v1/players")
        self.assertEqual(code, 404)
        self.assertEqual(headers["Content-Type"], "application/problem+json")
        self.assertEqual(json.loads(data)["status"], 404)
        self.assertEqual(self.fetch("/api/v1/chat?limit=-1")[0], 400)
        self.assertEqual(self.fetch("/api/v1/chat?limit=abc")[0], 400)
        self.assertEqual(self.fetch("/api/v1/chat?x=1")[0], 400)
        self.assertEqual(self.fetch("/api/v1/status", method="POST")[0], 405)

    def test_chat_retention_limit_and_options(self):
        code, _, body = self.fetch("/api/v1/chat?limit=200")
        self.assertEqual(code, 200)
        result = json.loads(body)
        self.assertEqual(result["limit"], 100)
        # Retention is independently bounded by the configured deque capacity.
        self.assertEqual([v["message"] for v in result["messages"]], ["msg1", "msg2", "msg3"])
        code, headers, body = self.fetch("/api/v1/chat", method="OPTIONS",
                                         headers={"Origin": "https://example.com",
                                                  "Access-Control-Request-Method": "GET"})
        self.assertEqual(code, 204)
        self.assertEqual(headers["Access-Control-Allow-Methods"], "GET, HEAD, OPTIONS")

    def test_stale_snapshot_is_unavailable_not_offline(self):
        self.api.max_stale_seconds = -1
        self.assertEqual(self.fetch("/healthz")[0], 503)
        code, _, body = self.fetch("/api/v1/status")
        self.assertEqual(code, 503)
        self.assertNotIn(b'"online":false', body)
