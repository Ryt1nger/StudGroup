"""Server-enforced Telegram registration and persistent bot state."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from test_bot_start import (
    allow_telegram,
    finish_legal,
    issue,
    labels,
    last,
    publish_legal,
    tap,
)
from test_headman import deliver, run
from test_headman import foreman as foreman  # noqa: PLC0414 -- pytest fixture

from studgroup.models import (
    LegalConsentEvent,
    Membership,
    MemberSubgroup,
    StartSession,
    Subgroup,
    SubjectSplit,
    User,
    UserProfile,
)

pytest_plugins = ["test_schedule_api"]


def test_legal_consents_precede_membership_and_resume_after_start(foreman, monkeypatch):
    publish_legal(foreman)
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "Я студент" in labels(foreman)
    assert "Я староста" in labels(foreman)
    assert "Принимаю условия использования" not in labels(foreman)

    async def membership(db):
        return await db.scalar(select(Membership).join(User).where(User.telegram_user_id == 33))

    assert run(foreman, membership) is None
    deliver(foreman, "/start", uid=33)
    assert "Я студент" in labels(foreman)
    tap(foreman, "Я студент")
    assert "Принимаю условия использования" in labels(foreman)
    tap(foreman, "Принимаю условия использования")
    assert "Даю отдельное согласие" in labels(foreman)
    assert run(foreman, membership) is None
    deliver(foreman, "/start", uid=33)
    assert "Даю отдельное согласие" in labels(foreman)
    tap(foreman, "Даю отдельное согласие")
    assert run(foreman, membership) is None
    assert "Регистрация · шаг 1 из 3" in last(foreman)["text"]
    assert "ID" not in last(foreman)["text"]
    deliver(foreman, "/start", uid=33)
    assert "Регистрация · шаг 1 из 3" in last(foreman)["text"]
    deliver(foreman, "Анна", uid=33)
    assert run(foreman, membership) is None
    assert "Регистрация · шаг 2 из 3" in last(foreman)["text"]
    deliver(foreman, "Иванова", uid=33)
    assert "Регистрация · шаг 3 из 3" in last(foreman)["text"]
    deliver(foreman, "anna@example.org", uid=33)
    assert run(foreman, membership).role == "student"
    assert "Пропустить" not in labels(foreman)
    assert "/onboarding" not in str(last(foreman))
    assert "Открыть StudGroup" in labels(foreman)

    async def persisted(db):
        user = await db.scalar(select(User).where(User.telegram_user_id == 33))
        profile = await db.get(UserProfile, user.id)
        events = (
            await db.scalars(
                select(LegalConsentEvent)
                .where(LegalConsentEvent.user_id == user.id)
                .order_by(LegalConsentEvent.created_at)
            )
        ).all()
        return (
            (profile.first_name, profile.last_name, profile.email) if profile else None,
            user.display_name,
            profile.sdu_login if profile else None,
            profile.sdu_password_encrypted if profile else None,
            {(e.purpose, e.source) for e in events},
        )

    identity, display_name, login, password, receipts = run(foreman, persisted)
    assert identity == ("Анна", "Иванова", "anna@example.org")
    assert display_name == "Анна Иванова"
    assert login is None and password is None
    assert receipts == {("terms", "telegram"), ("service", "telegram")}


def test_invalid_invitation_or_failed_telegram_check_cannot_join(foreman, monkeypatch):
    publish_legal(foreman)
    allow_telegram(monkeypatch)
    deliver(foreman, "/start invite_" + "x" * 32, uid=33)
    assert "истекло" in last(foreman)["text"]
    assert "Ввести код" not in labels(foreman)
    allow_telegram(monkeypatch, answer=False)
    deliver(foreman, f"/start invite_{issue(foreman)}", uid=33)
    assert "Не удалось подтвердить участие" in last(foreman)["text"]
    assert "Проверить участие ещё раз" in labels(foreman)
    assert "Ввести код" not in labels(foreman)

    async def no_member(db):
        return await db.scalar(select(User.id).where(User.telegram_user_id == 33))

    assert run(foreman, no_member) is None


def test_unapproved_documents_block_new_members_but_not_existing(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    code = issue(foreman)
    foreman.app.state.settings.legal_documents_approved = False
    foreman.app.state.settings.registration_legal_required = True
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "Я студент" in labels(foreman)
    tap(foreman, "Я студент")
    assert "правовые документы ещё не утверждены" in last(foreman)["text"]
    deliver(foreman, "/start", uid=33)
    assert "правовые документы ещё не утверждены" in last(foreman)["text"]

    async def count(db):
        return await db.scalar(select(Membership.id).join(User).where(User.telegram_user_id == 33))

    assert run(foreman, count) is None
    deliver(foreman, "/start", uid=1)
    assert "Открыть StudGroup" in labels(foreman)


def test_two_real_subgroups_persist_and_survive_repeated_start(foreman, monkeypatch):
    publish_legal(foreman)
    allow_telegram(monkeypatch)

    async def make_splits(db):
        headman = await db.scalar(select(Membership).where(Membership.role == "headman"))
        expected = []
        for name in ("Английский", "Информатика"):
            split = SubjectSplit(
                id=uuid.uuid4(),
                group_id=headman.group_id,
                subject_id=uuid.uuid4(),
                subject_name=name,
                created_at=datetime.now(UTC),
            )
            db.add(split)
            await db.flush()
            db.add_all(
                [
                    Subgroup(
                        id=uuid.uuid4(),
                        split_id=split.id,
                        name=f"{name} 1",
                        position=1,
                        signals="[]",
                    ),
                    Subgroup(
                        id=uuid.uuid4(),
                        split_id=split.id,
                        name=f"{name} 2",
                        position=2,
                        signals="[]",
                    ),
                ]
            )
            await db.flush()
            second = await db.scalar(
                select(Subgroup).where(
                    Subgroup.split_id == split.id,
                    Subgroup.position == 2,
                )
            )
            expected.append((split.id, second.id))
        return expected

    expected = run(foreman, make_splits)
    deliver(foreman, f"/start invite_{issue(foreman)}", uid=33)
    finish_legal(foreman)
    assert "Английский" in last(foreman)["text"]
    tap(foreman, "Английский 2")
    assert "Информатика" in last(foreman)["text"]
    deliver(foreman, "/start", uid=33)
    assert "Информатика" in last(foreman)["text"]
    tap(foreman, "Информатика 2")
    assert "Открыть StudGroup" in labels(foreman)
    assert "Пропустить" not in labels(foreman)

    async def loaded(db):
        member = await db.scalar(select(Membership).join(User).where(User.telegram_user_id == 33))
        choices = (
            await db.scalars(
                select(MemberSubgroup).where(MemberSubgroup.membership_id == member.id)
            )
        ).all()
        session = await db.get(StartSession, 33)
        return {(r.split_id, r.subgroup_id) for r in choices}, session.step

    chosen, step = run(foreman, loaded)
    assert chosen == set(expected)
    assert step == "registration_complete"
    deliver(foreman, "/start", uid=33)
    assert "Открыть StudGroup" in labels(foreman)


def test_link_retry_preserves_invitation_without_new_code(foreman, monkeypatch):
    publish_legal(foreman)
    code = issue(foreman)
    allow_telegram(monkeypatch, answer=False)
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "Проверить участие ещё раз" in labels(foreman)
    deliver(foreman, "/start", uid=33)
    assert "Проверить участие ещё раз" in labels(foreman)
    allow_telegram(monkeypatch)
    tap(foreman, "Проверить участие ещё раз")
    assert "Я студент" in labels(foreman)
    tap(foreman, "Я студент")
    assert "Принимаю условия использования" in labels(foreman)
    assert "Ввести код" not in labels(foreman)


def test_link_headman_does_not_request_registered_group_code(foreman, monkeypatch):
    publish_legal(foreman)
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, f"/start invite_{code}", uid=33)
    tap(foreman, "Я староста")
    assert "указан старостой" in last(foreman)["text"]
    assert "идентификатор" not in last(foreman)["text"].lower()
    assert "Ввести код" not in labels(foreman)
    tap(foreman, "Продолжить как студент")
    assert "Принимаю условия использования" in labels(foreman)


def test_profile_validation_blocks_invalid_email_before_membership(foreman, monkeypatch):
    publish_legal(foreman)
    allow_telegram(monkeypatch)
    deliver(foreman, f"/start invite_{issue(foreman)}", uid=33)
    tap(foreman, "Я студент")
    tap(foreman, "Принимаю условия использования")
    tap(foreman, "Даю отдельное согласие")
    deliver(foreman, "Ольга", uid=33)
    tap(foreman, "Пропустить")
    deliver(foreman, "нет почты", uid=33)
    assert "Адрес почты некорректен" in last(foreman)["text"]

    async def count(db):
        return await db.scalar(select(Membership.id).join(User).where(User.telegram_user_id == 33))

    assert run(foreman, count) is None
    tap(foreman, "Пропустить")
    assert "Открыть StudGroup" in labels(foreman)


def test_existing_member_never_sees_registration_again(foreman):
    foreman.app.state.settings.legal_documents_approved = False
    deliver(foreman, "/start", uid=1)
    assert "Открыть StudGroup" in labels(foreman)
    assert "Принимаю условия использования" not in labels(foreman)
