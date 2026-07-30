"""CLI entry point: ``python -m quicklearn`` / ``quicklearn``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config, default_data_dir, set_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quicklearn",
        description="Self-hosted AI study engine. Your data never leaves your machine.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"Where the database and media live (default: {default_data_dir()})",
    )
    parser.add_argument("--host", default=None, help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (default: 8080)")
    parser.add_argument(
        "--workers", type=int, default=None, help="Background job workers (default: 2)"
    )
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    parser.add_argument(
        "--dev-cors",
        action="store_true",
        help="Allow requests from the Vite dev server on port 5173",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error"],
        help="Logging verbosity (default: info)",
    )
    parser.add_argument("--version", action="version", version=f"quicklearn {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    overrides: dict[str, object] = {}
    if args.data_dir is not None:
        overrides["data_dir"] = args.data_dir.expanduser().resolve()
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.workers is not None:
        overrides["worker_concurrency"] = args.workers
    if args.log_level is not None:
        overrides["log_level"] = args.log_level
    if args.reload:
        overrides["reload"] = True
    if args.dev_cors:
        overrides["dev_cors"] = True

    config = Config(**overrides)  # type: ignore[arg-type]
    set_config(config)

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )
    # These are chatty at INFO and drown out anything useful.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)

    config.ensure_dirs()

    import uvicorn

    banner = f"""
  quicklearn {__version__}
  ────────────────────────────────────────────
  Open        http://{config.host}:{config.port}
  Data        {config.data_dir}
  Workers     {config.worker_concurrency}
"""
    print(banner, file=sys.stderr)

    if config.reload:
        # Reload needs an import string rather than an instantiated app.
        uvicorn.run(
            "quicklearn.main:get_application",
            factory=True,
            host=config.host,
            port=config.port,
            reload=True,
            log_level=config.log_level,
        )
    else:
        from .main import create_app

        uvicorn.run(
            create_app(config),
            host=config.host,
            port=config.port,
            log_level=config.log_level,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
