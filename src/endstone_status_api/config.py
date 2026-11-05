from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


@dataclass(frozen=True, slots=True)
class HTTPConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 19134
    cors_origins: tuple[str, ...] = ("*",)
    max_connections: int = 64
    per_ip_connections: int = 6
    rate_limit_per_second: float = 10.0
    rate_limit_burst: int = 40


@dataclass(frozen=True, slots=True)
class EndpointsConfig:
    status: bool = True
    players: bool = False
    history: bool = True
    chat: bool = False


@dataclass(frozen=True, slots=True)
class StatusConfig:
    snapshot_interval_ticks: int = 20
    include_versions: bool = True
    include_process_metrics: bool = False
    include_system_metrics: bool = False
    include_spark_metrics: bool = False


@dataclass(frozen=True, slots=True)
class HistoryConfig:
    seconds: int = 300


@dataclass(frozen=True, slots=True)
class ChatConfig:
    max_messages: int = 100


@dataclass(frozen=True, slots=True)
class ServerConfig:
    name: str = "Bedrock Server"
    address: str = ""


@dataclass(frozen=True, slots=True)
class APIConfig:
    http: HTTPConfig = HTTPConfig()
    endpoints: EndpointsConfig = EndpointsConfig()
    status: StatusConfig = StatusConfig()
    history: HistoryConfig = HistoryConfig()
    chat: ChatConfig = ChatConfig()
    server: ServerConfig = ServerConfig()


_DEFAULT = """# Public read-only Status API. Anyone can access enabled endpoints.
[http]
enabled = true
host = "127.0.0.1" # bind to 0.0.0.0 only when direct exposure is intended
port = 19134
cors_origins = ["*"]
max_connections = 64
per_ip_connections = 6
rate_limit_per_second = 10.0
rate_limit_burst = 40

[endpoints]
status = true
players = false
history = true
chat = false

[status]
snapshot_interval_ticks = 20
include_versions = true
include_process_metrics = false
include_system_metrics = false
include_spark_metrics = false

[history]
seconds = 300

[chat]
max_messages = 100

[server]
name = "Bedrock Server"
address = ""
"""


def _parse_section(name: str, data: Any, cls: type) -> Any:
    if type(data) is not dict:
        raise ValueError(f"[{name}] must be a table")
    original = cls()
    valid = {field.name for field in fields(original)}
    if unknown := set(data) - valid:
        raise ValueError(f"unknown [{name}] fields: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for field in fields(original):
        if field.name not in data:
            continue
        value, default = data[field.name], getattr(original, field.name)
        if isinstance(default, tuple):
            if type(value) is not list or not all(type(origin) is str for origin in value):
                raise ValueError(f"[{name}].{field.name} must be an array of strings")
            value = tuple(value)
        elif type(default) is float:
            if type(value) not in (int, float):
                raise ValueError(f"[{name}].{field.name} must be a number")
            value = float(value)
        elif type(value) is not type(default):
            raise ValueError(f"invalid type for [{name}].{field.name}")
        values[field.name] = value
    return cls(**values)


def _validate(config: APIConfig) -> None:
    http = config.http
    if not http.host or not (1 <= http.port <= 65535):
        raise ValueError("invalid HTTP host or port")
    if not (1 <= http.max_connections <= 512) or not (1 <= http.per_ip_connections <= http.max_connections):
        raise ValueError("invalid connection limits")
    if not (0.1 <= http.rate_limit_per_second <= 1000) or not (1 <= http.rate_limit_burst <= 1000):
        raise ValueError("invalid rate limits")
    if not http.cors_origins:
        raise ValueError("cors_origins must not be empty")
    if "*" in http.cors_origins and len(http.cors_origins) != 1:
        raise ValueError("wildcard CORS origin cannot be mixed with explicit origins")
    if any(origin != "*" and (not origin.startswith(("http://", "https://")) or "/" in origin.split("://", 1)[1]) for origin in http.cors_origins):
        raise ValueError("CORS origins must be exact http(s) origins without paths")
    if not 10 <= config.status.snapshot_interval_ticks <= 200:
        raise ValueError("snapshot_interval_ticks must be 10..200")
    if not 60 <= config.history.seconds <= 3600:
        raise ValueError("history.seconds must be 60..3600")
    if not 0 <= config.chat.max_messages <= 500:
        raise ValueError("chat.max_messages must be 0..500")
    if config.endpoints.chat and config.chat.max_messages == 0:
        raise ValueError("chat.max_messages must be positive when chat endpoint is enabled")
    if len(config.server.name) > 128 or len(config.server.address) > 255:
        raise ValueError("server name or address exceeds limit")


def load_config(folder: Path) -> tuple[APIConfig, str | None]:
    path = folder / "config.toml"
    if not path.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(_DEFAULT, encoding="utf-8")
        except OSError as exc:
            return APIConfig(), f"Unable to create API configuration: {exc}"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        if type(data) is not dict:
            raise ValueError("configuration must be a TOML table")
        types = {
            "http": HTTPConfig, "endpoints": EndpointsConfig,
            "status": StatusConfig, "history": HistoryConfig,
            "chat": ChatConfig, "server": ServerConfig,
        }
        if unknown := set(data) - set(types):
            raise ValueError(f"unknown configuration sections: {', '.join(sorted(unknown))}")
        config = APIConfig(**{
            section: _parse_section(section, data.get(section, {}), cls)
            for section, cls in types.items()
        })
        _validate(config)
        return config, None
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return APIConfig(), f"Invalid {path}: {exc}"
