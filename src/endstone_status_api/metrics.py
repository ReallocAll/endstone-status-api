from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    sampled_at_ms: int = 0
    process_cpu_percent: float = 0.0
    process_rss_bytes: int = 0
    logical_cpu_count: int = 1
    cpu_average_percent: float = 0.0
    cpu_max_core_percent: float = 0.0
    memory_used_bytes: int = 0
    memory_total_bytes: int = 0
    memory_percent: float = 0.0
    disk_used_bytes: int = 0
    disk_total_bytes: int = 0
    disk_percent: float = 0.0
    network_receive_bytes_per_second: float = 0.0
    network_send_bytes_per_second: float = 0.0

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


class SystemMetricsCollector:
    """Collects psutil metrics away from the Endstone/BDS main thread."""

    def __init__(self, interval_seconds: float, *, disk_path: Path | None = None) -> None:
        self.interval_seconds = interval_seconds
        self.disk_path = disk_path or Path.cwd()
        self._process = psutil.Process(os.getpid())
        self._snapshot = SystemSnapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_network = None
        self._last_network_time = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            psutil.cpu_percent(percpu=True)
            self._process.cpu_percent()
        except (OSError, psutil.Error):
            pass
        self._thread = threading.Thread(target=self._run, name="show-status-metrics", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def get_snapshot(self) -> SystemSnapshot:
        with self._lock:
            return self._snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                snapshot = self._collect(started)
            except Exception:
                # Keep the collector alive if a platform-specific psutil call fails.
                snapshot = None
            if snapshot is not None:
                with self._lock:
                    self._snapshot = snapshot
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.1, self.interval_seconds - elapsed))

    def _collect(self, now: float) -> SystemSnapshot:
        cpu_per_core = list(psutil.cpu_percent(percpu=True))
        cpu_average = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else 0.0
        cpu_max = max(cpu_per_core, default=0.0)

        process_cpu = float(self._process.cpu_percent())
        process_rss = int(self._process.memory_info().rss)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.disk_path))

        network = psutil.net_io_counters()
        receive_rate = 0.0
        send_rate = 0.0
        if network is not None and self._last_network is not None and self._last_network_time > 0:
            seconds = max(0.001, now - self._last_network_time)
            receive_rate = max(0.0, (network.bytes_recv - self._last_network.bytes_recv) / seconds)
            send_rate = max(0.0, (network.bytes_sent - self._last_network.bytes_sent) / seconds)
        self._last_network = network
        self._last_network_time = now

        return SystemSnapshot(
            sampled_at_ms=int(time.time() * 1000),
            process_cpu_percent=round(process_cpu, 2),
            process_rss_bytes=process_rss,
            logical_cpu_count=max(1, int(psutil.cpu_count(logical=True) or 1)),
            cpu_average_percent=round(cpu_average, 2),
            cpu_max_core_percent=round(cpu_max, 2),
            memory_used_bytes=int(memory.used),
            memory_total_bytes=int(memory.total),
            memory_percent=round(float(memory.percent), 2),
            disk_used_bytes=int(disk.used),
            disk_total_bytes=int(disk.total),
            disk_percent=round(float(disk.percent), 2),
            network_receive_bytes_per_second=round(receive_rate, 2),
            network_send_bytes_per_second=round(send_rate, 2),
        )
