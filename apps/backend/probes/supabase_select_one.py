"""One-shot Render to Supabase probe; never imports the StudGroup application."""

import asyncio
import os
import ssl
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

POOLER_HOST = "aws-0-eu-west-1.pooler.supabase.com"
PROJECT_REF = "egbodayfttbiznwtfiih"


def settings(environ: dict[str, str]):
    raw_url = environ.get("SUPABASE_PROBE_URL")
    ca_file = environ.get("SUPABASE_CA_FILE")
    if not raw_url or not ca_file:
        raise ValueError("probe URL and CA file are required")
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != POOLER_HOST
        or url.port != 5432
        or url.database != "postgres"
        or not (url.username or "").endswith(f".{PROJECT_REF}")
        or not url.password
        or "sslmode" in url.query
    ):
        raise ValueError("probe URL must use the verified session pooler")
    ca_path = Path(ca_file)
    if not ca_path.is_file():
        raise ValueError("Supabase CA file is missing")
    return url, ca_path


async def probe(url, ca_path: Path) -> None:
    tls = ssl.create_default_context(cafile=str(ca_path))
    tls.check_hostname = True
    tls.verify_mode = ssl.CERT_REQUIRED
    engine = create_async_engine(
        url,
        connect_args={"ssl": tls, "timeout": 10},
        pool_size=1,
        max_overflow=0,
        pool_timeout=10,
    )
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SELECT 1")) != 1:
                raise RuntimeError("unexpected SELECT 1 result")
    finally:
        await engine.dispose()


def main() -> int:
    try:
        url, ca_path = settings(os.environ)
        asyncio.run(probe(url, ca_path))
    except Exception as error:  # noqa: BLE001 - driver errors may contain credentials.
        print(f"SUPABASE_PROBE_FAILED {type(error).__name__}", file=sys.stderr)
        return 1
    print("SUPABASE_PROBE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
