"""Privacy-safe, bounded per-account chronology for diagnosing CAPTCHA triggers.

The trace uses a closed vocabulary: never accept URL, login, account data,
page HTML, image bytes, cookies or exception details. Time is elapsed since
the account claim, not a user event stream.
"""

import json
import time
from datetime import UTC, datetime

from studgroup.models import RanepaCrawlObservation

STEPS = frozenset(
    {
        "job_claimed",
        "credentials_ready",
        "session_available",
        "session_fresh",
        "browser_started",
        "stored_session_rejected",
        "month_requested",
        "month_verified",
        "week_requested",
        "week_verified",
        "week_retry",
        "week_unreadable",
        "week_month_mismatch",
        "sdo_exits_requested",
        "sdo_exits_finished",
        "captcha_month",
        "captcha_week",
        "captcha_sdo",
        "captcha_solver_started",
        "captcha_solver_submitted",
        "captcha_solver_failed",
        "month_recheck",
        "week_recheck",
        "schedule_verified",
    }
)
MAX_EVENTS = 40
_SAFE_RESULTS = frozenset(
    {
        "succeeded",
        "login_failed",
        "captcha",
        "source_unavailable",
        "identity_unverified",
        "parse_failed",
        "internal",
    }
)


class CrawlTrace:
    """Single-account trace, never shared across worker jobs."""

    def __init__(self, *, clock=None):
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self.events: list[dict] = []
        self.captcha_seen = False
        self.captcha_location: str | None = None
        self.preceding_step: str | None = None
        self.session_available = False
        # Predefined, privacy-safe reason for one incomplete weekly read.
        self.failure_detail: str | None = None

    def record(self, step: str) -> None:
        if step not in STEPS:
            raise ValueError("unsafe_crawl_trace_step")
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
        milliseconds = max(0, int((self._clock() - self._start) * 1000))
        self.events.append({"step": step, "elapsed_ms": milliseconds})

    def mark_captcha(self, location: str) -> None:
        if location not in {"month", "week", "sdo"}:
            raise ValueError("unsafe_crawl_trace_captcha_location")
        if not self.captcha_seen:
            self.captcha_seen = True
            self.captcha_location = location
            self.preceding_step = self.events[-1]["step"] if self.events else None
        self.record("captcha_" + location)

    def observation(self, *, job_id, attempt: int, failure_code: str | None):
        result = "succeeded" if failure_code is None else failure_code
        if result not in _SAFE_RESULTS:
            result = "internal"
        return RanepaCrawlObservation(
            job_id=job_id,
            attempt=attempt,
            observed_at=datetime.now(UTC),
            result_code=result,
            captcha_seen=self.captcha_seen,
            captcha_location=self.captcha_location,
            preceding_step=self.preceding_step,
            session_available=self.session_available,
            elapsed_ms=max(0, int((self._clock() - self._start) * 1000)),
            events_json=json.dumps(self.events, separators=(",", ":")),
        )
