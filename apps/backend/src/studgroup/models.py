"""Typed persistence for groups and their recurring schedule."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64))
    first_week_anchor: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default="active")
    pilot_authorized: Mapped[bool] = mapped_column(Boolean, default=False)


class SchedulePattern(Base):
    __tablename__ = "schedule_patterns"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="schedule_weekday"),
        CheckConstraint("ends > starts", name="schedule_time"),
        CheckConstraint("valid_until >= valid_from", name="schedule_validity"),
        CheckConstraint("week IN ('all', 'first', 'second')", name="schedule_week"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(255))
    weekday: Mapped[int]
    starts: Mapped[time] = mapped_column(Time)
    ends: Mapped[time] = mapped_column(Time)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date] = mapped_column(Date)
    week: Mapped[str] = mapped_column(String(8))
    teacher: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(512))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    online_url: Mapped[str | None] = mapped_column(String(2048))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id"),
        CheckConstraint("status IN ('active', 'suspended', 'left')", name="membership_status"),
        CheckConstraint("role IN ('student', 'headman', 'deputy')", name="membership_role"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))


class WebSession(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawMessage(Base):
    __tablename__ = "raw_messages"
    __table_args__ = (UniqueConstraint("group_id", "telegram_message_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    imported: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    processing_state: Mapped[str] = mapped_column(String(16), default="pending")
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    live_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReceivedUpdate(Base):
    __tablename__ = "received_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Homework(Base):
    __tablename__ = "homework"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    raw_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_messages.id", ondelete="SET NULL"), unique=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    subject_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_date_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="published")
    urgency: Mapped[str] = mapped_column(String(16), default="normal")
    verification_state: Mapped[str] = mapped_column(String(32), default="from_group_message")
    source_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    significant_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AcademicDeadline(Base):
    __tablename__ = "academic_deadlines"
    __table_args__ = (UniqueConstraint("group_id", "import_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    raw_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_messages.id", ondelete="SET NULL")
    )
    import_key: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_only: Mapped[bool] = mapped_column(Boolean, default=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_hint: Mapped[str | None] = mapped_column(String(1000))
    needs_clarification: Mapped[bool] = mapped_column(Boolean, default=True)
    source_message_ids: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonalCompletion(Base):
    __tablename__ = "personal_completions"
    __table_args__ = (UniqueConstraint("homework_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    homework_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homework.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_revision: Mapped[int | None] = mapped_column(Integer)


class AIControl(Base):
    __tablename__ = "ai_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), default=Decimal(0))
    provider_failure_streak: Mapped[int] = mapped_column(Integer, default=0)
    provider_circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_circuit_reason: Mapped[str | None] = mapped_column(String(64))


class AIRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (UniqueConstraint("group_id", "slot"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    slot: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    targets: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(24))


class AIJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (UniqueConstraint("raw_message_id", "source_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    raw_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_messages.id", ondelete="SET NULL")
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schedule_slot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    screen_checkpoint: Mapped[str | None] = mapped_column(Text)


class GroupAIActivity(Base):
    __tablename__ = "group_ai_activity"
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    last_signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signal_count: Mapped[int] = mapped_column(Integer, default=1)
    last_batch_slot: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AICandidate(Base):
    __tablename__ = "ai_candidates"
    __table_args__ = (UniqueConstraint("job_id", "ordinal"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_jobs.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class AIAttempt(Base):
    __tablename__ = "ai_attempts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_jobs.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    charged_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(24))
    metrics: Mapped[str | None] = mapped_column(Text)


class OwnerIncident(Base):
    __tablename__ = "owner_incidents"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_pending: Mapped[bool] = mapped_column(Boolean, default=True)
    current_episode_id: Mapped[int | None] = mapped_column(Integer)


class OwnerIncidentEpisode(Base):
    __tablename__ = "owner_incident_episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        ForeignKey("owner_incidents.code", ondelete="CASCADE"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_detail: Mapped[str | None] = mapped_column(String(64))
    last_detail: Mapped[str | None] = mapped_column(String(64))
    opening_pending: Mapped[bool] = mapped_column(Boolean, default=True)
    recovery_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(1000))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BotAdminSession(Base):
    __tablename__ = "bot_admin_sessions"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nonce: Mapped[str] = mapped_column(String(24))
    step: Mapped[str] = mapped_column(String(24))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"))
    candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    request_id: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    candidate_role: Mapped[str] = mapped_column(String(16), default="student")


class BotOutbox(Base):
    __tablename__ = "bot_outbox"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    method: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedup_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    panel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bot_panels.id", ondelete="CASCADE")
    )
    panel_generation: Mapped[int | None] = mapped_column(Integer)
    panel_revision: Mapped[int | None] = mapped_column(Integer)


class BotPanel(Base):
    __tablename__ = "bot_panels"
    __table_args__ = (UniqueConstraint("chat_id", "mode"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    mode: Mapped[str] = mapped_column(String(10))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    message_id: Mapped[int | None] = mapped_column(BigInteger)


class BotPanelMessage(Base):
    __tablename__ = "bot_panel_messages"
    __table_args__ = (UniqueConstraint("panel_id", "message_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    panel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bot_panels.id", ondelete="CASCADE"))
    generation: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))


class BotDialogRoute(Base):
    __tablename__ = "bot_dialog_routes"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mode: Mapped[str] = mapped_column(String(10))


class HeadmanSession(Base):
    __tablename__ = "headman_sessions"
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    nonce: Mapped[str] = mapped_column(String(24))
    step: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HeadmanDecision(Base):
    __tablename__ = "headman_decisions"
    entity_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    actor_role: Mapped[str] = mapped_column(String(16))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class HeadmanAudit(Base):
    __tablename__ = "headman_audit"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_key: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(32))
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    undone: Mapped[bool] = mapped_column(Boolean, default=False)


class HeadmanSettings(Base):
    __tablename__ = "headman_settings"
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), primary_key=True
    )
    questions_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_times: Mapped[str] = mapped_column(String(32), default="09:00")


class GroupInvitation(Base):
    __tablename__ = "group_invitations"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    issuer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ScheduleException(Base):
    __tablename__ = "schedule_exceptions"
    __table_args__ = (UniqueConstraint("pattern_id", "occurrence_date"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    pattern_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("schedule_patterns.id", ondelete="CASCADE")
    )
    occurrence_date: Mapped[date] = mapped_column(Date)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(512))
    online_url: Mapped[str | None] = mapped_column(String(2048))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class LessonOnlineLink(Base):
    __tablename__ = "lesson_online_links"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_messages.id", ondelete="CASCADE"), index=True
    )
    source_revision: Mapped[int] = mapped_column(Integer)
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("raw_messages.id", ondelete="SET NULL")
    )
    context_revision: Mapped[int | None] = mapped_column(Integer)
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_patterns.id", ondelete="CASCADE"), index=True
    )
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str] = mapped_column(String(2048))
    state: Mapped[str] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialLink(Base):
    __tablename__ = "material_links"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    entity_key: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))


class BotAdminAudit(Base):
    __tablename__ = "bot_admin_audit"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HeadmanQuestionDelivery(Base):
    __tablename__ = "headman_question_deliveries"
    __table_args__ = (UniqueConstraint("entity_key", "revision"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    entity_key: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE")
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
