from fastapi import APIRouter, status
from fastapi.responses import FileResponse

from api.deps import RepositoryDep, SettingsDep
from api.domain.proof.dataset import Document as FixtureDocument
from api.errors import ApiError, ErrorCode
from api.proof_schemas import CostItem, Document, DocumentSummary
from api.state import DemoState

router = APIRouter(tags=["proof"])


def _visible(state: DemoState) -> list[FixtureDocument]:
    """The documents the owner can see: their own documents. Mailbox attachments stay hidden
    until a finding cites them, and the rent adjustment until it is uploaded."""
    if state.proof is None:
        return []
    return [d for d in state.proof.documents if d.listed]


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
