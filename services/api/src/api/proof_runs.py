"""Proof runs for uploaded documents: receive → extract → check (docs/PROOF.md §5.2).

The upload is stored and its run starts in one transaction. The AI service reads the PDF
outside any lock; the backend then validates the answer, falls back to the stored values of a
demo document when the answer can't be used, and runs CHECK. The investigation of an
UNEXPLAINED finding comes with the Proof run's next stage (P10)."""

import base64
import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import status

from api.agent import AgentTimeoutError, AgentUnavailableError, ExtractionRefusedError
from api.audit import AuditActor, AuditAgent, record
from api.config import Settings
from api.domain.models import Event, EventType, Run, RunKind, RunMode, RunStatus, TraceStep
from api.domain.proof.check import check_document, finding_id
from api.domain.proof.dataset import Document, DocumentType
from api.domain.proof.extraction import check_extraction
from api.domain.proof.findings import (
    Classification,
    FindingHistoryEntry,
    FindingStatus,
    ProofFinding,
)
from api.domain.proof.uploads import MAX_UPLOAD_BYTES, Upload, UploadStatus, ValuesSource
from api.errors import ApiError, ErrorCode
from api.state import DemoState, StateRepository

logger = logging.getLogger(__name__)

REASON_DISABLED = "AGENT_DISABLED"
REASON_UNREACHABLE = "AI_UNREACHABLE"
REASON_DEADLINE = "DEADLINE_PASSED"
REASON_UNREADABLE = "UNREADABLE_DOCUMENT"
REASON_INVALID = "INVALID_ANSWER"
REASON_RESTARTED = "SERVICE_RESTARTED"
_REASON_LABELS = {
    REASON_DISABLED: "Reading with the model is off",
    REASON_UNREACHABLE: "The AI service is unavailable",
    REASON_DEADLINE: "The model took too long",
    REASON_UNREADABLE: "The PDF cannot be read",
    REASON_INVALID: "The model's reading failed the checks",
    REASON_RESTARTED: "Vero restarted while reading it",
}


@dataclass(frozen=True)
class Extraction:
    """What came back from the AI service: an answer, or why there is none."""

    answer: dict | None = None
    reason: str | None = None
    detail: str | None = None


class _Trace:
    def __init__(self, run: Run) -> None:
        self.steps = list(run.trace)

    def add(self, kind: str, label: str, at: datetime, data: dict | None = None) -> None:
        source = "AGENT" if kind == "EXTRACT" and data and data.get("model") else "BACKEND"
        self.steps.append(TraceStep(len(self.steps) + 1, kind, source, label, at, None, data))


def _kb(size: int) -> str:
    return f"{max(1, round(size / 1024))} KB"


def _active(state: DemoState) -> bool:
    return any(r.status in (RunStatus.RUNNING, RunStatus.FINALIZING) for r in state.runs.values())


def accept_upload(
    state: DemoState, filename: str, content: bytes, now: datetime, agent: bool
) -> Run:
    """Store the PDF and start its Proof run. Refused before anything is stored while another
    run is in progress, and for a file Vero already has."""
    if _active(state):
        raise ApiError(status.HTTP_409_CONFLICT, ErrorCode.RUN_IN_PROGRESS)
    if not content:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, "empty file"
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise ApiError(
            status.HTTP_413_CONTENT_TOO_LARGE,
            ErrorCode.FILE_TOO_LARGE,
            f"at most {MAX_UPLOAD_BYTES // 2**20} MB",
        )
    if not content.startswith(b"%PDF-"):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.VALIDATION_ERROR, "only PDF files"
        )
    sha = hashlib.sha256(content).hexdigest()
    fixture = (
        next((d for d in state.proof.documents if d.sha256 == sha), None) if state.proof else None
    )
    if any(u.sha256 == sha for u in state.uploads.values()) or (fixture and fixture.preloaded):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            ErrorCode.DOCUMENT_ALREADY_PROCESSED,
            f"{filename} was already processed",
        )
    document_id = fixture.id if fixture else f"upl_{sha[:16]}"
    event = Event(
        id=uuid4(),
        type=EventType.DOCUMENT_UPLOADED,
        payload={"document_id": document_id, "filename": filename, "sha256": sha},
        created_at=now,
        financial_version_after=state.financial_version,
    )
    state.events.append(event)
    run = Run(
        id=uuid4(),
        event_id=event.id,
        trigger_type=event.type,
        trigger_label=f"You uploaded {filename}",
        status=RunStatus.RUNNING,
        mode=RunMode.AGENT if agent else RunMode.FALLBACK,
        created_at=now,
        started_financial_version=state.financial_version,
        kind=RunKind.PROOF,
        document_id=document_id,
        trace=(
            TraceStep(1, "RECEIVE", "BACKEND", f"Received {filename} ({_kb(len(content))})", now),
        ),
    )
    state.runs[run.id] = run
    state.uploads[document_id] = Upload(
        document_id=document_id,
        filename=filename,
        sha256=sha,
        size=len(content),
        uploaded_at=now,
        run_id=run.id,
        status=UploadStatus.EXTRACTING,
        fixture_id=fixture.id if fixture else None,
    )
    state.new_files[document_id] = content
    record(
        state,
        now,
        AuditActor.OWNER,
        f"Uploaded {filename}",
        agent=AuditAgent.PROOF,
        run_id=run.id,
    )
    return run


def extract_payload(state: DemoState, upload: Upload, content: bytes) -> dict:
    counterparties = state.proof.counterparties if state.proof else ()
    return {
        "document_id": upload.document_id,
        "filename": upload.filename,
        "content_base64": base64.b64encode(content).decode(),
        "known_counterparties": [
            {"id": c.id, "name": c.name, "aliases": list(c.aliases)} for c in counterparties
        ],
    }


def _checked_documents(state: DemoState) -> list[Document]:
    """Billing documents CHECK has seen: the preloaded ones and earlier uploads."""
    preloaded = [
        d
        for d in (state.proof.documents if state.proof else ())
        if d.preloaded and d.type in (DocumentType.INVOICE, DocumentType.RENT_ADJUSTMENT)
    ]
    uploaded = [
        u.document
        for u in state.uploads.values()
        if u.status is UploadStatus.CHECKED and u.document is not None
    ]
    return preloaded + uploaded


def _duplicate(state: DemoState, document: Document) -> Document | None:
    if not document.document_number:
        return None
    return next(
        (
            d
            for d in _checked_documents(state)
            if d.id != document.id
            and d.counterparty_id == document.counterparty_id
            and d.document_number == document.document_number
        ),
        None,
    )


def _finish(
    state: DemoState,
    run: Run,
    trace: _Trace,
    upload: Upload,
    now: datetime,
    headline: str,
    *,
    failed: bool = False,
    mode: RunMode | None = None,
    findings: tuple[UUID, ...] = (),
) -> None:
    trace.add("SUMMARY", headline, now)
    state.uploads[upload.document_id] = upload
    state.runs[run.id] = replace(
        run,
        status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
        mode=mode or run.mode,
        finished_at=now,
        trace=tuple(trace.steps),
        headline=headline,
        finding_ids=findings,
    )
    record(state, now, AuditActor.SYSTEM, headline, agent=AuditAgent.PROOF, run_id=run.id)


def _upload_history(finding: ProofFinding, upload: Upload, read_label: str) -> ProofFinding:
    """The finding's first two steps told from the upload: who sent it and how it was read."""
    received = FindingHistoryEntry(
        at=upload.uploaded_at,
        status=FindingStatus.INGESTED,
        label=f"You uploaded {upload.filename}",
    )
    read = finding.history[1].model_copy(update={"label": read_label})
    return finding.model_copy(update={"history": (received, read, *finding.history[2:])})


def apply_extraction(state: DemoState, run_id: UUID, extraction: Extraction, now: datetime) -> None:
    """Validate the answer, fall back to a demo document's stored values if needed, CHECK."""
    run = state.runs.get(run_id)
    if run is None or run.status is not RunStatus.RUNNING or run.document_id is None:
        return
    upload = state.uploads[run.document_id]
    trace = _Trace(run)
    fixture = state.proof.document(upload.fixture_id) if upload.fixture_id and state.proof else None

    document: Document | None = None
    problems: list[str] = []
    reason = extraction.reason
    model = (extraction.answer or {}).get("model")
    if extraction.answer is not None:
        checked = check_extraction(
            extraction.answer,
            upload.document_id,
            upload.sha256,
            state.proof,
            state.cost_memory,
            state.as_of,
        )
        problems = checked.problems
        if checked.document is not None:
            document = checked.document
            sender = state.proof.counterparty(document.counterparty_id)
            trace.add(
                "EXTRACT",
                f"Read {document.title_en.lower()} from {sender.name if sender else '?'}: every "
                "value matches its printed text",
                now,
                {"model": model},
            )
        elif not problems:
            trace.add(
                "EXTRACT",
                f"Read it, but {checked.printed_counterparty!r} is not a known supplier or "
                "landlord: stored without a check",
                now,
                {"model": model},
            )
            _finish(
                state,
                run,
                trace,
                upload.model_copy(update={"status": UploadStatus.STORED}),
                now,
                f"Stored {upload.filename} without a check",
            )
            return
        else:
            reason = REASON_INVALID
    detail = "; ".join(problems[:3]) or extraction.detail or ""

    source = ValuesSource.MODEL
    mode = RunMode.AGENT
    if document is None:
        label = _REASON_LABELS.get(reason or "", reason or "Reading failed")
        if fixture is None:
            trace.add("FALLBACK", f"{label}: {detail}".rstrip(": "), now, {"reason": reason})
            _finish(
                state,
                run,
                trace,
                upload.model_copy(
                    update={
                        "status": UploadStatus.EXTRACTION_FAILED,
                        "problems": tuple(problems or [detail or label]),
                    }
                ),
                now,
                f"Could not read {upload.filename}: {label.lower()}",
                failed=True,
                mode=RunMode.FALLBACK,
            )
            return
        document = fixture
        source, mode = ValuesSource.FIXTURE, RunMode.FALLBACK
        trace.add(
            "FALLBACK",
            f"{label}: using the stored values of this demo document",
            now,
            {"reason": reason, "problems": problems[:5]},
        )

    duplicate = _duplicate(state, document)
    if duplicate is not None:
        trace.add("CHECK", f"Same document number as {duplicate.id}: already processed", now)
        _finish(
            state,
            run,
            trace,
            upload.model_copy(
                update={
                    "status": UploadStatus.DUPLICATE,
                    "document": document,
                    "values_source": source,
                }
            ),
            now,
            f"{upload.filename} was already processed as {duplicate.id}",
            failed=True,
            mode=mode,
        )
        return

    finding = check_document(
        document,
        state.proof,
        state.cost_memory,
        _checked_documents(state),
        now,
        finding_id(f"upload|{run.id}"),
    )
    upload = upload.model_copy(
        update={"status": UploadStatus.CHECKED, "document": document, "values_source": source}
    )
    if finding is None:
        trace.add("CHECK", "Matches what Vero expects: no change", now)
        _finish(state, run, trace, upload, now, f"{upload.filename}: no change", mode=mode)
        return

    read_label = (
        f"Read by the model ({model})"
        if source is ValuesSource.MODEL and model
        else "Read by the model"
    )
    if source is ValuesSource.FIXTURE:
        read_label = "Values from the stored demo document"
    finding = _upload_history(finding, upload, read_label).model_copy(update={"run_id": run.id})
    state.findings[finding.id] = finding
    upload = upload.model_copy(update={"finding_ids": (finding.id,)})
    trace.add("CHECK", finding.history[-1].label, now, {"finding_id": str(finding.id)})
    if finding.classification is Classification.UNEXPLAINED:
        trace.add("CHECK", "Needs evidence: the investigation looks for the reason next", now)
        headline = f"{finding.title}: needs evidence"
    else:
        headline = f"{finding.title}: matches"
    _finish(state, run, trace, upload, now, headline, mode=mode, findings=(finding.id,))


class ProofPipeline:
    """Runs the extraction for uploads, outside any transaction."""

    def __init__(self, repository: StateRepository, settings: Settings, client) -> None:
        self._repository = repository
        self._settings = settings
        self._client = client

    def process(self, run_id: UUID) -> None:
        with self._repository.read() as state:
            run = state.runs.get(run_id)
            if run is None or run.status is not RunStatus.RUNNING or run.document_id is None:
                return
            upload = state.uploads[run.document_id]
            payload = None
            if self._settings.agent_enabled:
                content = self._repository.read_file(upload.document_id)
                payload = extract_payload(state, upload, content or b"")
        extraction = self._extract(payload)
        with self._repository.write() as state:
            apply_extraction(state, run_id, extraction, datetime.now(UTC))

    def _extract(self, payload: dict | None) -> Extraction:
        if payload is None:
            return Extraction(reason=REASON_DISABLED, detail="no INTERNAL_TOKEN")
        try:
            return Extraction(answer=self._client.extract(payload))
        except ExtractionRefusedError as exc:
            return Extraction(reason=REASON_UNREADABLE, detail=f"{exc.code}: {exc.message}")
        except AgentTimeoutError as exc:
            return Extraction(reason=REASON_DEADLINE, detail=str(exc))
        except AgentUnavailableError as exc:
            logger.warning("extraction unavailable: %s", exc)
            return Extraction(reason=REASON_UNREACHABLE, detail=str(exc))

    def recover(self) -> None:
        """Uploads left reading by a restart finish without the model."""
        with self._repository.read() as state:
            running = [
                r.id
                for r in state.runs.values()
                if r.kind is RunKind.PROOF and r.status is RunStatus.RUNNING
            ]
        for run_id in running:
            with self._repository.write() as state:
                apply_extraction(
                    state, run_id, Extraction(reason=REASON_RESTARTED), datetime.now(UTC)
                )
