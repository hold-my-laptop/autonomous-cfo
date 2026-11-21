import hashlib
import json
from decimal import Decimal
from typing import Any

from api.config import get_settings
from api.domain.proof.dataset import DocumentType, ProofDataset, parse_dataset

FIXTURE = get_settings().proof_dataset_path
PDF_DIR = FIXTURE.parent / "pdf"
DATASET: ProofDataset = parse_dataset(json.loads(FIXTURE.read_text(encoding="utf-8")))
EXPECTED: list[dict[str, Any]] = json.loads((PDF_DIR / "expected.json").read_text(encoding="utf-8"))
BILLING = (DocumentType.INVOICE, DocumentType.RENT_ADJUSTMENT)


def test_every_document_has_its_pdf_with_the_stored_hash() -> None:
    files = {p.name for p in PDF_DIR.glob("*.pdf")}
    assert files == {f"{d.id}.pdf" for d in DATASET.documents}
    for document in DATASET.documents:
        data = (PDF_DIR / f"{document.id}.pdf").read_bytes()
        assert data.startswith(b"%PDF-")
        assert document.sha256 == hashlib.sha256(data).hexdigest()


def test_every_block_clause_and_value_has_a_box() -> None:
    for document in DATASET.documents:
        assert all(b.box for p in document.pages for b in p.blocks), document.id
        assert all(c.box for c in document.clauses), document.id
        refs = {h.ref for h in document.highlights}
        assert {"counterparty", "date"} <= refs, document.id
        for line in document.lines:
            fields = ("description", "quantity", "unit", "unit_price", "vat_rate", "net")
            assert {f"lines.{line.id}.{f}" for f in fields} <= refs, document.id
        if document.totals:
            assert {"totals.net", "totals.vat", "totals.gross"} <= refs, document.id
        if document.price_list:
            assert {f"price_list.{i.id}" for i in document.price_list.items} <= refs


def test_rent_adjustment_prints_the_new_rent_where_it_is_highlighted() -> None:
    document = DATASET.document("doc_rent_adjustment_2025_06")
    assert document is not None
    highlights = {h.ref: h for h in document.highlights}
    new_amount = highlights["rent_adjustment.new_amount"]
    assert (new_amount.page, new_amount.text) == (1, "4.104,00 €")
    assert highlights["rent_adjustment.old_amount"].text == "3.800,00 €"
    assert highlights["rent_adjustment.effective_from"].text == "01.06.2025"
    assert highlights["rent_adjustment.frequency"].text == "monatlich"
    x0, y0, x1, y1 = new_amount.box
    assert 0 < x0 < x1 < 300 and 0 < y0 < y1 < 421


def test_expected_extraction_lists_every_billing_document_from_the_fixture() -> None:
    by_file = {case["file"]: case for case in EXPECTED}
    billing = [d for d in DATASET.documents if d.type in BILLING]
    assert set(by_file) == {f"{d.id}.pdf" for d in billing}
    assert all((PDF_DIR / name).is_file() for name in by_file)
    for document in billing:
        expected = by_file[f"{document.id}.pdf"]["expected"]
        counterparty = DATASET.counterparty(document.counterparty_id)
        assert counterparty is not None
        assert expected["doc_type"] == document.type
        assert expected["counterparty"] == {
            "value": counterparty.name,
            "matched_id": counterparty.id,
        }
        assert expected["date"]["value"] == document.date.isoformat()
        assert len(expected["lines"]) == len(document.lines)
        for line, case in zip(document.lines, expected["lines"], strict=True):
            assert case["net"]["value"] == str(line.net)
            assert line.quantity == Decimal(case["quantity"]["value"])
            assert line.unit_price == Decimal(case["unit_price"]["value"])
        if document.totals:
            assert expected["totals"]["gross"]["value"] == str(document.totals.gross)


def test_expected_rent_adjustment_values() -> None:
    case = next(c for c in EXPECTED if c["file"] == "doc_rent_adjustment_2025_06.pdf")
    expected = case["expected"]
    assert {k: expected[k]["value"] for k in ("old_amount", "new_amount", "effective_from")} == {
        "old_amount": "380000",
        "new_amount": "410400",
        "effective_from": "2025-06-01",
    }
    assert expected["frequency"]["value"] == "MONTHLY"
    assert expected["lines"] == []
