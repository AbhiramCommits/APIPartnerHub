"""In-process request counters and latency percentiles for support engineering.

Counters are per worker process; with multiple uvicorn workers, query each worker (or read
the ``X-Request-ID`` in the access log) to attribute a single request.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

_STARTED_AT = datetime.now(UTC)
_STARTED_MONOTONIC = time.monotonic()
_LOCK = threading.Lock()
_TOTAL = 0
_BY_STATUS_CLASS: dict[str, int] = defaultdict(int)
_BY_AUTH_MODE: dict[str, int] = defaultdict(int)
_RATE_LIMITED = 0
_LATENCIES: deque[float] = deque(maxlen=10_000)
_ENDPOINT_LATENCIES: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=2_000))
_ENDPOINT_COUNTS: dict[str, int] = defaultdict(int)


def record_request(
    *,
    method: str,
    path_template: str,
    status_code: int,
    latency_ms: float,
    auth_mode: str | None,
) -> None:
    endpoint = f"{method.upper()} {path_template}"
    status_class = f"{status_code // 100}xx"
    global _TOTAL, _RATE_LIMITED
    with _LOCK:
        _TOTAL += 1
        _BY_STATUS_CLASS[status_class] += 1
        _BY_AUTH_MODE[auth_mode or "none"] += 1
        if status_code == 429:
            _RATE_LIMITED += 1
        _LATENCIES.append(latency_ms)
        _ENDPOINT_COUNTS[endpoint] += 1
        _ENDPOINT_LATENCIES[endpoint].append(latency_ms)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    weight = rank - lower
    result = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(result, 2)


def _latency_summary(values: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": round(max(values), 2) if values else 0.0,
    }


def snapshot() -> dict[str, Any]:
    with _LOCK:
        total = _TOTAL
        status_classes = dict(_BY_STATUS_CLASS)
        auth_modes = dict(_BY_AUTH_MODE)
        rate_limited = _RATE_LIMITED
        latencies = list(_LATENCIES)
        endpoint_counts = dict(_ENDPOINT_COUNTS)
        endpoint_latencies = {key: list(value) for key, value in _ENDPOINT_LATENCIES.items()}

    endpoints: dict[str, Any] = {}
    for endpoint, count in sorted(endpoint_counts.items(), key=lambda item: -item[1]):
        summary = _latency_summary(endpoint_latencies.get(endpoint, []))
        endpoints[endpoint] = {"count": count, **summary}

    return {
        "started_at": _STARTED_AT.isoformat(),
        "uptime_seconds": round(time.monotonic() - _STARTED_MONOTONIC, 2),
        "requests_total": total,
        "requests_by_status_class": status_classes,
        "rate_limited_total": rate_limited,
        "requests_by_auth_mode": auth_modes,
        "latency_ms": _latency_summary(latencies),
        "endpoints": endpoints,
    }


def reset() -> None:
    """Clear counters (tests only)."""

    global _TOTAL, _RATE_LIMITED
    with _LOCK:
        _TOTAL = 0
        _RATE_LIMITED = 0
        _BY_STATUS_CLASS.clear()
        _BY_AUTH_MODE.clear()
        _LATENCIES.clear()
        _ENDPOINT_LATENCIES.clear()
        _ENDPOINT_COUNTS.clear()
