"""Bounded, privacy-preserving HTTP telemetry for the owner's live console.

Only method-independent outcomes and duration are retained in process memory.
No paths, headers, IPs, bodies or identities. History resets at each deployment.
"""
from collections import deque
from datetime import UTC, datetime, timedelta


class RequestTelemetry:
    def __init__(self, capacity: int = 2000):
        self.started_at = datetime.now(UTC)
        self._samples = deque(maxlen=capacity)

    def record(self, status: int, elapsed_ms: float) -> None:
        if not isinstance(status, int):
            return
        self._samples.append((datetime.now(UTC), status, max(0, float(elapsed_ms))))

    def snapshot(self) -> dict:
        now = datetime.now(UTC)
        window = now - timedelta(minutes=15)
        rows = [(at, status, elapsed) for at, status, elapsed in self._samples if at >= window]
        elapsed = sorted(value for _, _, value in rows)

        def percentile(fraction):
            if not elapsed:
                return None
            return round(elapsed[min(len(elapsed) - 1, int((len(elapsed) - 1) * fraction))])

        labels = [(now.replace(second=0, microsecond=0) - timedelta(minutes=i))
                  for i in range(14, -1, -1)]
        counts = {minute.isoformat(): [0, 0] for minute in labels}
        for at, status, _ in rows:
            key = at.replace(second=0, microsecond=0).isoformat()
            if key in counts:
                counts[key][0] += 1
                if status >= 500:
                    counts[key][1] += 1
        return {
            "started_at": self.started_at.isoformat(),
            "window_minutes": 15,
            "sample_count": len(rows),
            "p50_ms": percentile(0.5),
            "p95_ms": percentile(0.95),
            "errors_5xx": sum(status >= 500 for _, status, _ in rows),
            "errors_4xx": sum(400 <= status < 500 for _, status, _ in rows),
            "requests": [
                {"minute": label.isoformat(), "count": counts[label.isoformat()][0],
                 "errors_5xx": counts[label.isoformat()][1]}
                for label in labels
            ],
            "note": "Только запросы API за 15 минут в памяти текущего сервера. "
                    "При перезапуске история начинается заново; это не uptime.",
        }
