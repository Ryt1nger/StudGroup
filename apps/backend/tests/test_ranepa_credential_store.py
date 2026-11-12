import asyncio
import json
import uuid

import pytest
from cryptography.fernet import Fernet

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

    def update_item(self, **kwargs):
        if self.fail or self.item is None:
            raise OSError("conditional update rejected")
        values = kwargs["ExpressionAttributeValues"]
        if any(
            self.item[name]["S"] != values[value]["S"]
            for name, value in (
                ("login", ":login"),
                ("ciphertext", ":ciphertext"),
                ("key_version", ":version"),
            )
        ):
            raise OSError("conditional update rejected")
        self.item["session_state"] = values[":state"]
        self.item["updated_at"] = values[":updated"]

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
    state = Fernet(Fernet.generate_key()).encrypt(
        json.dumps({"cookies": [], "origins": []}).encode()
    ).decode()
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
    state = Fernet(Fernet.generate_key()).encrypt(b'{"cookies":[],"origins":[]}').decode()
    with pytest.raises(CredentialStoreUnavailable) as error:
        await store.put(uuid.uuid4(), "student", "encrypted-value", "v1", session_state=state)
    assert state not in str(error.value)



def test_session_only_update_preserves_credentials_and_is_redacted():
    asyncio.run(_session_only_update_preserves_credentials_and_is_redacted())


async def _session_only_update_preserves_credentials_and_is_redacted():
    store = CredentialStore.__new__(CredentialStore)
    store.client = FakeClient()
    record_id = uuid.uuid4()
    await store.put(record_id, "student", "encrypted-password", "v1")
    state = Fernet(Fernet.generate_key()).encrypt(b'{"cookies":[],"origins":[]}').decode()
    await store.update_session_state(
        record_id,
        state,
        login="student",
        ciphertext="encrypted-password",
        key_version="v1",
    )
    assert await store.get(record_id) == ("student", "encrypted-password", "v1")
    assert await store.get(record_id, include_session_state=True) == (
        "student",
        "encrypted-password",
        "v1",
        state,
    )
    # Do not overwrite a changed account's session or recreate a deleted record.
    for changed_credentials in (True, False):
        if changed_credentials:
            store.client.item["ciphertext"] = {"S": "rotated-password"}
        else:
            await store.delete(record_id)
        with pytest.raises(CredentialStoreUnavailable) as error:
            await store.update_session_state(
                record_id,
                state,
                login="student",
                ciphertext="encrypted-password",
                key_version="v1",
            )
        assert state not in str(error.value)


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
