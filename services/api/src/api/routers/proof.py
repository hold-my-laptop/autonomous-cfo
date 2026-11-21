import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from api.audit import AuditActor, AuditAgent, record
from api.deps import ProofDep, RepositoryDep, SettingsDep
from api.domain.models import RunStatus
from api.domain.proof.dataset import Document as FixtureDocument
from api.domain.proof.findings import Classification, FindingStatus, ProofFinding
from api.domain.proof.uploads import MAX_UPLOAD_BYTES
from api.errors import ApiError, ErrorCode
from api.idempotency import IdempotencyKeyDep, request_hash, run_idempotent
from api.proof_runs import (
    REASON_DISABLED,
    Extraction,
    accept_upload,
    apply_extraction,
    conclude_investigation,
)
from api.proof_schemas import CostItem, Document, DocumentSummary, UploadAccepted
from api.state import DemoState

router = APIRouter(tags=["proof"])


def _cited(state: DemoState) -> set[str]:
    cited: set[str] = set()
    for finding in state.findings.values():
        cited |= {d for d in (finding.document_id, finding.previous_document_id) if d}
        cited |= {r.document_id for r in finding.evidence_refs if r.document_id}
    return cited


def _visible(state: DemoState) -> list[FixtureDocument]:
    """The documents the owner can see: their own documents, and any other document once a
    finding cites it (a mailbox attachment found by an investigation, an upload)."""
    if state.proof is None:
        return []
    shown = _cited(state) | set(state.uploads)
    fixture = [d for d in state.proof.documents if d.listed or d.id in shown]
    uploaded = [
        u.document
        for u in state.uploads.values()
        if u.fixture_id is None and u.document is not None
    ]
    return fixture + uploaded


def _find(state: DemoState, document_id: str) -> FixtureDocument:
    document = next((d for d in _visible(state) if d.id == document_id), None)
    if document is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no document {document_id}")
    return document


@router.get("/cost-memory")
def list_cost_memory(repository: RepositoryDep) -> list[CostItem]:
    """What Vero expects each recurring cost to be: fixed costs (RENT, UTIL, XFER, PAY, SOC,
    WTAX) from the statement and their contracts, then suppliers with today's list prices."""
    with repository.read() as state:
        return [CostItem.from_domain(item) for item in state.cost_memory]


@router.get("/documents")
def list_documents(repository: RepositoryDep) -> list[DocumentSummary]:
    """The owner's documents, oldest first."""
    with repository.read() as state:
        documents = sorted(_visible(state), key=lambda d: (d.date, d.id))
        return [DocumentSummary.from_domain(d, state.proof) for d in documents]


@router.get("/documents/{document_id}")
def get_document(document_id: str, repository: RepositoryDep) -> Document:
    """Metadata, pages with text blocks, values, clauses and the box of every printed value."""
    with repository.read() as state:
        return Document.from_document(_find(state, document_id), state.proof)


@router.get(
    "/documents/{document_id}/file",
    response_class=FileResponse,
    responses={
        200: {
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            "description": "The PDF",
        }
    },
)
def get_document_file(
    document_id: str, repository: RepositoryDep, settings: SettingsDep
) -> Response:
    with repository.read() as state:
        document = _find(state, document_id)
        upload = state.uploads.get(document.id)
    if upload is not None:
        content = repository.read_file(document.id)
        if content is not None:
            return Response(
                content,
                media_type="application/pdf",
                headers={"Content-Disposition": f'inline; filename="{document.id}.pdf"'},
            )
    path = settings.proof_dataset_path.parent / "pdf" / f"{document.id}.pdf"
    if not path.is_file():
        raise ApiError(
            status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no file for document {document_id}"
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{document.id}.pdf",
        content_disposition_type="inline",
    )


def _finding(state: DemoState, finding_id: UUID) -> ProofFinding:
    finding = state.findings.get(finding_id)
    if finding is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, f"no finding {finding_id}")
    return finding


@router.get("/findings")
def list_findings(
    repository: RepositoryDep,
    classification: Classification | None = None,
    status: FindingStatus | None = None,
) -> list[ProofFinding]:
    """Vero Proof findings, by the date of what they concern (oldest first)."""
    with repository.read() as state:
        return sorted(
            (
                f
                for f in state.findings.values()
                if (classification is None or f.classification is classification)
                and (status is None or f.status is status)
            ),
            key=lambda f: (f.period_from or f.created_at.date(), f.created_at, str(f.id)),
        )


@router.get("/findings/{finding_id}")
def get_finding(finding_id: UUID, repository: RepositoryDep) -> ProofFinding:
    with repository.read() as state:
        return _finding(state, finding_id)


@router.post("/findings/{finding_id}/acknowledge")
def acknowledge_finding(
    finding_id: UUID, key: IdempotencyKeyDep, repository: RepositoryDep, request: Request
) -> ProofFinding:
    """The owner's one tap on a change Vero handled quietly: it leaves the digest. A finding
    that is not quiet (it needs evidence or a decision) or was already acknowledged gives
    NOT_PENDING."""
    with repository.write() as state:

        def apply() -> ProofFinding:
            finding = _finding(state, finding_id)
            if not finding.quiet or finding.acknowledged_at is not None:
                raise ApiError(
                    status.HTTP_409_CONFLICT,
                    ErrorCode.NOT_PENDING,
                    "only a verified finding with nothing to decide can be acknowledged",
                )
            now = datetime.now(UTC)
            done = finding.advance(
                now, FindingStatus.RESOLVED, "You acknowledged it", acknowledged_at=now
            )
            state.findings[finding_id] = done
            record(
                state,
                now,
                AuditActor.OWNER,
                f"Acknowledged: {finding.title}",
                agent=AuditAgent.PROOF,
            )
            return done

        return run_idempotent(state, key, request_hash("POST", request.url.path, None), apply)


class _UploadFingerprint(BaseModel):
    filename: str
    sha256: str


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    file: Annotated[UploadFile, File(description="A PDF, at most 5 MB")],
    key: IdempotencyKeyDep,
    repository: RepositoryDep,
    settings: SettingsDep,
    proof: ProofDep,
    request: Request,
    background: BackgroundTasks,
) -> UploadAccepted:
    """Upload a document: Vero stores it and starts a Proof run that reads it (the model on
    OpenShift AI), checks the values and runs CHECK. Poll GET /runs/{run_id}.

    409 RUN_IN_PROGRESS while any run is in progress, 409 DOCUMENT_ALREADY_PROCESSED for a
    file Vero already has, 413 FILE_TOO_LARGE above 5 MB, 422 for a file that is not a PDF."""
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    filename = (file.filename or "document.pdf").rsplit("/", 1)[-1][:200]
    fingerprint = request_hash(
        "POST",
        request.url.path,
        _UploadFingerprint(filename=filename, sha256=hashlib.sha256(content).hexdigest()),
    )
    started: list[UUID] = []
    with repository.write() as state:

        def apply() -> UploadAccepted:
            now = datetime.now(UTC)
            run = accept_upload(state, filename, content, now, settings.agent_enabled)
            if settings.agent_enabled:
                started.append(run.id)
            else:
                apply_extraction(
                    state,
                    run.id,
                    Extraction(reason=REASON_DISABLED, detail="no INTERNAL_TOKEN"),
                    now,
                )
                conclude_investigation(state, run.id, now, reason=REASON_DISABLED)
            current = state.runs[run.id]
            return UploadAccepted(
                document_id=current.document_id or "",
                run_id=current.id,
                status="RUNNING" if current.status is RunStatus.RUNNING else current.status.value,
            )

        accepted = run_idempotent(state, key, fingerprint, apply)
    for run_id in started:
        background.add_task(proof.process, run_id)
    return accepted
