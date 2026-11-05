from __future__ import annotations

import json
import inspect
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

from endstone_status_api.config import APIConfig, EndpointsConfig
from endstone_status_api.plugin import StatusAPI
from endstone.event import Event, PlayerChatEvent
from endstone_status_api.state import APIState


class PluginTest(unittest.TestCase):
    def test_chat_handler_uses_real_event_class(self):
        # Endstone Plugin.register_events uses inspect.isclass(annotation).
        annotation = inspect.signature(StatusAPI.on_player_chat).parameters["event"].annotation
        self.assertIs(annotation, PlayerChatEvent)
        self.assertTrue(issubclass(annotation, Event))

    def setup_plugin(self):
        players = [SimpleNamespace(name="Steve", unique_id="uuid-1", ping=55)]
        settings = APIConfig(endpoints=EndpointsConfig(players=True, chat=True))
        return SimpleNamespace(
            settings=settings, running=True, _process_started=time.time()-60,
            server=SimpleNamespace(online_players=players, max_players=30,
                                   average_tps=19.9, average_mspt=14.2,
                                   minecraft_version="1.26"),
            state=APIState(3),
            _metrics=None, _spark=Mock(), _versions={"endstone": "0.11"}, _history=deque(maxlen=602),
            logger=SimpleNamespace(error=Mock())
        )

    def test_snapshot_and_optional_fields(self):
        api = self.setup_plugin()
        StatusAPI.update_snapshot(api)
        api.logger.error.assert_not_called()
        result = json.loads(api.state.get("/api/v1/status").body)
        self.assertEqual(result["server"]["players_online"], 1)
        self.assertEqual(result["server"]["performance"]["tps"], 19.9)
        self.assertNotIn("system", result["server"])
        players = json.loads(api.state.get("/api/v1/players").body)["players"]
        self.assertEqual(players, [{"name": "Steve", "ping_ms": 55}])
        self.assertIsNotNone(api.state.get("/api/v1/history"))

    def test_only_public_synchronous_chat_is_captured(self):
        api = self.setup_plugin()
        player = api.server.online_players[0]
        event = SimpleNamespace(player=player, message="Hello",
                                is_cancelled=False, is_asynchronous=False, recipients=[player])
        StatusAPI.on_player_chat(api, event)
        self.assertEqual(len(json.loads(api.state.chat(10).body)["messages"]), 1)
        event.recipients = []
        StatusAPI.on_player_chat(api, event)
        event.recipients = [player]
        event.is_cancelled = True
        StatusAPI.on_player_chat(api, event)
        event.is_cancelled = False
        event.is_asynchronous = True
        StatusAPI.on_player_chat(api, event)
        self.assertEqual(len(json.loads(api.state.chat(10).body)["messages"]), 1)
