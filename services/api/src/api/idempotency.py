"""Idempotency-Key handling and optimistic version checks for state-changing requests."""

import hashlib
import json
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, status
from pydantic import BaseModel

from api.errors import ApiError, ErrorCode, version_conflict
from api.state import DemoState, StoredResponse


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key:
        raise ApiError(status.HTTP_400_BAD_REQUEST, ErrorCode.IDEMPOTENCY_KEY_MISSING)
    try:
        return str(UUID(idempotency_key))
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            ErrorCode.VALIDATION_ERROR,
            "Idempotency-Key must be a UUID",
        ) from exc


IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def request_hash(method: str, path: str, body: BaseModel | None) -> str:
    payload = body.model_dump(mode="json") if body is not None else None
    canonical = json.dumps([method.upper(), path, payload], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def run_idempotent[T](
    state: DemoState, key: str, fingerprint: str, produce: Callable[[], T]
) -> T | Any:
    """Run `produce` once per key. Call it while holding the state's write lock.

    Same key and same request: the stored response is returned again without side effects.
    Same key, different request: IDEMPOTENCY_KEY_REUSED. Failed requests are not stored.
    Responses are kept as JSON-compatible data so any repository can persist them."""
    stored = state.idempotency.get(key)
    if stored is not None:
        if stored.request_hash != fingerprint:
            raise ApiError(status.HTTP_409_CONFLICT, ErrorCode.IDEMPOTENCY_KEY_REUSED)
        return stored.response
    response = produce()
    payload = response.model_dump(mode="json") if isinstance(response, BaseModel) else response
    state.idempotency[key] = StoredResponse(fingerprint, payload)
    return response


def check_financial_version(state: DemoState, expected: int) -> None:
    if expected != state.financial_version:
        raise version_conflict(
            {"financial_version": state.financial_version},
            f"expected financial_version {expected}, current is {state.financial_version}",
        )


def check_mandate_version(state: DemoState, expected: int) -> None:
    current = state.active_mandate.version
    if expected != current:
        raise version_conflict(
            {"mandate_version": current},
            f"expected active mandate version {expected}, current is {current}",
        )
