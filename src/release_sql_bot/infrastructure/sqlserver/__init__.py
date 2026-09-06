"""Assembly for the Phase 5A SQL Server validation adapter."""

from __future__ import annotations

from hashlib import sha256
from hmac import new as hmac_new

from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.sqlserver.odbc import (
    OdbcConnectionConfig,
    OdbcSqlServerValidator,
)

_IDENTITY_DOMAIN = "identity:v1:"


def identity_fingerprinter_for(hmac_key: str):
    """Build the domain-separated identity fingerprint function.

    The parameter-value fingerprinter uses a different domain tag with the same
    configured key, giving the two uses effective key separation.
    """

    def fingerprint(tagged_value: str) -> str:
        return hmac_new(
            hmac_key.encode("utf-8"),
            (_IDENTITY_DOMAIN + tagged_value).encode("utf-8"),
            sha256,
        ).hexdigest()

    return fingerprint


def value_fingerprinter_for(hmac_key: str):
    def fingerprint(json_value: str) -> str:
        return hmac_new(
            hmac_key.encode("utf-8"),
            ("param:v1:" + json_value).encode("utf-8"),
            sha256,
        ).hexdigest()

    return fingerprint


def build_validator_from_settings(settings: Settings) -> OdbcSqlServerValidator | None:
    """Assemble the adapter; returns None unless validation is safely enabled."""

    if not settings.sqlserver_validation_enabled or not settings.sqlserver_configured:
        return None
    if not settings.sqlserver_encrypt or settings.sqlserver_trust_server_certificate:
        return None
    if not settings._has_secret(settings.sqlserver_validation_parameter_hmac_key):
        return None
    config = OdbcConnectionConfig(
        host=settings.sqlserver_host or "",
        port=settings.sqlserver_port,
        database=settings.sqlserver_database or "",
        auth_mode=settings.sqlserver_auth_mode,
        username=settings.sqlserver_username,
        password=(
            settings.sqlserver_password.get_secret_value()
            if settings.sqlserver_password is not None
            else None
        ),
        odbc_driver=settings.sqlserver_odbc_driver,
        login_timeout_seconds=settings.sqlserver_login_timeout_seconds,
        encrypt=settings.sqlserver_encrypt,
        trust_server_certificate=settings.sqlserver_trust_server_certificate,
    )
    key = settings.sqlserver_validation_parameter_hmac_key.get_secret_value()
    return OdbcSqlServerValidator(
        config=config,
        fingerprint=identity_fingerprinter_for(key),
    )
