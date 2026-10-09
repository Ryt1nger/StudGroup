import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import schedule_data
from studgroup.models import Group, RawMessage, SchedulePattern
from studgroup.online_lessons import discover, joining_url

pytest_plugins = ["test_schedule_api"]
URL = "https://my.mts-link.ru/j/Ranepa/12345"


@pytest.mark.parametrize(
    "url,expected",
    [
        (URL, True),
        ("https://us06web.zoom.us/j/123?pwd=secret.1", True),
        ("javascript:alert(1)", False),
        ("http://my.mts-link.ru/j/Ranepa/1", False),
        ("https://zoom.us.evil.example/j/123", False),
        ("https://user:pass@zoom.us/j/1", False),
        ("https://lms.ranepa.ru/pluginfile.php/1/lecture.pdf", False),
        ("https://lms.ranepa.ru/mod/quiz/view.php?id=1", False),
        ("https://lms.ranepa.ru/mod/attendancernhgs/qrcode.php?qr=x", False),
        ("https://lms.ranepa.ru/mod/bigbluebuttonbn/view.php?id=1", True),
    ],
)
def test_joining_url_is_not_a_material_or_phishing_url(url, expected):
    assert joining_url(url) is expected


def test_permanent_link_only_remote_pattern_edit_revocation_and_manual_priority(client):
    async def run():
        now = datetime.now(UTC)
        day = date(2026, 10, 12)
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            remote = SchedulePattern(
                group_id=group.id,
                subject="Культура личности и общества",
                weekday=0,
                starts=time(9),
                ends=time(10, 20),
                location="СДО",
                week="all",
                valid_from=date(2026, 10, 5),
                valid_until=date(2026, 10, 18),
            )
            offline = SchedulePattern(
                group_id=group.id,
                subject=remote.subject,
                weekday=0,
                starts=time(11),
                ends=time(12),
                location="Ауд. 511",
                week="all",
                valid_from=remote.valid_from,
                valid_until=remote.valid_until,
            )
            db.add_all([remote, offline])
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=3456,
                imported=True,
                text="Постоянная ссылка на занятия по дисциплине Культура личности и общества\n"
                + URL,
                message_date=datetime.fromisoformat("2026-10-02T15:24:44+03:00"),
                version_date=datetime.fromisoformat("2026-10-02T15:24:44+03:00"),
                revision=1,
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.flush()
            assert await discover(db, raw) == 1
            data = await schedule_data(day, day, group, db)
            ours = [l for l in data["lessons"] if l["subject"] == remote.subject]
            assert [l["online_url"] for l in ours] == [URL, None]
            remote.online_url = "https://my.mts-link.ru/j/Ranepa/manual"
            assert (
                next(
                    l
                    for l in (await schedule_data(day, day, group, db))["lessons"]
                    if l["id"].startswith(str(remote.id))
                )["online_url"]
                == remote.online_url
            )
            remote.online_url = None
            raw.revision += 1
            raw.text = "Ссылку убрали"
            await discover(db, raw)
            assert all(
                l["online_url"] is None
                for l in (await schedule_data(day, day, group, db))["lessons"]
            )

    asyncio.run(run())


def test_one_off_link_does_not_repeat_next_week_and_source_processing_order_does_not_win(client):
    async def run():
        now = datetime.now(UTC)
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            db.add(
                SchedulePattern(
                    group_id=group.id,
                    subject="Культура личности и общества",
                    weekday=0,
                    starts=time(9),
                    ends=time(10, 20),
                    location="СДО",
                    week="all",
                    valid_from=date(2026, 10, 5),
                    valid_until=date(2026, 10, 18),
                )
            )
            sources = []
            for mid, url, clock in [(91, URL, "08:55:00"), (92, URL + "6", "08:56:00")]:
                stamp = datetime.fromisoformat("2026-10-05T" + clock + "+03:00")
                raw = RawMessage(
                    group_id=group.id,
                    telegram_message_id=mid,
                    text="#культура\n" + url,
                    message_date=stamp,
                    version_date=stamp,
                    revision=1,
                    delete_at=now + timedelta(days=30),
                )
                db.add(raw)
                sources.append(raw)
            await db.flush()
            await discover(db, sources[1])
            await discover(
                db, sources[0]
            )  # Older message processed later must not replace the newer URL.
            monday = await schedule_data(date(2026, 10, 5), date(2026, 10, 5), group, db)
            assert (
                next(l for l in monday["lessons"] if l["subject"].startswith("Культура"))[
                    "online_url"
                ]
                == URL + "6"
            )
            week = await schedule_data(date(2026, 10, 12), date(2026, 10, 12), group, db)
            assert (
                next(l for l in week["lessons"] if l["subject"].startswith("Культура"))[
                    "online_url"
                ]
                is None
            )
            # Expiration removes automatic links even if the record remains retained.
            for raw in sources:
                raw.delete_at = now - timedelta(seconds=1)
            assert (
                next(
                    l
                    for l in (await schedule_data(date(2026, 10, 5), date(2026, 10, 5), group, db))[
                        "lessons"
                    ]
                    if l["subject"].startswith("Культура")
                )["online_url"]
                is None
            )

    asyncio.run(run())
