import asyncio

from ai.agent import run_agent
from ai.llm import StubLLM
from ai.schemas import AgentRunStart

from conftest import CANDIDATE, CREDIT, OUTFLOW, ScriptedLLM

START = AgentRunStart(run_id="r1", event={"type": "CLOSURE_PLANNED", "label": "You plan to close 1-30 June"},
                      company={"name": "Pizzeria Amalfi", "as_of_date": "2025-05-05"},
                      limits={"max_tool_calls": 8, "deadline_s": 60})
MOVE = {"outflow_id": OUTFLOW, "to_date": CANDIDATE["to_date"]}
GOOD_SUBMIT = ("submit_proposals", {"proposals": [
    {"kind": "RESCHEDULE_PAYMENT", "move": MOVE, "rationale": "Pay later."},
    {"kind": "CREDIT_LINE", "credit_line": {"amount": CREDIT["amount"], "available_from": CREDIT["available_from"]}},
]})


def run(llm, backend, settings, start=START):
    return asyncio.run(run_agent(start, llm, backend, settings))


def test_happy_path_completes_in_agent_mode(backend, settings):
    llm = ScriptedLLM([("get_forecast", {}), ("find_reschedule_options", {}),
                       ("size_credit_line", {"after_moves": [MOVE]}), GOOD_SUBMIT])
    assert run(llm, backend, settings) == "COMPLETED"
    c = backend.completed
    assert [p.kind for p in c.proposals] == ["RESCHEDULE_PAYMENT", "CREDIT_LINE"]
    assert c.tool_calls == 3 and c.model == "scripted-model"
    assert [n for n, _ in backend.calls] == ["get_forecast", "find_reschedule_options", "size_credit_line"]
    assert backend.failed is None


def test_invented_credit_amount_is_corrected_then_accepted(backend, settings):
    wrong = ("submit_proposals", {"proposals": [
        {"kind": "CREDIT_LINE", "credit_line": {"amount": "2500000", "available_from": "2025-06-28"}}]})
    right = ("submit_proposals", {"proposals": [
        {"kind": "CREDIT_LINE", "credit_line": {"amount": "2000000", "available_from": "2025-06-28"}}]})
    llm = ScriptedLLM([("get_forecast", {}), ("size_credit_line", {}), wrong, right])
    assert run(llm, backend, settings) == "COMPLETED"
    last_tool_msg = llm.seen_messages[-1][-1]
    assert last_tool_msg["role"] == "tool" and "2000000" in last_tool_msg["content"]


def test_move_not_from_planner_fails_after_retries(backend, settings):
    bad = ("submit_proposals", {"proposals": [
        {"kind": "RESCHEDULE_PAYMENT", "move": {"outflow_id": "made-up", "to_date": "2025-08-01"}}]})
    llm = ScriptedLLM([("find_reschedule_options", {}), bad, bad, bad])
    assert run(llm, backend, settings) == "FAILED"
    assert backend.failed[0] == "INVALID_TOOL_CALLS" and backend.completed is None


def test_model_error_reports_failure(backend, settings):
    llm = ScriptedLLM([RuntimeError("connection refused")])
    assert run(llm, backend, settings) == "FAILED"
    assert backend.failed[0] == "MODEL_ERROR"


def test_chatty_model_without_tools_gives_no_final_answer(backend, settings):
    llm = ScriptedLLM(["Let me think.", "Still thinking.", "Hmm."])
    assert run(llm, backend, settings) == "FAILED"
    assert backend.failed[0] == "NO_FINAL_ANSWER"


def test_tool_limit_then_only_submit_is_offered(backend, settings):
    start = START.model_copy(update={"limits": START.limits.model_copy(update={"max_tool_calls": 2})})
    llm = ScriptedLLM([("get_forecast", {}), ("find_reschedule_options", {}), GOOD_SUBMIT[0:1] + (
        {"proposals": [{"kind": "RESCHEDULE_PAYMENT", "move": MOVE}]},)])
    assert run(llm, backend, settings, start) == "COMPLETED"
    offered_last = llm.seen_messages  # third call happened with only submit offered
    assert len(backend.calls) == 2 and len(offered_last) == 3


def test_stale_run_stops_quietly(backend, settings):
    backend.stale_after = 1
    llm = ScriptedLLM([("get_forecast", {}), ("find_reschedule_options", {})])
    assert run(llm, backend, settings) == "STOPPED"
    assert backend.failed is None and backend.completed is None


def test_no_breach_allows_empty_proposals(settings):
    from conftest import FakeBackend
    b = FakeBackend(breach=False)
    llm = ScriptedLLM([("get_forecast", {}), ("submit_proposals", {"proposals": []})])
    assert run(llm, b, settings) == "COMPLETED" and b.completed.proposals == []


def test_stub_llm_drives_a_full_run(backend, settings):
    assert run(StubLLM(), backend, settings) == "COMPLETED"
    assert [p.kind for p in backend.completed.proposals] == ["RESCHEDULE_PAYMENT", "CREDIT_LINE"]


def test_deadline_turns_into_model_timeout(backend, settings):
    class SlowLLM(ScriptedLLM):
        async def chat(self, *a, **k):
            await asyncio.sleep(5)

    start = START.model_copy(update={"limits": START.limits.model_copy(update={"deadline_s": 1})})
    assert run(SlowLLM([]), backend, settings, start) == "FAILED"
    assert backend.failed[0] == "MODEL_TIMEOUT"
