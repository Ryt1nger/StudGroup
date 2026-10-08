"""Private /st navigation. Drafts stay private until explicit, revision-checked confirmation."""

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from studgroup import headman_domain as domain
from studgroup import headman_schedule
from studgroup.api import schedule_data
from studgroup.bot_admin import enqueue, say
from studgroup.deadlines import ScheduleDeadlineContext, contextual_deadline, resolve_deadline
from studgroup.homework import utc
from studgroup.models import (
    AcademicDeadline,
    AICandidate,
    AIJob,
    GroupInvitation,
    HeadmanAudit,
    HeadmanDecision,
    HeadmanSession,
    HeadmanSettings,
    Homework,
    Membership,
    RawMessage,
    User,
)


def payload(session):
    return json.loads(session.payload)


async def screen(db, session, text, rows, step="menu", data=None):
    session.nonce = secrets.token_hex(6)
    session.step = step
    session.payload = domain.dumps(data or {})
    session.expires_at = datetime.now(UTC) + timedelta(minutes=15)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": f"s:{session.nonce}:{action}"}
                for label, action in row
            ]
            for row in rows
        ]
    }
    await say(db, session.telegram_user_id, text[:4000], keyboard)


def back():
    return [("‹ Главное меню", "menu")]


async def menu(db, session, group):
    questions = await domain.cards(db, group, "review")
    questions += await candidates(db, group)
    await screen(
        db,
        session,
        f"Староста · {group.name}\nВыбери раздел. Изменения сохраняются только после подтверждения.",
        [
            [(f"Требует проверки · {len(questions)}", "review")],
            [("ДЗ и КТ", "cards")],
            [("Расписание", "schedule")],
            [("Участники", "members")],
            [("Сводка группы", "summary")],
            [("Настройки", "settings")],
            [("Журнал правок / откат", "journal")],
            [("Выбрать другую группу", "groups")],
        ],
    )


async def groups(db, session, forward=None):
    allowed = await domain.memberships(db, session.telegram_user_id)
    if not allowed:
        await screen(
            db,
            session,
            "Нет роли старосты/помощника. Роль назначает владелец через /admin; /st не выдаёт её автоматически.",
            [],
            "closed",
        )
        return
    session.group_id = None
    await screen(
        db,
        session,
        "Выбери свою группу:",
        [[(group.name[:50], f"group{n}")] for n, (_, _, group) in enumerate(allowed[:40])],
        "groups",
        {"groups": [str(g.id) for _, _, g in allowed[:40]], "forward": forward},
    )


async def forward_screen(db, session, group, forward):
    await screen(
        db,
        session,
        f"{group.name}\nПересланное сообщение от {forward['source_date']}:\n\n{forward['description'][:2500]}\n\nСначала выбери тип. Без подтверждения ничего не публикуется.",
        [[("Добавить ДЗ", "forward_h"), ("Добавить КТ", "forward_e")], back()],
        "forward",
        {"forward": forward},
    )


def title(kind, row):
    return f"{'ДЗ' if kind == 'h' else 'КТ'} · {row.subject_name if kind == 'h' else row.subject} · {row.title}"[
        :60
    ]


async def candidates(db, group, now=None):
    now = now or datetime.now(UTC)
    rows = (
        await db.scalars(
            select(AICandidate)
            .where(
                AICandidate.group_id == group.id,
                AICandidate.state.in_(["pending", "review"]),
                AICandidate.delete_at > now,
            )
            .order_by(AICandidate.created_at)
        )
    ).all()
    result = []
    for row in rows:
        decision = await db.get(HeadmanDecision, f"c:{row.id}")
        if decision and (
            decision.locked or decision.deferred_until and utc(decision.deferred_until) > now
        ):
            continue
        data = json.loads(row.payload)
        job = await db.get(AIJob, row.job_id)
        raw = await db.get(RawMessage, job.raw_message_id) if job else None
        if not raw or data.get("kind") == "irrelevant":
            continue
        evidence = contextual_deadline(raw.text, utc(raw.message_date), group.timezone)
        if (
            data.get("kind") == "homework"
            and utc(raw.message_date) < now - timedelta(days=7)
            and not (evidence and evidence.at)
        ):
            continue
        if (
            evidence
            and evidence.at
            and (
                evidence.at.astimezone(ZoneInfo(group.timezone)).date()
                < now.astimezone(ZoneInfo(group.timezone)).date()
                if evidence.date_only
                else evidence.at <= now
            )
        ):
            continue
        result.append((f"c:{row.id}", "c", row))
    return result


async def listing(db, session, group, mode, search="", page=0):
    rows = await domain.cards(db, group, mode, search)
    if mode == "review":
        rows += await candidates(db, group)
    entries = rows[page * 8 : page * 8 + 8]
    keys = [key for key, _, _ in entries]
    buttons = [
        [
            (
                ("Разобрать: " + (json.loads(r.payload).get("title") or "Неясная карточка"))[:60]
                if kind == "c"
                else title(kind, r),
                f"open{n}",
            )
        ]
        for n, (_, kind, r) in enumerate(entries)
    ]
    nav = []
    if page:
        nav.append(("‹", "page_prev"))
    if len(rows) > (page + 1) * 8:
        nav.append(("›", "page_next"))
    if nav:
        buttons.append(nav)
    buttons += [
        [("Добавить ДЗ", "new_h"), ("Добавить КТ", "new_e")],
        [("Найти по предмету/названию", "search")],
        [("Актуальные", "active"), ("Архив", "archive")],
        back(),
    ]
    await screen(
        db,
        session,
        f"{'Требует проверки' if mode == 'review' else 'Карточки'} · {len(rows)}\n"
        + ("Выбери карточку." if entries else "Здесь пока нет карточек."),
        buttons,
        "list",
        {"keys": keys, "mode": mode, "search": search, "page": page},
    )


async def detail(db, session, group, key):
    kind, row = await domain.entity(db, group, key)
    if kind == "c":
        data = json.loads(row.payload)
        job = await db.get(AIJob, row.job_id)
        raw = await db.get(RawMessage, job.raw_message_id) if job else None
        text = f"Неопубликованный черновик\n{data.get('subject') or 'Предмет не определён'}\n{data.get('title') or 'Название не определено'}\n{data.get('description') or ''}\n\nИсточник: {raw.text[:1800] if raw else 'недоступен'}"
        await screen(
            db,
            session,
            text,
            [
                [("Создать ДЗ", "candidate_h"), ("Создать КТ", "candidate_e")],
                [("Не задание", "reject"), ("Вернуться позже", "defer")],
                back(),
            ],
            "detail",
            {"key": key, "revision": row.revision},
        )
        return
    deadline = row.deadline_at or (row.window_start if kind == "e" else None)
    stamp = (
        utc(deadline).astimezone(ZoneInfo(group.timezone)).strftime("%d.%m.%Y %H:%M")
        if deadline
        else "Срок не определён"
    )
    raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
    text = f"{title(kind, row)}\n\n{row.description or ''}\n\nСрок: {stamp} ({group.timezone})\n"
    if kind == "h" and row.verification_state == "inferred":
        text += "Срок предположительный: рассчитан по расписанию.\n"
    if kind == "e" and row.window_end:
        text += f"Период до {utc(row.window_end).astimezone(ZoneInfo(group.timezone)).strftime('%d.%m.%Y %H:%M')}\n"
    text += f"\nИсточник: {raw.text[:1500] if raw and utc(raw.delete_at) > datetime.now(UTC) else 'вручную или срок хранения истёк'}"
    await screen(
        db,
        session,
        text,
        [
            [("Подтвердить", "confirm")],
            [("Предмет", "edit_subject"), ("Название", "edit_title")],
            [("Описание", "edit_description"), ("Срок", "edit_deadline")],
            [("Добавить материал-ссылку", "material")],
            [("Это КТ" if kind == "h" else "Это ДЗ", "convert"), ("Объединить", "merge")],
            [("Отменить", "cancel"), ("Не задание", "reject")],
            [("Вернуться позже", "defer")],
            back(),
        ],
        "detail",
        {"key": key, "revision": row.revision},
    )


async def ask(db, session, prompt, data, field):
    await screen(db, session, prompt, [back()], "text", {**data, "field": field})


async def preview(db, session, text, draft):
    await screen(
        db,
        session,
        "Предпросмотр\n\n" + text + "\n\nСохранить для группы?",
        [[("Подтвердить изменение", "yes")], back()],
        "confirm",
        draft,
    )


def decode_draft(value):
    for name in ["deadline_at", "window_start", "window_end", "starts_at", "ends_at"]:
        if value.get(name) and isinstance(value[name], str):
            value[name] = datetime.fromisoformat(value[name])
    return value


def parse_date(text, group, kind):
    zone = ZoneInfo(group.timezone)
    text = text.strip()
    window = re.fullmatch(r"(\d{2}\.\d{2}\.\d{4})\s*[-–—]\s*(\d{2}\.\d{2}\.\d{4})", text)
    if window:
        if kind != "e":
            raise domain.StError("Период доступен для КТ. Для ДЗ укажи одну дату.")
        start, end = [
            datetime.strptime(s, "%d.%m.%Y").replace(tzinfo=zone) for s in window.groups()
        ]
        if end < start:
            raise domain.StError("Конец периода раньше начала.")
        return {
            "deadline_at": None,
            "date_only": False,
            "window_start": start,
            "window_end": end + timedelta(days=1),
        }
    for fmt in ["%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
        try:
            stamp = datetime.strptime(text, fmt).replace(tzinfo=zone)
            return {
                "deadline_at": stamp,
                "date_only": "%H" not in fmt,
                "window_start": None,
                "window_end": None,
            }
        except ValueError:
            continue
    raise domain.StError(
        "Формат: 12.10.2026 или 12.10.2026 18:30. Для КТ период: 10.10.2026–15.10.2026."
    )


async def date_menu(db, session, data):
    await screen(
        db,
        session,
        "Укажи срок. Указанное время сохранится точно; «До конца дня» не ограничивается парой.",
        [
            [("Следующая пара предмета", "date_next")],
            [("Сегодня", "date_today"), ("Завтра", "date_tomorrow")],
            [("Дата / время / период", "date_manual")],
            [("До конца выбранного дня", "date_end")],
            [("Срок пока неизвестен", "date_none")],
            back(),
        ],
        "date",
        data,
    )


async def finish_date(db, session, group, data, value):
    kind = data["key"][0] if data.get("edit") else data["kind"]
    if kind == "h" and value.get("date_only"):
        from studgroup.homework_timing import lesson_deadline

        _, existing = (
            await domain.entity(db, group, data["key"]) if data.get("edit") else (None, None)
        )
        subject = existing.subject_name if existing else data["values"]["subject"]
        value["deadline_at"], value["date_only"] = await lesson_deadline(
            db, group, subject, value.get("deadline_at"), True, ""
        )
    if data.get("edit"):
        kind = data["key"][0]
        patch = {
            "deadline_at": value.get("deadline_at"),
            "deadline_date_only" if kind == "h" else "date_only": value.get("date_only", False),
        }
        if kind == "e":
            patch.update({k: value.get(k) for k in ["window_start", "window_end"]})
        await preview(
            db,
            session,
            "Новый срок: " + domain.dumps(value),
            {"action": "edit", "key": data["key"], "revision": data["revision"], "patch": patch},
        )
    else:
        data["values"].update(value)
        await preview(db, session, domain.dumps(data["values"]), {**data, "action": "create"})


async def settings_screen(db, session, group, member):
    config = await db.get(HeadmanSettings, member.id)
    enabled = bool(config and config.questions_enabled)
    await screen(
        db,
        session,
        f"Часовой пояс: {group.timezone}\nЛичные вопросы: {'включены' if enabled else 'выключены'}\nВремя вопросов: {config.digest_times if config else '09:00'}\nНе более двух непустых подборок в день; ручная работа доступна при выключенном ИИ.",
        [
            [("Выключить вопросы" if enabled else "Включить вопросы", "toggle_questions")],
            [("Время вопросов", "digest_times")],
            [("Часовой пояс группы", "timezone")],
            back(),
        ],
        "settings",
        {"enabled": enabled},
    )


async def schedule_screen(db, session, group, page=0):
    day = datetime.now(UTC).astimezone(ZoneInfo(group.timezone)).date()
    calendar = await schedule_data(day, day + timedelta(days=14), group, db)
    entries = calendar["lessons"][page * 8 : page * 8 + 8]
    buttons = [
        [
            (
                f"{datetime.fromisoformat(l['starts_at']).astimezone(ZoneInfo(group.timezone)).strftime('%d.%m %H:%M')} · {l['subject']}"[
                    :60
                ],
                f"lesson{n}",
            )
        ]
        for n, l in enumerate(entries)
    ]
    if page:
        buttons += [[("‹", "schedule_prev")]]
    if len(calendar["lessons"]) > (page + 1) * 8:
        buttons += [[("›", "schedule_next")]]
    buttons += [[("Добавить отдельную пару", "schedule_add")], back()]
    await screen(
        db,
        session,
        "Расписание ближайших двух недель"
        if entries
        else "Пар в известном расписании нет. Срок по следующей паре определить нельзя.",
        buttons,
        "schedule",
        {"lessons": [l["id"] for l in entries], "page": page},
    )


async def members_screen(db, session, group, member, page=0):
    domain.headman_only(member)
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.group_id == group.id)
            .order_by(User.display_name, Membership.id)
        )
    ).all()
    entries = rows[page * 8 : page * 8 + 8]
    buttons = [
        [(f"{u.display_name} · {u.telegram_user_id} · {m.status}"[:60], f"member{n}")]
        for n, (m, u) in enumerate(entries)
    ]
    if page:
        buttons += [[("‹", "members_prev")]]
    if len(rows) > (page + 1) * 8:
        buttons += [[("›", "members_next")]]
    buttons += [
        [("Создать приглашения", "invite")],
        [("Отозвать действующие приглашения", "revoke_invites")],
        back(),
    ]
    await screen(
        db,
        session,
        f"Подключены к StudGroup: {len(rows)}. Это не полный список участников Telegram.",
        buttons,
        "members",
        {"members": [str(m.id) for m, _ in entries], "page": page},
    )


async def handle(db, update, settings):
    callback = update.callback_query
    message = callback.message if callback else update.message
    sender = callback.sender if callback else message.sender if message else None
    if not message or not sender or message.chat.type != "private" or message.chat.id != sender.id:
        return False
    text = (message.text or "").strip()
    if not callback and text.startswith("/start invite_"):
        try:
            async with db.begin_nested():
                group = await domain.redeem_invite(
                    db, settings, sender.id, text.split("invite_", 1)[1]
                )
            await say(
                db,
                sender.id,
                f"Ты подключён к «{group.name}». Открой приложение заново.",
                {
                    "inline_keyboard": [
                        [{"text": "Открыть StudGroup", "web_app": {"url": settings.webapp_origin}}]
                    ]
                },
            )
        except domain.StError as error:
            await say(db, sender.id, str(error))
        return True
    session = await db.scalar(
        select(HeadmanSession).where(HeadmanSession.telegram_user_id == sender.id).with_for_update()
    )
    command = text.split("@")[0]
    requested = not callback and command in {"/st", "/st_cancel"}
    own_callback = callback and (callback.data or "").startswith("s:")
    if not callback and message.forward_origin and text and (not session or session.step != "text"):
        allowed = await domain.memberships(db, sender.id)
        if not allowed:
            return False
        if session is None:
            session = HeadmanSession(
                telegram_user_id=sender.id,
                nonce="",
                step="groups",
                payload="{}",
                expires_at=datetime.now(UTC),
            )
            db.add(session)
        forward = {
            "description": text[:12000],
            "source_date": datetime.fromtimestamp(message.forward_origin.date, UTC).isoformat(),
        }
        if len(allowed) == 1:
            session.group_id = allowed[0][2].id
            await forward_screen(db, session, allowed[0][2], forward)
        else:
            await groups(db, session, forward)
        return True
    if (
        not requested
        and not own_callback
        and (
            not session
            or session.step
            in {
                "closed",
                "groups",
                "menu",
                "detail",
                "list",
                "confirm",
                "settings",
                "schedule",
                "members",
                "merge",
                "journal",
                "forward",
            }
            or text.startswith("/")
        )
    ):
        return False
    if callback and not own_callback:
        return False
    if callback:
        enqueue(db, "answerCallbackQuery", {"callback_query_id": callback.id})
    if requested:
        if session is None:
            session = HeadmanSession(
                telegram_user_id=sender.id,
                nonce="",
                step="groups",
                payload="{}",
                expires_at=datetime.now(UTC),
            )
            db.add(session)
        await groups(db, session)
        return True
    if not session or utc(session.expires_at) <= datetime.now(UTC):
        await say(db, sender.id, "Сессия истекла. Отправь /st заново.")
        return True
    try:
        data = payload(session)
        action = ""
        if callback:
            parts = (callback.data or "").split(":", 2)
            if len(parts) != 3 or not secrets.compare_digest(parts[1], session.nonce):
                raise domain.StError("Кнопка устарела. Отправь /st заново.")
            action = parts[2]
        if action == "groups":
            await groups(db, session)
            return True
        if session.step == "groups" and action.startswith("group"):
            selected = data["groups"][int(action[5:])]
            user, member, group = await domain.access(db, sender.id, uuid.UUID(selected))
            session.group_id = group.id
            if data.get("forward"):
                await forward_screen(db, session, group, data["forward"])
            else:
                await menu(db, session, group)
            return True
        user, member, group = await domain.access(db, sender.id, session.group_id)
        if action == "menu":
            await menu(db, session, group)
        elif action in {"cards", "active", "archive", "review"}:
            await listing(db, session, group, "active" if action in {"cards", "active"} else action)
        elif session.step == "list" and action in {"page_prev", "page_next"}:
            await listing(
                db,
                session,
                group,
                data["mode"],
                data["search"],
                max(0, data["page"] + (-1 if action == "page_prev" else 1)),
            )
        elif session.step == "list" and action.startswith("open"):
            await detail(db, session, group, data["keys"][int(action[4:])])
        elif action == "search":
            await ask(db, session, "Введи предмет или часть названия.", {}, "search")
        elif action in {"new_h", "new_e", "candidate_h", "candidate_e", "forward_h", "forward_e"}:
            draft = {"kind": action[-1], "values": {}, "source_date": datetime.now(UTC).isoformat()}
            if action.startswith("forward"):
                draft["values"]["description"] = data["forward"]["description"]
                draft["source_date"] = data["forward"]["source_date"]
            if action.startswith("candidate"):
                _, candidate = await domain.entity(db, group, data["key"])
                job = await db.get(AIJob, candidate.job_id)
                raw = await db.get(RawMessage, job.raw_message_id) if job else None
                draft.update(
                    {
                        "candidate": data["key"],
                        "candidate_revision": candidate.revision,
                        "source_date": utc(raw.message_date).isoformat()
                        if raw
                        else draft["source_date"],
                    }
                )
                draft["values"]["description"] = json.loads(candidate.payload).get(
                    "description"
                ) or (raw.text if raw else "")
            await ask(db, session, "Предмет?", draft, "new_subject")
        elif session.step == "detail" and action.startswith("edit_"):
            field = action[5:]
            if field == "deadline":
                await date_menu(db, session, {**data, "edit": True})
            else:
                await ask(
                    db, session, f"Новое значение: {field}. Отправь текст.", data, "edit_" + field
                )
        elif session.step == "detail" and action == "material":
            await ask(
                db,
                session,
                "Пришли название и HTTPS-ссылку на двух строках. Файлы/OCR в этот MVP не входят.",
                data,
                "material",
            )
        elif session.step == "detail" and action == "merge":
            targets = [
                r
                for r in await domain.cards(db, group)
                if r[0] != data["key"] and r[1] == data["key"][0]
            ]
            await screen(
                db,
                session,
                "С какой карточкой объединить? Описание и материалы добавятся к выбранной, исходная уйдёт в архив.",
                [[(title(k, r), f"target{n}")] for n, (_, k, r) in enumerate(targets[:30])]
                + [back()],
                "merge",
                {
                    **data,
                    "targets": [{"key": key, "revision": r.revision} for key, _, r in targets[:30]],
                },
            )
        elif session.step == "merge" and action.startswith("target"):
            target = data["targets"][int(action[6:])]
            await preview(
                db,
                session,
                "Объединить две карточки?",
                {**data, "action": "merge", "target": target},
            )
        elif session.step == "detail" and action in {
            "confirm",
            "cancel",
            "reject",
            "defer",
            "convert",
        }:
            await preview(
                db,
                session,
                {
                    "confirm": "Подтвердить информацию",
                    "cancel": "Отменить карточку. Это не отметка выполнения студентов",
                    "reject": "Исключить ошибочное задание из актуальных",
                    "defer": "Отложить вопрос на 12 часов",
                    "convert": "Сменить ДЗ ↔ КТ, без дубликата в актуальных",
                }[action],
                {**data, "action": action},
            )
        elif session.step == "date" and action.startswith("date_"):
            if action in {"date_manual", "date_end"}:
                await ask(
                    db,
                    session,
                    "Дата: 12.10.2026. Можно указать время: 12.10.2026 18:30. Для КТ можно период 10.10.2026–15.10.2026.",
                    {**data, "end_day": action == "date_end"},
                    "date",
                )
            elif action == "date_none":
                await finish_date(
                    db, session, group, data, {"deadline_at": None, "date_only": False}
                )
            elif action == "date_next":
                if data.get("edit"):
                    kind, row = await domain.entity(db, group, data["key"])
                    subject = row.subject_name if kind == "h" else row.subject
                    reference = datetime.now(UTC)
                else:
                    subject = data["values"]["subject"]
                    reference = datetime.fromisoformat(data["source_date"])
                day = reference.astimezone(ZoneInfo(group.timezone)).date()
                calendar = await schedule_data(day, day + timedelta(days=14), group, db)
                resolved = resolve_deadline(
                    [("", reference)],
                    group.timezone,
                    subject,
                    schedule=ScheduleDeadlineContext(
                        calendar["lessons"],
                        day,
                        day + timedelta(days=14),
                        calendar["week_state"] == "ready",
                    ),
                )
                if resolved.at is None:
                    raise domain.StError(
                        "Следующая пара неизвестна для этой даты сообщения. Укажи дату вручную или добавь расписание."
                    )
                await finish_date(
                    db, session, group, data, {"deadline_at": resolved.at, "date_only": False}
                )
            else:
                day = datetime.now(UTC).astimezone(ZoneInfo(group.timezone)).date() + timedelta(
                    days=action == "date_tomorrow"
                )
                await finish_date(
                    db,
                    session,
                    group,
                    data,
                    {
                        "deadline_at": datetime.combine(day, time.min, ZoneInfo(group.timezone)),
                        "date_only": True,
                    },
                )
        elif (
            action == "schedule"
            or session.step == "schedule"
            and action in {"schedule_prev", "schedule_next"}
        ):
            await schedule_screen(
                db,
                session,
                group,
                max(
                    0,
                    data.get("page", 0)
                    + (-1 if action == "schedule_prev" else 1 if action == "schedule_next" else 0),
                ),
            )
        elif session.step == "schedule" and action.startswith("lesson"):
            ident = data["lessons"][int(action[6:])]
            pattern, override, lesson, _ = await headman_schedule.occurrence(db, group, ident)
            await screen(
                db,
                session,
                f"{lesson['subject']}\n{lesson['starts_at']} — {lesson['ends_at']}\n{lesson['location'] or ''}\n{lesson['status']}",
                [
                    [("Перенести дату/время", "lesson_time")],
                    [("Аудитория / корпус / адрес", "lesson_location")],
                    [("Онлайн и ссылка", "lesson_online")],
                    [
                        (
                            "Восстановить" if lesson["status"] == "cancelled" else "Отменить",
                            "lesson_cancel",
                        )
                    ],
                    back(),
                ],
                "lesson",
                {
                    "ident": ident,
                    "pattern_revision": pattern.revision,
                    "override_revision": override.revision if override else 0,
                    "cancelled": lesson["status"] == "cancelled",
                    "validity": f"{pattern.valid_from} — {pattern.valid_until}",
                },
            )
        elif session.step == "lesson" and action.startswith("lesson_"):
            field = action[7:]
            if field == "cancel":
                patch = {"cancelled": not data["cancelled"]}
                await screen(
                    db,
                    session,
                    "Область изменения?",
                    [
                        [("Только эта пара", "scope_once")],
                        [("Всё повторяющееся правило", "scope_recurring")],
                        back(),
                    ],
                    "scope",
                    {**data, "patch": patch},
                )
            else:
                await ask(
                    db,
                    session,
                    "Формат: 12.10.2026 09:00–10:20"
                    if field == "time"
                    else "Введи аудиторию, корпус и адрес."
                    if field == "location"
                    else "Введи HTTPS-ссылку на дистанционную пару.",
                    data,
                    "lesson_" + field,
                )
        elif session.step == "scope" and action in {"scope_once", "scope_recurring"}:
            await preview(
                db,
                session,
                domain.dumps(data["patch"])
                + (
                    "\nТолько одна пара."
                    if action == "scope_once"
                    else "\nВсё правило, включая период "
                    + data["validity"]
                    + ". Разовые исключения останутся."
                ),
                {**data, "action": "schedule_change", "scope": action[6:]},
            )
        elif action == "schedule_add":
            await ask(
                db,
                session,
                "Четыре строки: предмет; дата ДД.ММ.ГГГГ; время 09:00–10:20; аудитория, корпус и адрес.",
                {},
                "schedule_add",
            )
        elif (
            action == "members"
            or session.step == "members"
            and action in {"members_prev", "members_next"}
        ):
            await members_screen(
                db,
                session,
                group,
                member,
                max(
                    0,
                    data.get("page", 0)
                    + (-1 if action == "members_prev" else 1 if action == "members_next" else 0),
                ),
            )
        elif session.step == "members" and action.startswith("member"):
            domain.headman_only(member)
            target = await db.get(Membership, uuid.UUID(data["members"][int(action[6:])]))
            if target.group_id != group.id or target.role != "student":
                raise domain.StError("Роли старост и помощников меняет владелец через /admin.")
            await preview(
                db,
                session,
                "Отозвать доступ к приложению (не бан в Telegram)?"
                if target.status == "active"
                else "Восстановить доступ? Потребуется проверка участия в Telegram.",
                {"action": "member_change", "member_id": str(target.id), "expected": target.status},
            )
        elif action == "invite":
            domain.headman_only(member)
            await ask(
                db,
                session,
                "Сколько одноразовых приглашений создать? От 1 до 10; действуют 24 часа.",
                {},
                "invite",
            )
        elif action == "revoke_invites":
            domain.headman_only(member)
            await preview(
                db,
                session,
                "Отозвать все ещё не использованные приглашения группы?",
                {"action": "revoke_invites"},
            )
        elif action == "summary":
            active = await domain.cards(db, group)
            review = await domain.cards(db, group, "review")
            received = await db.scalar(
                select(func.max(RawMessage.version_date)).where(RawMessage.group_id == group.id)
            )
            finished = await db.scalar(
                select(func.max(AIJob.created_at)).where(
                    AIJob.group_id == group.id, AIJob.state == "done"
                )
            )
            pending = await db.scalar(
                select(func.count())
                .select_from(AIJob)
                .where(AIJob.group_id == group.id, AIJob.state.in_(["pending", "retry", "running"]))
            )
            await screen(
                db,
                session,
                f"{group.name}\nАктуальных ДЗ и КТ: {len(active)}\nТребует проверки: {len(review)}\nПолучено последнее сообщение: {received or 'нет сообщений'}\nПоследнее успешно разобранное задание очереди создано: {finished or 'разборов пока нет'}\nОжидают разбора: {pending}\nРучные правки доступны независимо от ИИ.",
                [back()],
            )
        elif action == "settings":
            await settings_screen(db, session, group, member)
        elif session.step == "settings" and action == "toggle_questions":
            await preview(
                db,
                session,
                "Включить личные вопросы без пустых подборок?"
                if not data["enabled"]
                else "Выключить личные вопросы?",
                {"action": "questions", "enabled": not data["enabled"]},
            )
        elif session.step == "settings" and action in {"digest_times", "timezone"}:
            if action == "timezone":
                domain.headman_only(member)
            await ask(
                db,
                session,
                "Один или два времени через запятую: 09:00,18:00. Разрешено 07:00–22:00."
                if action == "digest_times"
                else "Часовой пояс IANA, например Europe/Moscow. Изменится календарь всей группы.",
                {},
                action,
            )
        elif action == "journal":
            audits = (
                await db.scalars(
                    select(HeadmanAudit)
                    .where(
                        HeadmanAudit.group_id == group.id,
                        HeadmanAudit.undone.is_(False),
                        HeadmanAudit.created_at > datetime.now(UTC) - timedelta(days=90),
                    )
                    .order_by(HeadmanAudit.created_at.desc())
                    .limit(20)
                )
            ).all()
            await screen(
                db,
                session,
                "Последние правки. Откат возможен, только если данные не изменились после этой правки.",
                [
                    [(f"{a.created_at:%d.%m %H:%M} · {a.operation}"[:60], f"undo{n}")]
                    for n, a in enumerate(audits)
                ]
                + [back()],
                "journal",
                {"audits": [str(a.id) for a in audits]},
            )
        elif session.step == "journal" and action.startswith("undo"):
            await preview(
                db,
                session,
                "Откатить выбранную правку? Выполнение студентов не сбрасывается.",
                {"action": "undo", "audit_id": data["audits"][int(action[4:])]},
            )
        elif session.step == "confirm" and action == "yes":
            async with db.begin_nested():
                await apply(db, session, group, user, member, data, settings)
        elif session.step == "text" and not callback:
            await typed(db, session, group, user, member, data, text, message)
        else:
            raise domain.StError("Открой раздел заново через /st.")
    except (domain.StError, ValueError, IndexError, KeyError, ZoneInfoNotFoundError) as error:
        await say(
            db,
            sender.id,
            str(error)
            if isinstance(error, domain.StError)
            else "Некорректный ввод или устаревшее меню. Для нового меню — /st.",
        )
    return True


async def typed(db, session, group, user, member, data, text, message):
    field = data["field"]
    if not text or len(text) > 12000:
        raise domain.StError("Нужен текст до 12 000 символов.")
    if field == "search":
        await listing(db, session, group, "active", text[:255])
    elif field.startswith("new_"):
        name = field[4:]
        if name in {"subject", "title"} and len(text) > 255:
            raise domain.StError("Предмет и название — до 255 символов.")
        data["values"][name] = text
        if message.forward_origin:
            data["source_date"] = datetime.fromtimestamp(
                message.forward_origin.date, UTC
            ).isoformat()
        if name == "subject":
            await ask(db, session, "Название задания?", data, "new_title")
        elif name == "title":
            if data["values"].get("description"):
                await date_menu(db, session, data)
            else:
                await ask(
                    db,
                    session,
                    "Описание задачи? Можно переслать исходное текстовое сообщение: его дата сохранится.",
                    data,
                    "new_description",
                )
        else:
            await date_menu(db, session, data)
    elif field.startswith("edit_"):
        kind, _row = await domain.entity(db, group, data["key"])
        name = field[5:]
        if (
            name not in {"subject", "title", "description"}
            or name != "description"
            and len(text) > 255
        ):
            raise domain.StError("Предмет/название — до 255 символов.")
        patch = {"subject_name" if kind == "h" and name == "subject" else name: text}
        if kind == "h" and name == "subject":
            patch["subject_id"] = str(uuid.uuid5(group.id, text.casefold()))
        await preview(
            db,
            session,
            text,
            {**data, "action": "description" if name == "description" else "edit", "patch": patch},
        )
    elif field == "date":
        kind = data["key"][0] if data.get("edit") else data["kind"]
        value = parse_date(text, group, kind)
        if data.get("end_day"):
            at = value.get("deadline_at")
            if at is None:
                raise domain.StError("Укажи одну дату для конца дня.")
            value["deadline_at"] = at.replace(hour=23, minute=59, second=59)
            value["date_only"] = False
        await finish_date(db, session, group, data, value)
    elif field == "material":
        parts = text.splitlines()
        if len(parts) != 2 or len(parts[0]) > 255:
            raise domain.StError("Пришли две строки: название и HTTPS-ссылка.")
        await preview(
            db,
            session,
            text,
            {**data, "action": "material", "title": parts[0], "url": domain.safe_url(parts[1])},
        )
    elif field.startswith("lesson_") or field == "schedule_add":
        if field == "schedule_add":
            parts = text.splitlines()
            if len(parts) != 4 or len(parts[0]) > 255 or len(parts[3]) > 512:
                raise domain.StError("Нужны четыре строки: предмет; дата; время; адрес.")
            clock = parts[1] + " " + parts[2]
        else:
            clock = text
        if field in {"lesson_time", "schedule_add"}:
            match = re.fullmatch(
                r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s*[-–—]\s*(\d{2}:\d{2})", clock
            )
            if not match:
                raise domain.StError("Формат: 12.10.2026 09:00–10:20.")
            starts = datetime.strptime(match[1] + " " + match[2], "%d.%m.%Y %H:%M").replace(
                tzinfo=ZoneInfo(group.timezone)
            )
            ends = datetime.strptime(match[1] + " " + match[3], "%d.%m.%Y %H:%M").replace(
                tzinfo=ZoneInfo(group.timezone)
            )
            if ends <= starts:
                raise domain.StError("Конец пары должен быть позже начала.")
            patch = {"starts_at": starts, "ends_at": ends}
        elif field == "lesson_location":
            if len(text) > 512:
                raise domain.StError("Адрес — до 512 символов.")
            patch = {"location": text}
        else:
            patch = {"location": "Онлайн", "online_url": domain.safe_url(text)}
        if field == "schedule_add":
            await preview(
                db,
                session,
                text,
                {"action": "schedule_add", "subject": parts[0], "location": parts[3], **patch},
            )
        else:
            await screen(
                db,
                session,
                "Область изменения?",
                [
                    [("Только эта пара", "scope_once")],
                    [("Всё повторяющееся правило", "scope_recurring")],
                    back(),
                ],
                "scope",
                {**data, "patch": patch},
            )
    elif field == "invite":
        count = int(text)
        if not 1 <= count <= 10:
            raise domain.StError("От 1 до 10 приглашений.")
        await preview(
            db,
            session,
            f"Создать {count} одноразовых приглашений на 24 часа?",
            {"action": "invite", "count": count},
        )
    elif field == "digest_times":
        values = [v.strip() for v in text.split(",")]
        if not 1 <= len(set(values)) == len(values) <= 2 or any(
            not re.fullmatch(r"\d{2}:\d{2}", v) or not "07:00" <= v <= "22:00" or int(v[3:]) >= 60
            for v in values
        ):
            raise domain.StError("Один или два разных времени в диапазоне 07:00–22:00.")
        await preview(
            db,
            session,
            "Время вопросов: " + text,
            {"action": "digest_times", "times": ",".join(sorted(values))},
        )
    elif field == "timezone":
        domain.headman_only(member)
        ZoneInfo(text)
        await preview(
            db,
            session,
            f"Часовой пояс всей группы: {group.timezone} → {text}. Изменится отображение дат и расписания.",
            {"action": "timezone", "timezone": text, "expected": group.timezone},
        )


async def apply(db, session, group, user, member, data, settings):
    action = data["action"]
    aid = None
    if action == "create":
        candidate = None
        if data.get("candidate"):
            _kind, candidate = await domain.entity(db, group, data["candidate"])
            await domain.guard(
                db, group, member, data["candidate"], candidate, data["candidate_revision"]
            )
            job = await db.get(AIJob, candidate.job_id)
            if job:
                await db.scalar(
                    select(RawMessage.id)
                    .where(RawMessage.id == job.raw_message_id, RawMessage.group_id == group.id)
                    .with_for_update()
                )
                existing = await db.scalar(
                    select(Homework.id).where(
                        Homework.raw_message_id == job.raw_message_id, Homework.group_id == group.id
                    )
                )
                event = await db.scalar(
                    select(AcademicDeadline.id).where(
                        AcademicDeadline.raw_message_id == job.raw_message_id,
                        AcademicDeadline.group_id == group.id,
                    )
                )
                if existing or event:
                    raise domain.StError(
                        "Источник уже превратился в карточку. Обнови список вместо создания дубликата."
                    )
        values = decode_draft(data["values"])
        key, aid = await domain.create_card(
            db,
            group,
            user,
            member,
            data["kind"],
            values,
            datetime.fromisoformat(data["source_date"]),
        )
        if data.get("candidate"):
            _kind, candidate = await domain.entity(db, group, data["candidate"])
            await domain.guard(
                db, group, member, data["candidate"], candidate, data["candidate_revision"]
            )
            candidate.state = "published"
            candidate.revision += 1
            job = await db.get(AIJob, candidate.job_id)
            if job:
                _, row = await domain.entity(db, group, key)
                await domain.lock_decision(db, group, user, member, f"r:{job.raw_message_id}", row)
    elif action == "convert":
        aid = await domain.convert(db, group, user, member, data["key"], data["revision"])
    elif action == "merge":
        aid = await domain.merge(
            db,
            group,
            user,
            member,
            data["key"],
            data["revision"],
            data["target"]["key"],
            data["target"]["revision"],
        )
    elif action == "material":
        aid = await domain.add_material(
            db, group, user, member, data["key"], data["revision"], data["title"], data["url"]
        )
    elif action in {"confirm", "cancel", "reject", "defer", "edit", "description"}:
        patch = decode_draft(data.get("patch", {}))
        if patch.get("subject_id"):
            patch["subject_id"] = uuid.UUID(patch["subject_id"])
        aid = await domain.mutate(
            db, group, user, member, data["key"], data["revision"], action, patch
        )
    elif action == "undo":
        aid = await domain.undo(db, group, user, member, uuid.UUID(data["audit_id"]))
    elif action == "schedule_change":
        aid = await headman_schedule.change(
            db,
            group,
            user,
            member,
            data["ident"],
            data["pattern_revision"],
            data["override_revision"],
            data["scope"],
            decode_draft(data["patch"]),
        )
    elif action == "schedule_add":
        data = decode_draft(data)
        aid = await headman_schedule.add(
            db,
            group,
            user,
            member,
            data["subject"],
            data["starts_at"],
            data["ends_at"],
            data["location"],
        )
    elif action == "invite":
        codes = await domain.issue_invites(db, group, member, data["count"])
        await screen(
            db,
            session,
            "Одноразовые коды (24 часа). Передай участникам: отправить боту /start invite_КОД.\n\n"
            + "\n".join(f"/start invite_{code}" for code in codes),
            [back()],
        )
        return
    elif action == "revoke_invites":
        domain.headman_only(member)
        rows = (
            await db.scalars(
                select(GroupInvitation)
                .where(
                    GroupInvitation.group_id == group.id,
                    GroupInvitation.used_by.is_(None),
                    GroupInvitation.expires_at > datetime.now(UTC),
                )
                .with_for_update()
            )
        ).all()
        for invitation in rows:
            invitation.revoked = True
    elif action == "member_change":
        domain.headman_only(member)
        target = await db.scalar(
            select(Membership)
            .where(Membership.id == uuid.UUID(data["member_id"]), Membership.group_id == group.id)
            .with_for_update()
        )
        if not target or target.role != "student" or target.status != data["expected"]:
            raise domain.StError("Участник или его права уже изменились.")
        if target.status != "active":
            student = await db.get(User, target.user_id)
            if not await domain.telegram_member(settings, group, student.telegram_user_id):
                raise domain.StError(
                    "Не удалось проверить участие в Telegram. Доступ не восстановлен."
                )
        target.status = "suspended" if target.status == "active" else "active"
    elif action in {"questions", "digest_times"}:
        config = await db.get(HeadmanSettings, member.id)
        if config is None:
            config = HeadmanSettings(membership_id=member.id)
            db.add(config)
        if action == "questions":
            config.questions_enabled = data["enabled"]
        else:
            config.digest_times = data["times"]
    elif action == "timezone":
        domain.headman_only(member)
        if group.timezone != data["expected"]:
            raise domain.StError("Часовой пояс уже изменён. Открой настройки заново.")
        group.timezone = data["timezone"]
    else:
        raise domain.StError("Неизвестное действие.")
    await db.flush()
    await screen(
        db,
        session,
        "Сохранено для группы."
        + (" Правка есть в журнале; её можно откатить до следующего изменения." if aid else ""),
        [back()],
    )
