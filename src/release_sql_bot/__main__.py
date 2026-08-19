"""Command-line entry point for ReleaseSQLBot."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

import uvicorn

from release_sql_bot.api.app import create_app
from release_sql_bot.config.settings import get_settings
from release_sql_bot.runtime import ensure_supported_python


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-sql-bot")
    parser.add_argument(
        "command",
        choices=("serve", "check-config"),
        default="serve",
        nargs="?",
        help="Start the API service or print a non-sensitive configuration summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    ensure_supported_python()
    args = build_parser().parse_args(argv)
    settings = get_settings()

    if args.command == "check-config":
        print(json.dumps(settings.safe_summary(), ensure_ascii=False, indent=2))
        return

    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
