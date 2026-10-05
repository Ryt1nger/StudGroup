import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from studgroup.security import (
    ExpiredInitData,
    InvalidInitData,
    issue_token,
    token_hash,
    verify_init_data,
)

TOKEN = "test-bot-token"


def signed(**overrides):
    fields = {"auth_date": "1000", "user": json.dumps({"id": 42, "first_name": "Анна"})}
    fields.update(overrides)
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_identity():
    identity = verify_init_data(signed(), TOKEN, now=1200)
    assert identity.telegram_user_id == 42
    assert identity.display_name == "Анна"


def test_expiry_boundary():
    verify_init_data(signed(), TOKEN, now=1600)
    with pytest.raises(ExpiredInitData):
        verify_init_data(signed(), TOKEN, now=1601)


@pytest.mark.parametrize(
    "raw", ["", "bad", signed() + "&auth_date=1000", signed().replace("1000", "1001")]
)
def test_rejects_invalid_or_tampered_data(raw):
    with pytest.raises(InvalidInitData):
        verify_init_data(raw, TOKEN, now=1200)


def test_rejects_wrong_bot_and_future_timestamp():
    with pytest.raises(InvalidInitData):
        verify_init_data(signed(), "wrong", now=1200)
    with pytest.raises(InvalidInitData):
        verify_init_data(signed(auth_date="2000"), TOKEN, now=1200)


def test_tokens_are_unique_and_only_hash_is_persisted():
    token, digest = issue_token()
    assert len(token) == 43
    assert digest == token_hash(token)
    assert token != issue_token()[0]
    assert token != digest
