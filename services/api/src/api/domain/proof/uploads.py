"""Uploaded documents: what was received and what became of it. The PDF bytes are kept
separately, behind the repository."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api.domain.proof.dataset import Document

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class UploadStatus(StrEnum):
    EXTRACTING = "EXTRACTING"
    CHECKED = "CHECKED"
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ValuesSource(StrEnum):
    MODEL = "MODEL"
    FIXTURE = "FIXTURE"


class Upload(BaseModel):
    """`status`: EXTRACTING until read; CHECKED after CHECK; STORED when the counterparty is
    not a known one (kept without a check); DUPLICATE when the same counterparty and document
    number were already processed; EXTRACTION_FAILED when no values could be used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    filename: str
    sha256: str
    size: int
    uploaded_at: datetime
    run_id: UUID
    status: UploadStatus
    fixture_id: str | None = None
    values_source: ValuesSource | None = None
    document: Document | None = None
    problems: tuple[str, ...] = ()
    finding_ids: tuple[UUID, ...] = ()
