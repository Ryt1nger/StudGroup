"""One-off, read-only copy of the whole database into a NEW, migrated PostgreSQL database.

    SOURCE_DATABASE_URL=... TARGET_DATABASE_URL=... uv run python -m studgroup.db_copy [--execute]

The source is only ever read. The target must already be migrated to the same Alembic head
(`DATABASE_URL=<target> uv run alembic upgrade head`) and must hold no data of its own (rows seeded by
migrations are replaced by the source's rows). Without `--execute` nothing is written: the tool only
reports what it would copy. After a copy every table is compared row for row (count and a hash of the
ordered rows) and the exit code is non-zero if anything differs.

The URLs come from the environment, never from arguments, so they do not land in shell history or logs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import MetaData, Table, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

CHUNK = 500
KEEP = {"alembic_version"}


def async_url(url: str) -> str:
    """`postgresql://…?sslmode=require` (what dashboards print) → the asyncpg form the app uses."""
    parts = urlsplit(url.strip())
    scheme = "postgresql+asyncpg" if parts.scheme in {"postgres", "postgresql"} else parts.scheme
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append(("ssl", value) if key == "sslmode" else (key, value))
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), ""))


def host_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}{parts.path}"


async def reflect(engine: AsyncEngine) -> MetaData:
    metadata = MetaData()
    async with engine.connect() as connection:
        await connection.run_sync(metadata.reflect)
    return metadata


def ordered_tables(metadata: MetaData) -> list[Table]:
    """Parents before children; a foreign-key cycle (reported as a warning by SQLAlchemy) keeps its order."""
    return [table for table in metadata.sorted_tables if table.name not in KEEP]


def order_columns(table: Table):
    keys = list(table.primary_key.columns)
    return keys or list(table.columns)


def fingerprint(row) -> str:
    return json.dumps(
        [_plain(value) for value in row], sort_keys=True, default=str, ensure_ascii=False
    )


def _plain(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "bytes:" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


async def table_digest(engine: AsyncEngine, table: Table) -> tuple[int, str]:
    """Row count and a hash of every row in primary-key order (portable between database engines)."""
    digest = hashlib.sha256()
    count = 0
    async with engine.connect() as connection:
        result = await connection.stream(select(table).order_by(*order_columns(table)))
        async for row in result:
            digest.update(fingerprint(row).encode())
            digest.update(b"\n")
            count += 1
    return count, digest.hexdigest()


async def counts(engine: AsyncEngine, tables: list[Table]) -> dict[str, int]:
    out = {}
    async with engine.connect() as connection:
        for table in tables:
            out[table.name] = await connection.scalar(select(func.count()).select_from(table)) or 0
    return out


async def head(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.scalar(text("select max(version_num) from alembic_version"))


async def reset_sequences(connection, tables: list[Table]) -> list[str]:
    """Serial/identity keys keep counting from the copied maximum, not from 1."""
    fixed = []
    for table in tables:
        for column in table.primary_key.columns:
            sequence = await connection.scalar(
                text("select pg_get_serial_sequence(:t, :c)"),
                {"t": f'"{table.name}"', "c": column.name},
            )
            if sequence:
                await connection.execute(
                    text(
                        f'select setval(\'{sequence}\', coalesce((select max("{column.name}") from "{table.name}"), 1), '
                        f'(select count(*) > 0 from "{table.name}"))'
                    )
                )
                fixed.append(f"{table.name}.{column.name}")
    return fixed


async def compare(
    source: AsyncEngine, target: AsyncEngine, tables: list[Table], dst_meta: MetaData
) -> int:
    bad = 0
    for table in tables:
        left = await table_digest(source, table)
        right = await table_digest(target, dst_meta.tables[table.name])
        status = "ok" if left == right else "DIFFERENT"
        if left != right:
            bad += 1
        print(f"  {table.name}: source {left[0]} / target {right[0]} — {status}")
    print("RESULT: identical" if bad == 0 else f"RESULT: {bad} table(s) differ")
    return 0 if bad == 0 else 1


async def copy_all(
    source: AsyncEngine, target: AsyncEngine, execute: bool, verify_only: bool = False
) -> int:
    source_head, target_head = await head(source), await head(target)
    print(f"source migration {source_head}, target migration {target_head}")
    if source_head != target_head:
        print("STOP: the target is not migrated to the same head as the source")
        return 2
    src_meta, dst_meta = await reflect(source), await reflect(target)
    tables = ordered_tables(src_meta)
    missing = [t.name for t in tables if t.name not in dst_meta.tables]
    if missing:
        print(f"STOP: tables missing in the target: {missing}")
        return 2
    planned = await counts(source, tables)
    for name, number in planned.items():
        print(f"  {name}: {number} rows")
    print(f"{sum(planned.values())} rows in {len(tables)} tables")
    if verify_only:
        return await compare(source, target, tables, dst_meta)
    if not execute:
        print("dry run: nothing written (add --execute to copy)")
        return 0

    async with target.begin() as connection:
        await connection.execute(text("set time zone 'UTC'"))
        names = ", ".join(f'"{t.name}"' for t in tables)
        await connection.execute(text(f"truncate table {names} restart identity cascade"))
        for table in tables:
            dst = dst_meta.tables[table.name]
            async with source.connect() as read:
                result = await read.stream(select(table).order_by(*order_columns(table)))
                batch = []
                async for row in result:
                    batch.append(dict(row._mapping))
                    if len(batch) >= CHUNK:
                        await connection.execute(insert(dst), batch)
                        batch = []
                if batch:
                    await connection.execute(insert(dst), batch)
        fixed = await reset_sequences(connection, tables)
        print(f"sequences adjusted: {fixed or 'none'}")
    print("copied; comparing every table …")

    return await compare(source, target, tables, dst_meta)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--execute", action="store_true", help="really copy (default: report only)")
    parser.add_argument(
        "--verify", action="store_true", help="only compare source and target row for row"
    )
    args = parser.parse_args()
    raw_source, raw_target = (
        os.environ.get("SOURCE_DATABASE_URL"),
        os.environ.get("TARGET_DATABASE_URL"),
    )
    if not raw_source or not raw_target:
        print("set SOURCE_DATABASE_URL and TARGET_DATABASE_URL")
        return 2
    if host_of(raw_source) == host_of(raw_target):
        print("STOP: source and target are the same database")
        return 2
    print(f"source: {host_of(raw_source)}\ntarget: {host_of(raw_target)}")
    source = create_async_engine(async_url(raw_source))
    target = create_async_engine(async_url(raw_target))
    try:
        return await copy_all(source, target, args.execute, args.verify)
    finally:
        await source.dispose()
        await target.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
