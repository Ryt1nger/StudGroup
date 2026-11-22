"""Privacy-safe per-request database timing for bounded API diagnostics."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class DatabaseTrace:
    query_count: int = 0
    total_ms: float = 0.0


_active_trace: ContextVar[DatabaseTrace | None] = ContextVar(
    "studgroup_request_database_trace", default=None
)


def start_database_trace() -> tuple[DatabaseTrace, Token[DatabaseTrace | None]]:
    """Start a request-local trace without recording SQL text or parameters."""
    trace = DatabaseTrace()
    return trace, _active_trace.set(trace)


def finish_database_trace(token: Token[DatabaseTrace | None]) -> None:
    _active_trace.reset(token)


def install_database_telemetry(engine: AsyncEngine) -> None:
    """Attach timing hooks to one engine; listeners retain no request data."""

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
        if _active_trace.get() is not None:
            context._studgroup_query_started_at = perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def after_cursor_execute(_conn, _cursor, _statement, _parameters, context, _executemany):
        trace = _active_trace.get()
        started_at = getattr(context, "_studgroup_query_started_at", None)
        if trace is None or started_at is None:
            return
        trace.query_count += 1
        trace.total_ms += (perf_counter() - started_at) * 1000
