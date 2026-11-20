"""Errors as RFC 9457 problem details (application/problem+json) with a machine-readable code."""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.state import StateNotLoadedError

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    STALE_RECOMMENDATION = "STALE_RECOMMENDATION"
    NOT_PENDING = "NOT_PENDING"
    MANDATE_INVALID = "MANDATE_INVALID"
    RUN_IN_PROGRESS = "RUN_IN_PROGRESS"
    IMPORT_INVALID = "IMPORT_INVALID"
    IDEMPOTENCY_KEY_MISSING = "IDEMPOTENCY_KEY_MISSING"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    DEMO_DISABLED = "DEMO_DISABLED"
    STATE_NOT_LOADED = "STATE_NOT_LOADED"
    HTTP_ERROR = "HTTP_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    RUN_CLOSED = "RUN_CLOSED"


_TITLES = {
    ErrorCode.VALIDATION_ERROR: "Invalid request",
    ErrorCode.NOT_FOUND: "Not found",
    ErrorCode.METHOD_NOT_ALLOWED: "Method not allowed",
    ErrorCode.VERSION_CONFLICT: "Data changed since you loaded it",
    ErrorCode.STALE_RECOMMENDATION: "Recommendation is out of date",
    ErrorCode.NOT_PENDING: "Recommendation is no longer pending",
    ErrorCode.MANDATE_INVALID: "Invalid rules",
    ErrorCode.RUN_IN_PROGRESS: "A run is already in progress",
    ErrorCode.IMPORT_INVALID: "Invalid statement file",
    ErrorCode.IDEMPOTENCY_KEY_MISSING: "Idempotency-Key header is required",
    ErrorCode.IDEMPOTENCY_KEY_REUSED: "Idempotency-Key was already used for a different request",
    ErrorCode.DEMO_DISABLED: "Demo mode is off",
    ErrorCode.STATE_NOT_LOADED: "Data is not loaded",
    ErrorCode.HTTP_ERROR: "Request failed",
    ErrorCode.UNAUTHORIZED: "Missing or invalid internal token",
    ErrorCode.RUN_CLOSED: "The run is no longer running",
}

_HTTP_CODES = {
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
}


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: ErrorCode
    detail: str | None = None
    current: dict[str, Any] | None = None
    errors: list[dict[str, Any]] | None = None


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        detail: str | None = None,
        current: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or _TITLES[code])
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.current = current


def problem_response(
    status_code: int,
    code: ErrorCode,
    detail: str | None = None,
    current: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    problem = Problem(
        type=f"urn:vero:problem:{code.lower()}",
        title=_TITLES[code],
        status=status_code,
        code=code,
        detail=detail,
        current=current,
        errors=errors,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        media_type=PROBLEM_MEDIA_TYPE,
    )


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return problem_response(exc.status_code, exc.code, exc.detail, exc.current)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return problem_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        ErrorCode.VALIDATION_ERROR,
        "; ".join(f"{e['field']}: {e['message']}" for e in errors),
        errors=errors,
    )


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, ErrorCode.HTTP_ERROR)
    return problem_response(exc.status_code, code, str(exc.detail) if exc.detail else None)


async def _state_not_loaded(request: Request, exc: StateNotLoadedError) -> JSONResponse:
    return problem_response(
        status.HTTP_503_SERVICE_UNAVAILABLE, ErrorCode.STATE_NOT_LOADED, str(exc)
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(StateNotLoadedError, _state_not_loaded)


def version_conflict(current: dict[str, Any], detail: str) -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, ErrorCode.VERSION_CONFLICT, detail, current)
