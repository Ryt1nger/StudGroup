"""Allowlisted durable control requests. Never executes commands or claims a worker started."""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from studgroup.models import BackgroundProcess

ProcessName = Literal["ai", "sdo", "telegram_outbox", "scheduler"]
ProcessAction = Literal["enable", "disable", "restart"]
PROCESS_NAMES = ("ai", "sdo", "telegram_outbox", "scheduler")
ACTIONS = ("enable", "disable", "restart")


async def ensure_process(db, name: ProcessName) -> None:
    if name not in PROCESS_NAMES:
        raise ValueError("unknown_process")
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgres_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise RuntimeError("unsupported_process_database")
    await db.execute(
        insert(BackgroundProcess).values(name=name).on_conflict_do_nothing(index_elements=["name"])
    )


async def request_process(db, name: ProcessName, action: ProcessAction) -> BackgroundProcess:
    if name not in PROCESS_NAMES or action not in ACTIONS:
        raise ValueError("invalid_process_request")
    await ensure_process(db, name)
    enabled = action != "disable"
    query = update(BackgroundProcess).where(BackgroundProcess.name == name)
    if action != "restart":
        query = query.where(
            or_(BackgroundProcess.enabled.is_(None), BackgroundProcess.enabled != enabled)
        )
    await db.execute(
        query.values(
            enabled=enabled,
            generation=BackgroundProcess.generation + 1,
            requested_at=datetime.now(UTC),
        )
    )
    return (
        await db.scalars(
            select(BackgroundProcess)
            .where(BackgroundProcess.name == name)
            .execution_options(populate_existing=True)
        )
    ).one()


async def process_snapshot(db) -> list[dict]:
    rows = {row.name: row for row in (await db.scalars(select(BackgroundProcess))).all()}
    return [
        process_json(rows[name]) if name in rows else {"name": name, "status": "unknown"}
        for name in PROCESS_NAMES
    ]


def process_json(row: BackgroundProcess) -> dict:
    return {
        "name": row.name,
        "enabled": row.enabled,
        "generation": row.generation,
        "applied_generation": row.applied_generation,
        "pending": row.generation != row.applied_generation,
        "status": row.status,
        "heartbeat_at": row.heartbeat_at,
        "started_at": row.started_at,
        "error_at": row.error_at,
        "error_code": row.error_code,
        "error_stage": row.error_stage,
        "error_active": row.status == "error",
        "requested_at": row.requested_at,
    }
