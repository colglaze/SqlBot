"""Command-line entry point for ReleaseSQLBot."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import uvicorn

from release_sql_bot.api.app import create_app
from release_sql_bot.config.settings import get_settings
from release_sql_bot.runtime import ensure_supported_python

_EXIT_PASSED = 0
_EXIT_BLOCKED = 2
_EXIT_INCONCLUSIVE = 3
_EXIT_WIRE_CONFIG_ERROR = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-sql-bot")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Start the API service.")
    subparsers.add_parser(
        "check-config",
        help="Print a non-sensitive configuration summary.",
    )

    validation = subparsers.add_parser(
        "validate-sqlserver",
        help="Run the Phase 5A describe-only SQL Server validation from a request file.",
    )
    validation.add_argument("--input", required=True, help="Path to the strict JSON request file.")
    validation.add_argument(
        "--output",
        required=True,
        help="Path for the validation report; existing files are never overwritten silently.",
    )
    validation.add_argument(
        "--mode",
        default="describeOnly",
        choices=("describeOnly",),
        help="Phase 5A only supports describeOnly.",
    )
    validation.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow overwriting an existing report file.",
    )
    return parser


def _write_report_exclusively(path: Path, payload: str, *, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        return False
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if not overwrite:
        flags |= os.O_EXCL
    parent = path.parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except OSError:
        raise
    return True


def _run_validate_sqlserver(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from release_sql_bot.application.sqlserver_validation import (
        SqlServerValidationProfile,
        validate_sqlserver_candidate_v2,
    )
    from release_sql_bot.domain.sqlserver_validation import ValidateSqlServerRequestV2
    from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector
    from release_sql_bot.infrastructure.sql.sqlserver_parameter_binder import (
        SqlServerTokenParameterBinder,
    )
    from release_sql_bot.infrastructure.sqlserver import (
        build_validator_from_settings,
        identity_fingerprinter_for,
        value_fingerprinter_for,
    )

    settings = get_settings()
    if not settings.sqlserver_validation_enabled:
        print(
            "SQL Server validation is disabled; set RSB_SQLSERVER_VALIDATION_ENABLED=true first.",
            file=sys.stderr,
        )
        return _EXIT_WIRE_CONFIG_ERROR
    if not settings.sqlserver_configured or not settings.sqlserver_validation_profile_id:
        print("SQL Server validation target or profile is not configured.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    if not settings._has_secret(settings.sqlserver_validation_parameter_hmac_key):
        print("Parameter HMAC key is not configured.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Input file does not exist: {input_path}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    try:
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"Input file is not readable JSON: {type(exc).__name__}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    try:
        request = ValidateSqlServerRequestV2.model_validate(raw)
    except ValidationError as exc:
        print("Input does not satisfy the Phase 5A wire contract.", file=sys.stderr)
        print(exc.title, file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    validator = build_validator_from_settings(settings)
    if validator is None:
        print("SQL Server validation adapter could not be assembled.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    profile = SqlServerValidationProfile(
        profile_id=settings.sqlserver_validation_profile_id or "",
        enabled=settings.sqlserver_validation_enabled,
        environment_class=settings.sqlserver_validation_environment_class,
        allowed_modes=tuple(settings.sqlserver_validation_allowed_modes),
        connect_timeout_seconds=settings.sqlserver_login_timeout_seconds,
        command_timeout_seconds=settings.sqlserver_query_timeout_seconds,
        lock_timeout_milliseconds=settings.sqlserver_lock_timeout_milliseconds,
        max_describe_rows=settings.sqlserver_validation_max_describe_rows,
        max_describe_bytes=settings.sqlserver_validation_max_describe_bytes,
        supported_major_versions=tuple(settings.sqlserver_supported_major_versions),
        encrypt=settings.sqlserver_encrypt,
        trust_server_certificate=settings.sqlserver_trust_server_certificate,
        read_only=settings.sqlserver_read_only,
        application_intent=settings.sqlserver_application_intent,
        host=settings.sqlserver_host or "",
        database=settings.sqlserver_database or "",
    )

    hmac_key = settings.sqlserver_validation_parameter_hmac_key.get_secret_value()
    started_at = datetime.now().astimezone()
    try:
        report = validate_sqlserver_candidate_v2(
            request=request,
            profile=profile,
            inspector=SqlglotTsqlInspector(),
            binder=SqlServerTokenParameterBinder(),
            validator_port=validator,
            value_fingerprinter=value_fingerprinter_for(hmac_key),
            identity_fingerprinter=identity_fingerprinter_for(hmac_key),
            run_id=uuid.uuid4().hex,
            started_at=started_at,
        )
    except Exception:  # noqa: BLE001 - CLI must never leak internals
        print("Validation failed unexpectedly; no report was written.", file=sys.stderr)
        return _EXIT_INCONCLUSIVE

    output_path = Path(args.output)
    written = _write_report_exclusively(
        output_path,
        json.dumps(report.model_dump(by_alias=True, mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        overwrite=args.overwrite,
    )
    if not written:
        print(f"Report file already exists: {output_path}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    print(
        json.dumps(
            {
                "validationRunId": report.validation_run_id,
                "mode": report.mode,
                "status": report.status,
                "issueCodes": [issue.code for issue in report.issues],
                "durationMs": report.duration_ms,
                "reportPath": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    if report.status == "passed":
        return _EXIT_PASSED
    if report.status == "blocked":
        return _EXIT_BLOCKED
    return _EXIT_INCONCLUSIVE


def main(argv: Sequence[str] | None = None) -> None:
    ensure_supported_python()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        settings = get_settings()
        uvicorn.run(
            create_app(settings),
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )
        return

    if args.command == "check-config":
        settings = get_settings()
        print(json.dumps(settings.safe_summary(), ensure_ascii=False, indent=2))
        return

    if args.command == "validate-sqlserver":
        raise SystemExit(_run_validate_sqlserver(args))
