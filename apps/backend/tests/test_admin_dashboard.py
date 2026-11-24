"""Admin telemetry is live, read-only and restricted to the configured bot operator."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    AcademicDeadline,
    AnalysisBudgetCall,
    Group,
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    RawMessage,
    ShadowEvaluationRun,
    User,
)

pytest_plugins = ["test_schedule_api"]


def test_dashboard_rejects_students_and_missing_operator(client):
    assert client.get("/v1/admin/dashboard").status_code == 401
    assert (
        client.get("/v1/admin/dashboard", headers={"Authorization": "Bearer valid"}).status_code
        == 403
    )
    client.app.state.settings.bot_admin_user_id = 1
    assert (
        client.get("/v1/admin/dashboard", headers={"Authorization": "Bearer expired"}).status_code
        == 401
    )


def test_dashboard_reads_current_rows_and_filters_group(client):
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            from studgroup.models import Group

            groups = (await db.scalars(select(Group))).all()
            selected = next(group for group in groups if group.pilot_authorized)
            other = next(group for group in groups if not group.pilot_authorized)
            selected_id = selected.id
            now = datetime.now(UTC)
            for group in (selected, other):
                db.add(
                    RawMessage(
                        group_id=group.id,
                        telegram_message_id=100 + int(group.pilot_authorized),
                        text="private",
                        message_date=now,
                        version_date=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            db.add(
                AcademicDeadline(
                    group_id=selected.id,
                    import_key="test",
                    kind="exam",
                    subject="Math",
                    title="Exam",
                    description="private",
                    needs_clarification=True,
                    source_message_ids="[]",
                    created_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            db.add(
                AnalysisBudgetCall(
                    group_id=selected.id,
                    attempt_key="test",
                    kind="text",
                    state="settled",
                    reserved_usd=0.02,
                    charged_usd=0.01,
                    created_at=now,
                    settled_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            await db.commit()
            return selected_id

    group_id = asyncio.run(seed())
    response = client.get(
        f"/v1/admin/dashboard?group_id={group_id}", headers={"Authorization": "Bearer valid"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["messages_7d"] == 1
    assert body["summary"]["active_deadlines"] == 1
    assert body["summary"]["needs_clarification"] == 1
    assert body["summary"]["charged_usd"] == 0.01
    assert "private" not in response.text
    assert (
        client.get(
            "/v1/admin/dashboard?group_id=" + str(uuid4()),
            headers={"Authorization": "Bearer valid"},
        ).status_code
        == 404
    )


def test_admin_page_has_no_embedded_credentials(client):
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "initData" not in response.text
    assert "no-store" in response.headers["cache-control"]


def test_owner_manages_payment_buttons_without_code_changes(client):
    client.app.state.settings.bot_admin_user_id = 1
    headers = {"Authorization": "Bearer valid"}
    created = client.post(
        "/v1/admin/billing-options",
        headers=headers,
        json={
            "label": "Оплатить месяц",
            "months": 1,
            "payment_url": "https://pay.alfabank.example/800",
            "enabled": True,
            "position": 2,
        },
    )
    assert created.status_code == 200, created.text
    option_id = created.json()["id"]
    assert (
        client.get("/v1/admin/billing-options", headers=headers).json()["items"][0]["label"]
        == "Оплатить месяц"
    )
    updated = client.put(
        f"/v1/admin/billing-options/{option_id}",
        headers=headers,
        json={
            "label": "Месяц для всей группы",
            "months": 1,
            "payment_url": "https://pay.alfabank.example/new-800",
            "enabled": False,
            "position": 1,
        },
    )
    assert updated.status_code == 200 and updated.json()["enabled"] is False
    assert (
        client.post(
            "/v1/admin/billing-options",
            headers=headers,
            json={
                "label": "Небезопасная",
                "months": 2,
                "payment_url": "http://example.com/pay",
                "enabled": True,
                "position": 2,
            },
        ).status_code
        == 422
    )
    assert client.delete(f"/v1/admin/billing-options/{option_id}", headers=headers).json() == {
        "deleted": True
    }
    assert client.get("/v1/admin/billing-options", headers=headers).json()["items"] == []


def test_owner_reliability_aggregates_are_group_scoped_and_private(client):
    """No account identity, course title or message body escapes the operator counters."""
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group))).all()
            selected = next(row for row in groups if row.pilot_authorized)
            other = next(row for row in groups if not row.pilot_authorized)
            user = await db.scalar(select(User))
            now = datetime.now(UTC)
            for index, group in enumerate((selected, other)):
                db.add(
                    RanepaPersonalSnapshot(
                        group_id=group.id,
                        user_id=user.id,
                        week_start=now.date(),
                        week_end=(now + timedelta(days=6)).date(),
                        data_digest="a" * 64,
                        rows_json=json.dumps(
                            [
                                {
                                    "room": "СДО",
                                    "link_state": "matched" if index == 0 else "not_checked",
                                    "subject": "PRIVATE_ACADEMIC_COURSE",
                                }
                            ]
                        ),
                        checked_at=now - timedelta(hours=1 if index == 0 else 12),
                        identity_verified=True,
                    )
                )
                db.add(
                    RanepaCrawlJob(
                        group_id=group.id,
                        user_id=user.id,
                        week_start=now.date(),
                        priority=1,
                        state="retry" if index == 0 else "completed",
                        retry_reason="captcha" if index == 0 else None,
                        created_at=now,
                    )
                )
                db.add(
                    ShadowEvaluationRun(
                        group_id=group.id,
                        source_snapshot_sha256="b" * 64,
                        engine_version="test",
                        state="completed",
                        metrics_json=json.dumps(
                            {
                                "product": {
                                    "summary": {
                                        "targets": 2,
                                        "useful": 1 if index == 0 else 0,
                                        "empty": 1 if index == 0 else 2,
                                        "by_outcome": {
                                            "published": 1 if index == 0 else 0,
                                            "no_academic_signal": 1 if index == 0 else 2,
                                        },
                                    }
                                }
                            }
                        ),
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            selected_id = selected.id
            await db.commit()
            return selected_id

    selected_id = asyncio.run(seed())
    response = client.get(
        f"/v1/admin/dashboard?group_id={selected_id}",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()["reliability"]
    assert payload["sdo"]["queue_states"] == {"retry": 1}
    assert payload["sdo"]["retry_reasons"] == {"captcha": 1}
    assert payload["sdo"]["snapshot_fresh_under_6h"] == 1
    assert payload["sdo"]["snapshot_stale_over_6h"] == 0
    assert payload["sdo"]["link_states"] == {"matched": 1}
    assert payload["ai_products"]["packages_7d"] == 1
    assert payload["ai_products"]["outcomes_7d"]["published"] == 1
    assert payload["ai_products"]["useful_packages_7d"] == 1
    assert "PRIVATE_ACADEMIC_COURSE" not in response.text
    assert "captcha" in response.text  # only enumerated retry codes, not user identity


def test_missing_billing_schema_returns_safe_owner_error(client):
    from sqlalchemy import text

    client.app.state.settings.bot_admin_user_id = 1

    async def remove_test_table():
        async with client.app.state.engine.begin() as connection:
            await connection.execute(text("DROP TABLE billing_payment_options"))

    asyncio.run(remove_test_table())
    url = "/v1/admin/billing-options"
    assert client.get(url).status_code == 401
    headers = {"Authorization": "Bearer valid"}
    response = client.get(url, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "billing_schema_missing"
    response = client.post(
        url,
        headers=headers,
        json={
            "label": "Месяц",
            "months": 1,
            "payment_url": "https://pay.example/800",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "billing_schema_missing"


def test_malformed_billing_url_is_validation_error(client):
    client.app.state.settings.bot_admin_user_id = 1
    for value in ("https://[broken", "https://pay.example:broken/800"):
        response = client.post(
            "/v1/admin/billing-options",
            headers={"Authorization": "Bearer valid"},
            json={"label": "Месяц", "months": 1, "payment_url": value},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_payment_url"


def test_owner_urgent_progress_exposes_durable_account_state(client):
    from studgroup.models import Membership, UrgentSDORun, UrgentSDOTarget

    client.app.state.settings.bot_admin_user_id = 1
    now = datetime.now(UTC)

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership))
            run = UrgentSDORun(state="running", requested_at=now, started_at=now)
            db.add(run)
            job = RanepaCrawlJob(
                group_id=member.group_id,
                user_id=member.user_id,
                week_start=now.date() - timedelta(days=now.weekday()),
                priority=0,
                state="running",
                attempts=2,
                created_at=now,
                claimed_at=now,
            )
            db.add(job)
            await db.flush()
            db.add(
                UrgentSDOTarget(
                    run_id=run.id,
                    user_id=member.user_id,
                    group_id=member.group_id,
                    job_id=job.id,
                    baseline_attempts=1,
                    state="pending",
                )
            )
            await db.commit()

    asyncio.run(seed())
    assert client.get("/v1/admin/sdo-runs").status_code == 401
    response = client.get("/v1/admin/sdo-runs", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200, response.text
    target = response.json()["run"]["targets"][0]
    assert target["state"] == "running"
    assert target["started_at"] is not None
    assert target["next_attempt_at"] is None
