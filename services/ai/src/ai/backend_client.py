"""Calls the backend's internal API (contract section 3.1, AI service -> backend).
All calls carry `Authorization: Bearer <INTERNAL_TOKEN>` when a token is configured."""

import logging
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .config import Settings
from .schemas import ToolError, ToolResult, ToolSpec

log = logging.getLogger("ai.backend")


class BackendUnavailable(Exception):
    pass


class Backend(Protocol):
    async def list_tools(self, run_id: str | None = None) -> list[ToolSpec]: ...
    async def call_tool(self, name: str, run_id: str, arguments: dict[str, Any], call_id: str) -> ToolResult: ...
    async def note(self, run_id: str, text: str) -> None: ...
    async def complete(self, run_id: str, completion: BaseModel) -> None: ...
    async def fail(self, run_id: str, reason: str, detail: str | None = None) -> None: ...


class HttpBackend:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        headers = {}
        if settings.internal_token:
            headers["Authorization"] = f"Bearer {settings.internal_token.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=settings.api_internal_url.rstrip("/"),
            headers=headers,
            timeout=settings.backend_timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return await self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise BackendUnavailable(f"{method} {path}: {e}") from e

    async def list_tools(self, run_id: str | None = None) -> list[ToolSpec]:
        """The tools of that run's kind (Cash or Proof); without a run: the Cash tools."""
        r = await self._send("GET", "/tools", params={"run_id": run_id} if run_id else None)
        r.raise_for_status()
        return [ToolSpec.model_validate(t) for t in r.json()]

    async def call_tool(self, name, run_id, arguments, call_id) -> ToolResult:
        r = await self._send("POST", f"/tools/{name}",
                             json={"run_id": run_id, "arguments": arguments, "call_id": call_id})
        if r.is_success:
            return ToolResult.model_validate(r.json())
        try:
            body = r.json()
            code = body.get("code") or (body.get("error") or {}).get("code") or f"HTTP_{r.status_code}"
            message = body.get("detail") or body.get("title") or ""
        except ValueError:
            code, message = f"HTTP_{r.status_code}", r.text[:200]
        return ToolResult(ok=False, error=ToolError(code=code, message=message))

    async def note(self, run_id, text) -> None:
        try:
            await self._send("POST", f"/runs/{run_id}/notes", json={"text": text[:500]})
        except BackendUnavailable as e:  # notes are optional
            log.info("note not delivered: %s", e)

    async def complete(self, run_id, completion) -> None:
        r = await self._send("POST", f"/runs/{run_id}/complete", json=completion.model_dump(exclude_none=True))
        r.raise_for_status()

    async def fail(self, run_id, reason, detail=None) -> None:
        body = {"reason": reason} | ({"detail": detail[:500]} if detail else {})
        r = await self._send("POST", f"/runs/{run_id}/fail", json=body)
        r.raise_for_status()
