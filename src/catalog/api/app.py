"""FastAPI application factory for the Navigate REST API.

Builds the application from an :class:`~catalog.api.config.ApiSettings`, wiring
CORS, the consistent error model, API-key enforcement, and every resource
router under ``/api``. OpenAPI docs are served at ``/docs``, ``/redoc`` and
``/openapi.json``.

A module-level ``app`` built from the on-disk config is exposed so the server
can be started with an import string (``catalog.api.app:app``) in reload mode.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..db import init_db
from .config import ApiSettings, load_api_config
from .dependencies import require_api_key
from .errors import ApiError
from .routes import ROUTERS

API_PREFIX = "/api"

_DESCRIPTION = (
    "Local-first REST API over the Navigate knowledge platform: artifacts, "
    "links, knowledge objects, relationships, evidence, governance, and graph "
    "exploration. The contract between navigate-core and its clients."
)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build and configure a FastAPI application."""

    settings = settings or load_api_config()
    init_db(settings.db_path)

    app = FastAPI(
        title="Navigate API",
        version="1.0.0",
        description=_DESCRIPTION,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)

    # API-key enforcement is a no-op unless require_api_key is set; applying it as
    # a router dependency keeps every /api route consistently protected.
    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX, dependencies=[Depends(require_api_key)])

    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "Request validation failed.",
                "details": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        error = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error, "message": str(exc.detail), "details": {}},
        )


# Default application instance built from config/api.yml (for reload mode).
app = create_app()


__all__ = ["create_app", "app", "API_PREFIX"]
