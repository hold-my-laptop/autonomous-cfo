from dataclasses import replace
from datetime import UTC, datetime

import pytest

from api.config import get_settings
from api.domain.proof.check import (
    check_document,
    finding_id,
    preloaded_findings,
    routine_checks,
    within_tolerance,
)
from api.domain.proof.findings import Classification, DifferenceKind, FindingStatus
from api.state import DemoState, build_demo_state

settings = get_settings()
pytestmark = pytest.mark.skipif(not settings.statement_path.exists(), reason="sample CSV not found")
AT = datetime(2025, 5, 5, 8, tzinfo=UTC)


@pytest.fixture(scope="module")
def state() -> DemoState:
    return build_demo_state(settings)


def by_document(state: DemoState) -> dict:
    findings, _ = preloaded_findings(state.proof, state.cost_memory, state.history, AT)
    return {f.document_id: f for f in findings}


def test_rent_adjustment_is_unexplained(state: DemoState) -> None:
    document = state.proof.document("doc_rent_adjustment_2025_06")
    finding = check_document(document, state.proof, state.cost_memory, [], AT, finding_id("x"))
    assert finding is not None
    assert (finding.classification, finding.status) == (
        Classification.UNEXPLAINED,
        FindingStatus.CHECKED,
    )
    assert (finding.cost_ref, finding.period_from.isoformat()) == ("RENT", "2025-06-01")
    impact = finding.impact
    assert (impact.expected, impact.billed, impact.delta, impact.bps) == (
        380000,
        410400,
        30400,
        800,
    )
    assert (
        finding.title
        == "Main Street Real Estate GmbH: rent 3 800,00 € → 4 104,00 € from 1 Jun 2025"
    )
    assert finding.history[-1].label.endswith("expected: +8,00 %")


def test_rent_adjustment_within_two_percent_is_no_finding(state: DemoState) -> None:
    document = state.proof.document("doc_rent_adjustment_2025_06")
    small = document.model_copy(
        update={
            "rent_adjustment": document.rent_adjustment.model_copy(update={"new_amount": 387600})
        }
    )
    assert check_document(small, state.proof, state.cost_memory, [], AT, finding_id("x")) is None


def test_preloaded_invoices_give_exactly_the_intended_findings(state: DemoState) -> None:
    found = by_document(state)
    assert set(found) == {"inv_AS-72217", "inv_FLV-94818", "inv_AS-11607"}

    fattoria = found["inv_FLV-94818"]
    assert (fattoria.classification, fattoria.status) == (
        Classification.VERIFIED,
        FindingStatus.CLASSIFIED,
    )
    assert fattoria.previous_document_id == "inv_FLV-16261"
    kinds = [d.kind for d in fattoria.differences]
    assert kinds.count(DifferenceKind.PRICE_CHANGE) == 4
    assert kinds.count(DifferenceKind.NEW_ITEM) == 1
    assert all(d.explained for d in fattoria.differences)
    assert {r.document_id for d in fattoria.differences for r in d.evidence} >= {"pl_fattoria_2025"}
    chianti = next(d for d in fattoria.differences if d.description.startswith("Chianti"))
    assert (str(chianti.previous_unit_price), str(chianti.unit_price), chianti.delta) == (
        "8.90",
        "9.40",
        1500,
    )

    asimex = found["inv_AS-72217"]
    assert (asimex.classification, asimex.previous_document_id) == (
        Classification.VERIFIED,
        "inv_AS-80497",
    )

    surcharge = found["inv_AS-11607"]
    assert (surcharge.classification, surcharge.status) == (
        Classification.UNEXPLAINED,
        FindingStatus.CHECKED,
    )
    assert surcharge.previous_document_id == "inv_AS-72217"
    [line] = surcharge.differences
    assert (line.kind, line.description, line.delta, line.explained) == (
        DifferenceKind.NEW_CHARGE,
        "Logistikzuschlag",
        2450,
        False,
    )
    assert (surcharge.impact.expected, surcharge.impact.billed) == (55871, 58321)


def test_first_invoice_of_a_supplier_is_the_baseline(state: DemoState) -> None:
    first = state.proof.document("inv_FLV-16261")
    assert check_document(first, state.proof, state.cost_memory, [], AT, finding_id("x")) is None


def test_price_list_issued_after_the_invoice_does_not_explain_it(state: DemoState) -> None:
    documents = [
        d.model_copy(update={"date": d.date.replace(month=5, day=4)})
        if d.id == "pl_fattoria_2025"
        else d
        for d in state.proof.documents
    ]
    dataset = state.proof.model_copy(update={"documents": tuple(documents)})
    previous = dataset.document("inv_FLV-16261")
    finding = check_document(
        dataset.document("inv_FLV-94818"),
        dataset,
        state.cost_memory,
        [previous],
        AT,
        finding_id("x"),
    )
    assert finding is not None and finding.classification is Classification.UNEXPLAINED


def test_routine_checks_match_their_contracts(state: DemoState) -> None:
    checks, mismatches = routine_checks(state.cost_memory, state.history, AT)
    assert mismatches == []
    assert {c.cost_ref for c in checks} == {"RENT", "UTIL", "XFER"}
    assert all(c.matches for c in checks)
    april = {c.cost_ref: c for c in checks if c.date.month == 4}
    assert (april["RENT"].paid, april["RENT"].document_id, april["RENT"].clause_id) == (
        380000,
        "lease_main_street_2021",
        "s4_1",
    )
    assert april["UTIL"].paid == 65000
    xfer = april["XFER"]
    assert (xfer.paid, xfer.currency, xfer.eur_amount, xfer.fx_rate) == (
        416667,
        "CHF",
        433334,
        "1.040",
    )
    march = next(c for c in checks if c.cost_ref == "XFER" and c.date.month == 3)
    assert (march.paid, march.eur_amount) == (416667, 432500)
    assert len(state.routine_checks) == len(checks)


def test_a_payment_off_the_contract_is_a_difference(state: DemoState) -> None:
    rent = next(t for t in state.history if t.category == "RENT" and t.booking_date.month == 4)
    history = [replace(t, amount=-390000) if t.id == rent.id else t for t in state.history]
    checks, mismatches = routine_checks(state.cost_memory, history, AT)
    [finding] = mismatches
    assert (finding.transaction_id, finding.classification) == (
        rent.id,
        Classification.UNEXPLAINED,
    )
    assert (finding.impact.expected, finding.impact.billed, finding.impact.delta) == (
        380000,
        390000,
        10000,
    )
    assert not next(c for c in checks if c.transaction_id == rent.id).matches


def test_tolerance_is_two_percent() -> None:
    assert within_tolerance(387600, 380000)
    assert not within_tolerance(387601, 380000)
    assert within_tolerance(372400, 380000)
