from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any

_COLOR_CODE = re.compile(r"§[0-9a-fk-or]", re.IGNORECASE)


@dataclass(frozen=True)
class SparkPapiMetrics:
    available: bool = False
    tps_5s: float | None = None
    mspt_10s_median: float | None = None
    process_cpu_10s_percent: float | None = None
    system_cpu_1m_percent: float | None = None

    def to_dict(self) -> dict[str, bool | float | None]:
        return asdict(self)


def _plain(value: str) -> str:
    return _COLOR_CODE.sub("", value).strip()


def _finite_nonnegative(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def _parse_tps(value: str) -> float | None:
    return _finite_nonnegative(_plain(value).removeprefix("*"))


def _parse_mspt_median(value: str) -> float | None:
    parts = _plain(value).split("/")
    return _finite_nonnegative(parts[1]) if len(parts) == 4 else None


def _parse_percent(value: str) -> float | None:
    plain = _plain(value)
    if not plain.endswith("%"):
        return None
    parsed = _finite_nonnegative(plain[:-1])
    return parsed if parsed is not None and parsed <= 100.0 else None


class SparkPapiClient:
    def __init__(self) -> None:
        self._service: Any | None = None

    def _active_service(self, server: Any) -> Any | None:
        try:
            from endstone_papi import PlaceholderAPI

            if isinstance(self._service, PlaceholderAPI) and self._service.active:
                return self._service
            self._service = PlaceholderAPI.load(server.service_manager)
            if isinstance(self._service, PlaceholderAPI) and self._service.active:
                return self._service
        except (ImportError, AttributeError, RuntimeError, TypeError):
            pass
        self._service = None
        return None

    def read(self, server: Any) -> SparkPapiMetrics:
        service = self._active_service(server)
        if service is None:
            return SparkPapiMetrics()
        try:
            if not service.is_registered("spark"):
                return SparkPapiMetrics()
            tps = service.set_placeholders(None, "{spark:tps_5s}")
            mspt = service.set_placeholders(None, "{spark:tickduration_10s}")
            process_cpu = service.set_placeholders(None, "{spark:cpu_process_10s}")
            system_cpu = service.set_placeholders(None, "{spark:cpu_system_1m}")
            return SparkPapiMetrics(
                available=True,
                tps_5s=_parse_tps(tps),
                mspt_10s_median=_parse_mspt_median(mspt),
                process_cpu_10s_percent=_parse_percent(process_cpu),
                system_cpu_1m_percent=_parse_percent(system_cpu),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._service = None
            return SparkPapiMetrics()

    def close(self) -> None:
        self._service = None
