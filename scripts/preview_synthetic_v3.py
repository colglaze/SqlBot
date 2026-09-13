"""Synthetic V3 SQL candidate preview over the current M3 contracts.

Fully offline: does NOT read .env, does NOT connect to MongoDB, SQL Server,
or any online model. Uses pre-registered synthetic handoff batch (in-memory
repository), pre-registered synthetic approval record (in-memory port),
and a fixed provider that returns a synthetic SQL payload. Runs the SAME
real generation service (generate_sql_candidate_v3) with all gate checks
enabled — no bypass.

Outputs the candidate JSON to a git-ignored local temporary directory.
This is a partial M3 delivery: source facts only, no aggregation, no time
range, no filters, no joins, single authorized relation.

Expected command: uv run python -m scripts.preview_synthetic_v3
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from release_sql_bot.application.candidates_v3 import (
    CandidateGateErrorV3,
    CandidateGenerationOutputInvalidV3Error,
    CandidateGenerationProviderRejectedV3Error,
    CandidateGenerationProviderUnavailableV3Error,
    CandidateScopeErrorV3,
    generate_sql_candidate_v3,
)
from release_sql_bot.application.ports.approval_records_v3 import (
    InMemoryApprovalRecordPortV3,
)
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
)
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    StoredFactBindingHandoffBatchV3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3
from tests.fakes import FixedCandidateModelProvider
from tests.v3_metadata_support import (
    valid_handoff_closure_v3_wire,
    valid_resolve_metadata_request_v3_wire,
)

DEFAULT_OUTPUT_DIR = Path(".codex_tmp")
CANDIDATE_OUTPUT_NAME = "v3-candidate-preview.json"
REPORT_OUTPUT_NAME = "v3-static-report-preview.json"

OFFLINE_PROVIDER_NAME = "fixed-offline-v3"
OFFLINE_MODEL_NAME = "fixed-offline-v3"
OFFLINE_REQUEST_ID = "preview-synthetic-v3-001"


class InMemoryHandoffBatchRepositoryV3(FactBindingHandoffBatchRepositoryV3):
    """In-memory V3 handoff batch repository backed by a pre-registered batch."""

    def __init__(self, batch: StoredFactBindingHandoffBatchV3) -> None:
        self._batch = batch

    async def get_batch_by_rule_version(
        self,
        rule_version: str,
    ) -> StoredFactBindingHandoffBatchV3 | None:
        if self._batch.rule_version == rule_version:
            return self._batch
        return None


def _build_synthetic_handoff_batch() -> StoredFactBindingHandoffBatchV3:
    """Build a synthetic V3 handoff batch from the shared fixture."""
    from release_sql_bot.application.canonical import canonical_sha256

    closure_wire = valid_handoff_closure_v3_wire()
    payload = FactBindingRequestV3.model_validate(closure_wire["payload"])
    rule_version = closure_wire["ruleVersion"]
    request_id = closure_wire["requestId"]
    fact_code = closure_wire["factCode"]
    payload_sha256 = closure_wire["payloadSha256"]

    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)

    # Use snake_case keys (model expects validate_by_alias with field names)
    request_dict = {
        "request_id": request_id,
        "rule_version": rule_version,
        "fact_code": fact_code,
        "contract_version": "3.0.0",
        "payload_sha256": payload_sha256,
        "created_at": now,
        "payload": payload.model_dump(by_alias=True, mode="json"),
    }

    # Compute batch_sha256 the same way intake does
    batch_sha256 = canonical_sha256([{"requestId": request_id, "payloadSha256": payload_sha256}])

    return StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": rule_version,
            "rule_version": rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [request_id],
            "batch_sha256": batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )


def _build_synthetic_approval_port() -> InMemoryApprovalRecordPortV3:
    """Build an in-memory approval port with the synthetic record pre-registered."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    return InMemoryApprovalRecordPortV3(records={approval.approval_id: approval})


def _build_synthetic_handoff_repository() -> InMemoryHandoffBatchRepositoryV3:
    """Build an in-memory handoff batch repository with the synthetic batch."""
    batch = _build_synthetic_handoff_batch()
    return InMemoryHandoffBatchRepositoryV3(batch)


def _build_generation_request() -> GenerateSqlCandidateRequestV3:
    """Build the synthetic V3 generation request from the shared fixture."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    # Fix the batch hash to match the synthetic batch
    batch = _build_synthetic_handoff_batch()
    req_wire["handoffClosure"]["batchSha256"] = batch.batch_sha256
    resolution_report = _resolve_metadata_v3_wire(req_wire)
    return GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": resolution_report,
        }
    )


def _resolve_metadata_v3_wire(req_wire: dict) -> dict[str, object]:
    """Build a valid metadataResolved report wire for the synthetic request."""
    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    request = ResolveMetadataRequestV3.model_validate(req_wire)
    report = resolve_metadata_v3(request)
    return report.model_dump(by_alias=True, mode="json")


def _synthetic_provider_content() -> str:
    """Build the synthetic model output content (valid V3 payload)."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding = req_wire["bindingRequest"]
    fact = binding["fact"]
    usages = binding["usages"]

    # Build parameters from fact entity-key parameters
    parameters = []
    for param in fact["parameters"]:
        parameters.append(
            {
                "name": param["name"],
                "dataType": param["dataType"],
                "required": param["required"],
                "source": f"fact.parameters.{param['name']}",
            }
        )

    # Build declared objects (single relation)
    declared_objects = [{"schemaName": "dbo", "relationName": "synthetic_table"}]

    # Build declared usage coverage (full six-tuples)
    declared_usage_coverage = []
    for usage in usages:
        declared_usage_coverage.append(
            {
                "stage": usage["stage"],
                "ruleCode": usage["ruleCode"],
                "priority": usage["priority"],
                "conditionId": usage["conditionId"],
                "conditionPath": usage["conditionPath"],
                "outcome": usage["outcome"],
            }
        )

    # Build result
    result = {
        "columnName": "fact_value",
        "dataType": fact["dataType"],
        "cardinality": "scalar",
        "nullable": fact["nullable"],
        "nullPolicy": fact["nullPolicy"],
        "unit": fact.get("unit"),
    }

    # Build SQL template (parameterized, single relation)
    sql = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )

    payload = {
        "templateCode": "SYNTHETIC_V3",
        "sqlTemplate": sql,
        "parameters": parameters,
        "result": result,
        "declaredObjects": declared_objects,
        "declaredUsageCoverage": declared_usage_coverage,
        "assumptions": [],
        "warnings": [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def offline_provider() -> FixedCandidateModelProvider:
    """Return a fixed offline provider that emits the canonical valid V3 payload."""
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider=OFFLINE_PROVIDER_NAME,
                request_id=OFFLINE_REQUEST_ID,
                model=OFFLINE_MODEL_NAME,
                content=_synthetic_provider_content(),
            )
        ]
    )


async def run_preview(
    *,
    provider: FixedCandidateModelProvider,
    handoff_repository: FactBindingHandoffBatchRepositoryV3,
    approval_port: InMemoryApprovalRecordPortV3,
    model: str,
    max_retries: int,
) -> tuple[Path, dict[str, object], Path, dict[str, object]]:
    """Run one V3 generation + static validation through the real services."""
    payload = _build_generation_request()
    candidate = await generate_sql_candidate_v3(
        provider=provider,
        payload=payload,
        handoff_repository=handoff_repository,
        approval_port=approval_port,
        model=model,
        max_retries=max_retries,
    )

    # Write candidate JSON
    output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / CANDIDATE_OUTPUT_NAME
    candidate_wire = candidate.model_dump(by_alias=True, mode="json")
    candidate_path.write_text(
        json.dumps(candidate_wire, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Run V3 static validation
    validation_request = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": candidate_wire,
        }
    )
    report = validate_sql_candidate_v3(validation_request, SqlglotTsqlInspectorV3())
    report_path = output_dir / REPORT_OUTPUT_NAME
    report_wire = report.model_dump(by_alias=True, mode="json")
    report_path.write_text(
        json.dumps(report_wire, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return candidate_path, candidate_wire, report_path, report_wire


def _print_summary(
    candidate_path: Path,
    candidate_wire: dict[str, object],
    report_path: Path,
    report_wire: dict[str, object],
) -> None:
    print("V3 合成预览完成（未启动服务，未执行任何 SQL）。")
    provenance = candidate_wire.get("provenance", {})
    print(f"  provider/model: {provenance.get('provider')}/{provenance.get('model')}")
    print(f"  prompt version: {provenance.get('promptVersion')}")
    print(f"  candidate status: {candidate_wire.get('status')}")
    print(f"  executable: {candidate_wire.get('executable')}")
    print(f"  reviewStatus: {candidate_wire.get('reviewStatus')}")
    print(f"  content sha256: {candidate_wire.get('contentSha256')}")
    issues_count = len(report_wire.get("issues", []))
    print(f"  static gate status: {report_wire.get('status')}（issues={issues_count}）")
    print(f"  candidate output: {candidate_path}")
    print(f"  static report output: {report_path}")
    print("候选与报告固定 executable=false / reviewStatus=pending，不得复制到数据库客户端执行。")


def main(argv: list[str] | None = None) -> int:
    """Run the synthetic V3 candidate preview (offline only)."""
    provider = offline_provider()
    handoff_repository = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    model = OFFLINE_MODEL_NAME
    max_retries = 0

    try:
        candidate_path, candidate_wire, report_path, report_wire = asyncio.run(
            run_preview(
                provider=provider,
                handoff_repository=handoff_repository,
                approval_port=approval_port,
                model=model,
                max_retries=max_retries,
            )
        )
    except CandidateScopeErrorV3 as exc:
        print(f"请求超出 M3 首版支持范围（{exc.code}），预览中止。", file=sys.stderr)
        return 3
    except CandidateGateErrorV3 as exc:
        print(f"生成门禁阻断（{exc.code}），provider 未被调用。", file=sys.stderr)
        return 4
    except CandidateGenerationOutputInvalidV3Error as exc:
        print(
            f"模型响应在 {exc.attempts} 次有界尝试内未通过输出门禁，未形成有效候选。",
            file=sys.stderr,
        )
        return 5
    except CandidateGenerationProviderRejectedV3Error:
        print("provider 拒绝了请求（鉴权、余额或参数问题）。", file=sys.stderr)
        return 6
    except CandidateGenerationProviderUnavailableV3Error:
        print("provider 暂不可用（超时、限流或网络问题，且有界重试已耗尽）。", file=sys.stderr)
        return 7

    _print_summary(candidate_path, candidate_wire, report_path, report_wire)
    return 0


if __name__ == "__main__":
    sys.exit(main())
