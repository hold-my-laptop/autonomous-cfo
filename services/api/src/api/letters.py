"""The clarification letter of a conflict (docs/PROOF.md §6, P16): the agent's draft when it
passes the backend's checks, else a German template from the engine's numbers. Neutral and
evidence-based: it asks for the basis of the change and never refuses to pay."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from api.domain.models import Category
from api.domain.proof.dataset import Clause, Document
from api.domain.proof.findings import Classification, EvidenceKind, ProofFinding
from api.money import Cents
from api.state import DemoState

LetterSource = Literal["MODEL", "TEMPLATE"]

_REFUSALS = (
    "verweiger",
    "zurückbehalt",
    "zurueckbehalt",
    "einbehalt",
    "mietminderung",
    "nicht zahlen",
    "nicht bezahlen",
    "nicht mehr zahlen",
    "nicht überweisen",
    "zahlung einstell",
    "zahlungen einstell",
    "rechtliche schritte",
    "anwalt",
    "klage",
)
_NUMBER = r"(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}|-))?"
_EURO = re.compile(rf"(?:€|\bEUR|\bEuro)\s*{_NUMBER}|{_NUMBER}\s*(?:€|EUR\b|Euro\b)")
_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


@dataclass(frozen=True, slots=True)
class Letter:
    text: str
    source: LetterSource
    rejected: str | None = None


def eur_de(cents: Cents) -> str:
    """380000 -> '3.800,00 €', as a German letter writes it."""
    whole, fraction = divmod(abs(cents), 100)
    return f"{'-' if cents < 0 else ''}{whole:,}".replace(",", ".") + f",{fraction:02d} €"


def _day(d: date) -> str:
    return f"{d:%d.%m.%Y}"


def _month(period: str) -> str:
    year, month = period.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def _index(tenths: int) -> str:
    return f"{tenths // 10},{tenths % 10}"


def _percent(bps: int) -> str:
    return f"{bps // 100} %" if bps % 100 == 0 else f"{bps // 100},{bps % 100:02d} %"


def euro_amounts(text: str) -> list[Cents]:
    """Every euro amount written in the text, in cents ('3.800,00 €', 'EUR 3800', '3 800 €')."""
    amounts = []
    for match in _EURO.finditer(text):
        whole, fraction = match.group(1, 2) if match.group(1) else match.group(3, 4)
        cents = int(fraction.ljust(2, "0")) if fraction and fraction != "-" else 0
        amounts.append(int(re.sub(r"\D", "", whole)) * 100 + cents)
    return amounts


def _document(state: DemoState, document_id: str | None) -> Document | None:
    if not document_id:
        return None
    upload = next((u for u in state.uploads.values() if u.document_id == document_id), None)
    if upload is not None and upload.document is not None:
        return upload.document
    return state.proof.document(document_id) if state.proof else None


def cited_clause(state: DemoState, finding: ProofFinding) -> tuple[Document, Clause] | None:
    """The first contract clause the finding cites."""
    for ref in finding.evidence_refs:
        if ref.kind is EvidenceKind.CLAUSE and ref.document_id and ref.item_id:
            document = _document(state, ref.document_id)
            clause = document.clause(ref.item_id) if document else None
            if clause:
                return document, clause
    return None


def contract_word(document: Document) -> str:
    return "Mietvertrag" if document.type.value == "LEASE" else "Vertrag"


def _allowed_amounts(finding: ProofFinding) -> set[Cents]:
    values = {finding.impact.expected, finding.impact.billed}
    if finding.investigation:
        i = finding.investigation
        values |= {i.billed, i.justified, i.disputed}
    if finding.propagation:
        p = finding.propagation
        values |= {
            p.current_amount,
            p.billed_amount,
            p.proposed_amount,
            p.disputed,
            p.disputed_annual,
            p.disputed_in_window,
        }
    return {v for v in values if v is not None}


def check_draft(state: DemoState, finding: ProofFinding, draft: str) -> str | None:
    """Why the agent's draft cannot be sent as it is, or None when it can."""
    justified = finding.investigation.justified if finding.investigation else None
    amounts = euro_amounts(draft)
    unknown = sorted(set(amounts) - _allowed_amounts(finding))
    lowered = draft.lower()
    clause = cited_clause(state, finding)
    if unknown:
        return f"it quotes {', '.join(eur_de(a) for a in unknown)}, not an amount Vero computed"
    if justified is not None and justified not in amounts:
        return f"it does not state the amount the contract allows ({eur_de(justified)})"
    if clause and clause[1].section.replace(" ", "") not in draft.replace(" ", ""):
        return f"it does not cite {clause[1].section}"
    refusal = next((word for word in _REFUSALS if word in lowered), None)
    if refusal:
        return f"it reads as a refusal or a threat (“{refusal}”)"
    return None


def _change(state: DemoState, finding: ProofFinding, category: Category | None) -> str:
    billed = finding.investigation.billed if finding.investigation else finding.impact.billed
    before = finding.impact.expected
    document = _document(state, finding.document_id)
    start = finding.period_from
    if category is Category.RENT and document and start and before is not None:
        return (
            f"mit Ihrem Schreiben vom {_day(document.date)} kündigen Sie an, die monatliche "
            f"Miete ab dem {_day(start)} von {eur_de(before)} auf {eur_de(billed)} anzupassen."
        )
    if document and document.document_number:
        return (
            f"Ihre Rechnung {document.document_number} vom {_day(document.date)} weist "
            f"{eur_de(billed)} aus."
        )
    since = f" ab dem {_day(start)}" if start else ""
    previous = f" statt bisher {eur_de(before)}" if before is not None else ""
    return f"Sie berechnen{since} {eur_de(billed)}{previous}."


def _basis(state: DemoState, finding: ProofFinding, category: Category | None) -> str:
    i = finding.investigation
    clause = cited_clause(state, finding)
    where = f"{clause[1].section} des {contract_word(clause[0])}s" if clause else None
    what = "die Miete" if category is Category.RENT else "der Betrag"
    if i and i.formula_id == "vpi_threshold_indexation" and clause and i.inputs and i.outputs:
        params = clause[1].parameters
        current = int(i.inputs["current_index"])
        period = _current_period(state, current)
        threshold = int(i.inputs["threshold_bps"])
        text = (
            f"Nach {where} ist eine Anpassung an den Verbraucherpreisindex (VPI) möglich, wenn "
            f"sich der Index gegenüber seinem Stand bei der letzten Anpassung um mindestens "
            f"{_percent(threshold)} verändert hat. Nach unseren Unterlagen lag der VPI im "
            f"{_month(params['base_period'])} bei {_index(int(i.inputs['base_index']))}"
            f"{f' und im {_month(period)}' if period else ', zuletzt'} bei {_index(current)}; "
            f"das entspricht einer Veränderung von {_percent(int(i.outputs['change_bps']))}."
        )
        if i.outputs.get("adjustment_allowed"):
            return f"{text} Daraus ergibt sich nach unserem Verständnis {eur_de(i.justified)}."
        return (
            f"{text} Eine Anpassung nach {clause[1].section} ergibt sich daraus nach unserem "
            f"Verständnis nicht; {what} beträgt weiterhin {eur_de(i.justified)}."
        )
    if where and i and i.justified is not None:
        return f"Nach {where} ergibt sich nach unserem Verständnis {eur_de(i.justified)}."
    if i and i.justified is not None:
        return f"Nach unseren Unterlagen ist {eur_de(i.justified)} vereinbart."
    return "Eine vertragliche Grundlage für diese Änderung konnten wir nicht finden."


def _current_period(state: DemoState, value: int) -> str | None:
    series = next(
        (
            r
            for r in (state.proof.reference_values if state.proof else ())
            if r.name == "DESTATIS_VPI"
        ),
        None,
    )
    if series is None:
        return None
    periods = [
        v.period
        for v in series.values
        if int(v.value) == value and v.period <= f"{state.as_of:%Y-%m}"
    ]
    return max(periods, default=None)


def template_letter(state: DemoState, finding: ProofFinding, category: Category | None) -> str:
    """The backend's German letter: the change, what the contract allows and why, and a
    request for the basis of the change and its calculation."""
    return "\n\n".join(
        (
            "Sehr geehrte Damen und Herren,",
            _change(state, finding, category),
            _basis(state, finding, category),
            "Wir bitten Sie daher um eine kurze Erläuterung, auf welcher vertraglichen "
            "Grundlage die Änderung beruht, und um Ihre Berechnung. Sollten wir einen Umstand "
            "übersehen haben, freuen wir uns über Ihren Hinweis.",
            f"Mit freundlichen Grüßen\n\n{state.company.owner}\n{state.company.name}",
        )
    )


def write_letter(
    state: DemoState,
    finding: ProofFinding,
    category: Category | None,
    draft: str | None,
) -> Letter:
    """The agent's draft when it found the same conflict and the draft passes the checks;
    otherwise the template, with the reason the draft was not used."""
    template = template_letter(state, finding, category)
    if not draft or not draft.strip():
        return Letter(template, "TEMPLATE")
    proposed = finding.investigation.proposed if finding.investigation else None
    if proposed is not Classification.CONFLICT:
        return Letter(template, "TEMPLATE", "the agent did not find this conflict")
    problem = check_draft(state, finding, draft)
    if problem:
        return Letter(template, "TEMPLATE", problem)
    return Letter(draft.strip(), "MODEL")
