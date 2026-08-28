"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel

from release_sql_bot import __version__
from release_sql_bot.application.binding_intake_v2 import analyze_binding_gaps_v2
from release_sql_bot.application.bindings import validate_binding_readiness
from release_sql_bot.application.candidates import (
    CandidateGenerationOutputInvalidError,
    CandidateGenerationProviderRejectedError,
    CandidateGenerationProviderUnavailableError,
    CandidateInputNotReadyError,
    generate_sql_candidate,
)
from release_sql_bot.application.candidates_v2 import (
    CandidateGenerationOutputInvalidV2Error,
    CandidateGenerationProviderRejectedV2Error,
    CandidateGenerationProviderUnavailableV2Error,
    CandidateInputNotReadyV2Error,
    generate_sql_candidate_v2,
)
from release_sql_bot.application.handoff_intake_v2 import (
    FactBindingHandoffInvalidError,
    FactBindingHandoffNotFoundError,
    intake_fact_binding_handoffs_v2,
)
from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.application.ports.candidates import CandidateModelProvider
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffRepositoryUnavailableError,
)
from release_sql_bot.application.ports.rules import (
    RuleDocumentInvalidError,
    RuleRepositoryUnavailableError,
)
from release_sql_bot.application.ports.sql_ast import SqlDialectInspector
from release_sql_bot.application.readiness import build_readiness_graph
from release_sql_bot.application.rules import (
    LatestRuleNotFoundError,
    LatestRuleQuery,
    load_latest_rule,
)
from release_sql_bot.application.runtime import DatabaseResources, RuntimeContainer
from release_sql_bot.application.sql_validation import validate_sql_candidate_v2
from release_sql_bot.config.logging import configure_logging
from release_sql_bot.config.settings import Settings, get_settings
from release_sql_bot.domain.fact_binding_handoffs_v2 import (
    FactBindingHandoffIntakeBatchV2,
)
from release_sql_bot.domain.fact_bindings import (
    BindingReadiness,
    ValidateFactBindingRequest,
)
from release_sql_bot.domain.fact_bindings_v2 import BindingGapReport, FactBindingRequestV2
from release_sql_bot.domain.project_bindings_v2 import (
    BindingResolutionReportV2,
    ResolveMetadataRequestV2,
)
from release_sql_bot.domain.rule_versions import StoredRuleVersion
from release_sql_bot.domain.sql_candidates import SqlTemplateCandidate
from release_sql_bot.domain.sql_candidates_v2 import (
    GenerateSqlCandidateRequestV2,
    SqlTemplateCandidateV2,
)
from release_sql_bot.domain.sql_validation import (
    SqlStaticValidationReportV2,
    ValidateSqlCandidateRequestV2,
)
from release_sql_bot.infrastructure.database import build_database_resources
from release_sql_bot.infrastructure.llm import build_candidate_provider
from release_sql_bot.infrastructure.sql import build_sql_dialect_inspector
from release_sql_bot.runtime import ensure_supported_python


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


def create_app(
    settings: Settings | None = None,
    database_resources: DatabaseResources | None = None,
    candidate_provider: CandidateModelProvider | None = None,
    sql_inspector: SqlDialectInspector | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_sql_inspector = sql_inspector or build_sql_dialect_inspector()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_supported_python()
        configure_logging(resolved_settings.log_level)
        resources = database_resources or build_database_resources(resolved_settings)
        database = resources.initializer
        await database.initialize()
        app.state.runtime = RuntimeContainer(
            settings=resolved_settings,
            database=database,
            rule_repository=resources.rule_repository,
            fact_binding_repository=resources.fact_binding_repository,
            candidate_provider=(
                candidate_provider
                if candidate_provider is not None
                else build_candidate_provider(resolved_settings)
            ),
            readiness_graph=build_readiness_graph(database),
        )
        try:
            yield
        finally:
            await database.close()

    app = FastAPI(
        title=resolved_settings.service_name,
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=resolved_settings.service_name,
            version=__version__,
            environment=resolved_settings.environment,
        )

    @app.get("/ready", response_model=ReadinessResponse, tags=["operations"])
    async def ready(request: Request) -> ReadinessResponse:
        runtime: RuntimeContainer = request.app.state.runtime
        result = await runtime.readiness_graph.ainvoke({})
        response = ReadinessResponse(
            status="ready" if result["ready"] else "not_ready",
            checks=result["checks"],
        )
        if not result["ready"]:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=response.model_dump(),
            )
        return response

    @app.post(
        "/api/v1/fact-bindings/validate",
        response_model=BindingReadiness,
        tags=["fact-bindings"],
    )
    async def validate_fact_binding(payload: ValidateFactBindingRequest) -> BindingReadiness:
        return validate_binding_readiness(payload)

    @app.post(
        "/api/v1/fact-bindings/v2/analyze",
        response_model=BindingGapReport,
        tags=["fact-bindings"],
    )
    async def analyze_fact_binding_v2(payload: FactBindingRequestV2) -> BindingGapReport:
        return analyze_binding_gaps_v2(payload)

    @app.get(
        "/api/v1/fact-binding-handoffs/v2",
        response_model=FactBindingHandoffIntakeBatchV2,
        tags=["fact-bindings"],
    )
    async def read_fact_binding_handoffs_v2(
        request: Request,
        rule_version: Annotated[
            str,
            Query(
                alias="ruleVersion",
                min_length=1,
                max_length=220,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._@-]*$",
            ),
        ],
    ) -> FactBindingHandoffIntakeBatchV2:
        runtime: RuntimeContainer = request.app.state.runtime
        if runtime.fact_binding_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "FACT_BINDING_HANDOFF_REPOSITORY_UNAVAILABLE",
                    "message": "RuleReader 事实交接只读仓储未启用",
                },
            )
        try:
            return await intake_fact_binding_handoffs_v2(
                runtime.fact_binding_repository,
                rule_version,
            )
        except FactBindingHandoffNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "FACT_BINDING_HANDOFF_NOT_FOUND",
                    "message": "精确规则版本没有事实交接记录",
                },
            ) from None
        except FactBindingHandoffInvalidError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "FACT_BINDING_HANDOFF_INVALID",
                    "message": "RuleReader 事实交接记录未通过 V2 intake 门禁",
                },
            ) from None
        except FactBindingHandoffRepositoryUnavailableError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "FACT_BINDING_HANDOFF_REPOSITORY_UNAVAILABLE",
                    "message": "RuleReader 事实交接只读仓储当前不可用",
                },
            ) from None

    @app.post(
        "/api/v1/fact-bindings/v2/resolve-metadata",
        response_model=BindingResolutionReportV2,
        tags=["fact-bindings"],
    )
    async def resolve_fact_binding_metadata_v2(
        payload: ResolveMetadataRequestV2,
    ) -> BindingResolutionReportV2:
        return resolve_metadata_v2(payload)

    @app.post(
        "/api/v1/sql-candidates/generate",
        response_model=SqlTemplateCandidate,
        tags=["sql-candidates"],
    )
    async def generate_candidate(
        request: Request,
        payload: ValidateFactBindingRequest,
    ) -> SqlTemplateCandidate:
        runtime: RuntimeContainer = request.app.state.runtime
        if runtime.candidate_provider is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "CANDIDATE_PROVIDER_UNAVAILABLE",
                    "message": "DeepSeek 候选生成未配置",
                },
            )
        try:
            return await generate_sql_candidate(
                runtime.candidate_provider,
                payload,
                model=runtime.settings.deepseek_model,
                max_retries=runtime.settings.deepseek_max_retries,
            )
        except CandidateInputNotReadyError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CANDIDATE_INPUT_NOT_READY",
                    "message": "事实绑定尚未满足候选生成前置条件",
                    "readiness": exc.readiness.model_dump(by_alias=True, mode="json"),
                },
            ) from None
        except CandidateGenerationOutputInvalidError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "CANDIDATE_OUTPUT_INVALID",
                    "message": "模型响应无法形成有效候选",
                },
            ) from None
        except CandidateGenerationProviderRejectedError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "CANDIDATE_PROVIDER_REJECTED",
                    "message": "DeepSeek 拒绝了候选生成请求",
                },
            ) from None
        except CandidateGenerationProviderUnavailableError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "CANDIDATE_PROVIDER_UNAVAILABLE",
                    "message": "DeepSeek 候选生成当前不可用",
                },
            ) from None

    @app.post(
        "/api/v1/sql-candidates/v2/generate",
        response_model=SqlTemplateCandidateV2,
        tags=["sql-candidates"],
    )
    async def generate_candidate_v2(
        request: Request,
        payload: GenerateSqlCandidateRequestV2,
    ) -> SqlTemplateCandidateV2:
        runtime: RuntimeContainer = request.app.state.runtime
        if runtime.candidate_provider is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "V2_CANDIDATE_PROVIDER_UNAVAILABLE",
                    "message": "V2 候选 provider 未配置",
                },
            )
        try:
            return await generate_sql_candidate_v2(
                runtime.candidate_provider,
                payload,
                model=runtime.settings.deepseek_model,
                max_retries=runtime.settings.deepseek_max_retries,
            )
        except CandidateInputNotReadyV2Error as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "V2_CANDIDATE_INPUT_NOT_READY",
                    "message": "V2 生成输入未形成精确 metadataResolved 闭包",
                    "resolution": exc.resolution.model_dump(by_alias=True, mode="json"),
                },
            ) from None
        except CandidateGenerationOutputInvalidV2Error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "V2_CANDIDATE_OUTPUT_INVALID",
                    "message": "模型响应无法形成有效 V2 候选",
                },
            ) from None
        except CandidateGenerationProviderRejectedV2Error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "V2_CANDIDATE_PROVIDER_REJECTED",
                    "message": "V2 候选 provider 拒绝了请求",
                },
            ) from None
        except CandidateGenerationProviderUnavailableV2Error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "V2_CANDIDATE_PROVIDER_UNAVAILABLE",
                    "message": "V2 候选 provider 当前不可用",
                },
            ) from None

    @app.post(
        "/api/v1/sql-candidates/v2/validate-static",
        response_model=SqlStaticValidationReportV2,
        tags=["sql-candidates"],
    )
    async def validate_candidate_static_v2(
        payload: ValidateSqlCandidateRequestV2,
    ) -> SqlStaticValidationReportV2:
        return validate_sql_candidate_v2(resolved_sql_inspector, payload)

    @app.get(
        "/api/v1/rules/latest",
        response_model=StoredRuleVersion,
        tags=["rules"],
    )
    async def get_latest_rule(
        request: Request,
        rule_id: Annotated[
            str,
            Query(
                alias="ruleId",
                min_length=1,
                max_length=160,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
            ),
        ],
    ) -> StoredRuleVersion:
        runtime: RuntimeContainer = request.app.state.runtime
        if runtime.rule_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "RULE_REPOSITORY_UNAVAILABLE",
                    "message": "MongoDB 规则读取未启用",
                },
            )
        try:
            return await load_latest_rule(
                runtime.rule_repository,
                LatestRuleQuery(rule_id=rule_id),
            )
        except LatestRuleNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RULE_NOT_FOUND", "message": "未找到匹配的规则"},
            ) from None
        except RuleDocumentInvalidError:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "RULE_DOCUMENT_INVALID",
                    "message": "最新规则不符合当前规则契约",
                },
            ) from None
        except RuleRepositoryUnavailableError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "RULE_REPOSITORY_UNAVAILABLE",
                    "message": "MongoDB 规则仓储当前不可用",
                },
            ) from None

    return app
