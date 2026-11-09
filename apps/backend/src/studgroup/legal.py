"""Public legal texts and append-only, versioned receipts for separate user consents.

Legal drafting is not legal approval: accepting is disabled until operator details,
a documented Russia-residency check and approval are configured.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import LegalConsentEvent, UserProfile

router = APIRouter(prefix="/v1/legal")
VERSION = "2026-11-09-v2"

DOCUMENTS = {
    "privacy": """ПОЛИТИКА ОБРАБОТКИ ПЕРСОНАЛЬНЫХ ДАННЫХ STUDGROUP

Оператор: {operator}.
Адрес: {address}. Контакт для запросов, исправления, отзыва и удаления: {contact}.
Редакция: {version}.

1. StudGroup — независимый помощник учебных Telegram-групп, не официальный сервис РАНХиГС.
2. Цели: создание учётной записи, подтверждение Telegram-членства, роли, расписание, домашние задания, подгруппы, события и пользовательские уведомления. Для добровольного хранения учётных данных СДО/ЛК используется отдельное согласие.
3. Категории: Telegram ID, имя/ник, группа/роль, настройки и действия, указанные самим пользователем контакты, учебные задания и сообщения в подключённом чате, сведения о подтверждённых согласиях, технические события безопасности.
4. Обработка: сбор, запись, хранение, уточнение, использование, ограниченное предоставление по правам, удаление и уничтожение. Основание определяется для каждой цели отдельно: договор, согласие либо иное применимое основание ст. 6 152-ФЗ.
5. Сообщения учебного Telegram-чата могут включать сведения третьих лиц. Их обработка и возможная передача внешним поставщикам AI допускаются только после установления правовых оснований и выполнения требований 152-ФЗ.
6. При доступе к СДО/ЛК пароль передаётся по защищённому HTTPS-каналу, шифруется на сервере, не возвращается пользователю и не передаётся старосте. Реальная интеграция с вузовской системой не заявляется до подтверждения её допустимости.
7. Хранение персональных данных граждан РФ: {data_region}. Вопросы локализации, подрядчиков, резервных копий и трансграничной передачи оператор проверяет по ст. 12 и 18 152-ФЗ. Список подрядчиков доступен по запросу на {contact}.
8. Сроки: до прекращения законной цели обработки и выполнения установленных законом обязанностей. Добровольные логин и пароль СДО/ЛК удаляются при отзыве отдельного согласия. В установленных законом случаях уничтожение иных данных осуществляется в сроки ст. 21 152-ФЗ.
9. Права пользователя: запрос сведений, исправление, блокирование, удаление данных и отзыв согласий через {contact}; доступ к обычным функциям StudGroup не зависит от добровольной передачи пароля.
10. Меры защиты: управление доступом, шифрование секретов, ограниченный доступ к данным и журналам, реагирование на инциденты и резервное копирование.
11. Существенные изменения опубликованной версии фиксируются отдельно; при необходимости запрашивается новое согласие.
""",
    "terms": """ПОЛЬЗОВАТЕЛЬСКОЕ СОГЛАШЕНИЕ STUDGROUP

Оператор: {operator}. Адрес: {address}. Контакт: {contact}.
Редакция: {version}.

1. StudGroup — независимый Telegram-бот и Mini App, не официальный ресурс РАНХиГС.
2. Сервис помогает отображать сведения учебной группы: расписание, задания, события и уведомления. Информация требует сверки с источниками вуза.
3. Пользователь использует собственную Telegram-учётную запись и не загружает чужие пароли или сведения без законного основания. Староста подтверждает полномочия администратора учебной группы.
4. Использование персональных данных описано в отдельной Политике. Принятие настоящих условий НЕ заменяет отдельное согласие на обработку персональных данных, когда такое согласие необходимо.
5. Сохранение реквизитов СДО/ЛК необязательно. Его отказ или отзыв не мешает пользоваться другими функциями StudGroup. Использование внешних учебных систем допустимо только при соблюдении их правил.
6. Платные тарифы, условия партнёрства и новые возможности действуют только после отдельного опубликования и необходимого подтверждения.
7. По вопросам данных, исправления, прекращения использования: {contact}. Применяется законодательство Российской Федерации.
""",
    "service": """ОТДЕЛЬНОЕ СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ STUDGROUP

Оператор: {operator}. Адрес: {address}. Контакт для отзыва: {contact}.
Редакция: {version}.

Я добровольно разрешаю обработку относящихся ко мне Telegram ID и отображаемого имени, принадлежности к группе, моей роли, выбранных учебных подгрупп и настроек для предоставления функций StudGroup.
Цели: привязка моей учётной записи к учебной группе, показ расписания, заданий и событий, личные уведомления.
Действия: сбор, запись, систематизация, накопление, хранение, уточнение, извлечение, использование в рамках ролей, блокирование и уничтожение. Возможна автоматизированная обработка.
Срок: до достижения цели или отзыва при отсутствии иного законного основания. Отзыв через {contact}.
Это согласие не разрешает публикацию данных всем желающим, маркетинг, обработку чужих сообщений без основания, передачу данных внешнему AI и хранение моего пароля СДО/ЛК: для последнего существует самостоятельное согласие.
Это отдельный документ, который я подтверждаю собственным действием, независимо от принятия пользовательских условий.
""",
    "sdo": """ОТДЕЛЬНОЕ ДОБРОВОЛЬНОЕ СОГЛАСИЕ НА ХРАНЕНИЕ ЛОГИНА И ПАРОЛЯ СДО / ЛК

Оператор: {operator}. Адрес: {address}. Контакт для отзыва: {contact}.
Редакция: {version}.

Я добровольно разрешаю приём и обработку моего личного логина и пароля, одинаковых для СДО и личного кабинета (ЛК) моего вуза, а также адреса учебной платформы.
Цель — показывать моё расписание и ссылки на дистанционные занятия из ЛК и СДО РАНХиГС в StudGroup.
Действия: получение из защищённой формы Mini App по HTTPS, серверное шифрование, хранение, обновление, автоматический вход в ЛК и СДО от моего имени, чтение месячного и недельного расписания и страниц курсов с пометкой СДО, сохранение подтверждённых занятий и ссылок в базе StudGroup, удаление реквизитов после моего отзыва. Обход может повторяться примерно раз в сутки с ограниченной скоростью; при истечении сессии выполняется повторный вход. Если вход не удался, ранее подтверждённое расписание может оставаться доступным с отметкой о времени проверки.
Я понимаю, что передача этих реквизитов НЕ обязательна для получения доступа к расписанию, заданиям и другим обычным функциям StudGroup. Я могу отказаться и отозвать согласие в профиле; при отзыве сохранённые логин и пароль удаляются.
Пароль не отправляется сообщением в Telegram, не показывается старосте и не возвращается API, не направляется внешним AI-поставщикам.
Сохранение данных само по себе НЕ означает, что вход удался или расписание получено. Статус подключения и время последней успешной проверки показываются отдельно. Автоматический вход включается оператором только после проверки правил систем вуза и условий обработки персональных данных. Баллы БРС, контрольные точки и посещаемость этим согласием и текущим модулем не собираются.
Согласие действует до достижения цели или моего отзыва. Отзыв: в профиле или через {contact}.
""",
    "cookies": """ТЕХНИЧЕСКИЕ ДАННЫЕ MINI APP STUDGROUP

Оператор: {operator}. Контакт: {contact}. Редакция: {version}.
Временный токен веб-сессии хранится в памяти приложения, данные Telegram initData используются для проверки личности. Технические cookie могут хранить только настройки оформления: тему и цвет. Учебный кэш отделяется по пользователю и группе. Пароли СДО/ЛК, сессионные токены и секреты не должны попадать в cookie, localStorage, внешнюю аналитику или журналы браузера. До включения необязательной рекламы/аналитики потребуется отдельная оценка и, при необходимости, согласие.
""",
}


def legal_ready(settings):
    return bool(
        settings.legal_operator_name.strip()
        and settings.legal_operator_address.strip()
        and settings.legal_operator_contact.strip()
        and settings.legal_data_region.strip()
        and settings.legal_documents_approved
    )


def credentials_ready(settings):
    if not legal_ready(settings):
        return False
    key = settings.profile_encryption_key.get_secret_value()
    if not key:
        return False
    try:
        Fernet(key.encode())
    except ValueError:
        return False
    return True


def document_text(document, settings):
    template = DOCUMENTS.get(document)
    if template is None:
        raise ApiError("not_found", "Документ не найден", 404)
    safe = {
        "operator": settings.legal_operator_name or "[ОПЕРАТОР НЕ УКАЗАН]",
        "address": settings.legal_operator_address or "[АДРЕС НЕ УКАЗАН]",
        "contact": settings.legal_operator_contact or "[КОНТАКТ НЕ УКАЗАН]",
        "data_region": settings.legal_data_region or "[МЕСТО ХРАНЕНИЯ НЕ ПРОВЕРЕНО]",
        "version": VERSION,
    }
    return template.format(**safe)


def document_hash(document, settings):
    return hashlib.sha256(document_text(document, settings).encode("utf-8")).hexdigest()


async def latest_consent(db, user_id, purpose):
    return await db.scalar(
        select(LegalConsentEvent)
        .where(LegalConsentEvent.user_id == user_id, LegalConsentEvent.purpose == purpose)
        .order_by(LegalConsentEvent.created_at.desc(), LegalConsentEvent.id.desc())
        .limit(1)
    )


async def has_consent(db, user_id, purpose, settings):
    item = await latest_consent(db, user_id, purpose)
    return bool(
        item
        and item.action in {"grant", "accept"}
        and item.document_version == VERSION
        and item.document_sha256 == document_hash(purpose, settings)
    )


def receipt(db, user_id, purpose, action, source, settings):
    if purpose not in {"terms", "service", "sdo"}:
        raise ApiError("invalid_request", "Неизвестное согласие", 422)
    if source not in {"webapp", "telegram"}:
        raise ApiError("invalid_request", "Недопустимый источник", 422)
    db.add(
        LegalConsentEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            purpose=purpose,
            action=action,
            document_version=VERSION,
            document_sha256=document_hash(purpose, settings),
            source=source,
            created_at=datetime.now(UTC),
        )
    )


class ConsentBody(BaseModel):
    purpose: Literal["terms", "service", "sdo"]
    version: str
    confirmed: Literal[True]


async def authenticated_user(db, authorization):
    from studgroup.profile import current

    user, _ = await current(db, authorization)
    return user


@router.get("/documents/{document}", response_class=PlainTextResponse)
async def public_document(document: str, request: Request):
    return PlainTextResponse(
        document_text(document, request.app.state.settings),
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/status")
async def status(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    user = await authenticated_user(db, authorization)
    settings = request.app.state.settings
    return {
        "version": VERSION,
        "ready": legal_ready(settings),
        "credentials_ready": credentials_ready(settings),
        "terms": await has_consent(db, user.id, "terms", settings),
        "service": await has_consent(db, user.id, "service", settings),
        "sdo": await has_consent(db, user.id, "sdo", settings),
    }


@router.post("/consents")
async def accept(
    body: ConsentBody,
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    settings = request.app.state.settings
    if not legal_ready(settings):
        raise ApiError("legal_not_ready", "Правовые документы ещё не утверждены", 503)
    if body.version != VERSION:
        raise ApiError("legal_outdated", "Пожалуйста, прочитайте новую редакцию документа", 409)
    user = await authenticated_user(db, authorization)
    if body.purpose == "sdo":
        if not await has_consent(db, user.id, "terms", settings):
            raise ApiError("terms_required", "Подтвердите условия использования", 403)
        if not await has_consent(db, user.id, "service", settings):
            raise ApiError("consent_required", "Отдельно подтвердите обработку данных", 403)
        if not credentials_ready(settings):
            raise ApiError("credentials_unavailable", "Хранение пароля ещё не настроено", 503)
    receipt(
        db,
        user.id,
        body.purpose,
        "accept" if body.purpose == "terms" else "grant",
        "webapp",
        settings,
    )
    await db.commit()
    return {"purpose": body.purpose, "granted": True, "version": VERSION}


@router.delete("/consents/sdo")
async def revoke_sdo(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    settings = request.app.state.settings
    user = await authenticated_user(db, authorization)
    profile = await db.get(UserProfile, user.id)
    if profile is not None:
        profile.sdu_login = None
        profile.sdu_password_encrypted = None
        profile.sdu_platform = None
        profile.sdu_url = None
        profile.sdu_calendar_url = None
        profile.updated_at = datetime.now(UTC)
    receipt(db, user.id, "sdo", "revoke", "webapp", settings)
    await db.commit()
    return {"revoked": True, "credentials_deleted": True}
