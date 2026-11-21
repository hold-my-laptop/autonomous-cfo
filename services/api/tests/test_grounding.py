from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_investigations import (
    ANSWER,
    HERO_CALLS,
    InvestigatingAI,
    call,
    finding,
    hero,
    labels,
    make_client,
    post,
    run,
    start,
)

from api.config import get_settings
from api.domain.models import Run, RunKind, RunMode, RunStatus
from api.domain.proof.check import preloaded_findings
from api.domain.proof.findings import Classification
from api.domain.proof.grounding import (
    Call,
    base_sources,
    calls_of,
    canon,
    ground,
)
from api.proof_tools import PROOF_TOOLS
from api.state import build_demo_state, demo_load_time

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")


def investigate(answer: dict, calls=HERO_CALLS) -> tuple[dict, dict, list[str]]:
    """Upload the rent, make the calls, complete with `answer`: the run, the finding, labels."""
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        hero(client, run_id, calls)
        assert post(client, run_id, "complete", answer).status_code == 204
        return run(client, run_id), finding(client, run_id), labels(client, run_id)


def with_inputs(**changes) -> tuple:
    call_id, name, arguments = HERO_CALLS[3]
    inputs = arguments["inputs"] | changes
    return (*HERO_CALLS[:3], (call_id, name, arguments | {"inputs": inputs}))


def test_hero_conflict_with_the_engine_numbers() -> None:
    done, result, steps = investigate(ANSWER)

    assert (result["classification"], result["escalated"]) == ("CONFLICT", False)
    investigation = result["investigation"]
    assert investigation["mode"] == "AGENT"
    assert investigation["formula_id"] == "vpi_threshold_indexation"
    assert investigation["outputs"] == {
        "change_bps": 306,
        "adjustment_allowed": False,
        "justified": "380000",
    }
    assert (investigation["billed"], investigation["justified"], investigation["disputed"]) == (
        "410400",
        "380000",
        "30400",
    )
    assert investigation["note"] == ANSWER["explanation"]
    assert investigation["searched"] == [
        'Searching the inbox for "rent increase" from Main Street Real Estate GmbH',
        "Reading §4.2 of the lease with Main Street Real Estate GmbH",
        "Looking up the consumer price index for Jan 2024 and Mar 2025",
        "Computing what the index clause allows",
    ]
    assert [h["label"] for h in result["history"][-2:]] == [
        "Conflict: 4 104,00 € billed, 3 800,00 € allowed",
        "Waiting for your decision on 2 actions",
    ]
    cited = [(r["kind"], r["document_id"], r["item_id"], r["ref"]) for r in result["evidence_refs"]]
    assert cited[-3:] == [
        ("DOCUMENT", "ntc_main_street_2025_04", None, "ntc_main_street_2025_04"),
        ("CLAUSE", "lease_main_street_2021", "s4_2", "lease_main_street_2021#s4_2"),
        ("REFERENCE_VALUE", None, None, "ref_vpi_demo"),
    ]


def test_a_claimed_verified_rent_ends_as_conflict() -> None:
    done, result, steps = investigate(ANSWER | {"classification": "VERIFIED"})

    assert result["classification"] == "CONFLICT"
    assert result["investigation"]["proposed"] == "VERIFIED"
    assert result["investigation"]["justified"] == "380000"
    assert "Vero's agent proposed verified; the backend's check says conflict" in steps


def test_a_citation_no_tool_returned_is_dropped() -> None:
    refs = [*ANSWER["evidence_refs"], "inv_AS-11607", "lease_main_street_2021#s4_3"]

    done, result, steps = investigate(ANSWER | {"evidence_refs": refs})

    assert result["investigation"]["dropped_refs"] == [
        "inv_AS-11607",
        "lease_main_street_2021#s4_3",
    ]
    assert (
        "Dropped 2 citations no tool returned in this run: inv_AS-11607, "
        "lease_main_street_2021#s4_3"
    ) in steps
    cited = {r["ref"] for r in result["evidence_refs"] if r["ref"]}
    assert "inv_AS-11607" not in cited
    assert "lease_main_street_2021#s4_3" not in cited
    assert result["classification"] == "CONFLICT"


def test_a_threshold_not_in_the_clause_is_rejected() -> None:
    done, result, steps = investigate(ANSWER, with_inputs(threshold_bps=1000))

    assert (result["classification"], result["escalated"]) == ("UNEXPLAINED", True)
    investigation = result["investigation"]
    assert investigation["problems"] == [
        "threshold_bps 1000 has no source in Cost Memory, the document or an earlier tool result"
    ]
    assert (investigation["justified"], investigation["formula_id"]) == (None, None)
    assert len(investigation["searched"]) == 4
    assert steps[-4].startswith("Can't use the answer: threshold_bps 1000 has no source")
    assert steps[-2] == "Keep paying 3 800,00 € for RENT"
    assert done["result"]["headline"]["text"].endswith(": needs your review")
    assert done["mode"] == "AGENT"


def test_an_index_no_tool_returned_is_rejected() -> None:
    done, result, steps = investigate(ANSWER, with_inputs(current_index=1300))

    assert result["classification"] == "UNEXPLAINED"
    assert result["investigation"]["problems"][0].startswith("current_index 1300 has no source")


def test_the_billed_rent_is_not_a_base_amount() -> None:
    done, result, steps = investigate(ANSWER, with_inputs(base_amount="410400"))

    assert result["classification"] == "UNEXPLAINED"
    assert result["investigation"]["problems"][0].startswith("base_amount '410400' has no source")


def test_clause_terms_must_come_from_an_earlier_call() -> None:
    clause, index, computed = HERO_CALLS[1], HERO_CALLS[2], HERO_CALLS[3]

    done, result, steps = investigate(ANSWER, (HERO_CALLS[0], index, computed, clause))

    assert result["classification"] == "UNEXPLAINED"
    assert result["investigation"]["problems"] == [
        "threshold_bps 500 has no source in Cost Memory, the document or an earlier tool result"
    ]


@pytest.mark.parametrize(
    ("compute_call_id", "problem"),
    [
        (None, "the answer names no compute call"),
        ("c9", "no compute call 'c9' in this run"),
        ("c3", "no compute call 'c3' in this run"),
    ],
)
def test_the_label_needs_this_runs_compute_call(compute_call_id, problem) -> None:
    done, result, steps = investigate(ANSWER | {"compute_call_id": compute_call_id})

    assert (result["classification"], result["escalated"]) == ("UNEXPLAINED", True)
    assert result["investigation"]["problems"] == [problem]


def test_a_failed_compute_call_is_not_usable() -> None:
    failing = ("c4", "compute", {"formula_id": "vpi_threshold_indexation", "inputs": {}})
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        hero(client, run_id, HERO_CALLS[:3])
        assert not call(client, run_id, *failing)["ok"]
        post(client, run_id, "complete", ANSWER)
        result = finding(client, run_id)

    assert result["investigation"]["problems"] == ["compute call 'c4' failed"]


def test_a_formula_that_does_not_fit_the_finding() -> None:
    surcharge = (
        "c4",
        "compute",
        {
            "formula_id": "surcharge_rule",
            "inputs": {
                "net_goods": "380000",
                "date": "2025-06-01",
                "surcharge": "30400",
                "below_net_goods": "60000",
                "valid_from": "2025-05-01",
            },
        },
    )
    annualize = ("c4", "compute", {"formula_id": "annualize", "inputs": {"monthly": "30400"}})

    first = investigate(ANSWER, (*HERO_CALLS[:3], surcharge))[1]
    second = investigate(ANSWER, (*HERO_CALLS[:3], annualize))[1]

    assert first["investigation"]["problems"] == ["surcharge_rule does not fit this finding"]
    assert second["investigation"]["problems"] == ["annualize does not decide a label"]


def test_the_cited_letter_becomes_visible_in_documents() -> None:
    with make_client(InvestigatingAI()) as client:
        listed = [d["id"] for d in client.get("/api/v1/documents").json()]
        assert "ntc_main_street_2025_04" not in listed
        run_id = start(client)
        hero(client, run_id)
        post(client, run_id, "complete", ANSWER)
        listed = [d["id"] for d in client.get("/api/v1/documents").json()]
    assert "ntc_main_street_2025_04" in listed


def test_without_the_agent_its_own_steps_are_what_was_searched() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        hero(client, run_id, HERO_CALLS[:1])
        post(client, run_id, "fail", {"reason": "MODEL_ERROR"})
        investigation = finding(client, run_id)["investigation"]
    assert investigation["mode"] == "FALLBACK"
    assert investigation["searched"] == [
        'Searching the inbox for "rent increase" from Main Street Real Estate GmbH',
        "Reading §4.2 of the lease with Main Street Real Estate GmbH",
        "Looking up the consumer price index for Jan 2024 and Mar 2025",
        "Computing what the index clause allows",
    ]
    assert investigation["problems"] == []


def asimex_calls(state, run) -> list[Call]:
    """The surcharge investigation through the real tool handlers."""
    steps = [
        ("search_inbox", {"query": "Logistikzuschlag", "counterparty": "Asimex"}),
        ("read_clause", {"doc_id": "terms_asimex_2025", "section": "§5"}),
        (
            "compute",
            {
                "formula_id": "surcharge_rule",
                "inputs": {
                    "net_goods": "55871",
                    "date": "2025-05-03",
                    "surcharge": "2450",
                    "below_net_goods": "60000",
                    "valid_from": "2025-05-01",
                },
            },
        ),
    ]
    calls = []
    for seq, (name, arguments) in enumerate(steps, start=1):
        tool = PROOF_TOOLS[name]
        output = tool.handler(state, run, tool.args.model_validate(arguments), base)
        calls.append(Call(seq, f"a{seq}", name, arguments, output.label, True, output.result))
    return calls


def test_asimex_surcharge_is_verified() -> None:
    state = build_demo_state(base)
    checked, _ = preloaded_findings(
        state.proof, state.cost_memory, state.history, demo_load_time(state.as_of)
    )
    surcharge = next(f for f in checked if f.document_id == "inv_AS-11607")
    run = Run(
        id=surcharge.id,
        event_id=surcharge.id,
        trigger_type="DOCUMENT_UPLOADED",
        trigger_label="test",
        status=RunStatus.RUNNING,
        mode=RunMode.AGENT,
        created_at=datetime.now(UTC),
        kind=RunKind.PROOF,
    )
    calls = asimex_calls(state, run)
    document = state.proof.document("inv_AS-11607")
    answer = {
        "classification": "VERIFIED",
        "compute_call_id": "a3",
        "evidence_refs": ["mail_asimex_2025_04_14", "terms_asimex_2025#s5"],
        "explanation": "Announced on 14 Apr, §5 of the terms.",
    }

    grounding = ground(
        surcharge,
        answer,
        calls,
        base_sources(surcharge, None, document),
        state.proof,
        {d.id: d for d in state.proof.documents},
        state.forecast_input(),
    )

    assert grounding.classification is Classification.VERIFIED
    assert (grounding.investigation.billed, grounding.investigation.justified) == (2450, 2450)
    assert grounding.investigation.disputed is None
    assert all(d.explained for d in grounding.differences)
    assert [(r.kind.value, r.ref) for r in grounding.evidence] == [
        ("EMAIL", "mail_asimex_2025_04_14"),
        ("CLAUSE", "terms_asimex_2025#s5"),
    ]


def test_calls_come_back_in_order() -> None:
    log = {
        "calls": {
            "b": {"seq": 2, "tool": "compute", "arguments": {}, "label": "x", "response": {}},
            "a": {"seq": 1, "tool": "read_clause", "arguments": {}, "label": "y", "response": {}},
        }
    }
    assert [c.call_id for c in calls_of(log)] == ["a", "b"]
    assert calls_of(None) == []


def test_canon_compares_numbers_by_value() -> None:
    assert canon(380000) == canon("380000") == canon(Decimal("380000.00")) == "380000"
    assert canon("1.040") == canon("1.04")
    assert canon("2024-01") == "2024-01"
    assert canon(True) is None
