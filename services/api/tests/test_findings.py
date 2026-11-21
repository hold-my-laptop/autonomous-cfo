from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings
from api.domain.proof.findings import EvidenceKind, EvidenceRef, FindingStatus
from api.main import create_app

settings = get_settings()
RENT_PDF = (
    settings.proof_dataset_path.parent / "pdf" / "doc_rent_adjustment_2025_06.pdf"
).read_bytes()
pytestmark = pytest.mark.skipif(not settings.statement_path.exists(), reason="sample CSV not found")


@pytest.fixture
def client():
    with TestClient(create_app(settings)) as test_client:
        test_client.post("/api/v1/demo/reset")
        yield test_client


def findings(client: TestClient, **params) -> list[dict]:
    response = client.get("/api/v1/findings", params=params)
    assert response.status_code == 200
    return response.json()


def find(client: TestClient, document_id: str) -> dict:
    return next(f for f in findings(client) if f["document_id"] == document_id)


def acknowledge(client: TestClient, finding_id: str, key: str | None = None):
    return client.post(
        f"/api/v1/findings/{finding_id}/acknowledge",
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_reset_creates_the_preloaded_findings(client: TestClient) -> None:
    listed = findings(client)
    assert [(f["document_id"], f["classification"], f["status"]) for f in listed] == [
        ("inv_AS-72217", "VERIFIED", "CLASSIFIED"),
        ("inv_FLV-94818", "VERIFIED", "CLASSIFIED"),
        ("inv_AS-11607", "VERIFIED", "CLASSIFIED"),
    ]
    assert [(f["quiet"], f["in_digest"]) for f in listed] == [
        (True, True),
        (True, True),
        (True, True),
    ]
    fattoria = listed[1]
    assert fattoria["impact"] == {
        "currency": "EUR",
        "expected": "72829",
        "billed": "75385",
        "delta": "2556",
        "bps": 351,
    }
    assert [h["status"] for h in fattoria["history"]] == [
        "INGESTED",
        "EXTRACTED",
        "CHECKED",
        "CLASSIFIED",
    ]
    assert fattoria["created_at"] == "2025-05-05T08:00:00Z"
    assert client.get(f"/api/v1/findings/{fattoria['id']}").json() == fattoria


def test_filters(client: TestClient) -> None:
    assert [f["document_id"] for f in findings(client, classification="VERIFIED")] == [
        "inv_AS-72217",
        "inv_FLV-94818",
        "inv_AS-11607",
    ]
    assert findings(client, classification="UNEXPLAINED") == []
    assert len(findings(client, status="CLASSIFIED")) == 3
    assert findings(client, classification="CONFLICT") == []
    assert client.get("/api/v1/findings", params={"status": "DONE"}).status_code == 422


def test_acknowledging_the_fattoria_finding_removes_it_from_the_digest(
    client: TestClient,
) -> None:
    fattoria = find(client, "inv_FLV-94818")
    response = acknowledge(client, fattoria["id"])
    assert response.status_code == 200
    done = response.json()
    assert (done["status"], done["in_digest"], done["quiet"]) == ("RESOLVED", False, False)
    assert done["acknowledged_at"] is not None
    assert done["history"][-1]["label"] == "You acknowledged it"
    in_digest = [f["document_id"] for f in findings(client) if f["in_digest"]]
    assert in_digest == ["inv_AS-72217", "inv_AS-11607"]
    last = client.get("/api/v1/audit").json()[-1]
    assert (last["actor"], last["agent"], last["action"]) == (
        "OWNER",
        "PROOF",
        "Acknowledged: Fattoria La Vino GmbH: new prices on FLV-94818",
    )


def test_reset_recreates_the_preloaded_findings_identically(client: TestClient) -> None:
    before = findings(client)
    acknowledge(client, find(client, "inv_FLV-94818")["id"])
    assert findings(client) != before
    client.post("/api/v1/demo/reset")
    assert findings(client) == before


def test_only_a_quiet_finding_can_be_acknowledged_once(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/documents",
        files={"file": ("Miete.pdf", RENT_PDF, "application/pdf")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    rent = find(client, "doc_rent_adjustment_2025_06")
    assert (uploaded.status_code, rent["classification"]) == (202, "CONFLICT")
    refused = acknowledge(client, rent["id"])
    assert (refused.status_code, refused.json()["code"]) == (409, "NOT_PENDING")

    fattoria = find(client, "inv_FLV-94818")
    key = str(uuid4())
    first = acknowledge(client, fattoria["id"], key)
    assert acknowledge(client, fattoria["id"], key).json() == first.json()
    again = acknowledge(client, fattoria["id"])
    assert (again.status_code, again.json()["code"]) == (409, "NOT_PENDING")

    missing = acknowledge(client, str(uuid4()))
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    no_key = client.post(f"/api/v1/findings/{fattoria['id']}/acknowledge")
    assert no_key.status_code in (400, 422)


def test_findings_round_trip_in_the_repository(client: TestClient) -> None:
    repository = client.app.state.repository
    finding_id = UUID(find(client, "inv_AS-11607")["id"])
    at = datetime(2025, 5, 5, 9, 30, tzinfo=UTC)
    with repository.write() as state:
        investigating = state.findings[finding_id].advance(
            at,
            FindingStatus.INVESTIGATING,
            "Looking for the reason",
            evidence_refs=(
                *state.findings[finding_id].evidence_refs,
                EvidenceRef(kind=EvidenceKind.EMAIL, document_id="mail_asimex_2025_04_14"),
            ),
        )
        state.findings[finding_id] = investigating
    with repository.read() as state:
        assert state.findings[finding_id] == investigating
    stored = client.get(f"/api/v1/findings/{finding_id}").json()
    assert stored["status"] == "INVESTIGATING"
    assert stored["history"][-1] == {
        "at": "2025-05-05T09:30:00Z",
        "status": "INVESTIGATING",
        "classification": None,
        "label": "Looking for the reason",
    }


def test_a_document_becomes_visible_when_a_finding_cites_it(client: TestClient) -> None:
    assert client.get("/api/v1/documents/ntc_main_street_2025_04").status_code == 404
    repository = client.app.state.repository
    finding_id = UUID(find(client, "inv_AS-11607")["id"])
    with repository.write() as state:
        finding = state.findings[finding_id]
        state.findings[finding_id] = finding.model_copy(
            update={
                "evidence_refs": (
                    *finding.evidence_refs,
                    EvidenceRef(kind=EvidenceKind.DOCUMENT, document_id="ntc_main_street_2025_04"),
                )
            }
        )
    assert client.get("/api/v1/documents/ntc_main_street_2025_04").status_code == 200
    assert "ntc_main_street_2025_04" in {d["id"] for d in client.get("/api/v1/documents").json()}


def test_schema_name_is_proof_finding() -> None:
    schemas = create_app(settings).openapi()["components"]["schemas"]
    assert "ProofFinding" in schemas and "Finding" in schemas
    assert schemas["ProofFinding"]["properties"]["impact"]["$ref"].endswith("FindingImpact")
