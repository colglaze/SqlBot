"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from release_sql_bot import __version__
from release_sql_bot.application.readiness import build_readiness_graph
from release_sql_bot.application.runtime import RuntimeContainer
from release_sql_bot.config.logging import configure_logging
from release_sql_bot.config.settings import Settings, get_settings
from release_sql_bot.infrastructure.database import build_database_initializer


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(resolved_settings.log_level)
        database = build_database_initializer(resolved_settings)
        await database.initialize()
        app.state.runtime = RuntimeContainer(
            settings=resolved_settings,
            database=database,
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

    return app
