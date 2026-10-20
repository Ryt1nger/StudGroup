"""The duplicate watchdog: pure rules and its report from the package worker."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_package_worker import BASE, PackageProvider, make_new, source, step

from studgroup.analysis.duplicate_guard import Candidate, possible_duplicates
from studgroup.models import OwnerIncident, ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]


def card(ident, title, description="", sources=(), subject="Основы российской государственности"):
    return Candidate(ident, subject, title, description, frozenset(sources))


ESSAY = "Эссе минимум 1500 слов, два источника, формат PDF или Word, сдать в личном кабинете"


def test_same_task_with_shared_sources_and_similar_text_is_reported():
    pairs = possible_duplicates(
        [
            card("a", "Эссе по основам российской государственности", ESSAY, {1, 2, 3}),
            card("b", "Эссе по российской идентичности", ESSAY + ", критерии в СДО", {3, 4}),
        ]
    )
    assert [(left, right, signal) for left, right, signal, _ in pairs] == [
        ("a", "b", "shared_sources")
    ]


def test_very_alike_text_is_reported_even_without_a_shared_source():
    pairs = possible_duplicates(
        [
            card("a", "Доклады по цивилизационным проектам", ESSAY),
            card("b", "Доклады по цивилизационным проектам", ESSAY),
        ]
    )
    assert [signal for _l, _r, signal, _s in pairs] == ["alike_text"]


def test_different_known_subjects_are_never_duplicates():
    pairs = possible_duplicates(
        [
            card("a", "Эссе", ESSAY, {1, 2}),
            card("b", "Эссе", ESSAY, {1, 2}, subject="История России"),
        ]
    )
    assert pairs == []


def test_unrelated_tasks_with_a_shared_source_are_not_reported():
    pairs = possible_duplicates(
        [
            card("a", "Определиться с командой и темой проекта", "Выберите команду", {1, 2}),
            card("b", "Видеоролик на две минуты", "Снять ролик про культуру", {1, 3}),
        ]
    )
    assert pairs == []


def test_focus_limits_the_report_to_the_cards_a_package_touched():
    cards = [
        card("a", "Эссе", ESSAY, {1}),
        card("b", "Эссе", ESSAY, {1}),
        card("c", "Другое", "иное", {9}),
    ]
    assert possible_duplicates(cards, focus={"c"}) == []
    assert len(possible_duplicates(cards, focus={"a"})) == 1


def metrics_of_runs(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return [
                json.loads(row.metrics_json)
                for row in (await db.scalars(select(ShadowEvaluationRun))).all()
            ]

    return asyncio.run(read())


def incident_state(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(OwnerIncident, "possible_duplicate_cards")
            return None if row is None else row.active

    return asyncio.run(read())


def test_the_worker_reports_a_duplicate_a_forgetful_model_created_across_packages(client):
    # The first package creates the card; the next one re-creates the same task from a message that
    # shares no source with it (a source shared with a card is linked to it automatically), as a
    # forgetful model would. The watchdog must notice: metrics on the run and one owner incident.
    source(client, mid=31, text="По математике решить задачи 1–3", when=BASE)
    source(client, mid=32, text="кто-нибудь сделал?", when=BASE + timedelta(minutes=1))
    source(
        client, mid=33, text="напоминаю: задачи 1–3 по математике", when=BASE + timedelta(minutes=2)
    )
    make_new(client)
    assert step(client, PackageProvider(cited=(31, 33))) == "completed"
    assert incident_state(client) in (None, False)
    source(
        client, mid=34, text="по математике ещё раз: задачи 1–3", when=BASE + timedelta(minutes=5)
    )
    forgetful = PackageProvider(cited=(34,))
    assert step(client, forgetful, now=datetime.now(UTC) + timedelta(hours=1)) == "completed"
    watch = [m.get("duplicate_watch") for m in metrics_of_runs(client)]
    assert any(watch), watch
    assert incident_state(client) is True


def test_no_report_when_the_second_package_updates_the_same_card(client):
    source(client, mid=31, text="По математике решить задачи 1–3", when=BASE)
    source(client, mid=32, text="кто-нибудь сделал?", when=BASE + timedelta(minutes=1))
    source(
        client, mid=33, text="напоминаю: задачи 1–3 по математике", when=BASE + timedelta(minutes=2)
    )
    make_new(client)
    assert step(client, PackageProvider(cited=(31, 33))) == "completed"
    source(client, mid=34, text="по математике добавили задачу 4", when=BASE + timedelta(minutes=5))
    assert (
        step(
            client,
            PackageProvider(cited=(34,), update_existing=True),
            now=datetime.now(UTC) + timedelta(hours=1),
        )
        == "completed"
    )
    assert not any(m.get("duplicate_watch") for m in metrics_of_runs(client))
    assert incident_state(client) in (None, False)


def test_a_create_citing_a_source_of_an_existing_card_becomes_an_update_not_a_second_card(client):
    # The mechanism that merged the essay discussion into one card: a proposal that cites a message
    # already backing a card is linked to that card even when the model asked to create.
    from sqlalchemy import func

    from studgroup.models import CanonicalAcademicItem, Homework

    source(client, mid=31, text="По математике решить задачи 1–3", when=BASE)
    source(client, mid=32, text="кто-нибудь сделал?", when=BASE + timedelta(minutes=1))
    source(
        client, mid=33, text="напоминаю: задачи 1–3 по математике", when=BASE + timedelta(minutes=2)
    )
    make_new(client)
    assert step(client, PackageProvider(cited=(31, 33))) == "completed"
    source(client, mid=34, text="и ещё по математике: формат PDF", when=BASE + timedelta(minutes=5))
    assert (
        step(client, PackageProvider(cited=(33, 34)), now=datetime.now(UTC) + timedelta(hours=1))
        == "completed"
    )

    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            items = (await db.scalars(select(CanonicalAcademicItem))).all()
            homework = await db.scalar(select(func.count()).select_from(Homework))
            return len(items), homework, items[0].title

    assert asyncio.run(read()) == (1, 1, "Решить задачи 1–3")
