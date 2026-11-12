import asyncio
import json
import uuid

import pytest

from studgroup.ranepa_credential_store import CredentialStore, CredentialStoreUnavailable


class FakeClient:
    def __init__(self):
        self.item = None
        self.fail = False

    def put_item(self, **kwargs):
        if self.fail:
            raise OSError("private provider error")
        self.item = kwargs["Item"]

    def get_item(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        return {"Item": self.item} if self.item else {}

    def delete_item(self, **kwargs):
        self.item = None

    def describe_table(self, **kwargs):
        return {"Table": {"KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}]}}


def test_store_confirms_write_read_and_delete():
    asyncio.run(_store_confirms_write_read_and_delete())


async def _store_confirms_write_read_and_delete():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    key = uuid.uuid4()
    await store.put(key, "student", "encrypted-value", "v1")
    assert await store.get(key) == ("student", "encrypted-value", "v1")
    assert await store.get(key, include_session_state=True) == (
        "student",
        "encrypted-value",
        "v1",
        None,
    )
    assert "session_state" not in store.client.item
    await store.delete(key)
    assert await store.get(key) is None



def test_store_roundtrips_serialized_browser_state():
    asyncio.run(_store_roundtrips_serialized_browser_state())


async def _store_roundtrips_serialized_browser_state():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    key = uuid.uuid4()
    state = json.dumps({"cookies": [], "origins": []})
    await store.put(key, "student", "encrypted-value", "v1", session_state=state)
    assert store.client.item["session_state"] == {"S": state}
    # Existing consumers continue receiving the original three fields.
    assert await store.get(key) == ("student", "encrypted-value", "v1")
    assert await store.get(key, include_session_state=True) == (
        "student",
        "encrypted-value",
        "v1",
        state,
    )


def test_store_handles_explicit_ydb_null_session_state():
    asyncio.run(_store_handles_explicit_ydb_null_session_state())


async def _store_handles_explicit_ydb_null_session_state():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    key = uuid.uuid4()
    await store.put(key, "student", "encrypted-value", "v1")
    store.client.item["session_state"] = {"NULL": True}
    assert await store.get(key, include_session_state=True) == (
        "student",
        "encrypted-value",
        "v1",
        None,
    )


def test_store_rejects_unconfirmed_session_state_write():
    asyncio.run(_store_rejects_unconfirmed_session_state_write())


async def _store_rejects_unconfirmed_session_state_write():
    class DroppingStateClient(FakeClient):
        def put_item(self, **kwargs):
            super().put_item(**kwargs)
            self.item.pop("session_state", None)

    store = CredentialStore.__new__(CredentialStore)
    store.client = DroppingStateClient()
    with pytest.raises(CredentialStoreUnavailable) as error:
        await store.put(uuid.uuid4(), "student", "encrypted-value", "v1", session_state="secret")
    assert "secret" not in str(error.value)


def test_store_failure_is_redacted():
    asyncio.run(_store_failure_is_redacted())


async def _store_failure_is_redacted():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    store.client.fail = True
    with pytest.raises(CredentialStoreUnavailable) as error:
        await store.put(uuid.uuid4(), "student", "encrypted-value", "v1")
    assert "encrypted-value" not in str(error.value)


def test_existing_table_schema_is_checked():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    asyncio.run(store.ensure_table())
