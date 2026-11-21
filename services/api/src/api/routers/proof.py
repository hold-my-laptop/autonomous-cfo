from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse

from api.audit import AuditActor, AuditAgent, record
from api.deps import RepositoryDep, SettingsDep
from api.domain.proof.dataset import Document as FixtureDocument
from api.domain.proof.findings import Classification, FindingStatus, ProofFinding
from api.errors import ApiError, ErrorCode
from api.idempotency import IdempotencyKeyDep, request_hash, run_idempotent
from api.proof_schemas import CostItem, Document, DocumentSummary
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
    cited = _cited(state)
    return [d for d in state.proof.documents if d.listed or d.id in cited]


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
) -> FileResponse:
    with repository.read() as state:
        document = _find(state, document_id)
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
