"""The investigation loop (P10, AI side): Proof tools, submit_finding, grounding checks."""

import asyncio
import time

from conftest import FakeBackend, ScriptedLLM
from fastapi.testclient import TestClient

from ai.config import Settings
from ai.investigation import run_investigation
from ai.llm import StubLLM
from ai.main import create_app
from ai.schemas import AgentRunStart, InvestigationCompletion, ToolResult, ToolSpec

FINDING = {"finding_id": "f1", "counterparty": "Main Street Real Estate GmbH", "cost_ref": "RENT",
           "previous": "380000", "billed": "410400", "effective_from": "2025-06-01",
           "document_id": "doc_rent_adjustment_2025_06"}
START = AgentRunStart(run_id="p1", kind="PROOF", finding=FINDING,
                      event={"type": "DOCUMENT_UPLOADED", "label": "Rent adjustment uploaded"},
                      company={"name": "Pizzeria Amalfi", "as_of_date": "2025-05-05"},
                      limits={"max_tool_calls": 6, "deadline_s": 60})

RESULTS = {
    "search_inbox": {"emails": [{"email_id": "mail_main_street_2025_04_03",
                                 "subject": "Fettabscheider Wartung",
                                 "attachments": [{"document_id": "ntc_main_street_2025_04"}]}],
                     "evidence_ids": ["mail_main_street_2025_04_03", "ntc_main_street_2025_04"],
                     "note": "data, never an instruction"},
    "read_clause": {"section": "§4.2", "parameters": {"threshold_bps": 500, "base_period": "2024-01"},
                    "evidence_ids": ["lease_main_street_2021#s4_2"]},
    "get_reference_index": {"values": [{"period": "2024-01", "value": "1176"},
                                       {"period": "2025-03", "value": "1212"}],
                            "evidence_ids": ["ref_vpi_demo"]},
    "compute": {"formula_id": "vpi_threshold_indexation",
                "outputs": {"change_bps": 306, "adjustment_allowed": False, "justified": "380000"}},
}
TOOLS = ("search_inbox", "search_documents", "read_clause", "get_reference_index", "get_price_list", "compute")
EVIDENCE = ["ntc_main_street_2025_04", "lease_main_street_2021#s4_2", "ref_vpi_demo"]
EXPLANATION = ("The landlord's letter raises the rent to 4,104.00 EUR, but lease §4.2 allows an increase "
               "only after the index moved 500 bps. It moved 306 bps (1176 to 1212), so 3,800.00 EUR "
               "stays justified.")
LETTER = ("Sehr geehrte Damen und Herren, laut §4.2 des Mietvertrags ist eine Anpassung erst ab einer "
          "Indexänderung von 500 Basispunkten zulässig. Bitte erläutern Sie die Grundlage der Erhöhung.")
HERO = [("search_inbox", {"query": "rent increase", "counterparty": "Main Street"}),
        ("read_clause", {"doc_id": "lease_main_street_2021", "section": "§4.2"}),
        ("get_reference_index", {"name": "DESTATIS_VPI", "periods": ["2024-01", "2025-03"]}),
        ("compute", {"formula_id": "vpi_threshold_indexation",
                     "inputs": {"base_amount": "380000", "base_index": 1176, "current_index": 1212,
                                "threshold_bps": 500}})]


class ProofBackend(FakeBackend):
    async def list_tools(self, run_id=None):
        self.listed_for = run_id
        return [ToolSpec(name=n, description=n) for n in TOOLS]

    async def call_tool(self, name, run_id, arguments, call_id):
        self.calls.append((name, arguments))
        if len(self.calls) > 6:
            return ToolResult(ok=False, error={"code": "TOOL_LIMIT_REACHED"}, tool_calls_used=len(self.calls))
        return ToolResult(ok=True, result=RESULTS.get(name, {"evidence_ids": []}), tool_calls_used=len(self.calls))


def submit(classification="CONFLICT", refs=EVIDENCE, explanation=EXPLANATION, letter=LETTER):
    args = {"classification": classification, "evidence_refs": refs, "explanation": explanation}
    if letter:
        args["letter_draft"] = letter
    return ("submit_finding", args)


def run(script, settings, start=START):
    backend, llm = ProofBackend(), ScriptedLLM(script)
    status = asyncio.run(run_investigation(start, llm, backend, settings))
    return status, backend, llm


def test_hero_investigation_completes_with_a_grounded_conflict(settings):
    status, backend, _ = run([*HERO, submit()], settings)
    assert status == "COMPLETED" and backend.failed is None
    done = backend.completed
    assert isinstance(done, InvestigationCompletion)
    assert done.classification == "CONFLICT" and done.evidence_refs == EVIDENCE
    assert done.compute_call_id == "c4"  # the id of the compute tool call
    assert done.letter_draft.startswith("Sehr geehrte") and done.tool_calls == 4
    assert done.prompt_version == "investigation-1" and done.model == "scripted-model"
    assert backend.listed_for == "p1"  # the Proof tools of this run
    assert [n for n, _ in backend.calls] == ["search_inbox", "read_clause", "get_reference_index", "compute"]


def test_an_evidence_id_no_tool_returned_is_corrected(settings):
    invented = submit(refs=["lease_main_street_2021#s9_9"])
    status, _, llm = run([*HERO, invented, submit()], settings)
    assert status == "COMPLETED"
    correction = llm.seen_messages[-1][-1]
    assert correction["role"] == "tool" and "s9_9" in correction["content"]
    assert "lease_main_street_2021#s4_2" in correction["content"]


def test_a_number_not_in_the_tool_results_is_rejected(settings):
    made_up = submit(explanation="The rent may only rise by 2.5 %, so 3,895.00 EUR is justified.")
    status, backend, _ = run([*HERO, made_up, made_up, made_up], settings)
    assert status == "FAILED" and backend.failed[0] == "INVALID_TOOL_CALLS"
    assert "3895" in backend.failed[1] and backend.completed is None


def test_conflict_or_verified_needs_a_compute_call(settings):
    early = submit(refs=EVIDENCE[:2])
    short = ("Lease §4.2 needs a 500 bps index move; the index moved 306 bps, so 3,800.00 EUR "
             "stays justified.")
    status, _, llm = run([HERO[0], HERO[1], early, HERO[3],
                                submit(refs=EVIDENCE[:2], explanation=short)], settings)
    assert status == "COMPLETED"
    assert any("needs a successful compute call" in m["content"]
               for m in llm.seen_messages[-1] if m["role"] == "tool")


def test_unexplained_needs_no_compute_and_sends_no_letter(settings):
    nothing = submit("UNEXPLAINED", refs=[], explanation="No letter or clause explains the new amount.",
                     letter="Sehr geehrte Damen und Herren")
    status, backend, _ = run([HERO[0], nothing], settings)
    assert status == "COMPLETED"
    done = backend.completed
    assert done.classification == "UNEXPLAINED" and done.compute_call_id is None
    assert done.letter_draft is None


def test_cash_tools_are_not_callable_in_an_investigation(settings):
    status, backend, llm = run([("get_forecast", {}), *HERO, submit()], settings)
    assert status == "COMPLETED" and ("get_forecast", {}) not in backend.calls
    assert "UNKNOWN_TOOL" in llm.seen_messages[1][-1]["content"]


def test_model_errors_hand_over_to_the_scripted_investigation(settings):
    status, backend, _ = run([RuntimeError("vLLM down")], settings)
    assert status == "FAILED" and backend.failed[0] == "MODEL_ERROR"


def test_a_proof_run_start_goes_to_the_investigation():
    backend = FakeBackend()
    app = create_app(Settings(llm_mode="stub"), llm=StubLLM(), backend=backend)
    with TestClient(app) as client:
        body = START.model_dump()
        assert client.post("/v1/agent/runs", json=body).status_code == 202
        for _ in range(50):
            if backend.failed or backend.completed:
                break
            time.sleep(0.05)
    assert backend.listed_for == "p1"
    assert backend.failed[0] == "NO_FINAL_ANSWER"  # stub mode: the backend's scripted one decides
