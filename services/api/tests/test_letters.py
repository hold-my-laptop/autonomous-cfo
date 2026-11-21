from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_investigations import (
    ANSWER,
    InvestigatingAI,
    finding,
    hero,
    labels,
    make_client,
    post,
    start,
)
from test_proof_actions import client_for, upload_rent, vpi_above_threshold

from api.config import get_settings
from api.letters import euro_amounts

base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
SUBJECT = "Ihre Mietanpassung ab 01.06.2025 – Bitte um Erläuterung gemäß §4.2 Mietvertrag"
TEMPLATE = """Sehr geehrte Damen und Herren,

mit Ihrem Schreiben vom 30.04.2025 kündigen Sie an, die monatliche Miete ab dem 01.06.2025 \
von 3.800,00 € auf 4.104,00 € anzupassen.

Nach §4.2 des Mietvertrags ist eine Anpassung an den Verbraucherpreisindex (VPI) möglich, wenn \
sich der Index gegenüber seinem Stand bei der letzten Anpassung um mindestens 5 % verändert hat. \
Nach unseren Unterlagen lag der VPI im Januar 2024 bei 117,6 und im März 2025 bei 121,2; das \
entspricht einer Veränderung von 3,06 %. Eine Anpassung nach §4.2 ergibt sich daraus nach unserem \
Verständnis nicht; die Miete beträgt weiterhin 3.800,00 €.

Wir bitten Sie daher um eine kurze Erläuterung, auf welcher vertraglichen Grundlage die Änderung \
beruht, und um Ihre Berechnung. Sollten wir einen Umstand übersehen haben, freuen wir uns über \
Ihren Hinweis.

Mit freundlichen Grüßen

Vincenzo Amalfi
Pizzeria Amalfi"""
DRAFT = (
    "Sehr geehrte Damen und Herren,\n\nSie kündigen eine Miete von 4.104,00 € ab dem 01.06.2025 "
    "an. Nach §4.2 des Mietvertrags hat sich der VPI um 3,06 % verändert, weniger als 5 %; die "
    "Miete bleibt daher bei 3.800,00 €. Bitte erläutern Sie uns die Grundlage der Anpassung.\n\n"
    "Mit freundlichen Grüßen\nVincenzo Amalfi"
)


def letter(client: TestClient, finding_id: str) -> dict:
    listed = client.get("/api/v1/recommendations").json()
    [rec] = [r for r in listed if r["finding_id"] == finding_id and r["kind"] == "SEND_LETTER"]
    return rec["subject"]


def agent_letter(draft: str | None, **answer) -> tuple[dict, list[str]]:
    """The hero investigated by the agent, which sends `draft` with its answer."""
    with make_client(InvestigatingAI()) as client:
        run_id = start(client)
        hero(client, run_id)
        response = post(client, run_id, "complete", ANSWER | {"letter_draft": draft} | answer)
        assert response.status_code == 204, response.text
        result = finding(client, run_id)
        return letter(client, result["id"]), labels(client, run_id)


def rejected(steps: list[str]) -> list[str]:
    return [s for s in steps if s.startswith("The agent's letter is not used")]


def test_with_the_ai_down_the_letter_comes_from_the_template() -> None:
    with make_client(InvestigatingAI(unreachable=True)) as client:
        run_id = start(client)
        result = finding(client, run_id)
        subject = letter(client, result["id"])

    assert result["investigation"]["mode"] == "FALLBACK"
    assert subject["letter_subject"] == SUBJECT
    assert (subject["language"], subject["draft_source"]) == ("de", "TEMPLATE")
    assert subject["draft"] == TEMPLATE


def test_the_template_never_refuses_to_pay() -> None:
    with client_for(base) as client:
        draft = letter(client, upload_rent(client)["id"])["draft"].lower()

    assert not any(word in draft for word in ("verweiger", "zurückbehalt", "einbehalt", "klage"))
    assert "nicht zahlen" not in draft
    assert euro_amounts(draft) == [380000, 410400, 380000]


def test_the_agents_checked_draft_is_used() -> None:
    subject, steps = agent_letter(DRAFT)

    assert (subject["draft"], subject["draft_source"]) == (DRAFT, "MODEL")
    assert subject["letter_subject"] == SUBJECT
    assert rejected(steps) == []


def test_without_a_draft_the_agent_run_uses_the_template() -> None:
    subject, steps = agent_letter(None)

    assert (subject["draft"], subject["draft_source"]) == (TEMPLATE, "TEMPLATE")
    assert rejected(steps) == []


@pytest.mark.parametrize(
    ("draft", "reason"),
    [
        (
            DRAFT.replace("3.800,00 €", "3.993,88 €"),
            "it quotes 3.993,88 €, not an amount Vero computed",
        ),
        (
            DRAFT.replace("die Miete bleibt daher bei 3.800,00 €", "die Miete bleibt gleich"),
            "it does not state the amount the contract allows (3.800,00 €)",
        ),
        (DRAFT.replace("§4.2", "dem Vertrag"), "it does not cite §4.2"),
        (
            DRAFT + "\nBis dahin werden wir die Miete einbehalten.",
            "it reads as a refusal or a threat (“einbehalt”)",
        ),
    ],
)
def test_a_draft_that_fails_a_check_is_replaced_by_the_template(draft: str, reason: str) -> None:
    subject, steps = agent_letter(draft)

    assert (subject["draft"], subject["draft_source"]) == (TEMPLATE, "TEMPLATE")
    assert rejected(steps) == [f"The agent's letter is not used: {reason}"]


def test_a_draft_for_a_label_the_backend_overruled_is_not_used() -> None:
    subject, steps = agent_letter(DRAFT, classification="VERIFIED")

    assert subject["draft_source"] == "TEMPLATE"
    assert rejected(steps) == [
        "The agent's letter is not used: the agent did not find this conflict"
    ]


def test_above_the_threshold_the_letter_states_the_indexed_rent(tmp_path: Path) -> None:
    with client_for(vpi_above_threshold(tmp_path)) as client:
        draft = letter(client, upload_rent(client)["id"])["draft"]

    assert (
        "Nach unseren Unterlagen lag der VPI im Januar 2024 bei 117,6 und im März 2025 bei "
        "123,6; das entspricht einer Veränderung von 5,10 %. Daraus ergibt sich nach unserem "
        "Verständnis 3.993,88 €." in draft
    )
    assert euro_amounts(draft) == [380000, 410400, 399388]


@pytest.mark.parametrize(
    ("text", "amounts"),
    [
        ("3.800,00 €", [380000]),
        ("EUR 3800", [380000]),
        ("3 800 € und 24,50 EUR", [380000, 2450]),
        ("Euro 1.234.567,8", [123456780]),
        ("ab 01.06.2025 3.800,- €", [380000]),
        ("am 01.06.2025, 5 % mehr", []),
    ],
)
def test_euro_amounts_in_a_letter(text: str, amounts: list[int]) -> None:
    assert euro_amounts(text) == amounts
