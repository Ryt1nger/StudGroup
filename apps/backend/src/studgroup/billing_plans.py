"""Pure group-level access and pricing rules.

This module does not initiate payments, issue bank transfer links, or grant access.
A verified provider settlement must be recorded transactionally by the future payment service.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

FREE_TRIAL_DAYS = 7
BILLING_MONTH_DAYS = 30
PRICE_RUB_PER_GROUP_MONTH = 800
ALLOWED_MONTHS = (1, 2, 3, 4)


@dataclass(frozen=True, slots=True)
class GroupPriceQuote:
    group_id: UUID
    months: int
    amount_rub: int
    access_days: int


def price_quote(group_id: UUID, months: int) -> GroupPriceQuote:
    """Quote a fixed, non-discounted group plan (not a Telegram Stars invoice)."""
    if not isinstance(group_id, UUID):
        raise ValueError("Valid group_id is required")
    if type(months) is not int or months not in ALLOWED_MONTHS:
        raise ValueError("Choose 1, 2, 3, or 4 months")
    return GroupPriceQuote(
        group_id=group_id,
        months=months,
        amount_rub=PRICE_RUB_PER_GROUP_MONTH * months,
        access_days=BILLING_MONTH_DAYS * months,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include timezone")
    return value


def trial_end(connected_at: datetime) -> datetime:
    """The seven-day trial starts once per verified group, not per student or /start."""
    return _aware(connected_at) + timedelta(days=FREE_TRIAL_DAYS)


def extended_paid_until(*, now: datetime, paid_until: datetime | None, months: int) -> datetime:
    """Compute a new deadline AFTER a verified and uniquely processed payment.

    When renewing early, unused paid time remains intact. This function never
    persists an entitlement or performs payment verification.
    """
    if type(months) is not int or months not in ALLOWED_MONTHS:
        raise ValueError("Choose 1, 2, 3, or 4 months")
    base = _aware(now)
    if paid_until is not None:
        current = _aware(paid_until)
        if current > base:
            base = current
    return base + timedelta(days=BILLING_MONTH_DAYS * months)


def per_student_share_rub(group_size: int) -> float:
    """Illustrative one-month share only; the invoice remains 800 RUB per group."""
    if type(group_size) is not int or group_size < 1:
        raise ValueError("Group size must be positive")
    return round(PRICE_RUB_PER_GROUP_MONTH / group_size, 2)
