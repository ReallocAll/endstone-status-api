from __future__ import annotations

import hashlib
import math
import os
import re
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from importlib import metadata, resources
from pathlib import Path
from typing import Any, ClassVar

import psutil
from endstone.event import EventPriority, PlayerChatEvent, event_handler
from endstone.plugin import Plugin

from .config import load_config
from .http import APIHTTPServer
from .metrics import SystemMetricsCollector
from .spark_papi import SparkPapiClient
from .state import APIState, Encoded, encode

_COLORS = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0.0, result) if math.isfinite(result) else None


def ping_ms(value: Any) -> int | None:
    try:
        return max(0, min(9999, int(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def clean(value: object, length: int) -> str:
    return " ".join(_COLORS.sub("", str(value)).split())[:length]


class StatusAPI(Plugin):
    api_version = "0.11"
    soft_depend: ClassVar[list[str]] = ["papi"]

    def on_load(self) -> None:
        folder = Path(str(self.data_folder)) if self.data_folder else Path("plugins/status_api")
        self.settings, self._config_error = load_config(folder)
        self.state = APIState(self.settings.chat.max_messages)
        self.running = False
        self._http: APIHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._task: Any | None = None
        self._metrics: SystemMetricsCollector | None = None
        self._spark = SparkPapiClient()
        self._history: deque[dict[str, Any]] = deque(
            maxlen=(self.settings.history.seconds * 2 + 2)
        )
        self.max_stale_seconds = max(5.0, self.settings.status.snapshot_interval_ticks / 20.0 * 4)
        try:
            self._process_started = psutil.Process(os.getpid()).create_time()
        except (OSError, psutil.Error):
            self._process_started = time.time()
        self.info = encode({
            "api_version": "v1",
            "server": {
                "name": self.settings.server.name,
                "address": self.settings.server.address or None,
            },
            "endpoints": {
                name: getattr(self.settings.endpoints, name)
                for name in ("status", "players", "history", "chat")
            },
        })
        self.openapi = self._read_openapi()
        self._versions = self._read_versions()

    @staticmethod
    def _read_versions() -> dict[str, str | None]:
        try:
            installed = metadata.version("endstone")
        except metadata.PackageNotFoundError:
            installed = None
        return {"endstone": installed}

    @staticmethod
    def _read_openapi() -> Encoded:
        raw = resources.files("endstone_status_api").joinpath("openapi.json").read_bytes()
        return Encoded(raw, '"' + hashlib.sha256(raw).hexdigest()[:24] + '"',
                       "application/json; charset=utf-8")

    def on_enable(self) -> None:
        if self._config_error:
            self.logger.error(self._config_error)
            return
        if not self.settings.http.enabled:
            self.logger.info("Status API is disabled by configuration")
            return
        http = self.settings.http
        try:
            self._http = APIHTTPServer((http.host, http.port), self)
            if self.settings.status.include_process_metrics or self.settings.status.include_system_metrics:
                self._metrics = SystemMetricsCollector(2.0, disk_path=Path.cwd())
                self._metrics.start()
            if self.settings.endpoints.chat:
                self.register_events(self)
            self.running = True
            self._task = self.server.scheduler.run_task(
                self, self.update_snapshot,
                delay=0, period=self.settings.status.snapshot_interval_ticks
            )
            # The first snapshot is generated synchronously on the Endstone thread.
            self.update_snapshot()
            self._thread = threading.Thread(
                target=self._serve_http, name="status-api-http", daemon=True
            )
            self._thread.start()
            self.logger.info(f"Status API listening on {http.host}:{http.port}")
        except Exception as exc:
            self.logger.error(f"Cannot enable Status API: {type(exc).__name__}: {exc}")
            self.on_disable()

    def _serve_http(self) -> None:
        http = self._http
        if http is None:
            return
        try:
            http.serve_forever(poll_interval=0.25)
        except Exception as exc:
            if not http.stopping.is_set():
                self.logger.error(f"Status API HTTP thread stopped: {exc}")

    def on_disable(self) -> None:
        self.running = False
        task, self._task = self._task, None
        if task is not None:
            with suppress(Exception):
                task.cancel()
        http, self._http = self._http, None
        thread, self._thread = self._thread, None
        if http is not None:
            http.stopping.set()
            if thread is not None and thread.is_alive():
                http.shutdown()
                thread.join(timeout=3)
                if thread.is_alive():
                    self.logger.warning("Status API HTTP thread did not stop within 3 seconds")
            http.server_close()
        metrics, self._metrics = self._metrics, None
        if metrics is not None and not metrics.stop(timeout=3):
            self.logger.warning("Status API metrics collector did not stop")
        self._spark.close()

    def update_snapshot(self) -> None:
        """Must be called on the Endstone server thread; HTTP workers see only cached JSON."""
        if not self.running:
            return
        try:
            conf = self.settings
            now = timestamp()
            players = list(self.server.online_players)
            online = len(players)
            tps = number(self.server.average_tps)
            mspt = number(self.server.average_mspt)
            try:
                max_players = max(0, int(getattr(self.server, "max_players", 0)))
            except (TypeError, ValueError):
                max_players = 0
            server = {
                "online": True,
                "players_online": online,
                "players_max": max_players,
                "uptime_seconds": max(0, int(time.time() - self._process_started)),
                "performance": {
                    "tps": round(tps, 3) if tps is not None else None,
                    "mspt": round(mspt, 3) if mspt is not None else None,
                },
            }
            if conf.status.include_versions:
                server["versions"] = {
                    "minecraft": clean(getattr(self.server, "minecraft_version", ""), 80) or None,
                    **self._versions,
                }
            if conf.status.include_spark_metrics:
                spark = self._spark.read(self.server)
                server["performance"]["spark"] = spark.to_dict()
            metrics = self._metrics.get_snapshot().to_dict() if self._metrics else None
            if metrics is not None:
                if conf.status.include_process_metrics:
                    server["process"] = {
                        "cpu_percent": metrics["process_cpu_percent"],
                        "rss_bytes": metrics["process_rss_bytes"],
                    }
                if conf.status.include_system_metrics:
                    server["system"] = {
                        key: value for key, value in metrics.items()
                        if key not in ("process_cpu_percent", "process_rss_bytes")
                    }

            published: dict[str, Any] = {}
            if conf.endpoints.status:
                published["/api/v1/status"] = {
                    "api_version": "v1", "sampled_at": now, "server": server
                }
            if conf.endpoints.players:
                published["/api/v1/players"] = {
                    "api_version": "v1", "sampled_at": now,
                    "players": sorted([
                        {
                            "name": clean(player.name, 64),
                            "ping_ms": ping_ms(getattr(player, "ping", None)),
                        }
                        for player in players
                    ], key=lambda row: row["name"].casefold()),
                }
            if conf.endpoints.history:
                point = {
                    "timestamp": now,
                    "tps": server["performance"]["tps"],
                    "mspt": server["performance"]["mspt"],
                    "players_online": online,
                }
                self._history.append(point)
                cutoff = time.time() - conf.history.seconds
                # RFC3339 UTC timestamps sort lexicographically at fixed millisecond precision.
                cutoff_text = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                while self._history and self._history[0]["timestamp"] < cutoff_text:
                    self._history.popleft()
                published["/api/v1/history"] = {
                    "api_version": "v1", "sampled_at": now,
                    "history_seconds": conf.history.seconds,
                    "points": list(self._history),
                }
            self.state.publish(published)
        except Exception as exc:
            self.logger.error(f"Status API snapshot failed: {type(exc).__name__}: {exc}")

    @event_handler(priority=EventPriority.MONITOR, ignore_cancelled=True)
    def on_player_chat(self, event: PlayerChatEvent) -> None:
        if not self.running or not self.settings.endpoints.chat:
            return
        if event.is_cancelled or event.is_asynchronous:
            return
        # Preserve only synchronous messages addressed to all currently online players.
        online = {str(p.unique_id) for p in self.server.online_players}
        recipients = {str(p.unique_id) for p in event.recipients}
        if not online or not online.issubset(recipients):
            return
        self.state.add_chat({
            "timestamp": timestamp(),
            "player": clean(event.player.name, 64),
            "message": clean(event.message, 512),
        })
