import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_document_upload import FakeAI, upload

from api.agent import AgentUnavailableError
from api.config import Settings, get_settings
from api.main import create_app
from api.proof_runs import accept_upload

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
PDF_DIR = base.proof_dataset_path.parent / "pdf"
RENT_PDF = (PDF_DIR / "doc_rent_adjustment_2025_06.pdf").read_bytes()
ANSWER = {
    "classification": "CONFLICT",
    "compute_call_id": "c4",
    "evidence_refs": ["ntc_main_street_2025_04", "lease_main_street_2021#s4_2", "ref_vpi_demo"],
    "explanation": "The index moved 3.06 %, below the 5 % threshold of §4.2.",
    "letter_draft": None,
    "model": "vero-llm",
    "prompt_version": "investigate-1",
    "tool_calls": 4,
}


class InvestigatingAI(FakeAI):
    def __init__(self, unreachable: bool = False) -> None:
        super().__init__()
        self.started: list[dict[str, Any]] = []
        self._unreachable = unreachable

    def start_run(self, payload: dict[str, Any]) -> None:
        if self._unreachable:
            raise AgentUnavailableError("connection refused")
        self.started.append(payload)


def make_client(ai: FakeAI, **changes) -> TestClient:
    settings = Settings(
        **(
            base.model_dump()
            | {"internal_token": TOKEN, "investigation_agent_enabled": True}
            | changes
        )
    )
    return TestClient(create_app(settings, ai))


def start(client: TestClient) -> str:
    response = upload(client, RENT_PDF, "Mietanpassung.pdf")
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def run(client: TestClient, run_id: str) -> dict:
    return client.get(f"/api/v1/runs/{run_id}").json()


def finding(client: TestClient, run_id: str) -> dict:
    [finding_id] = run(client, run_id)["finding_ids"]
    return client.get(f"/api/v1/findings/{finding_id}").json()


def labels(client: TestClient, run_id: str) -> list[str]:
    return [step["label"] for step in run(client, run_id)["trace"]]


def post(client: TestClient, run_id: str, path: str, body: dict):
    return client.post(f"/internal/v1/runs/{run_id}/{path}", json=body, headers=AUTH)


def tool(client: TestClient, run_id: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments, "call_id": str(uuid4())},
        headers=AUTH,
    )
    return response.json()


HERO_CALLS = (
    ("c1", "search_inbox", {"query": "rent increase", "counterparty": "Main Street"}),
    ("c2", "read_clause", {"doc_id": "lease_main_street_2021", "section": "§4.2"}),
    ("c3", "get_reference_index", {"name": "DESTATIS_VPI", "periods": ["2024-01", "2025-03"]}),
    (
        "c4",
        "compute",
        {
            "formula_id": "vpi_threshold_indexation",
            "inputs": {
                "base_amount": "380000",
                "base_index": 1176,
                "current_index": 1212,
                "threshold_bps": 500,
            },
        },
    ),
)


def call(client: TestClient, run_id: str, call_id: str, name: str, arguments: dict) -> dict:
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments, "call_id": call_id},
        headers=AUTH,
    )
    return response.json()


def hero(client: TestClient, run_id: str, calls=HERO_CALLS) -> None:
    """The hero's four tool calls, as the agent makes them (PROOF.md §7.1)."""
    for call_id, name, arguments in calls:
        assert call(client, run_id, call_id, name, arguments)["ok"]


def fell_back_because(client: TestClient, run_id: str) -> str:
    """Why the investigation agent didn't finish, from the run's last fallback step."""
    label = [s["label"] for s in run(client, run_id)["trace"] if s["kind"] == "FALLBACK"][-1]
    assert label.endswith(": Vero follows its own steps")
    return label.removesuffix(": Vero follows its own steps")


def assert_scripted_conflict(client: TestClient, run_id: str) -> None:
    """Without the agent, the scripted investigation gives the hero's label and numbers."""
    result = finding(client, run_id)
    assert (result["classification"], result["escalated"]) == ("CONFLICT", False)
    assert result["investigation"]["mode"] == "FALLBACK"
    assert (result["investigation"]["justified"], result["investigation"]["disputed"]) == (
        "380000",
        "30400",
    )


def wait_until_finished(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 5
    while run(client, run_id)["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.02)
    return run(client, run_id)


def test_agent_path() -> None:
    ai = InvestigatingAI()
    with make_client(ai) as client:
        run_id = start(client)
        running = run(client, run_id)
        assert (running["status"], running["mode"]) == ("RUNNING", "AGENT")
        assert labels(client, run_id)[-1] == (
            "Vero's agent looks for the reason: letters, contracts, reference values "
            "(at most 6 look-ups)"
        )
        assert finding(client, run_id)["status"] == "INVESTIGATING"

        [payload] = ai.started
        assert (payload["run_id"], payload["kind"]) == (run_id, "PROOF")
        assert payload["event"]["type"] == "DOCUMENT_UPLOADED"
        assert payload["limits"] == {"max_tool_calls": 6, "deadline_s": 60}
        assert payload["finding"]["id"] == running["finding_ids"][0]
        assert payload["finding"]["impact"]["billed"] == "410400"
        assert payload["finding"]["status"] == "INVESTIGATING"
        investigation = payload["investigation"]
        assert investigation["cost_item"]["cost_ref"] == "RENT"
        assert investigation["cost_item"]["expected_amount"] == "380000"
        assert investigation["hypotheses"] == [
            "NOTICE",
            "CLAUSE",
            "REFERENCE_OR_PRICE_LIST",
            "QUANTITY",
        ]

        tools = client.get(f"/internal/v1/tools?run_id={run_id}", headers=AUTH).json()
        assert tools[0]["name"] == "search_inbox"
        hero(client, run_id)
        assert post(client, run_id, "notes", {"text": "Checking the lease"}).status_code == 204

        assert post(client, run_id, "complete", ANSWER).status_code == 204

        done = run(client, run_id)
        assert (done["status"], done["mode"]) == ("SUCCEEDED", "AGENT")
        assert done["result"]["headline"]["text"] == (
            "Main Street Real Estate GmbH: rent 3 800,00 € → 4 104,00 € from 1 Jun 2025: "
            "304,00 € more than allowed"
        )
        steps = [(s["kind"], s["source"], s["label"]) for s in done["trace"]]
        assert ("THINK", "AGENT", "Checking the lease") in steps
        assert steps[-6:] == [
            (
                "INVESTIGATE",
                "AGENT",
                "Vero's note: The index moved 3.06 %, below the 5 % threshold of §4.2.",
            ),
            ("GROUND", "BACKEND", "Recomputed: 3 800,00 € allowed, 4 104,00 € billed"),
            (
                "PROPAGATE",
                "BACKEND",
                "Keep paying 3 800,00 € for RENT; 912,00 € disputed in the next 13 weeks",
            ),
            ("POLICY", "BACKEND", "Keep paying 3 800,00 € for RENT: needs approval"),
            (
                "POLICY",
                "BACKEND",
                "Send a letter to Main Street Real Estate GmbH: needs approval",
            ),
            ("SUMMARY", "BACKEND", done["result"]["headline"]["text"]),
        ]
        result = finding(client, run_id)
        assert (result["classification"], result["status"], result["escalated"]) == (
            "CONFLICT",
            "PENDING_APPROVAL",
            False,
        )
        with client.app.state.repository.read() as state:
            stored = state.runs[UUID(run_id)].tool_log
        assert stored["completion"] == ANSWER
        assert [c["seq"] for c in stored["calls"].values()] == [1, 2, 3, 4]
        log = client.get("/api/v1/audit").json()
        actions = [e["action"] for e in log if e["run_id"] == run_id]
        assert actions[-1] == done["result"]["headline"]["text"]


def test_agent_completion_must_have_the_investigation_shape() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)

        cash_shape = post(client, run_id, "complete", {"proposals": [], "summary_note": "x"})
        no_explanation = post(client, run_id, "complete", ANSWER | {"explanation": ""})
        bad_label = post(client, run_id, "complete", ANSWER | {"classification": "MAYBE"})

        for response in (cash_shape, no_explanation, bad_label):
            assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")
        assert "body.classification" in cash_shape.json()["detail"]
        assert run(client, run_id)["status"] == "RUNNING"


def test_cash_runs_still_take_proposals() -> None:
    with make_client(InvestigatingAI()) as client:
        from test_tools import start_run

        cash = start_run(client)
        wrong = post(client, cash, "complete", ANSWER)
        assert (wrong.status_code, wrong.json()["code"]) == (422, "VALIDATION_ERROR")


def test_deadline_falls_back_and_late_callbacks_are_closed() -> None:
    with make_client(InvestigatingAI(), agent_deadline_s=0.2) as client:
        run_id = start(client)

        done = wait_until_finished(client, run_id)

        assert (done["status"], done["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert fell_back_because(client, run_id) == "The agent took too long"
        steps = [s for s in done["trace"] if s["kind"] == "FALLBACK"]
        assert steps[-1]["data"] == {"reason": "DEADLINE_PASSED"}
        assert_scripted_conflict(client, run_id)
        assert done["result"]["headline"]["text"].endswith(": 304,00 € more than allowed")

        for path, body in (
            ("complete", ANSWER),
            ("notes", {"text": "late"}),
            ("fail", {"reason": "MODEL_ERROR"}),
        ):
            late = post(client, run_id, path, body)
            assert (late.status_code, late.json()["code"]) == (409, "RUN_CLOSED")
        late_tool = tool(client, run_id, "search_inbox", {"query": "rent"})
        assert late_tool["error"]["code"] == "RUN_CLOSED"


def test_agent_failure_falls_back() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)

        assert post(client, run_id, "fail", {"reason": "MODEL_ERROR"}).status_code == 204

        done = run(client, run_id)
        assert (done["status"], done["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert fell_back_because(client, run_id) == "The agent stopped (MODEL_ERROR)"
        assert_scripted_conflict(client, run_id)


def test_unreachable_ai_falls_back_at_once() -> None:
    with make_client(InvestigatingAI(unreachable=True)) as client:
        run_id = start(client)

        done = run(client, run_id)
        assert (done["status"], done["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert fell_back_because(client, run_id) == "The AI service is unavailable"


def test_investigation_agent_switch_turns_the_agent_off() -> None:
    ai = InvestigatingAI()
    with make_client(ai, investigation_agent_enabled=False) as client:
        run_id = start(client)

        assert ai.started == []
        assert run(client, run_id)["status"] == "SUCCEEDED"
        assert fell_back_because(client, run_id) == "The investigation agent is off"
    assert Settings().investigation_agent_enabled is True


def test_without_internal_token_the_upload_finishes_at_once() -> None:
    settings = Settings(**(base.model_dump() | {"internal_token": None}))
    with TestClient(create_app(settings, InvestigatingAI())) as client:
        response = upload(client, RENT_PDF, "Mietanpassung.pdf")
        assert response.json()["status"] == "SUCCEEDED"
        run_id = response.json()["run_id"]
        assert fell_back_because(client, run_id) == "The investigation agent is off"
        assert_scripted_conflict(client, run_id)


def test_data_change_during_the_investigation_falls_back() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        with client.app.state.repository.write() as state:
            state.financial_version += 1

        assert tool(client, run_id, "search_inbox", {"query": "rent"})["error"]["code"] == (
            "STALE_RUN"
        )
        assert post(client, run_id, "complete", ANSWER).status_code == 204

        assert fell_back_because(client, run_id) == "Your data changed during the investigation"


def test_restart_during_the_investigation_finishes_without_the_agent() -> None:
    ai = InvestigatingAI()
    with make_client(ai) as client:
        run_id = start(client)

        client.app.state.proof.recover()

        done = run(client, run_id)
        assert (done["status"], done["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert fell_back_because(client, run_id) == "Vero restarted during the investigation"
        assert len(ai.started) == 1


def test_restart_during_extraction_also_ends_the_investigation() -> None:
    ai = InvestigatingAI()
    with make_client(ai) as client:
        repository = client.app.state.repository
        with repository.write() as state:
            started = accept_upload(state, "late.pdf", RENT_PDF, datetime.now(UTC), agent=True)
        run_id = str(started.id)

        client.app.state.proof.recover()

        done = run(client, run_id)
        assert (done["status"], done["mode"]) == ("SUCCEEDED", "FALLBACK")
        assert labels(client, run_id)[1].startswith("Vero restarted while reading it")
        assert fell_back_because(client, run_id) == "Vero restarted during the investigation"
        assert (ai.extracted, ai.started) == ([], [])


def test_callbacks_before_the_investigation_are_closed() -> None:
    with make_client(InvestigatingAI()) as client:
        with client.app.state.repository.write() as state:
            reading = accept_upload(state, "late.pdf", RENT_PDF, datetime.now(UTC), agent=True)

        early = post(client, str(reading.id), "complete", ANSWER)

        assert (early.status_code, early.json()["code"]) == (409, "RUN_CLOSED")


def test_one_run_at_a_time_while_investigating() -> None:
    with make_client(InvestigatingAI()) as client:
        start(client)
        other = (PDF_DIR / "inv_AS-11607.pdf").read_bytes() + b"\n"

        refused = upload(client, other, "other.pdf")

        assert (refused.status_code, refused.json()["code"]) == (409, "RUN_IN_PROGRESS")
