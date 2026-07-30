"""FastAPI application: API, WebSockets, the compiled SPA, and the worker pool.

One process serves everything (PRD §3.2). The SPA is mounted last, with a catch-all that
returns ``index.html`` for unknown paths so client-side routing survives a page refresh —
without it, reloading on ``/library/3`` would 404.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .config import Config, get_config
from .db.bootstrap import init_database
from .errors import QuicklearnError
from .jobs.worker import get_pool, reset_pool
from .providers import registry

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = get_config()
    log.info("quicklearn %s starting — data directory: %s", __version__, config.data_dir)

    init_database(config)

    pool = get_pool()
    await pool.start()
    try:
        yield
    finally:
        log.info("shutting down")
        await pool.stop()
        reset_pool()
        await registry.close_all()


def create_app(config: Config | None = None) -> FastAPI:
    if config is not None:
        from .config import set_config

        set_config(config)
    config = get_config()

    app = FastAPI(
        title="quicklearn",
        version=__version__,
        description="Self-hosted AI study engine. Your data never leaves your machine.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    if config.dev_cors:
        # Development only: the Vite dev server runs on a different origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    _install_error_handlers(app)
    app.include_router(api_router)
    _mount_frontend(app, config)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(QuicklearnError)
    async def handle_known(_: Request, exc: QuicklearnError) -> JSONResponse:
        # The whole point of the error taxonomy: the client receives the actionable message,
        # not a generic 500 (PRD §11).
        if exc.status_code >= 500:
            log.error("%s: %s", type(exc).__name__, exc.message)
        else:
            log.info("%s: %s", type(exc).__name__, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = " → ".join(str(part) for part in first.get("loc", ())[1:])
        message = first.get("msg", "Invalid request.")
        return JSONResponse(
            status_code=422,
            content={
                "error": "ValidationError",
                "message": f"{location}: {message}" if location else message,
                "detail": exc.errors()[:10],
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={
                "error": type(exc).__name__,
                "message": f"Something went wrong: {exc}",
            },
        )


def _mount_frontend(app: FastAPI, config: Config) -> None:
    static_dir = config.static_dir
    index = static_dir / "index.html"

    if not index.exists():
        log.warning(
            "no compiled frontend at %s — the API is up but the UI is not. "
            "Run `npm run build` in frontend/, or use the Vite dev server.",
            static_dir,
        )

        @app.get("/", include_in_schema=False)
        async def missing_ui() -> dict[str, Any]:
            return {
                "status": "api-only",
                "message": "The frontend has not been built. Run `npm install && npm run "
                "build` in the frontend directory, or start the Vite dev server.",
                "docs": "/api/docs",
            }

        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve real files, and index.html for everything else.

        The catch-all is what makes a hard refresh on a client-side route work.
        """
        candidate = (static_dir / full_path).resolve()
        try:
            # Guard against traversal: a request for `../../etc/passwd` must not escape.
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            return FileResponse(index)

        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def get_application() -> FastAPI:
    """Entry point for ``uvicorn quicklearn.main:get_application --factory``."""
    return create_app()


app_path = Path(__file__)
