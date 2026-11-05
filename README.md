# Endstone Status API

[简体中文](README_ZH.md) · [In-game HUD](https://github.com/ReallocAll/endstone-show-status)

Public read-only HTTP JSON API for [Endstone](https://github.com/EndstoneMC/endstone) Minecraft Bedrock servers. No authentication, database, bundled website or in-game HUD. A static web frontend, bot, mobile app or monitoring service can consume the same documented v1 contract. Server administrators decide what is public.

## Install

Python 3.10+ and Endstone 0.11+ required; `psutil>=5` is installed with the package. Install the published wheel into the **Python environment used by Endstone**:

```bash
python -m pip install ./endstone_status_api-0.1.0-py3-none-any.whl
```

Restart Endstone after installation. Default bind is **127.0.0.1:19134**, not a public Internet address. Set up HTTPS via a reverse proxy/Tunnel, or deliberately use `host = "0.0.0.0"` if direct HTTP exposure is desired. Avoid running the old show-status HTTP listener on the same port.

## Configuration

Created at `plugins/status_api/config.toml` on first start:

```toml
[http]
enabled = true
host = "127.0.0.1"
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
```

Invalid or unknown TOML fields prevent API startup; no invalid configuration is silently replaced. Changes take effect after restarting/reloading the plugin. If disabled, a business endpoint returns HTTP 404 (not an empty payload). `/api/v1/info` advertises enabled features.

All enabled information is **unauthenticated and public**. Chat, player names and host metrics are opt-in. The `/chat?limit=20` default requests 20 messages; the administrator's `max_messages` is the in-memory retention and per-response ceiling (0–500; must be positive if chat is enabled). Messages are in chronological order, reset on restart, and have no persistence or pagination.

Chat observes Endstone's synchronous MONITOR event and only includes non-cancelled broadcasts delivered to every currently online player. It cannot observe later Script API moderation/cancellation; keep `chat=false` on servers with such downstream moderation unless that limitation is acceptable. No UUID, IP address or device IDs are published.

## Endpoints

| HTTP route | Response | Default |
|---|---|---|
| `GET /api/v1/info` | API and exposure discovery | Always |
| `GET /api/v1/status` | Server state, counts, base TPS/MSPT, optional metrics | On |
| `GET /api/v1/players` | Public online names and ping | Off |
| `GET /api/v1/history` | In-memory rolling TPS/MSPT/player counts | On |
| `GET /api/v1/chat?limit=20` | Recent public chat | Off |
| `GET /healthz` | Snapshot health, HTTP 200/503 | Always |
| `GET /openapi.json` | Machine-readable OpenAPI 3.1 contract | Always |

All routes accept HEAD; OPTIONS supports CORS preflight. HTTP error bodies follow `application/problem+json`. Snapshot responses include `sampled_at` (RFC 3339 UTC), optional fields are omitted when not exposed, unavailable numeric metrics are `null`, and base TPS/MSPT always use Endstone averages. Spark values are separately named. `If-None-Match` / ETag returns HTTP 304 when unchanged. Stale snapshots return 503 rather than fabricated offline data. Basic rate limiting, per-IP concurrency limits and temporary blocks protect the HTTP listener; 429 includes Retry-After.

CORS is `*` by default because the API carries no credentials. Custom exact origins are supported (e.g. `["https://example.com"]`). Use native HTTP caching, not browser credential mode. `GET /api/v1/info` is also a practical first endpoint for dynamically hiding disabled widgets.

This is a **new v1 protocol**; legacy `/data`, old show-status JSON structures and the bundled UI are intentionally absent.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m build --wheel
```

## License

GPL-3.0-only.
