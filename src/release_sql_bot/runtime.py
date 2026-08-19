"""Runtime version guard shared by CLI and service startup."""

from __future__ import annotations

import sys

SUPPORTED_PYTHON = (3, 11, 9)


def ensure_supported_python() -> None:
    actual = sys.version_info[:3]
    if actual != SUPPORTED_PYTHON:
        expected = ".".join(str(part) for part in SUPPORTED_PYTHON)
        found = ".".join(str(part) for part in actual)
        raise RuntimeError(f"ReleaseSQLBot requires Python {expected}; found {found}")
