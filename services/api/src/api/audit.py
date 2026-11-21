"""Append-only activity log, sealed as a hash chain.

Each entry's hash covers the previous entry's hash and every field of the entry, so changing
or removing an entry later breaks the chain for everything after it. Entries are only ever
added; a demo reset starts a new chain."""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from api.domain.models import Run, RunMode

if TYPE_CHECKING:
    from api.state import DemoState

GENESIS_HASH = "0" * 64


class AuditActor(StrEnum):
    AGENT = "AGENT"
    RULES = "RULES"
    OWNER = "OWNER"
    SYSTEM = "SYSTEM"


class AuditAgent(StrEnum):
    CASH = "CASH"
    PROOF = "PROOF"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: int
    timestamp: str
    actor: AuditActor
    action: str
    prev_hash: str
    hash: str
    agent: AuditAgent | None = None
    run_id: UUID | None = None
    recommendation_id: UUID | None = None
    lane: str | None = None
    execution: str | None = None
    model: str | None = None


def stamp(moment: datetime) -> str:
    """UTC with milliseconds, e.g. 2025-05-05T08:00:00.000Z: the exact text that is hashed."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hash_input(entry: AuditEntry) -> str:
    """prev_hash|seq|timestamp|actor|agent|run_id|recommendation_id|lane|execution|model|action,
    absent values as empty text."""
    fields = (
        entry.prev_hash,
        str(entry.seq),
        entry.timestamp,
        entry.actor.value,
        entry.agent.value if entry.agent else "",
        str(entry.run_id or ""),
        str(entry.recommendation_id or ""),
        entry.lane or "",
        entry.execution or "",
        entry.model or "",
        entry.action,
    )
    return "|".join(fields)


def seal(entry: AuditEntry) -> str:
    return hashlib.sha256(hash_input(entry).encode("utf-8")).hexdigest()


def broken_at(entries: list[AuditEntry]) -> int | None:
    """The seq of the first entry that doesn't follow from the one before, or None."""
    previous = GENESIS_HASH
    for expected_seq, entry in enumerate(entries, start=1):
        if entry.seq != expected_seq or entry.prev_hash != previous or entry.hash != seal(entry):
            return entry.seq
        previous = entry.hash
    return None


def record(
    state: "DemoState",
    now: datetime,
    actor: AuditActor,
    action: str,
    *,
    agent: AuditAgent | None = None,
    run_id: UUID | None = None,
    recommendation_id: UUID | None = None,
    lane: str | None = None,
    execution: str | None = None,
    model: str | None = None,
) -> AuditEntry:
    """Append one entry to the state's log, chained to the last one."""
    unsealed = AuditEntry(
        seq=len(state.audit) + 1,
        timestamp=stamp(now),
        actor=actor,
        action=action,
        prev_hash=state.audit[-1].hash if state.audit else GENESIS_HASH,
        hash="",
        agent=agent,
        run_id=run_id,
        recommendation_id=recommendation_id,
        lane=lane,
        execution=execution,
        model=model,
    )
    entry = replace(unsealed, hash=seal(unsealed))
    state.audit.append(entry)
    return entry


def audit_run_finished(state: "DemoState", run: Run, now: datetime) -> None:
    """The run's headline as its last entry, by the agent when the model ran it."""
    model = None
    if run.mode is RunMode.AGENT:
        summary = next((s for s in reversed(run.trace) if s.kind == "SUMMARY"), None)
        model = ((summary.data or {}).get("model") if summary else None) or None
    record(
        state,
        now,
        AuditActor.AGENT if run.mode is RunMode.AGENT else AuditActor.SYSTEM,
        run.headline or run.trigger_label,
        agent=AuditAgent.CASH,
        run_id=run.id,
        model=model,
    )
