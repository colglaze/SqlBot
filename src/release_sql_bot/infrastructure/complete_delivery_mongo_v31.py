"""Read-only Schema v6 complete-delivery source. Never writes MongoDB."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from pymongo import AsyncMongoClient, ReadPreference
from pymongo.errors import PyMongoError

from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.complete_delivery_v31 import (
    StoredCompleteDeliveryV31,
    StoredFactBindingHandoffBatchV31,
)


class MongodbCompleteDeliveryV31Error(RuntimeError):
    """Stable loader error without payload or connection details."""


class MongodbCompleteDeliverySourceV31:
    """Load a 3.1.0 complete delivery from existing V3 collections."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    async def get_delivery(self, rule_version: str) -> StoredCompleteDeliveryV31 | None:
        if not self._settings.database_enabled or self._settings.mongodb_uri is None:
            raise MongodbCompleteDeliveryV31Error("Complete delivery MongoDB source is unavailable")
        client = self._client
        close_client = False
        if client is None:
            client = AsyncMongoClient(
                self._settings.mongodb_uri.get_secret_value(),
                appname="ReleaseSQLBot-CompleteDeliveryV31",
                read_preference=ReadPreference.PRIMARY,
                connectTimeoutMS=int(self._settings.mongodb_connect_timeout_seconds * 1000),
                serverSelectionTimeoutMS=int(
                    self._settings.mongodb_server_selection_timeout_seconds * 1000
                ),
                timeoutMS=int(self._settings.mongodb_operation_timeout_seconds * 1000),
                tls=self._settings.mongodb_tls,
                tz_aware=True,
            )
            close_client = True
        try:
            database = client[self._settings.mongodb_database]
            rule_doc = await database[self._settings.mongodb_rule_versions_v3_collection].find_one(
                {"_id": rule_version}
            )
            batch_doc = await database[
                self._settings.mongodb_fact_binding_batch_collection
            ].find_one({"_id": rule_version})
            return _stored_from_documents(rule_doc, batch_doc)
        except MongodbCompleteDeliveryV31Error:
            raise
        except (OSError, PyMongoError, ValidationError, TypeError, ValueError, KeyError):
            raise MongodbCompleteDeliveryV31Error(
                "Complete delivery MongoDB documents could not be read"
            ) from None
        finally:
            if close_client:
                try:
                    await client.close()
                except (OSError, PyMongoError):
                    pass


def _stored_from_documents(
    rule_doc: dict[str, Any] | None,
    batch_doc: dict[str, Any] | None,
) -> StoredCompleteDeliveryV31 | None:
    if rule_doc is None:
        return None
    schema_version = str(rule_doc.get("schema_version") or "")
    if schema_version != "3.1.0":
        return None
    batch = None
    if isinstance(batch_doc, dict) and batch_doc.get("contract_version") == "3.1.0":
        batch = StoredFactBindingHandoffBatchV31.model_validate(batch_doc)
    payload = rule_doc.get("payload")
    catalog = rule_doc.get("catalog_payload")
    candidate = rule_doc.get("candidate_payload")
    return StoredCompleteDeliveryV31(
        rule_version=str(rule_doc.get("rule_version") or ""),
        schema_version=schema_version,
        purpose=str(rule_doc.get("purpose") or ""),
        status=str(rule_doc.get("status") or ""),
        executable=bool(rule_doc.get("executable")),
        source_file_sha256=_optional_hash(rule_doc.get("source_file_sha256")),
        parse_input_sha256=_optional_hash(rule_doc.get("parse_input_sha256")),
        catalog_digest=_optional_hash(rule_doc.get("catalog_digest")),
        catalog_payload=catalog if isinstance(catalog, dict) else None,
        candidate_payload=candidate if isinstance(candidate, dict) else None,
        result_payload=payload if isinstance(payload, dict) else None,
        batch=batch,
    )


def _optional_hash(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 64:
        return value
    return None
