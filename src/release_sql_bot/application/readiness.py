"""LangGraph workflow for service readiness checks."""

from __future__ import annotations

from typing import Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from release_sql_bot.application.ports.database import DatabaseInitializer, DatabaseStatus


class ReadinessState(TypedDict, total=False):
    config_loaded: bool
    database_status: str
    checks: dict[str, str]
    ready: bool


class ReadinessGraph(Protocol):
    async def ainvoke(self, state: ReadinessState) -> ReadinessState: ...


def build_readiness_graph(database: DatabaseInitializer) -> ReadinessGraph:
    def check_config(state: ReadinessState) -> ReadinessState:
        return {
            "config_loaded": True,
            "checks": {**state.get("checks", {}), "config": "ok"},
        }

    def check_database(state: ReadinessState) -> ReadinessState:
        database_status = database.status.value
        return {
            "database_status": database_status,
            "checks": {**state.get("checks", {}), "database": database_status},
        }

    def summarize(state: ReadinessState) -> ReadinessState:
        acceptable_database_states = {DatabaseStatus.READY.value, DatabaseStatus.DISABLED.value}
        return {
            "ready": bool(state.get("config_loaded"))
            and state.get("database_status") in acceptable_database_states
        }

    builder = StateGraph(ReadinessState)
    builder.add_node("check_config", check_config)
    builder.add_node("check_database", check_database)
    builder.add_node("summarize", summarize)
    builder.add_edge(START, "check_config")
    builder.add_edge("check_config", "check_database")
    builder.add_edge("check_database", "summarize")
    builder.add_edge("summarize", END)
    return cast(ReadinessGraph, builder.compile())
