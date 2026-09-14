"""Command-line entry point for ReleaseSQLBot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import uvicorn

from release_sql_bot.api.app import create_app
from release_sql_bot.config.settings import get_settings
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.infrastructure.database import build_database_resources
from release_sql_bot.infrastructure.llm import build_candidate_provider
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

    generate_v3 = subparsers.add_parser(
        "generate-v3",
        help="Run the V3 evidence loop from a ResolveMetadataRequestV3 JSON file.",
    )
    generate_v3.add_argument(
        "--input",
        required=True,
        help="Path to the strict ResolveMetadataRequestV3 JSON file.",
    )
    generate_v3.add_argument(
        "--output",
        required=True,
        help="Path for the evidence pack; existing files are never overwritten silently.",
    )
    generate_v3.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow overwriting an existing evidence pack file.",
    )
    generate_v3.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Maximum provider retries (0-5). Default: 0.",
    )
    generate_v3.add_argument(
        "--authorize-online-provider",
        action="store_true",
        help="Required per-run authorization to invoke the configured model provider.",
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


def _to_cli_evidence_pack_v3(
    pack: EvidencePackV3,
    *,
    run_id: str,
    authorized_online_provider: bool,
) -> EvidencePackV3:
    """Rebuild a 1.0.0 loop pack as a validated 1.1.0 CLI evidence pack."""
    wire = pack.model_dump(by_alias=True, mode="json")
    wire["schemaVersion"] = "1.1.0"
    wire["runId"] = run_id
    wire["authorizedOnlineProvider"] = authorized_online_provider
    return EvidencePackV3.model_validate(wire)


def _commit_evidence_pack_temp(tmp_path: Path, dest: Path, *, overwrite: bool) -> None:
    """Move a complete temp file into dest without unauthorized overwrite."""
    if overwrite:
        os.replace(tmp_path, dest)
        return
    if dest.exists():
        raise FileExistsError(dest)
    try:
        os.link(tmp_path, dest)
    except FileExistsError:
        raise
    except OSError:
        if os.name != "nt":
            raise
        os.rename(tmp_path, dest)


def _write_evidence_pack_v3(path: Path, payload: str, *, overwrite: bool) -> None:
    """Write a V3 evidence pack through a temp file then a safe commit.

    Completes the payload in a sibling temp file first. Without ``overwrite``,
    never uses ``os.replace`` onto an existing target. Cleans the temp file
    on every path. Mode ``0o600`` is applied to the temp file (POSIX).
    """
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    parent = path.parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".rsb-v3-evidence-",
        suffix=".tmp",
        dir=str(parent) if str(parent) else None,
    )
    tmp_path = Path(tmp_name)
    fd_owned = True
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, NotImplementedError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd_owned = False
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _commit_evidence_pack_temp(tmp_path, path, overwrite=overwrite)
    finally:
        if fd_owned:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


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


async def _maybe_call(resource: object | None, method_name: str) -> None:
    if resource is None:
        return
    method = getattr(resource, method_name, None)
    if method is None:
        return
    await method()


def _run_generate_v3(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from release_sql_bot.application.evidence_loop_v3 import (
        TrustedIdentifiersV3,
        run_offline_v3_evidence_loop,
    )
    from release_sql_bot.application.metadata_resolution_v3 import (
        MetadataResolutionStructureErrorV3,
        resolve_metadata_v3,
    )
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3
    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3

    if not args.authorize_online_provider:
        print(
            "Online provider authorization is required; pass --authorize-online-provider.",
            file=sys.stderr,
        )
        return _EXIT_WIRE_CONFIG_ERROR

    max_retries = args.max_retries
    if max_retries < 0 or max_retries > 5:
        print("--max-retries must be an integer between 0 and 5.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    try:
        settings = get_settings()
    except Exception:  # noqa: BLE001 - never leak config values or traces
        print("Configuration could not be loaded.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    input_path = Path(args.input)
    try:
        if not input_path.is_file():
            print(f"Input file does not exist: {input_path}", file=sys.stderr)
            return _EXIT_WIRE_CONFIG_ERROR
        raw = json.loads(input_path.read_text(encoding="utf-8"))
    except OSError:
        print("Input file could not be accessed.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Input file is not readable JSON: {type(exc).__name__}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    try:
        request = ResolveMetadataRequestV3.model_validate(raw)
    except ValidationError as exc:
        print(exc.title, file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    try:
        report = resolve_metadata_v3(request)
    except MetadataResolutionStructureErrorV3:
        print("Metadata resolution request is structurally invalid.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    except Exception:  # noqa: BLE001 - CLI must never leak internals
        print("V3 generation failed unexpectedly; no evidence pack was written.", file=sys.stderr)
        return _EXIT_INCONCLUSIVE

    if report.status != "metadataResolved":
        print(
            json.dumps(
                {"issueCodes": [issue.code for issue in report.issues]},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return _EXIT_BLOCKED

    try:
        payload = GenerateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
                "resolutionReport": report.model_dump(by_alias=True, mode="json"),
            }
        )
    except ValidationError as exc:
        print(exc.title, file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    try:
        resources = build_database_resources(settings)
    except Exception:  # noqa: BLE001 - never leak assembly internals
        print(
            "V3 runtime assembly failed unexpectedly; no evidence pack was written.",
            file=sys.stderr,
        )
        return _EXIT_INCONCLUSIVE
    try:
        provider = build_candidate_provider(settings)
    except Exception:  # noqa: BLE001 - never leak assembly internals
        print(
            "V3 runtime assembly failed unexpectedly; no evidence pack was written.",
            file=sys.stderr,
        )
        return _EXIT_INCONCLUSIVE
    missing: list[str] = []
    if resources.fact_binding_batch_repository_v3 is None:
        missing.append("fact_binding_batch_repository_v3")
    if resources.approval_port_v3 is None:
        missing.append("approval_port_v3")
    if resources.candidate_store_v3 is None:
        missing.append("candidate_store_v3")
    if provider is None:
        missing.append("candidate_provider")
    if missing:
        print(f"Missing runtime ports: {', '.join(missing)}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR

    handoff_repository = resources.fact_binding_batch_repository_v3
    approval_port = resources.approval_port_v3
    store = resources.candidate_store_v3
    assert handoff_repository is not None
    assert approval_port is not None
    assert store is not None
    assert provider is not None

    trusted = TrustedIdentifiersV3(
        providers=frozenset(settings.evidence_trusted_providers),
        models=frozenset(settings.evidence_trusted_models),
        prompt_versions=frozenset(settings.evidence_trusted_prompt_versions),
    )
    run_id = uuid.uuid4().hex

    async def _execute() -> EvidencePackV3:
        close_error: Exception | None = None
        try:
            await _maybe_call(resources.initializer, "initialize")
            await _maybe_call(approval_port, "initialize")
            await _maybe_call(store, "initialize")
            return await run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repository,
                approval_port=approval_port,
                store=store,
                model=settings.deepseek_model,
                max_retries=max_retries,
                trusted_identifiers=trusted,
            )
        finally:
            for resource in (store, approval_port, resources.initializer):
                try:
                    await _maybe_call(resource, "close")
                except Exception as exc:  # noqa: BLE001 - still close remaining resources
                    if close_error is None:
                        close_error = exc
            if close_error is not None:
                raise close_error

    try:
        pack = asyncio.run(_execute())
    except Exception:  # noqa: BLE001 - CLI must never leak internals
        print("V3 generation failed unexpectedly; no evidence pack was written.", file=sys.stderr)
        return _EXIT_INCONCLUSIVE

    authorized = bool(args.authorize_online_provider)
    try:
        cli_pack = _to_cli_evidence_pack_v3(
            pack,
            run_id=run_id,
            authorized_online_provider=authorized,
        )
        dumped = cli_pack.model_dump(by_alias=True, mode="json")
        payload_text = json.dumps(dumped, ensure_ascii=False, indent=2) + "\n"
    except Exception:  # noqa: BLE001 - never leak pack internals
        print("V3 generation failed unexpectedly; no evidence pack was written.", file=sys.stderr)
        return _EXIT_INCONCLUSIVE

    output_path = Path(args.output)
    try:
        _write_evidence_pack_v3(output_path, payload_text, overwrite=args.overwrite)
    except FileExistsError:
        print(f"Report file already exists: {output_path}", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    except OSError:
        print("Evidence pack could not be written.", file=sys.stderr)
        return _EXIT_WIRE_CONFIG_ERROR
    except Exception:  # noqa: BLE001 - never leak filesystem internals
        print(
            "Evidence pack write failed unexpectedly; no evidence pack was written.",
            file=sys.stderr,
        )
        return _EXIT_INCONCLUSIVE

    print(
        json.dumps(
            {
                "runId": dumped["runId"],
                "stage": dumped["stage"],
                "storeOutcome": dumped["storeOutcome"],
                "staticStatus": dumped["staticStatus"],
                "issueCodes": dumped["issueCodes"],
                "attemptCount": dumped["attemptCount"],
                "reportPath": str(output_path),
                "authorizedOnlineProvider": dumped["authorizedOnlineProvider"],
                "startedAt": dumped["startedAt"],
                "endedAt": dumped["endedAt"],
            },
            ensure_ascii=False,
        )
    )
    if cli_pack.stage == "evidenceComplete":
        return _EXIT_PASSED
    return _EXIT_BLOCKED


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

    if args.command == "generate-v3":
        raise SystemExit(_run_generate_v3(args))
