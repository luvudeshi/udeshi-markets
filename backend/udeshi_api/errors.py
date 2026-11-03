"""A single error format for every failure: ``{"error": {"code", "message", "details"}}``."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match, Route

from udeshi_markets.simulation import SimulationStateError

logger = logging.getLogger("udeshi_api")


class ApiError(Exception):
    """An expected failure with an HTTP status and a stable machine-readable code."""

    def __init__(
        self, status_code: int, code: str, message: str, details: Any | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def not_found(code: str, message: str) -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, code, message)


def _body(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def _allowed_methods(request: Request) -> str:
    methods: set[str] = set()
    pending: list[Any] = list(request.app.router.routes)
    while pending:
        route = pending.pop()
        # FastAPI (0.162) wraps each included router; its own routes carry the full path.
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        if nested is not None:
            pending.extend(nested)
        elif isinstance(route, Route) and route.methods:
            match, _ = route.matches(request.scope)
            if match is not Match.NONE:
                methods |= route.methods
    return ", ".join(sorted(methods))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(error.code, error.message, error.details), status_code=error.status_code
        )

    @app.exception_handler(SimulationStateError)
    async def state_error(_: Request, error: SimulationStateError) -> JSONResponse:
        return JSONResponse(
            _body("invalid_state", str(error)), status_code=status.HTTP_409_CONFLICT
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse(
            _body("validation_error", "request validation failed", details),
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "http_error"
        headers = dict(error.headers or {})  # Keep the exception's headers.
        if error.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
            # RFC 9110 requires Allow on 405. Starlette lists only the first route matching the
            # path, so list the methods of every route that matches it.
            headers["Allow"] = _allowed_methods(request) or headers.get("Allow", "")
        return JSONResponse(
            _body(code, str(error.detail)), status_code=error.status_code, headers=headers
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # Logged with the traceback, then reported without internals.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            _body("internal_error", "internal server error"),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
