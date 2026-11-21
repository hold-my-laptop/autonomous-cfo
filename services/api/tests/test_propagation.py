from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_investigations import (
    ANSWER,
    InvestigatingAI,
    finding,
    hero,
    make_client,
    post,
    run,
    start,
)

from api.config import get_settings
from api.domain.proof.findings import Classification, PaymentDecision
from api.domain.proof.propagation import describe, propagate
from api.main import create_app
from api.proof_runs import with_propagation
from api.scripted_investigation import investigate
from api.state import build_demo_state

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")


def s0(client: TestClient):
    with client.app.state.repository.read() as state:
        summary = state.forecast().summary
    return summary.lowest.closing, summary.lowest.date.isoformat(), summary.end_balance


def rent_finding_investigating():
    """The rent finding as CHECK hands it to the investigation, from a real upload."""
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        with client.app.state.repository.read() as state:
            return state.findings[state.runs[UUID(run_id)].finding_ids[0]]


def with_vpi(state, current: str):
    series = tuple(
        s.model_copy(
            update={
                "values": tuple(
                    v.model_copy(update={"value": Decimal(current)}) if v.period == "2025-03" else v
                    for v in s.values
                )
            }
        )
        if s.name == "DESTATIS_VPI"
        else s
        for s in state.proof.reference_values
    )
    state.proof = state.proof.model_copy(update={"reference_values": series})
    return state


@pytest.mark.parametrize("path", ["AGENT", "FALLBACK"])
def test_hero_keeps_the_rent_and_leaves_s0_unchanged(path: str) -> None:
    with make_client(InvestigatingAI()) as client:
        before = s0(client)
        run_id = start(client)
        if path == "AGENT":
            hero(client, run_id)
            post(client, run_id, "complete", ANSWER)
        else:
            post(client, run_id, "fail", {"reason": "MODEL_ERROR"})
        propagation = finding(client, run_id)["propagation"]
        trace = [s["label"] for s in run(client, run_id)["trace"] if s["kind"] == "PROPAGATE"]
        after = s0(client)

    assert propagation == {
        "decision": "KEEP",
        "cost_ref": "RENT",
        "effective_from": "2025-06-01",
        "current_amount": "380000",
        "billed_amount": "410400",
        "proposed_amount": "380000",
        "disputed": "30400",
        "disputed_annual": "364800",
        "disputed_in_window": "91200",
        "forecast": {
            "payments": ["RENT-06", "RENT-07", "RENT-08"],
            "outflow_difference": "0",
            "lowest_before": "-39630",
            "lowest_after": "-39630",
        },
        "engine_version": "proof-engine-1.0.0",
    }
    assert trace == ["Keep paying 3 800,00 € for RENT; 912,00 € disputed in the next 13 weeks"]
    assert before == after == (-39630, "2025-06-01", 239220)


def test_vpi_above_the_threshold_proposes_the_indexed_rent() -> None:
    state = with_vpi(build_demo_state(base), "1236")
    rent = rent_finding_investigating()
    schedule = [(o.ref, o.amount) for o in state.outflows if o.ref.startswith("RENT-")]

    classified = investigate(state, rent).grounding.apply(rent, rent.updated_at)
    result = with_propagation(state, classified)

    assert result.classification is Classification.CONFLICT
    assert result.investigation.outputs == {
        "change_bps": 510,
        "adjustment_allowed": True,
        "justified": "399388",
    }
    p = result.propagation
    assert (p.decision, p.proposed_amount, p.current_amount) == (
        PaymentDecision.UPDATE,
        399388,
        380000,
    )
    assert (p.disputed, p.disputed_annual, p.disputed_in_window) == (11012, 132144, 33036)
    assert p.forecast.payments == ("RENT-06", "RENT-07", "RENT-08")
    assert p.forecast.outflow_difference == 58164
    assert p.forecast.lowest_after == p.forecast.lowest_before - 19388
    assert describe(p) == (
        "Proposed: pay 3 993,88 € for RENT from 1 Jun 2025 "
        "(3 payments in the next 13 weeks, 581,64 € more)"
    )
    assert [(o.ref, o.amount) for o in state.outflows if o.ref.startswith("RENT-")] == schedule


def test_a_verified_new_amount_proposes_the_billed_amount() -> None:
    state = build_demo_state(base)
    rent = rent_finding_investigating()
    classified = investigate(state, rent).grounding.apply(rent, rent.updated_at)
    verified = classified.model_copy(
        update={
            "classification": Classification.VERIFIED,
            "investigation": classified.investigation.model_copy(
                update={"justified": 410400, "disputed": None}
            ),
        }
    )

    p = with_propagation(state, verified).propagation

    assert (p.decision, p.proposed_amount, p.disputed) == (PaymentDecision.UPDATE, 410400, None)
    assert p.forecast.outflow_difference == 91200
    assert p.disputed_in_window is None


def test_an_unexplained_change_keeps_paying_without_a_dispute() -> None:
    state = build_demo_state(base)
    rent = rent_finding_investigating()
    escalated = rent.model_copy(
        update={"status": "CLASSIFIED", "classification": Classification.UNEXPLAINED}
    )
    item = next(c for c in state.cost_memory if c.cost_ref == "RENT")

    p = propagate(escalated, item, state.forecast_input())

    assert (p.decision, p.proposed_amount) == (PaymentDecision.KEEP, 380000)
    assert (p.disputed, p.disputed_annual, p.disputed_in_window) == (None, None, None)
    assert propagate(rent, item, state.forecast_input()) is None


def test_preloaded_invoices_are_already_paid() -> None:
    with TestClient(create_app(base)) as client:
        client.post("/api/v1/demo/reset")
        listed = client.get("/api/v1/findings").json()

    assert [(f["document_id"], f["propagation"]["decision"]) for f in listed] == [
        ("inv_AS-72217", "PAID"),
        ("inv_FLV-94818", "PAID"),
        ("inv_AS-11607", "PAID"),
    ]
    assert all(f["propagation"]["disputed"] is None for f in listed)
    assert all(f["propagation"]["forecast"] is None for f in listed)
