"""Server-only YDB Document API storage for encrypted RANEPA credentials.

The key is an opaque UUID. Callers must keep account ownership and consent in
Postgres and never return values from this module to a client.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import boto3
from botocore.config import Config

TABLE = "studgroup_credentials_pilot"


class CredentialStoreUnavailable(Exception):
    """A storage operation was not confirmed; do not report success."""


def configured_store(settings):
    """Return a store only when every required setting is present."""
    endpoint = settings.ydb_document_endpoint
    access_key = settings.ydb_access_key_id.get_secret_value()
    secret_key = settings.ydb_secret_access_key.get_secret_value()
    if not all((endpoint, access_key, secret_key)):
        return None
    try:
        return CredentialStore(endpoint, access_key, secret_key)
    except ValueError:
        return None


class CredentialStore:
    def __init__(self, endpoint: str, access_key_id: str, secret_access_key: str):
        if not endpoint.startswith("https://docapi.serverless.yandexcloud.net/ru-central1/"):
            raise ValueError("invalid YDB Document API endpoint")
        if not access_key_id or not secret_access_key:
            raise ValueError("missing YDB credentials")
        self.client = boto3.client(
            "dynamodb",
            region_name="ru-central1",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
        )

    async def ensure_table(self):
        try:
            response = await asyncio.to_thread(self.client.describe_table, TableName=TABLE)
        except self.client.exceptions.ResourceNotFoundException:
            try:
                await asyncio.to_thread(
                    self.client.create_table,
                    TableName=TABLE,
                    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                )
                response = None
                for _ in range(10):
                    await asyncio.sleep(1)
                    try:
                        response = await asyncio.to_thread(
                            self.client.describe_table, TableName=TABLE
                        )
                        break
                    except self.client.exceptions.ResourceNotFoundException:
                        continue
                if response is None:
                    raise CredentialStoreUnavailable()
            except Exception as exc:
                raise CredentialStoreUnavailable() from exc
        except Exception as exc:
            raise CredentialStoreUnavailable() from exc
        table = response.get("Table", {})
        if table.get("KeySchema") != [{"AttributeName": "id", "KeyType": "HASH"}]:
            raise CredentialStoreUnavailable()

    async def put(self, record_id: uuid.UUID, login: str, ciphertext: str, key_version: str):
        item = {
            "id": {"S": str(record_id)},
            "login": {"S": login},
            "ciphertext": {"S": ciphertext},
            "key_version": {"S": key_version},
            "updated_at": {"S": datetime.now(UTC).isoformat()},
        }
        try:
            await asyncio.to_thread(self.client.put_item, TableName=TABLE, Item=item)
            saved = await self.get(record_id)
            if saved != (login, ciphertext, key_version):
                raise CredentialStoreUnavailable()
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable() from exc

    async def get(self, record_id: uuid.UUID):
        try:
            response = await asyncio.to_thread(
                self.client.get_item,
                TableName=TABLE,
                Key={"id": {"S": str(record_id)}},
                ConsistentRead=True,
            )
            item = response.get("Item")
            if item is None:
                return None
            return (item["login"]["S"], item["ciphertext"]["S"], item["key_version"]["S"])
        except Exception as exc:
            raise CredentialStoreUnavailable() from exc

    async def delete(self, record_id: uuid.UUID):
        try:
            await asyncio.to_thread(
                self.client.delete_item,
                TableName=TABLE,
                Key={"id": {"S": str(record_id)}},
            )
            if await self.get(record_id) is not None:
                raise CredentialStoreUnavailable()
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable() from exc


def main():
    """Create the document table from a restricted one-off job, without printing secrets."""
    store = CredentialStore(
        os.environ["YDB_DOCUMENT_ENDPOINT"],
        os.environ["YDB_ACCESS_KEY_ID"],
        os.environ["YDB_SECRET_ACCESS_KEY"],
    )
    asyncio.run(store.ensure_table())
    print("YDB credential table ready")


if __name__ == "__main__":
    main()
