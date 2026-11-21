import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.agent import AgentTimeoutError, AgentUnavailableError, ExtractionRefusedError
from api.config import Settings, get_settings
from api.explain import ExplainResult
from api.main import create_app
from api.proof_runs import accept_upload

TOKEN = "test-internal-token"
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
PDF_DIR = base.proof_dataset_path.parent / "pdf"
RENT_PDF = (PDF_DIR / "doc_rent_adjustment_2025_06.pdf").read_bytes()
UNKNOWN_PDF = b"%PDF-1.4\n% an invoice Vero has never seen\n"
EXPECTED = {c["file"]: c for c in json.loads((PDF_DIR / "expected.json").read_text())}
DATASET = json.loads(base.proof_dataset_path.read_text())


def answer_for(document_id: str) -> dict[str, Any]:
    """What a correct extraction of a demo PDF returns: P03's expected values with their
    printed text, page and box."""
    expected = EXPECTED[f"{document_id}.pdf"]["expected"]
    document = next(d for d in DATASET["documents"] if d["id"] == document_id)
    printed = {h["ref"]: h for h in document["highlights"]}

    def sourced(item: dict) -> dict:
        return {
            "value": item["value"],
            "source": {"page": item["page"], "text": item["text"], "box": item["box"]},
        }

    answer: dict[str, Any] = {
        "document_id": document_id,
        "status": "OK",
        "errors": [],
        "pages": [
            {"page": p["page"], "width": p["width"], "height": p["height"]}
            for p in document["pages"]
        ],
        "doc_type": {"value": expected["doc_type"], "source": None},
        "counterparty": {
            "value": expected["counterparty"]["value"],
            "matched_id": expected["counterparty"]["matched_id"],
            "source": {
                "page": 1,
                "text": printed["counterparty"]["text"],
                "box": printed["counterparty"]["box"],
            },
        },
        "currency": {"value": expected["currency"]["value"], "source": None},
        "model": "vero-llm",
    }
    for name in ("date", "document_number", "effective_from", "old_amount", "new_amount"):
        if name in expected:
            answer[name] = sourced(expected[name])
    if "frequency" in expected:
        answer["frequency"] = sourced(expected["frequency"])
    answer["lines"] = [{k: sourced(v) for k, v in line.items()} for line in expected["lines"]]
    if "totals" in expected:
        answer["totals"] = {k: sourced(v) for k, v in expected["totals"].items()}
    return answer


class FakeAI:
    """The AI service as the backend sees it, with a scripted extraction."""

    def __init__(self, extract: Callable[[dict], dict] | None = None) -> None:
        self.extracted: list[dict] = []
        self._extract = extract or (lambda payload: answer_for(payload["document_id"]))

    def start_run(self, payload: dict[str, Any]) -> None:
        return None

    def explain(self, payload: dict[str, Any]) -> ExplainResult:
        raise AgentUnavailableError("not used")

    def ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise AgentUnavailableError("not used")

    def model_health(self):
        raise AgentUnavailableError("not used")

    def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.extracted.append(payload)
        return self._extract(payload)


def agent_settings() -> Settings:
    """Extraction with the model; the investigation is tested in test_investigations.py."""
    return Settings(
        **(base.model_dump() | {"internal_token": TOKEN, "investigation_agent_enabled": False})
    )


def make_client(ai: FakeAI) -> TestClient:
    return TestClient(create_app(agent_settings(), ai))


def upload(client: TestClient, content: bytes, name: str = "upload.pdf", key: str | None = None):
    return client.post(
        "/api/v1/documents",
        files={"file": (name, content, "application/pdf")},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def run_of(client: TestClient, response) -> dict:
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/runs/{response.json()['run_id']}").json()


def labels(run: dict) -> list[str]:
    return [step["label"] for step in run["trace"]]


def rent_finding(client: TestClient, run: dict) -> dict:
    [finding_id] = run["finding_ids"]
    return client.get(f"/api/v1/findings/{finding_id}").json()


def test_correct_extraction_creates_the_rent_finding() -> None:
    ai = FakeAI()
    with make_client(ai) as client:
        run = run_of(client, upload(client, RENT_PDF, "Mietanpassung.pdf"))
        payload = ai.extracted[0]
        assert payload["document_id"] == "doc_rent_adjustment_2025_06"
        assert payload["filename"] == "Mietanpassung.pdf"
        assert {c["id"] for c in payload["known_counterparties"]} >= {"cp_main_street"}
        assert (run["kind"], run["status"], run["mode"]) == ("PROOF", "SUCCEEDED", "FALLBACK")
        assert run["document_id"] == "doc_rent_adjustment_2025_06"
        extract = next(s for s in run["trace"] if s["kind"] == "EXTRACT")
        assert (extract["source"], extract["data"]) == ("AGENT", {"model": "vero-llm"})
        assert "Needs evidence: the investigation looks for the reason next" in labels(run)
        assert "The investigation agent is off: Vero follows its own steps" in labels(run)
        finding = rent_finding(client, run)
        assert (
            finding["classification"],
            finding["status"],
            finding["escalated"],
            finding["run_id"],
        ) == ("CONFLICT", "PENDING_APPROVAL", False, run["run_id"])
        assert finding["impact"] | {"currency": "EUR"} == {
            "currency": "EUR",
            "expected": "380000",
            "billed": "410400",
            "delta": "30400",
            "bps": 800,
        }
        assert finding["period_from"] == "2025-06-01"
        assert [h["label"] for h in finding["history"][:2]] == [
            "You uploaded Mietanpassung.pdf",
            "Read by the model (vero-llm)",
        ]
        document = client.get("/api/v1/documents/doc_rent_adjustment_2025_06").json()
        assert document["rent_adjustment"]["new_amount"] == "410400"
        file = client.get("/api/v1/documents/doc_rent_adjustment_2025_06/file")
        assert file.content == RENT_PDF


def wrong_value(payload: dict) -> dict:
    answer = answer_for(payload["document_id"])
    answer["new_amount"]["value"] = "410500"
    return answer


def unsupported_match(payload: dict) -> dict:
    answer = answer_for(payload["document_id"])
    answer["counterparty"]["matched_id"] = "cp_asimex"
    return answer


@pytest.mark.parametrize(
    ("extract", "reason", "problem"),
    [
        (wrong_value, "INVALID_ANSWER", "new_amount: printed '4.104,00 €' reads as 410400"),
        (unsupported_match, "INVALID_ANSWER", "does not support 'cp_asimex'"),
        (
            lambda p: (_ for _ in ()).throw(ExtractionRefusedError("NO_TEXT_LAYER", "scanned")),
            "UNREADABLE_DOCUMENT",
            None,
        ),
        (
            lambda p: (_ for _ in ()).throw(AgentUnavailableError("connection refused")),
            "AI_UNREACHABLE",
            None,
        ),
        (
            lambda p: (_ for _ in ()).throw(AgentTimeoutError("no answer within 35 s")),
            "DEADLINE_PASSED",
            None,
        ),
        (lambda p: {"status": "OK"}, "INVALID_ANSWER", "unusable answer"),
    ],
)
def test_rejected_extraction_of_the_demo_document_falls_back_to_its_stored_values(
    extract: Callable, reason: str, problem: str | None
) -> None:
    with make_client(FakeAI(extract)) as client:
        run = run_of(client, upload(client, RENT_PDF))
        assert (run["status"], run["mode"]) == ("SUCCEEDED", "FALLBACK")
        fallback = next(s for s in run["trace"] if s["kind"] == "FALLBACK")
        assert fallback["data"]["reason"] == reason
        assert fallback["label"].endswith("using the stored values of this demo document")
        if problem:
            assert any(problem in p for p in fallback["data"]["problems"])
        finding = rent_finding(client, run)
        assert finding["impact"]["delta"] == "30400"
        assert finding["history"][1]["label"] == "Values from the stored demo document"


def unknown_invoice(change: Callable[[dict], None] | None = None) -> Callable[[dict], dict]:
    """FLV-94818's extraction as if it were another invoice, optionally changed."""

    def extract(payload: dict) -> dict:
        answer = answer_for("inv_FLV-94818")
        answer["document_id"] = payload["document_id"]
        answer["document_number"]["value"] = answer["document_number"]["source"]["text"] = (
            "FLV-99001"
        )
        answer["date"]["value"], answer["date"]["source"]["text"] = "2025-05-04", "04.05.2025"
        if change:
            change(answer)
        return answer

    return extract


def vat_off_by_two(answer: dict) -> None:
    vat = answer["totals"]["vat"]
    vat["value"] = str(int(vat["value"]) + 2)
    vat["source"]["text"] = "143,25 €"
    gross = answer["totals"]["gross"]
    gross["value"] = str(int(gross["value"]) + 2)
    gross["source"]["text"] = "897,10 €"


@pytest.mark.parametrize(
    ("extract", "problem"),
    [
        (unknown_invoice(vat_off_by_two), "totals.vat: VAT per rate gives 14323 cents, not 14325"),
        (
            lambda p: (_ for _ in ()).throw(ExtractionRefusedError("NO_TEXT_LAYER", "scanned")),
            "NO_TEXT_LAYER: scanned",
        ),
        (
            unknown_invoice(lambda a: a["counterparty"].update(matched_id="cp_main_street")),
            "does not support 'cp_main_street'",
        ),
    ],
)
def test_rejected_extraction_of_an_unknown_pdf_fails(extract: Callable, problem: str) -> None:
    with make_client(FakeAI(extract)) as client:
        before = client.get("/api/v1/findings").json()
        run = run_of(client, upload(client, UNKNOWN_PDF, "scan.pdf"))
        assert (run["status"], run["finding_ids"]) == ("FAILED", [])
        assert run["result"]["headline"]["text"].startswith("Could not read scan.pdf")
        assert any(problem in label for label in labels(run))
        assert client.get("/api/v1/findings").json() == before
        with client.app.state.repository.read() as state:
            stored = state.uploads[run["document_id"]]
        assert stored.status == "EXTRACTION_FAILED"
        assert any(problem in p for p in stored.problems)


def test_a_valid_unknown_invoice_is_checked_against_the_previous_one() -> None:
    with make_client(FakeAI(unknown_invoice())) as client:
        run = run_of(client, upload(client, UNKNOWN_PDF, "FLV-99001.pdf"))
        assert (run["status"], run["mode"], run["finding_ids"]) == ("SUCCEEDED", "AGENT", [])
        assert "Matches what Vero expects: no change" in labels(run)
        document_id = run["document_id"]
        assert document_id.startswith("upl_")
        listed = {d["id"]: d for d in client.get("/api/v1/documents").json()}
        assert listed[document_id]["document_number"] == "FLV-99001"
        detail = client.get(f"/api/v1/documents/{document_id}").json()
        assert detail["totals"]["gross"] == "89708"
        assert {h["ref"] for h in detail["highlights"]} >= {"totals.gross", "lines.l1.unit_price"}
        assert client.get(f"/api/v1/documents/{document_id}/file").content == UNKNOWN_PDF


def test_an_unknown_counterparty_is_stored_without_a_check() -> None:
    def extract(payload: dict) -> dict:
        answer = unknown_invoice()(payload)
        answer["counterparty"]["matched_id"] = None
        return answer

    with make_client(FakeAI(extract)) as client:
        run = run_of(client, upload(client, UNKNOWN_PDF, "other.pdf"))
        assert (run["status"], run["finding_ids"]) == ("SUCCEEDED", [])
        assert "not a known supplier or landlord: stored without a check" in labels(run)[1]


def test_the_same_document_number_again_is_a_duplicate() -> None:
    def extract(payload: dict) -> dict:
        answer = answer_for("inv_FLV-94818")
        answer["document_id"] = payload["document_id"]
        return answer

    with make_client(FakeAI(extract)) as client:
        run = run_of(client, upload(client, UNKNOWN_PDF, "copy.pdf"))
        assert run["status"] == "FAILED"
        assert (
            run["result"]["headline"]["text"] == "copy.pdf was already processed as inv_FLV-94818"
        )


def test_the_same_file_twice_is_refused() -> None:
    with make_client(FakeAI()) as client:
        assert upload(client, RENT_PDF).status_code == 202
        again = upload(client, RENT_PDF, "renamed.pdf")
        assert (again.status_code, again.json()["code"]) == (409, "DOCUMENT_ALREADY_PROCESSED")
        preloaded = upload(client, (PDF_DIR / "inv_FLV-94818.pdf").read_bytes())
        assert (preloaded.status_code, preloaded.json()["code"]) == (
            409,
            "DOCUMENT_ALREADY_PROCESSED",
        )


def test_a_replayed_upload_returns_the_same_run() -> None:
    ai = FakeAI()
    with make_client(ai) as client:
        key = str(uuid4())
        first = upload(client, RENT_PDF, key=key)
        assert upload(client, RENT_PDF, key=key).json() == first.json()
        assert len(ai.extracted) == 1


def test_upload_is_refused_while_a_run_is_in_progress() -> None:
    with make_client(FakeAI()) as client:
        version = client.get("/api/v1/company").json()["financial_version"]
        closure = client.post(
            "/api/v1/events",
            json={
                "type": "CLOSURE_PLANNED",
                "expected_financial_version": version,
                "payload": {"date_from": "2025-06-01", "date_to": "2025-06-30"},
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert closure.json()["status"] == "RUNNING"
        refused = upload(client, RENT_PDF)
        assert (refused.status_code, refused.json()["code"]) == (409, "RUN_IN_PROGRESS")
        with client.app.state.repository.read() as state:
            assert state.uploads == {}


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (b"%PDF-" + b"0" * (5 * 1024 * 1024), 413, "FILE_TOO_LARGE"),
        (b"plain text", 422, "VALIDATION_ERROR"),
        (b"", 422, "VALIDATION_ERROR"),
    ],
)
def test_unusable_files_are_refused(content: bytes, status: int, code: str) -> None:
    with make_client(FakeAI()) as client:
        response = upload(client, content)
        assert (response.status_code, response.json()["code"]) == (status, code)


def test_without_the_agent_a_demo_document_uses_its_stored_values() -> None:
    with TestClient(create_app(base)) as client:
        client.post("/api/v1/demo/reset")
        response = upload(client, RENT_PDF)
        assert response.json()["status"] == "SUCCEEDED"
        run = run_of(client, response)
        assert run["mode"] == "FALLBACK"
        assert labels(run)[1] == (
            "Reading with the model is off: using the stored values of this demo document"
        )
        unknown = upload(client, UNKNOWN_PDF)
        assert unknown.json()["status"] == "FAILED"


def test_reset_deletes_uploads() -> None:
    with make_client(FakeAI()) as client:
        run = run_of(client, upload(client, RENT_PDF))
        assert len(client.get("/api/v1/findings").json()) == 4
        client.post("/api/v1/demo/reset")
        assert client.get("/api/v1/documents/doc_rent_adjustment_2025_06").status_code == 404
        assert len(client.get("/api/v1/findings").json()) == 3
        assert client.app.state.repository.read_file(run["document_id"]) is None
        with client.app.state.repository.read() as state:
            assert state.uploads == {}


def test_a_restart_finishes_an_interrupted_upload_without_the_model() -> None:
    ai = FakeAI()
    with make_client(ai) as client:
        repository = client.app.state.repository
        with repository.write() as state:
            run = accept_upload(state, "late.pdf", RENT_PDF, datetime.now(UTC), agent=True)
        client.app.state.proof.recover()
        finished = client.get(f"/api/v1/runs/{run.id}").json()
        assert (finished["status"], finished["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert labels(finished)[1].startswith("Vero restarted while reading it")
        assert ai.extracted == []
        assert repository.read_file(run.document_id) == RENT_PDF


def test_upload_is_in_the_activity_log() -> None:
    with make_client(FakeAI()) as client:
        run = run_of(client, upload(client, RENT_PDF, "Mietanpassung.pdf"))
        log = client.get("/api/v1/audit").json()
        mine = [(e["actor"], e["agent"], e["action"]) for e in log if e["run_id"] == run["run_id"]]
        assert mine == [
            ("OWNER", "PROOF", "Uploaded Mietanpassung.pdf"),
            ("SYSTEM", "PROOF", "Proposed to keep paying 3 800,00 € for RENT"),
            ("SYSTEM", "PROOF", "Proposed to send a letter to Main Street Real Estate GmbH"),
            ("SYSTEM", "PROOF", run["result"]["headline"]["text"]),
        ]


def test_stored_upload_round_trips() -> None:
    with make_client(FakeAI()) as client:
        run = run_of(client, upload(client, RENT_PDF))
        repository = client.app.state.repository
        with repository.read() as state:
            stored = copy.deepcopy(state.uploads[run["document_id"]])
            finding = state.findings[UUID(run["finding_ids"][0])]
        with repository.read() as state:
            assert state.uploads[run["document_id"]] == stored
            assert state.findings[finding.id] == finding
        assert stored.values_source == "MODEL"
        assert stored.document.rent_adjustment.new_amount == 410400
