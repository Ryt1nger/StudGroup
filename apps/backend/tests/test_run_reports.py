import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_processing import Provider, source

from studgroup.ai import ProviderFailure
from studgroup.main import Settings
from studgroup.models import AIAttempt, BotOutbox
from studgroup.processing import process_next

pytest_plugins = ["test_schedule_api"]


def config():
    return Settings(
        _env_file=None,
        ai_enabled=True,
        owner_telegram_user_id=5282463254,
        owner_update_notifications_enabled=True,
        owner_update_notifications_since=datetime.now(UTC) - timedelta(days=1),
    )


def process(client, provider, now=None):
    return asyncio.run(process_next(client.app.state.engine, config(), provider, now=now))


def read(client):
    async def query():
        async with AsyncSession(client.app.state.engine) as db:
            attempts = (await db.scalars(select(AIAttempt).order_by(AIAttempt.created_at))).all()
            rows = (await db.scalars(select(BotOutbox))).all()
            return attempts, [json.loads(row.payload) for row in rows]

    return asyncio.run(query())


def test_started_and_finished_reports_have_actual_tokens_and_creation_metrics(client):
    source(client)
    provider = Provider()
    assert process(client, provider) == "completed"
    assert process(client, provider) == "idle"
    attempts, notices = read(client)
    assert len(notices) == 2
    assert {n["chat_id"] for n in notices} == {5282463254}
    assert any("Начало прогона" in n["text"] for n in notices)
    final = next(n["text"] for n in notices if "Итог прогона" in n["text"])
    assert "всего 200" in final
    assert "$0.000150" in final
    data = json.loads(attempts[0].metrics)
    assert data["created_cards"] == 1
    assert data["applied_fragments"] == 1
    assert data["used_source_messages"] == 1
    assert data["analyzed_fragments"] == 1
    assert attempts[0].finished_at is not None


def test_new_information_reports_updated_card_not_a_new_card(client):
    source(client)
    assert process(client, Provider()) == "completed"
    source(client, mid=32, reply=31, text="Сдать до 15 октября")
    assert process(client, Provider(date="2026-10-15T10:00:00+03:00", ids=[31, 32])) == "completed"
    attempts, notices = read(client)
    data = json.loads(attempts[-1].metrics)
    assert data["updated_cards"] == 1
    assert data.get("created_cards", 0) == 0
    assert data["used_source_messages"] == 2
    assert len(notices) == 4


def test_uncertain_usage_is_reported_as_reservation_not_actual_spend(client):
    source(client)
    assert process(client, Provider(failure="provider_unreachable")) == "retry"
    _, notices = read(client)
    final = next(n["text"] for n in notices if "Итог прогона" in n["text"])
    assert "Неуспех" in final
    assert "Удержан резерв $0.012000" in final
    assert "Расчётная стоимость" not in final


def test_confirmed_rejection_reports_zero_cost_and_zero_applied_fragments(client):
    source(client)

    class Rejected:
        async def extract_batch(self, *args, **kwargs):
            raise ProviderFailure("rate_limited", True, reservation_releasable=True)

    assert process(client, Rejected()) == "retry"
    attempts, notices = read(client)
    assert json.loads(attempts[0].metrics)["analyzed_fragments"] == 0
    assert any("Расход $0" in n["text"] for n in notices)


def test_local_filter_reports_actual_new_sources_without_calling_deepseek_or_replaying(client):
    source(client, text="Доброе утро")
    provider = Provider()
    assert process(client, provider) == "idle"
    assert process(client, provider) == "idle"
    assert provider.calls == 0
    attempts, notices = read(client)
    assert attempts == []
    assert len(notices) == 2
    assert any("Отфильтровано новых сообщений: 1" in n["text"] for n in notices)
    assert any("расход: $0" in n["text"] for n in notices)


def test_restart_finishes_interrupted_attempt_and_reports_recovery(client):
    source(client)
    now = datetime.now(UTC)

    class Interrupted:
        async def extract_batch(self, *args, **kwargs):
            raise asyncio.CancelledError

    import pytest

    with pytest.raises(asyncio.CancelledError):
        process(client, Interrupted(), now)
    assert process(client, Provider(), now + timedelta(minutes=4)) == "completed"
    attempts, notices = read(client)
    assert [a.outcome for a in attempts] == ["interrupted", "completed"]
    assert len(notices) == 4
    assert any("Попытка прервана" in n["text"] for n in notices)
    assert any("2 попыток" in n["text"] for n in notices)


def test_review_proposals_are_not_counted_as_applied_cards(client):
    source(client)

    class Uncertain(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.batch.assignments[0].confidence = 50
            return result

    assert process(client, Uncertain()) == "completed"
    attempts, _ = read(client)
    data = json.loads(attempts[0].metrics)
    assert data["review_proposals"] == 1
    assert data["important_proposals"] == 1
    assert data.get("applied_fragments", 0) == 0
    assert data.get("created_cards", 0) == 0


def test_known_usage_of_failed_output_is_charged_and_reported_not_kept_as_reservation(client):
    from decimal import Decimal

    from studgroup.ai import TokenUsage

    source(client)

    class InvalidOutput:
        async def extract_batch(self, *args, **kwargs):
            raise ProviderFailure(
                "invalid_provider_output",
                True,
                usage=TokenUsage(prompt_tokens=100, completion_tokens=100),
            )

    assert process(client, InvalidOutput()) == "retry"
    attempts, notices = read(client)
    assert attempts[0].charged_usd == Decimal("0.00015")
    assert attempts[0].prompt_tokens == 100
    final = next(n["text"] for n in notices if "Итог прогона" in n["text"])
    assert "Неуспех" in final and "всего 200" in final
    assert "Расчётная стоимость: $0.000150" in final


def test_no_change_to_existing_card_is_not_reported_as_creation_or_update(client):
    source(client)
    now = datetime.now(UTC)
    date = (now + timedelta(days=2)).isoformat()
    assert process(client, Provider(date=date), now) == "completed"
    source(client, mid=32, reply=31)
    assert (
        process(client, Provider(date=date, ids=[31, 32]), now + timedelta(seconds=1))
        == "completed"
    )
    attempts, _ = read(client)
    data = json.loads(attempts[-1].metrics)
    assert data.get("created_cards", 0) == 0
    assert data.get("updated_cards", 0) == 0
    assert data["unchanged_cards"] == 1
    assert data.get("applied_fragments", 0) == 0
