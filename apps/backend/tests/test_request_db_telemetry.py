import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.request_db_telemetry import (
    finish_database_trace,
    install_database_telemetry,
    start_database_trace,
)

pytest_plugins = ["test_schedule_api"]


def test_database_trace_counts_queries_without_recording_query_data(tmp_path):
    async def exercise():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/telemetry.db")
        install_database_telemetry(engine)
        trace, token = start_database_trace()
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SELECT :private_value"), {"private_value": "secret"})
                await connection.execute(text("SELECT 2"))
        finally:
            finish_database_trace(token)
            await engine.dispose()
        return trace

    trace = asyncio.run(exercise())

    assert trace.query_count == 2
    assert trace.total_ms >= 0
    assert set(vars(trace)) == {"query_count", "total_ms"}


def test_database_trace_ignores_queries_outside_request(tmp_path):
    async def exercise():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/telemetry.db")
        install_database_telemetry(engine)
        trace, token = start_database_trace()
        finish_database_trace(token)

        async with engine.begin() as connection:
            await connection.execute(text("SELECT 1"))
        await engine.dispose()
        return trace

    trace = asyncio.run(exercise())

    assert trace.query_count == 0


def test_academic_timing_log_is_bounded_and_includes_database_totals(client, capsys):
    response = client.get(
        "/v1/schedule?start=2026-10-05&end=2026-10-11",
        headers={"Authorization": "Bearer valid"},
    )

    assert response.status_code == 200
    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("ACADEMIC_API_TIMING ")
    ]
    assert len(lines) == 1
    fields = dict(part.split("=", 1) for part in lines[0].split()[1:])
    assert fields.keys() == {"route", "status", "ms", "db_queries", "db_ms"}
    assert fields["route"] == "/v1/schedule"
    assert fields["status"] == "200"
    assert int(fields["db_queries"]) > 0
    assert int(fields["db_ms"]) >= 0
    assert "valid" not in lines[0]
