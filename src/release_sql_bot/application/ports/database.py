"""Database lifecycle port."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class DatabaseStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    UNAVAILABLE = "unavailable"


class DatabaseInitializer(Protocol):
    @property
    def status(self) -> DatabaseStatus: ...

    async def initialize(self) -> DatabaseStatus: ...

    async def close(self) -> None: ...
