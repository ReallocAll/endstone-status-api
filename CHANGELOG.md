# Changelog

## 0.1.0 (2026-11-05)

- Initial independent public read-only Status API plugin, split from endstone-show-status.
- New versioned JSON contract and OpenAPI specification; old HTTP protocol intentionally not supported.
- Granular endpoint switches, optional public chat with bounded message history, and exposure-controlled metrics.
- HTTP CORS, HEAD/OPTIONS, ETag/304, request concurrency/rate limits, and shutdown handling.
