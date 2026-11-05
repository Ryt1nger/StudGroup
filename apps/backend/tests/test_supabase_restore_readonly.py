"""Opt-in, read-only HTTP smoke test against a restored Supabase snapshot.

The application lifespan is deliberately never entered. A synthetic web session
exists only inside a transaction that is rolled back after the requests.
"""

import asyncio
import os
import secrets
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup.api import database
from studgroup.main import Settings, create_app
from studgroup.models import Group, Membership, WebSession
from studgroup.security import token_hash

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("STUDGROUP_STAGE4_TEST") == "1"
        and os.environ.get("SUPABASE_RESTORE_TEST_URL")
        and os.environ.get("SUPABASE_CA_FILE")
    ),
    reason="restored Supabase test database not configured",
)


async def smoke_test():
    assert (
        "@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
        in os.environ["SUPABASE_RESTORE_TEST_URL"]
    )
    tls = ssl.create_default_context(cafile=os.environ["SUPABASE_CA_FILE"])
    engine = create_async_engine(
        os.environ["SUPABASE_RESTORE_TEST_URL"],
        connect_args={"ssl": tls, "timeout": 10},
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(connection, expire_on_commit=False) as session:
                    eligible = (
                        select(Membership)
                        .join(Group, Membership.group_id == Group.id)
                        .where(
                            Group.pilot_authorized.is_(True),
                            Group.status == "active",
                        )
                    )
                    student = await session.scalar(
                        eligible.where(Membership.role == "student", Membership.status == "active")
                    )
                    headman = await session.scalar(
                        eligible.where(Membership.role == "headman", Membership.status == "active")
                    )
                    suspended = await session.scalar(
                        eligible.where(
                            Membership.role == "student", Membership.status == "suspended"
                        )
                    )
                    assert student and headman and suspended
                    tokens = {}
                    for label, member in (
                        ("student", student),
                        ("headman", headman),
                        ("suspended", suspended),
                    ):
                        token = secrets.token_urlsafe(32)
                        tokens[label] = token
                        session.add(
                            WebSession(
                                token_hash=token_hash(token),
                                user_id=member.user_id,
                                membership_id=member.id,
                                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                            )
                        )
                    await session.flush()

                    settings = Settings(
                        cabinet_schedule_background_enabled=False,
                        image_event_publication_enabled=True,
                        image_analysis_groups=[str(student.group_id)],
                    )
                    app = create_app(settings)

                    async def database_override():
                        yield session

                    app.dependency_overrides[database] = database_override
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://isolated-test"
                    ) as client:
                        student_headers = {"Authorization": f"Bearer {tokens['student']}"}
                        headman_headers = {"Authorization": f"Bearer {tokens['headman']}"}
                        suspended_headers = {"Authorization": f"Bearer {tokens['suspended']}"}
                        checks = [
                            ("/v1/homework", student_headers, 200),
                            ("/v1/schedule?start=2026-11-05&end=2026-11-12", student_headers, 200),
                            ("/v1/deadlines", student_headers, 200),
                            ("/v1/subjects", student_headers, 200),
                            ("/v1/group-events", student_headers, 200),
                            ("/v1/homework?filter=review", student_headers, 403),
                            ("/v1/homework?filter=review", headman_headers, 200),
                            ("/v1/homework", suspended_headers, 403),
                            ("/v1/homework", {}, 401),
                        ]
                        for path, headers, expected_status in checks:
                            response = await client.get(path, headers=headers)
                            assert (path, response.status_code) == (path, expected_status)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def test_restored_supabase_reads_and_roles_without_startup():
    asyncio.run(smoke_test())


def test_cutover_read_only_lifespan_against_restored_supabase():
    app = create_app(
        Settings(
            database_url=os.environ["SUPABASE_RESTORE_TEST_URL"],
            database_ca_file=os.environ["SUPABASE_CA_FILE"],
            database_pool_size=3,
            database_max_overflow=0,
            database_pool_timeout=10,
            cutover_read_only=True,
        )
    )
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "read_only"
        assert response.json()["checks"]["postgres"] is True
        assert client.post("/v1/telegram/webhook", json={}).status_code == 503
