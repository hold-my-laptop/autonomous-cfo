import copy
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.importer import StatementImport
from api.domain.proof.dataset import (
    LineCheck,
    ProofDataset,
    ProofDatasetError,
    invoice_line_checks,
    load_dataset,
    parse_dataset,
    validate_dataset,
    vat_per_rate,
)
from api.main import create_app

settings = get_settings()
DEMO_AS_OF = settings.demo_as_of_date
RAW: dict[str, Any] = json.loads(settings.proof_dataset_path.read_text(encoding="utf-8"))

INVOICE_TOTALS = {
    "inv_FLV-16261": ("FLV-16261", "2025-03-07", 103721),
    "inv_FLV-94818": ("FLV-94818", "2025-04-28", 89708),
    "inv_AS-80497": ("AS-80497", "2025-03-08", 58474),
    "inv_AS-72217": ("AS-72217", "2025-04-26", 68723),
    "inv_AS-11607": ("AS-11607", "2025-05-03", 62403),
}


@pytest.fixture(scope="module")
def dataset(sample_statement: StatementImport) -> ProofDataset:
    return load_dataset(settings.proof_dataset_path, sample_statement, DEMO_AS_OF)


def changed(edit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    data = copy.deepcopy(RAW)
    edit(data)
    return data


def document(data: dict[str, Any], document_id: str) -> dict[str, Any]:
    return next(d for d in data["documents"] if d["id"] == document_id)


def problems(data: dict[str, Any], statement: StatementImport) -> str:
    with pytest.raises(ProofDatasetError) as error:
        validate_dataset(parse_dataset(data), statement, DEMO_AS_OF)
    return str(error.value)


def test_every_invoice_equals_its_statement_payment(
    dataset: ProofDataset, sample_statement: StatementImport
) -> None:
    invoices = {d.id: d for d in dataset.documents if d.type == "INVOICE"}
    assert set(invoices) == set(INVOICE_TOTALS)
    for document_id, (number, day, gross) in INVOICE_TOTALS.items():
        invoice = invoices[document_id]
        assert invoice.totals is not None
        assert (invoice.document_number, invoice.date.isoformat(), invoice.totals.gross) == (
            number,
            day,
            gross,
        )
        counterparty = dataset.counterparty(invoice.counterparty_id)
        assert counterparty is not None
        payments = [
            t
            for t in sample_statement.transactions
            if number in t.reference and t.booking_date == invoice.date
        ]
        assert [(t.counterparty, t.amount) for t in payments] == [(counterparty.name, -gross)]


def test_invoice_totals_split_per_vat_rate_as_in_proof(dataset: ProofDataset) -> None:
    expected = {
        "inv_FLV-16261": {700: (3000, 210), 1900: (84463, 16048)},
        "inv_FLV-94818": {1900: (75385, 14323)},
        "inv_AS-80497": {700: (54649, 3825)},
        "inv_AS-72217": {700: (64227, 4496)},
        "inv_AS-11607": {700: (58321, 4082)},
    }
    for document_id, rates in expected.items():
        invoice = dataset.document(document_id)
        assert invoice is not None
        net_by_rate = {
            rate: sum(line.net for line in invoice.lines if line.vat_rate == rate) for rate in rates
        }
        vat = vat_per_rate(invoice.lines)
        assert {rate: (net_by_rate[rate], vat[rate]) for rate in vat} == rates


def test_asimex_surcharge_invoice_is_goods_below_the_threshold_plus_surcharge(
    dataset: ProofDataset,
) -> None:
    invoice = dataset.document("inv_AS-11607")
    terms = dataset.document("terms_asimex_2025")
    assert invoice is not None and terms is not None
    surcharge = [line for line in invoice.lines if line.description == "Logistikzuschlag"]
    goods = sum(line.net for line in invoice.lines if line not in surcharge)
    clause = terms.clause("s5")
    assert clause is not None
    assert clause.parameters == {
        "surcharge": "2450",
        "below_net_goods": "60000",
        "valid_from": "2025-05-01",
    }
    assert [line.net for line in surcharge] == [2450]
    assert goods == 55871 < int(clause.parameters["below_net_goods"])


def test_lease_clause_and_rent_adjustment_carry_their_parameters(dataset: ProofDataset) -> None:
    lease = dataset.document("lease_main_street_2021")
    adjustment = dataset.document("doc_rent_adjustment_2025_06")
    assert lease is not None and adjustment is not None
    clause = lease.clause("s4_2")
    assert clause is not None and clause.section == "§4.2"
    assert clause.parameters == {
        "index": "DESTATIS_VPI",
        "base_period": "2024-01",
        "threshold_bps": 500,
        "last_adjustment": "2024-02-01",
        "min_months_between": 12,
        "written_request": True,
    }
    assert adjustment.rent_adjustment is not None
    assert adjustment.lines == ()
    assert adjustment.preloaded is False
    assert adjustment.rent_adjustment.model_dump(mode="json") == {
        "old_amount": "380000",
        "new_amount": "410400",
        "effective_from": "2025-06-01",
        "frequency": "MONTHLY",
    }


def test_reference_values_are_demo_values(dataset: ProofDataset) -> None:
    series = {s.id: s for s in dataset.reference_values}
    assert all(s.demo for s in series.values())
    assert {v.period: v.value for v in series["ref_vpi_demo"].values} == {
        "2024-01": Decimal(1176),
        "2025-03": Decimal(1212),
    }
    assert [str(v.value) for v in series["ref_eurchf_demo"].values] == [
        "1.042",
        "1.045",
        "1.038",
        "1.040",
    ]


def test_landlord_letter_is_an_attachment_in_the_grease_trap_thread(
    dataset: ProofDataset,
) -> None:
    assert 18 <= len(dataset.mailbox) <= 25
    carrying = [e for e in dataset.mailbox if "ntc_main_street_2025_04" in e.attachments]
    assert [e.thread_id for e in carrying] == ["thr_grease_trap"]
    assert "Fettabscheider" in carrying[0].subject
    announcement = [e for e in dataset.mailbox if e.id == "mail_asimex_2025_04_14"]
    assert announcement[0].attachments == ("terms_asimex_2025",)


def test_check_in_date_order_finds_only_the_intended_differences(dataset: ProofDataset) -> None:
    checks = invoice_line_checks(dataset)
    by_document: dict[str, list[tuple[str, LineCheck]]] = {}
    for check in checks:
        by_document.setdefault(check.document_id, []).append((check.description, check.result))
    assert {c.document_id: c.previous_document_id for c in checks} == {
        "inv_FLV-94818": "inv_FLV-16261",
        "inv_AS-72217": "inv_AS-80497",
        "inv_AS-11607": "inv_AS-72217",
    }
    assert {r for d in ("inv_FLV-94818", "inv_AS-72217") for _, r in by_document[d]} == {
        LineCheck.PRICE_LIST
    }
    assert by_document["inv_AS-11607"] == [("Logistikzuschlag", LineCheck.UNEXPLAINED)]


def test_dataset_without_the_asimex_announcement_is_still_valid(
    sample_statement: StatementImport,
) -> None:
    data = changed(
        lambda d: d.update(mailbox=[e for e in d["mailbox"] if e["id"] != "mail_asimex_2025_04_14"])
    )
    validate_dataset(parse_dataset(data), sample_statement, DEMO_AS_OF)


def set_gross(data: dict[str, Any]) -> None:
    document(data, "inv_FLV-94818")["totals"]["gross"] = "89709"


def set_net(data: dict[str, Any]) -> None:
    document(data, "inv_AS-72217")["lines"][0]["net"] = "18000"


def set_vat(data: dict[str, Any]) -> None:
    totals = document(data, "inv_FLV-16261")["totals"]
    totals["vat"], totals["gross"] = "16260", "103723"


def set_number(data: dict[str, Any]) -> None:
    document(data, "inv_AS-80497")["document_number"] = "AS-80498"


def set_late(data: dict[str, Any]) -> None:
    document(data, "pl_fattoria_2025")["date"] = "2025-05-05"


def set_attachment(data: dict[str, Any]) -> None:
    data["mailbox"][0]["attachments"] = ["missing_doc"]


def set_price(data: dict[str, Any]) -> None:
    line = document(data, "inv_FLV-94818")["lines"][0]
    line["unit_price"], line["net"] = "9.50", "28500"


def set_rate(data: dict[str, Any]) -> None:
    data["reference_values"][1]["values"][0]["value"] = "1.043"


def set_highlight(data: dict[str, Any]) -> None:
    document(data, "doc_rent_adjustment_2025_06")["highlights"] = [
        {"ref": "rent_adjustment.amount", "page": 1, "text": "4.104,00 €", "box": [1, 1, 2, 2]}
    ]


def set_box(data: dict[str, Any]) -> None:
    document(data, "lease_main_street_2021")["clauses"][0]["box"] = [10, 10, 700, 20]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (set_gross, "expected one statement payment of 89709"),
        (set_gross, "gross 89709 is not net + VAT"),
        (set_net, "line l1: 14 × 12.90 = 18060 cents, not 18000"),
        (set_vat, "VAT per rate gives 16258 cents"),
        (set_number, "for AS-80498, found 0"),
        (set_late, "dated 2025-05-05, not before 2025-05-05"),
        (set_attachment, "attachment 'missing_doc' is not a document"),
        (set_price, "is neither on inv_FLV-16261 nor on a price list"),
        (set_rate, "1.043 does not match the statement rates ['1.042']"),
        (set_highlight, "highlight for unknown 'rent_adjustment.amount'"),
        (set_box, "is not inside the page"),
    ],
)
def test_a_broken_dataset_is_rejected(
    sample_statement: StatementImport, edit: Callable[[dict[str, Any]], None], message: str
) -> None:
    assert message in problems(changed(edit), sample_statement)


def test_amounts_and_quantities_must_be_strings() -> None:
    data = changed(lambda d: document(d, "inv_AS-80497")["lines"][3].update(quantity=2.506))
    with pytest.raises(ProofDatasetError, match="decimal string"):
        parse_dataset(data)


def test_a_broken_total_fails_the_state_load(tmp_path: Path, sample_statement: object) -> None:
    broken_path = tmp_path / "dataset.json"
    broken_path.write_text(json.dumps(changed(set_gross)), encoding="utf-8")
    broken = Settings(**(settings.model_dump() | {"proof_dataset_path": broken_path}))
    with TestClient(create_app(broken)) as client:
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 503
        assert client.get("/api/v1/company").status_code == 503
