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
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..db import init_db
from .config import ApiSettings, load_api_config
from .dependencies import require_api_key
from .errors import ApiError
from .routes import ROUTERS
from .schemas import ErrorResponse

API_PREFIX = "/api"

_DESCRIPTION = (
    "Local-first REST API over the Navigate knowledge platform: artifacts, "
    "links, knowledge objects, relationships, evidence, governance, and graph "
    "exploration. The contract between navigate-core and its clients."
)

_TAGS_METADATA = [
    {
        "name": "health",
        "description": "Health checks and aggregate catalog statistics.",
    },
    {
        "name": "artifacts",
        "description": "Indexed source documents, derived links, evidence, and per-artifact jobs.",
    },
    {
        "name": "links",
        "description": "Discovered hyperlinks and link analytics.",
    },
    {
        "name": "knowledge",
        "description": "Consolidated knowledge objects and object review actions.",
    },
    {
        "name": "relationships",
        "description": "Knowledge-graph relationships and relationship review actions.",
    },
    {
        "name": "evidence",
        "description": "Evidence snippets that support knowledge objects.",
    },
    {
        "name": "governance",
        "description": "Governance dashboards, quality, freshness, ownership, and alerts.",
    },
    {
        "name": "compliance",
        "description": "Standards, requirements, coverage, gaps, assessments, and proofs.",
    },
    {
        "name": "graph",
        "description": "Graph nodes, edges, traversal, impact, path, and export endpoints.",
    },
    {
        "name": "ask",
        "description": "GraphRAG question-answering endpoint when enabled.",
    },
    {
        "name": "jobs",
        "description": "Long-running or batch operations and job history.",
    },
]

_COMMON_ERROR_RESPONSES = {
    401: {
        "model": ErrorResponse,
        "description": "API key authentication failed or is not configured.",
    },
    404: {"model": ErrorResponse, "description": "Requested resource was not found."},
    422: {"model": ErrorResponse, "description": "Request validation failed."},
    500: {"model": ErrorResponse, "description": "Unexpected server error."},
}


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build and configure a FastAPI application."""

    settings = settings or load_api_config()
    init_db(settings.db_path)

    app = FastAPI(
        title="Navigate API",
        version="1.0.0",
        summary="REST API for the Navigate local knowledge catalog.",
        description=_DESCRIPTION,
        openapi_tags=_TAGS_METADATA,
        responses=_COMMON_ERROR_RESPONSES,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={
            "displayRequestDuration": True,
            "filter": True,
            "persistAuthorization": True,
            "tryItOutEnabled": True,
        },
        contact={"name": "Navigate API maintainers"},
        license_info={"name": "MIT", "identifier": "MIT"},
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
    _install_openapi_schema(app)

    # API-key enforcement is a no-op unless require_api_key is set; applying it as
    # a router dependency keeps every /api route consistently protected.
    for router in ROUTERS:
        app.include_router(
            router, prefix=API_PREFIX, dependencies=[Depends(require_api_key)]
        )

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


def _install_openapi_schema(app: FastAPI) -> None:
    """Attach a Swagger/OpenAPI schema builder with Navigate-specific metadata."""

    def _custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        # Paths already include API_PREFIX because routers are mounted with that
        # prefix. Keep the OpenAPI server at the application root so Swagger UI
        # does not prepend /api a second time when executing requests.
        schema.setdefault(
            "servers",
            [{"url": "/", "description": "Navigate API application root"}],
        )
        schema.setdefault("info", {}).setdefault("x-logo", {"altText": "Navigate API"})
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]


# Default application instance built from config/api.yml (for reload mode).
app = create_app()


__all__ = ["create_app", "app", "API_PREFIX"]
