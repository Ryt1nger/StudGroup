"""New group subscription price and time rules, without payment side effects."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from studgroup.billing_plans import (
    ALLOWED_MONTHS,
    FREE_TRIAL_DAYS,
    PRICE_RUB_PER_GROUP_MONTH,
    extended_paid_until,
    per_student_share_rub,
    price_quote,
    trial_end,
)


@pytest.mark.parametrize("months", ALLOWED_MONTHS)
def test_group_quote_multiplies_the_same_monthly_price(months):
    group_id = uuid4()
    quote = price_quote(group_id, months)
    assert quote.group_id == group_id
    assert quote.amount_rub == 800 * months
    assert quote.access_days == 30 * months


@pytest.mark.parametrize("months", [0, -1, 5, True, "2", 1.5])
def test_quote_rejects_unapproved_periods(months):
    with pytest.raises(ValueError):
        price_quote(uuid4(), months)


def test_trial_is_seven_days_from_group_activation():
    activated = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    assert FREE_TRIAL_DAYS == 7
    assert trial_end(activated) == activated + timedelta(days=7)


def test_early_group_renewal_does_not_discard_remaining_paid_days():
    now = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    current_end = now + timedelta(days=11)
    assert extended_paid_until(now=now, paid_until=current_end, months=3) == (
        current_end + timedelta(days=90)
    )


def test_expired_group_renews_from_payment_date():
    now = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    old_end = now - timedelta(days=5)
    assert extended_paid_until(now=now, paid_until=old_end, months=2) == (
        now + timedelta(days=60)
    )


def test_timestamps_must_be_timezone_aware():
    naive = datetime(2026, 11, 23, 8, 0)
    with pytest.raises(ValueError):
        trial_end(naive)
    with pytest.raises(ValueError):
        extended_paid_until(now=naive, paid_until=None, months=1)


def test_student_share_is_informational_not_per_student_billing():
    assert PRICE_RUB_PER_GROUP_MONTH == 800
    assert per_student_share_rub(35) == 22.86
    assert per_student_share_rub(30) == 26.67
    assert per_student_share_rub(25) == 32.0
    assert per_student_share_rub(20) == 40.0
    with pytest.raises(ValueError):
        per_student_share_rub(0)
