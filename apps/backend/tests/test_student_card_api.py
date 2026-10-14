import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    AcademicDeadline,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    CanonicalProjectionLink,
    Group,
    Homework,
    MaterialLink,
    MessageAttachment,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]


def snapshot(item, description):
    return json.dumps(
        {
            "title": item.title,
            "description": description,
            "subject": item.subject,
            "deadline_at": item.deadline_at.isoformat() if item.deadline_at else None,
            "deadline_date_only": item.deadline_date_only,
            "state": item.state,
        }
    )


def seed_published_card(client, *, cancelled=False, missing_subject=False, expired_source=False):
    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            now = datetime.now(UTC)
            raws = []
            for message_id, text in [
                (7101, "Решить задачи 1–3 до пятницы"),
                (7102, "Дополнение: оформить решение в PDF"),
            ]:
                raw = RawMessage(
                    group_id=group.id,
                    telegram_message_id=message_id,
                    text=text,
                    message_date=now - timedelta(hours=3 - len(raws)),
                    version_date=now,
                    revision=1,
                    imported=False,
                    processing_state="completed",
                    delete_at=(
                        now - timedelta(minutes=1) if expired_source else now + timedelta(days=30)
                    ),
                )
                db.add(raw)
                await db.flush()
                raws.append(raw)
            db.add_all(
                [
                    MessageAttachment(
                        raw_message_id=raws[0].id,
                        group_id=group.id,
                        source_revision=1,
                        kind="photo",
                        stable_file_id="photo-7101",
                        telegram_file_id="private-photo-id",
                        file_name=None,
                        mime_type="image/jpeg",
                        size_bytes=1234,
                        processing_state="metadata_only",
                        active=True,
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    ),
                    MessageAttachment(
                        raw_message_id=raws[1].id,
                        group_id=group.id,
                        source_revision=1,
                        kind="document",
                        stable_file_id="doc-7102",
                        telegram_file_id="private-document-id",
                        file_name="условия.pdf",
                        mime_type="application/pdf",
                        size_bytes=4321,
                        processing_state="metadata_only",
                        active=True,
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    ),
                ]
            )
            item = CanonicalAcademicItem(
                group_id=group.id,
                destination="task",
                semantic_kind="homework",
                subject=None if missing_subject else "Математика",
                title="Решить задачи 1–3",
                description="Решить задачи 1–3 и оформить решение в PDF.",
                deadline_at=now + timedelta(days=2),
                deadline_date_only=True,
                deadline_basis="explicit",
                audience_scope="group",
                state="cancelled" if cancelled else "active",
                attention_reasons="[]",
                manual_lock=False,
                revision=2,
                created_at=now - timedelta(hours=3),
                updated_at=now,
            )
            db.add(item)
            await db.flush()
            first_description = "Решить задачи 1–3."
            db.add_all(
                [
                    CanonicalItemRevision(
                        item_id=item.id,
                        item_revision=1,
                        action="create",
                        actor="ai_shadow",
                        snapshot_json=snapshot(item, first_description),
                        created_at=now - timedelta(hours=3),
                    ),
                    CanonicalItemRevision(
                        item_id=item.id,
                        item_revision=2,
                        action="cancel" if cancelled else "update",
                        actor="ai_shadow",
                        snapshot_json=snapshot(item, item.description),
                        created_at=now,
                    ),
                ]
            )
            for raw in raws:
                db.add(
                    CanonicalItemEvidence(
                        item_id=item.id,
                        item_revision=2,
                        raw_message_id=raw.id,
                        source_revision=1,
                        fact_path="description",
                    )
                )
            card = Homework(
                group_id=group.id,
                raw_message_id=raws[0].id,
                subject_id=uuid.uuid5(group.id, "математика"),
                subject_name="Предмет не указан" if missing_subject else "Математика",
                title=item.title,
                description=item.description,
                deadline_at=item.deadline_at,
                deadline_date_only=True,
                status="cancelled" if cancelled else "published",
                cancelled_at=now if cancelled else None,
                verification_state="from_group_message",
                source_message_at=raws[0].message_date,
                created_at=now - timedelta(hours=3),
                updated_at=now,
                significant_updated_at=now,
                revision=2,
                delete_at=now + timedelta(days=90),
            )
            db.add(card)
            await db.flush()
            db.add(
                CanonicalProjectionLink(
                    item_id=item.id,
                    entity_type="homework",
                    entity_id=card.id,
                    projected_revision=2,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                MaterialLink(
                    group_id=group.id,
                    entity_key=f"h:{card.id}",
                    title="Методичка",
                    url="https://example.edu/guide",
                )
            )
            # A real shadow-only item exists but has no projection link and must stay invisible.
            db.add(
                CanonicalAcademicItem(
                    group_id=group.id,
                    destination="deadline",
                    semantic_kind="exam",
                    subject="Скрытый предмет",
                    title="Shadow-only",
                    description="Не публиковать",
                    deadline_date_only=False,
                    deadline_basis="unknown",
                    audience_scope="group",
                    state="active",
                    attention_reasons="[]",
                    manual_lock=False,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            foreign = Homework(
                group_id=other.id,
                subject_id=uuid.uuid4(),
                subject_name="Секретный предмет",
                title="Чужая карточка",
                created_at=now,
                updated_at=now,
                delete_at=now + timedelta(days=30),
            )
            db.add(foreign)
            await db.commit()
            return str(card.id), str(foreign.id)

    return asyncio.run(seed())


def test_published_canonical_card_reaches_student_api_without_shadow_or_private_file_ids(client):
    card_id, foreign_id = seed_published_card(client)
    headers = {"Authorization": "Bearer valid"}
    response = client.get(f"/v1/homework/{card_id}", headers=headers)
    assert response.status_code == 200
    card = response.json()
    assert card["card_kind"] == "homework"
    assert card["confirmed_subject"]["name"] == "Математика"
    assert card["deadline"]["certainty"] == "from_source"
    assert card["deadline"]["origin"] == "explicit_message"
    assert [source["telegram_message_id"] for source in card["sources"]] == [7101, 7102]
    assert card["source_detail"] == card["sources"][0]
    assert [material["kind"] for material in card["materials"]] == ["link", "photo", "document"]
    assert card["materials"][2]["file_name"] == "условия.pdf"
    assert "private-photo-id" not in response.text
    assert "private-document-id" not in response.text
    assert card["updates"][0]["kind"] == "supplement"
    assert "Shadow-only" not in response.text
    assert client.get(f"/v1/homework/{foreign_id}", headers=headers).status_code == 404

    listed = client.get("/v1/homework", headers=headers).json()["items"]
    listed_card = next(item for item in listed if item["id"] == card_id)
    assert listed_card["card_kind"] == "homework"
    assert "sources" not in listed_card
    completed = client.put(
        f"/v1/homework/{card_id}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 2},
    )
    assert completed.status_code == 200
    assert completed.json()["my_state"]["completion"] == "completed"


def test_missing_subject_unavailable_sources_and_cancellation_are_explicit(client):
    card_id, _ = seed_published_card(
        client, cancelled=True, missing_subject=True, expired_source=True
    )
    card = client.get(f"/v1/homework/{card_id}", headers={"Authorization": "Bearer valid"}).json()
    assert card["confirmed_subject"] is None
    assert card["status"] == "cancelled"
    assert card["updates"][0]["kind"] == "cancellation"
    assert all(source["availability"]["state"] == "unavailable" for source in card["sources"])
    assert [material["kind"] for material in card["materials"]] == ["link"]


def test_subject_overview_is_group_scoped_and_excludes_unknown_subject(client):
    seed_published_card(client, missing_subject=True)
    response = client.get("/v1/subjects", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert "Математика" in names  # timetable subject
    assert "Предмет не указан" not in names
    assert "Секретный предмет" not in names
    assert client.get("/v1/subjects").status_code == 401


def test_published_non_homework_card_keeps_kind_sources_and_deadline_evidence(client):
    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC)
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=7201,
                text="Экзамен по физике 20 октября",
                message_date=now - timedelta(hours=1),
                version_date=now,
                revision=1,
                imported=False,
                processing_state="completed",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.flush()
            item = CanonicalAcademicItem(
                group_id=group.id,
                destination="deadline",
                semantic_kind="exam",
                subject="Физика",
                title="Экзамен по физике",
                description="Подготовиться к экзамену.",
                deadline_at=now + timedelta(days=6),
                deadline_date_only=True,
                deadline_basis="explicit",
                audience_scope="group",
                state="active",
                attention_reasons="[]",
                manual_lock=False,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            db.add(item)
            await db.flush()
            db.add(
                CanonicalItemEvidence(
                    item_id=item.id,
                    item_revision=1,
                    raw_message_id=raw.id,
                    source_revision=1,
                    fact_path="deadline",
                )
            )
            deadline = AcademicDeadline(
                group_id=group.id,
                raw_message_id=raw.id,
                import_key=f"canonical:{item.id}",
                kind="exam",
                subject="Физика",
                title=item.title,
                description=item.description,
                deadline_at=item.deadline_at,
                date_only=True,
                needs_clarification=False,
                source_message_ids=json.dumps([7201]),
                created_at=now,
                delete_at=now + timedelta(days=90),
            )
            db.add(deadline)
            await db.flush()
            db.add(
                CanonicalProjectionLink(
                    item_id=item.id,
                    entity_type="deadline",
                    entity_id=deadline.id,
                    projected_revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            return deadline.id

    deadline_id = asyncio.run(seed())
    response = client.get(f"/v1/deadlines/{deadline_id}", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    item = response.json()["item"]
    assert item["kind"] == "exam"
    assert item["card_kind"] == "exam"
    assert item["confirmed_subject"]["name"] == "Физика"
    assert item["deadline_certainty"] == "from_source"
    assert item["deadline_origin"] == "explicit_message"
    assert item["sources"][0]["telegram_message_id"] == 7201
    assert item["sources"][0]["action"]["type"] == "open_telegram_link"
