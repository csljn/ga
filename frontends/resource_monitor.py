"""System resource monitor — CPU / memory / disk usage with historical sampling.

Uses `psutil` to collect resource stats every N seconds in a background daemon
thread, keeping a bounded ring-buffer of recent data points.  Exposes a simple
API for dashboards (JSON-friendly) and integrates with the cost_tracker bridge.

Architecture mirrors cost_tracker.py: thread-safe, dataclass-based, singleton
background thread.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None  # graceful degrade — monitoring simply won't start

import os  # for platform-aware disk path

# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class ResourceStats:
    """A single resource snapshot."""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_DEFAULT_INTERVAL = 5.0   # seconds between samples
_DEFAULT_HISTORY = 100    # max data points retained

_lock = threading.Lock()
_history: list[ResourceStats] = []
_collector_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Collector loop
# ---------------------------------------------------------------------------

def _collect_once() -> ResourceStats:
    """Collect one resource snapshot. Returns a zero-filled stats if psutil
    is unavailable."""
    if psutil is None:
        return ResourceStats(timestamp=time.time())

    cpu = psutil.cpu_percent(interval=0)  # non-blocking, uses last measurement
    mem = psutil.virtual_memory()
    # Use current dir for cross-platform compatibility ( "/" fails on Windows )
    disk = psutil.disk_usage(os.getcwd())

    return ResourceStats(
        cpu_percent=cpu,
        memory_percent=mem.percent,
        memory_used_mb=round(mem.used / (1024 * 1024), 2),
        memory_total_mb=round(mem.total / (1024 * 1024), 2),
        disk_percent=disk.percent,
        disk_used_gb=round(disk.used / (1024 ** 3), 2),
        disk_total_gb=round(disk.total / (1024 ** 3), 2),
        timestamp=time.time(),
    )


def _collector_loop(interval: float, max_history: int) -> None:
    """Background daemon: sample resources and push into ring-buffer."""
    # Prime the CPU percent counter so first reading is meaningful.
    if psutil is not None:
        psutil.cpu_percent(interval=0)

    while not _stop_event.wait(timeout=interval):
        sample = _collect_once()
        with _lock:
            _history.append(sample)
            # Trim to max_history (ring-buffer).
            if len(_history) > max_history:
                del _history[: len(_history) - max_history]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_monitoring(interval: float = _DEFAULT_INTERVAL,
                     max_history: int = _DEFAULT_HISTORY) -> bool:
    """Start the background collector.  Returns True on success, False if
    already running or psutil is unavailable."""
    global _collector_thread
    if _collector_thread is not None and _collector_thread.is_alive():
        return False  # already running
    if psutil is None:
        return False  # can't monitor without psutil

    _stop_event.clear()
    _collector_thread = threading.Thread(
        target=_collector_loop,
        args=(interval, max_history),
        name="resource-monitor",
        daemon=True,
    )
    _collector_thread.start()
    return True


def stop_monitoring() -> None:
    """Signal the collector to stop and wait for it."""
    _stop_event.set()
    if _collector_thread is not None and _collector_thread.is_alive():
        _collector_thread.join(timeout=3.0)


def is_monitoring() -> bool:
    """Check whether the collector thread is alive."""
    return _collector_thread is not None and _collector_thread.is_alive()


def get_current() -> ResourceStats:
    """Return an immediate (non-cached) resource snapshot."""
    return _collect_once()


def get_history(count: int | None = None) -> list[ResourceStats]:
    """Return recent history (newest last).  Pass `count` to limit."""
    with _lock:
        if count is None:
            return list(_history)
        return list(_history[-count:])


def get_history_json(count: int | None = None) -> str:
    """Return history as a JSON string — convenient for dashboard endpoints."""
    return json.dumps(
        {"samples": [s.to_dict() for s in get_history(count)]},
        ensure_ascii=False,
    )


def get_dashboard_json() -> str:
    """Single-call endpoint: current snapshot + full history, as JSON."""
    current = get_current()
    return json.dumps(
        {
            "current": current.to_dict(),
            "history": [s.to_dict() for s in get_history()],
            "monitoring": is_monitoring(),
            "psutil_available": psutil is not None,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Auto-start helper (opt-in)
# ---------------------------------------------------------------------------

def auto_start(interval: float = _DEFAULT_INTERVAL,
               max_history: int = _DEFAULT_HISTORY) -> None:
    """Convenience: start monitoring only if psutil is installed and the
    collector is not yet running.  Safe to call multiple times."""
    if psutil is not None and not is_monitoring():
        start_monitoring(interval, max_history)
