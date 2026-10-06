"""Typed persistence for groups and their recurring schedule."""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
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
    text: Mapped[str] = mapped_column(Text)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    imported: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    processing_state: Mapped[str] = mapped_column(String(16), default="pending")
    delete_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class PersonalCompletion(Base):
    __tablename__ = "personal_completions"
    __table_args__ = (UniqueConstraint("homework_id", "user_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    homework_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("homework.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_revision: Mapped[int | None] = mapped_column(Integer)
