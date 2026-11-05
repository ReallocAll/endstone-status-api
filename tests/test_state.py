from __future__ import annotations

import json
import unittest

from endstone_status_api.state import APIState, encode


class StateTest(unittest.TestCase):
    def test_freshness_and_atomic_publish(self):
        state = APIState(2)
        self.assertFalse(state.is_fresh(5))
        state.publish({"/api/v1/status": {"x": 1}})
        self.assertTrue(state.is_fresh(5))
        self.assertEqual(json.loads(state.get("/api/v1/status").body)["x"], 1)
        self.assertIsNone(state.get("/api/v1/players"))
        state.publish({"/api/v1/players": {"players": []}})
        self.assertIsNone(state.get("/api/v1/status"))

    def test_chat_is_bounded_and_immutable(self):
        state = APIState(2)
        for index in range(4):
            state.add_chat({"player": "p", "message": str(index)})
        response = state.chat(20)
        self.assertEqual([row["message"] for row in json.loads(response.body)["messages"]], ["2", "3"])
        self.assertEqual(response.etag, state.chat(20).etag)
        state.add_chat({"player": "p", "message": "4"})
        self.assertNotEqual(response.etag, state.chat(20).etag)

    def test_json_rejects_nonfinite_numbers(self):
        with self.assertRaises(ValueError):
            encode({"bad": float("nan")})
