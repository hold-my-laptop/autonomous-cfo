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
from api.domain.proof.check import preloaded_findings
from api.domain.proof.findings import Classification
from api.main import create_app
from api.scripted_investigation import investigate, investigate_preloaded
from api.state import build_demo_state, demo_load_time

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
SAME = ("classification", "escalated")
NUMBERS = ("formula_id", "inputs", "outputs", "billed", "justified", "disputed")


def checked(state, document_id: str):
    """The finding as CHECK left it, before any investigation."""
    findings, _ = preloaded_findings(
        state.proof, state.cost_memory, state.history, demo_load_time(state.as_of)
    )
    return next(f for f in findings if f.document_id == document_id)


def without_email(state, email_id: str):
    mailbox = tuple(e for e in state.proof.mailbox if e.id != email_id)
    state.proof = state.proof.model_copy(update={"mailbox": mailbox})
    return state


def test_fallback_hero_gives_the_agent_paths_label_and_numbers() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        hero(client, run_id)
        post(client, run_id, "complete", ANSWER)
        agent = finding(client, run_id)
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        post(client, run_id, "fail", {"reason": "MODEL_ERROR"})
        scripted = finding(client, run_id)
        done = run(client, run_id)

    assert [scripted[k] for k in SAME] == [agent[k] for k in SAME] == ["CONFLICT", False]
    assert [scripted["investigation"][k] for k in NUMBERS] == [
        agent["investigation"][k] for k in NUMBERS
    ]
    assert scripted["investigation"]["justified"] == "380000"
    assert scripted["investigation"]["disputed"] == "30400"
    assert {r["ref"] for r in scripted["evidence_refs"] if r["ref"]} == {
        "mail_main_street_2025_04_03",
        *(r["ref"] for r in agent["evidence_refs"] if r["ref"]),
    }
    assert scripted["investigation"]["mode"] == "FALLBACK"
    assert done["mode"] == "FALLBACK"
    assert done["result"]["headline"]["text"] == agent_headline()


def agent_headline() -> str:
    return (
        "Main Street Real Estate GmbH: rent 3 800,00 € → 4 104,00 € from 1 Jun 2025: "
        "304,00 € more than allowed"
    )


def test_fallback_steps_are_backend_steps_in_hypothesis_order() -> None:
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        post(client, run_id, "fail", {"reason": "MODEL_ERROR"})
        trace = run(client, run_id)["trace"]

    fallback = next(i for i, s in enumerate(trace) if s["kind"] == "FALLBACK")
    scripted = trace[fallback:]
    assert {s["source"] for s in scripted} == {"BACKEND"}
    assert [s["label"] for s in scripted] == [
        "The agent stopped (MODEL_ERROR): Vero follows its own steps",
        "H1 · A letter or notice about the change",
        'Searching the inbox for "rent increase" from Main Street Real Estate GmbH',
        "Found 4 emails",
        "H2 · A contract clause that allows it",
        "Reading §4.2 of the lease with Main Street Real Estate GmbH",
        "H3 · The reference index then and now",
        "Looking up the consumer price index for Jan 2024 and Mar 2025",
        "117.6 in Jan 2024; 121.2 in Mar 2025 (demo values)",
        "Computing what the index clause allows",
        "Index moved 3.06%, less than 5.00%: the amount stays at 3 800,00 €",
        "Recomputed: 3 800,00 € allowed, 4 104,00 € billed",
        "Keep paying 3 800,00 € for RENT; 912,00 € disputed in the next 13 weeks",
        "Keep paying 3 800,00 € for RENT: needs approval",
        "Send a letter to Main Street Real Estate GmbH: needs approval",
        agent_headline(),
    ]


def test_asimex_surcharge_is_verified_at_load() -> None:
    with TestClient(create_app(base)) as client:
        client.post("/api/v1/demo/reset")
        listed = client.get("/api/v1/findings").json()
    surcharge = next(f for f in listed if f["document_id"] == "inv_AS-11607")

    assert (surcharge["classification"], surcharge["status"], surcharge["quiet"]) == (
        "VERIFIED",
        "CLASSIFIED",
        True,
    )
    investigation = surcharge["investigation"]
    assert (investigation["mode"], investigation["formula_id"]) == ("FALLBACK", "surcharge_rule")
    assert investigation["inputs"] == {
        "net_goods": "55871",
        "date": "2025-05-03",
        "surcharge": "2450",
        "below_net_goods": "60000",
        "valid_from": "2025-05-01",
    }
    assert (investigation["billed"], investigation["justified"]) == ("2450", "2450")
    assert investigation["outputs"] == {"applies": True, "surcharge": "2450"}
    assert [r["ref"] for r in surcharge["evidence_refs"] if r["ref"]] == [
        "mail_asimex_2025_04_14",
        "terms_asimex_2025",
        "terms_asimex_2025#s5",
    ]
    assert all(d["explained"] for d in surcharge["differences"])
    assert [(h["status"], h["label"], h["at"]) for h in surcharge["history"][-2:]] == [
        ("INVESTIGATING", "Looking for the reason", "2025-05-05T08:00:00Z"),
        ("CLASSIFIED", "Verified: 24,50 € allowed by the evidence", "2025-05-05T08:00:00Z"),
    ]


def test_asimex_without_the_announcement_is_escalated_with_its_searches() -> None:
    state = without_email(build_demo_state(base), "mail_asimex_2025_04_14")
    surcharge = checked(state, "inv_AS-11607")

    result = investigate(state, surcharge)

    assert result.grounding.classification is Classification.UNEXPLAINED
    assert result.grounding.escalated
    investigation = result.grounding.investigation
    assert investigation.searched == (
        'Searching the inbox for "Logistikzuschlag" from Asimex Wholesale Ltd',
    )
    assert investigation.problems == ("no clause and reference value explain the change",)
    assert [label for _, label in result.steps] == [
        "H1 · A letter or notice about the change",
        'Searching the inbox for "Logistikzuschlag" from Asimex Wholesale Ltd',
        "Nothing found in the inbox",
        "H2 · A clause in what was announced",
        "Can't use the answer: no clause and reference value explain the change",
    ]


def test_preloaded_investigation_without_the_announcement() -> None:
    state = without_email(build_demo_state(base), "mail_asimex_2025_04_14")
    raw = checked(state, "inv_AS-11607")
    state.findings[raw.id] = raw

    findings = investigate_preloaded(state, demo_load_time(state.as_of))

    escalated = findings[raw.id]
    assert (escalated.classification, escalated.escalated) == (Classification.UNEXPLAINED, True)
    assert escalated.history[-1].label == "No usable evidence: needs your review"
    assert not escalated.quiet


def test_rent_without_the_landlords_letter_still_conflicts() -> None:
    state = without_email(build_demo_state(base), "mail_main_street_2025_04_03")
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        with client.app.state.repository.read() as live:
            rent = live.findings[live.runs[UUID(run_id)].finding_ids[0]]

    result = investigate(state, rent)

    assert result.grounding.classification is Classification.CONFLICT
    assert result.grounding.investigation.justified == 380000
    refs = {r.ref for r in result.grounding.evidence}
    assert refs == {"lease_main_street_2021#s4_2", "ref_vpi_demo"}
