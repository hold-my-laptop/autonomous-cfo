"""Render the Vero Proof demo documents (src/api/fixtures/proof/dataset.json) as PDFs.

    cd services/api
    uv run --no-project --with reportlab==5.1.0 --with pypdfium2==5.15.2 \\
        python scripts/make_proof_pdfs.py [--check]

Writes fixtures/proof/pdf/<doc_id>.pdf with a real text layer and the Vera fonts embedded, then
writes back into the fixture the box of every block, clause and printed value (PDF points,
origin top-left) and each PDF's SHA-256. Every box is measured with pypdfium2 inside the area
where this script drew the text, the way the AI service measures extracted values. It also
writes pdf/expected.json for the AI team's live extraction test (EXTRACT_FIXTURES=<pdf dir>),
for every billing document (the documents an upload checks). Re-running it changes no file;
--check only reports what would change and verifies every stored value box against a plain text
search of the PDF.
"""

import argparse
import hashlib
import io
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FIXTURE = Path(__file__).resolve().parents[1] / "src" / "api" / "fixtures" / "proof"
DATASET = FIXTURE / "dataset.json"
PDF_DIR = FIXTURE / "pdf"
EXPECTED = PDF_DIR / "expected.json"
BILLING_TYPES = ("INVOICE", "RENT_ADJUSTMENT")
BOX_TOLERANCE = 2.0

REGULAR, BOLD = "Vera", "VeraBd"
_FONTS = Path(reportlab.__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont(REGULAR, str(_FONTS / "Vera.ttf")))
pdfmetrics.registerFont(TTFont(BOLD, str(_FONTS / "VeraBd.ttf")))

PAGE_W, PAGE_H = A4
LEFT, RIGHT = 56.0, PAGE_W - 56.0
BOTTOM = 60.0
LABEL_X, VALUE_X = 360.0, 460.0
AFTER_BODY = ("delivery", "payment_terms", "instruction", "settlement", "closing", "signatures")


def german_money(cents: int) -> str:
    whole, fraction = divmod(abs(cents), 100)
    sign = "-" if cents < 0 else ""
    return f"{sign}{whole:,}".replace(",", ".") + f",{fraction:02d} €"


def german_decimal(value: str, min_places: int = 0) -> str:
    number = Decimal(value)
    if -number.as_tuple().exponent < min_places:
        number = number.quantize(Decimal(1).scaleb(-min_places))
    return format(number, "f").replace(".", ",")


def german_date(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def german_rate(bps: int) -> str:
    return f"{german_decimal(str(Decimal(bps) / 100))} %"


def contract_decimal(value: str) -> str:
    """The AI service's Decimal format: no trailing zeros ("8.90" -> "8.9")."""
    text = format(Decimal(value).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class Run:
    page: int
    x: float
    y: float
    text: str
    font: str
    size: float

    def span_x(self, start: int, end: int) -> tuple[float, float]:
        before = pdfmetrics.stringWidth(self.text[:start], self.font, self.size)
        through = pdfmetrics.stringWidth(self.text[:end], self.font, self.size)
        return self.x + before, self.x + through


@dataclass
class Rendered:
    pdf: bytes
    areas: dict[str, list[Run]] = field(default_factory=dict)
    values: dict[str, tuple[Run, int, int]] = field(default_factory=dict)


class Layout:
    """Draws one document top-down and remembers where every block, clause and value went."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.buffer = io.BytesIO()
        self.canvas = canvas.Canvas(self.buffer, pagesize=A4, invariant=1)
        self.canvas.setTitle(f"{document['title']} ({document['id']})")
        self.canvas.setAuthor("Vero demo fixture (synthetic)")
        self.canvas.setCreator("services/api/scripts/make_proof_pdfs.py")
        self.page = 1
        self.y = PAGE_H - 50
        self.out = Rendered(pdf=b"")

    def draw(
        self, x: float, text: str, font: str = REGULAR, size: float = 9, *, right: bool = False
    ) -> Run:
        width = pdfmetrics.stringWidth(text, font, size)
        if right:
            x -= width
        if x < LEFT - 30 or x + width > RIGHT + 0.01:
            raise SystemExit(f"{self.document['id']}: {text!r} does not fit the page width")
        if self.y < BOTTOM:
            raise SystemExit(
                f"{self.document['id']} page {self.page}: content does not fit the page"
            )
        self.canvas.setFont(font, size)
        self.canvas.drawString(x, self.y, text)
        return Run(self.page, x, self.y, text, font, size)

    def down(self, points: float) -> None:
        self.y -= points

    def paragraph(
        self, text: str, font: str = REGULAR, size: float = 9, x: float = LEFT
    ) -> list[Run]:
        runs = []
        for part in text.split("\n"):
            for line in simpleSplit(part, font, size, RIGHT - x):
                runs.append(self.draw(x, line, font, size))
                self.down(size * 1.4)
        return runs

    def value(self, ref: str, run: Run, printed: str | None = None) -> None:
        printed = run.text if printed is None else printed
        start = run.text.find(printed)
        if start < 0:
            raise SystemExit(f"{self.document['id']}: {printed!r} is not in {run.text!r}")
        self.out.values[ref] = (run, start, start + len(printed))

    def value_in(self, ref: str, runs: list[Run], printed: str) -> bool:
        for run in runs:
            if printed in run.text:
                self.value(ref, run, printed)
                return True
        return False

    def area(self, key: str, runs: list[Run]) -> None:
        self.out.areas[key] = runs

    def block(self, block: dict[str, Any], font: str = REGULAR, size: float = 9) -> list[Run]:
        runs = self.paragraph(block["text"], font, size)
        self.area(f"blocks.{block['id']}", runs)
        return runs

    def next_page(self) -> None:
        self.canvas.showPage()
        self.page += 1
        self.y = PAGE_H - 60

    def finish(self) -> Rendered:
        self.canvas.save()
        self.out.pdf = self.buffer.getvalue()
        return self.out


def _header(layout: Layout, dataset: dict[str, Any], blocks: dict[str, dict[str, Any]]) -> None:
    document = layout.document
    counterparty = next(
        c for c in dataset["counterparties"] if c["id"] == document["counterparty_id"]
    )
    layout.value("counterparty", layout.draw(LEFT, counterparty["name"], BOLD, 13))
    layout.down(14)
    if "letterhead" in blocks:
        layout.block(blocks["letterhead"], size=7.5)
    layout.down(16)
    if "recipient" in blocks:
        layout.block(blocks["recipient"])
        layout.down(8)

    rows: list[tuple[str, str, str]] = []
    printed_date = german_date(document["date"])
    if document["type"] == "INVOICE":
        rows = [
            ("Rechnung Nr.", document["document_number"], "document_number"),
            ("Rechnungsdatum", printed_date, "date"),
        ]
    elif not any(printed_date in b["text"] for p in document["pages"] for b in p["blocks"]):
        rows = [("Datum", printed_date, "date")]
    for label, text, ref in rows:
        layout.draw(LABEL_X, label)
        layout.value(ref, layout.draw(VALUE_X, text))
        layout.down(13)
    layout.down(14)
    layout.paragraph(document["title"], BOLD, 14)
    layout.down(8)


def _invoice_body(layout: Layout) -> None:
    document = layout.document
    size = 8
    columns = (
        ("Pos", 70, True),
        ("Beschreibung", 78, False),
        ("Menge", 350, True),
        ("Einheit", 356, False),
        ("Einzelpreis", 440, True),
        ("USt", 478, True),
        ("Netto", RIGHT, True),
    )
    for label, x, right in columns:
        layout.draw(x, label, BOLD, size, right=right)
    layout.down(15)
    for number, line in enumerate(document["lines"], start=1):
        cells = (
            (str(number), None),
            (line["description"], "description"),
            (german_decimal(line["quantity"]), "quantity"),
            (line["unit"], "unit"),
            (german_decimal(line["unit_price"], 2) + " €", "unit_price"),
            (german_rate(line["vat_rate"]), "vat_rate"),
            (german_money(int(line["net"])), "net"),
        )
        for (text, key), (_, x, right) in zip(cells, columns, strict=True):
            run = layout.draw(x, text, REGULAR, size, right=right)
            if key:
                layout.value(f"lines.{line['id']}.{key}", run)
        layout.down(13)
    layout.down(10)

    totals = document["totals"]
    by_rate: dict[int, int] = {}
    for line in document["lines"]:
        by_rate[line["vat_rate"]] = by_rate.get(line["vat_rate"], 0) + int(line["net"])
    rows: list[tuple[str, str, str | None, str]] = [
        ("Nettobetrag", german_money(int(totals["net"])), "totals.net", REGULAR)
    ]
    if len(by_rate) == 1:
        rows.append(
            (
                f"zzgl. USt {german_rate(next(iter(by_rate)))}",
                german_money(int(totals["vat"])),
                "totals.vat",
                REGULAR,
            )
        )
    else:
        for rate in sorted(by_rate):
            vat = (Decimal(by_rate[rate]) * rate / 10_000).quantize(Decimal(1), "ROUND_HALF_UP")
            rows.append(
                (
                    f"zzgl. USt {german_rate(rate)} auf {german_money(by_rate[rate])}",
                    german_money(int(vat)),
                    None,
                    REGULAR,
                )
            )
        rows.append(
            ("Umsatzsteuer gesamt", german_money(int(totals["vat"])), "totals.vat", REGULAR)
        )
    rows.append(("Gesamtbetrag", german_money(int(totals["gross"])), "totals.gross", BOLD))
    for label, amount, ref, font in rows:
        layout.draw(LABEL_X - 60, label, font, 9)
        run = layout.draw(RIGHT, amount, font, 9, right=True)
        if ref:
            layout.value(ref, run)
        layout.down(14)
    layout.down(12)


def _rent_adjustment_body(layout: Layout) -> None:
    fields = layout.document["rent_adjustment"]
    rows = (
        ("Miete bisher:", german_money(int(fields["old_amount"])), "old_amount"),
        ("Miete neu:", german_money(int(fields["new_amount"])), "new_amount"),
        ("Zahlungsweise:", {"MONTHLY": "monatlich"}[fields["frequency"]], "frequency"),
        ("Gültig ab:", german_date(fields["effective_from"]), "effective_from"),
    )
    layout.down(4)
    for label, text, key in rows:
        layout.draw(LEFT + 20, label, BOLD if key == "new_amount" else REGULAR, 10)
        run = layout.draw(LEFT + 140, text, BOLD if key == "new_amount" else REGULAR, 10)
        layout.value(f"rent_adjustment.{key}", run)
        layout.down(16)
    layout.down(10)


def _price_list_body(layout: Layout) -> None:
    size = 8.5
    columns = (
        ("Artikel", LEFT, False),
        ("Einheit", 340, False),
        ("Preis netto", 460, True),
        ("USt", RIGHT, True),
    )
    for label, x, right in columns:
        layout.draw(x, label, BOLD, size, right=right)
    layout.down(15)
    for item in layout.document["price_list"]["items"]:
        cells = (
            item["description"],
            item["unit"],
            german_decimal(item["unit_price"], 2) + " €",
            german_rate(item["vat_rate"]),
        )
        for index, (text, (_, x, right)) in enumerate(zip(cells, columns, strict=True)):
            run = layout.draw(x, text, REGULAR, size, right=right)
            if index == 2:
                layout.value(f"price_list.{item['id']}", run)
        layout.down(13)
    layout.down(12)


def _clause(layout: Layout, clause: dict[str, Any]) -> None:
    runs = layout.paragraph(f"{clause['section']} {clause['title']}", BOLD, 9.5)
    runs += layout.paragraph(clause["text"])
    layout.area(f"clauses.{clause['id']}", runs)
    layout.down(8)


def render(document: dict[str, Any], dataset: dict[str, Any]) -> Rendered:
    layout = Layout(document)
    for page in document["pages"]:
        if page["page"] > 1:
            layout.next_page()
        blocks = {b["id"]: b for b in page["blocks"]}
        if page["page"] == 1:
            _header(layout, dataset, blocks)
        before = [
            b for b in page["blocks"] if b["id"] not in ("letterhead", "recipient", *AFTER_BODY)
        ]
        for block in before:
            layout.block(block, BOLD if block["id"] in ("subject", "increase") else REGULAR)
            layout.down(6)
        if page["page"] == 1:
            if document["lines"]:
                _invoice_body(layout)
            if document["rent_adjustment"]:
                _rent_adjustment_body(layout)
            if document["price_list"]:
                _price_list_body(layout)
        for clause in document["clauses"]:
            if clause["page"] == page["page"]:
                _clause(layout, clause)
        for block_id in AFTER_BODY:
            if block_id in blocks:
                layout.block(blocks[block_id])
                layout.down(6)
    if "date" not in layout.out.values:
        printed = german_date(document["date"])
        if not any(layout.value_in("date", runs, printed) for runs in layout.out.areas.values()):
            raise SystemExit(f"{document['id']}: the document date is not printed")
    return layout.finish()


def _page_chars(pdf: pdfium.PdfDocument) -> tuple[list[float], list[list[tuple[str, tuple]]]]:
    heights, pages = [], []
    for index in range(len(pdf)):
        page = pdf[index]
        heights.append(round(page.get_size()[1], 2))
        text_page = page.get_textpage()
        pages.append(
            [
                (text_page.get_text_range(i, 1), text_page.get_charbox(i))
                for i in range(text_page.count_chars())
            ]
        )
        text_page.close()
        page.close()
    return heights, pages


def _ink(char: str, box: tuple) -> bool:
    return not char.isspace() and box[2] > box[0] and box[3] > box[1]


def _union(boxes: list[tuple], height: float) -> list[float]:
    x0 = min(b[0] for b in boxes)
    x1 = max(b[2] for b in boxes)
    bottom = min(b[1] for b in boxes)
    top = max(b[3] for b in boxes)
    return [round(x0, 2), round(height - top, 2), round(x1, 2), round(height - bottom, 2)]


def _measure_run(chars: list[tuple[str, tuple]], run: Run, start: int, end: int) -> list[tuple]:
    """The glyph boxes pdfium reports where `run.text[start:end]` was drawn."""
    x0, x1 = run.span_x(start, end)
    picked = [
        (char, box)
        for char, box in chars
        if _ink(char, box)
        and x0 - 0.5 <= (box[0] + box[2]) / 2 <= x1 + 0.5
        and run.y - 0.4 * run.size <= (box[1] + box[3]) / 2 <= run.y + run.size
    ]
    expected = "".join(run.text[start:end].split())
    found = "".join(char for char, _ in picked)
    if found != expected:
        raise SystemExit(f"measured {found!r} where {expected!r} was drawn")
    return [box for _, box in picked]


def measure(rendered: Rendered) -> tuple[dict[str, list[float]], dict[str, dict[str, Any]]]:
    pdf = pdfium.PdfDocument(rendered.pdf)
    try:
        heights, chars = _page_chars(pdf)
    finally:
        pdf.close()
    areas = {}
    for key, runs in rendered.areas.items():
        boxes = [
            b for run in runs for b in _measure_run(chars[run.page - 1], run, 0, len(run.text))
        ]
        areas[key] = _union(boxes, heights[runs[0].page - 1])
    values = {}
    for ref, (run, start, end) in rendered.values.items():
        box = _union(_measure_run(chars[run.page - 1], run, start, end), heights[run.page - 1])
        values[ref] = {"ref": ref, "page": run.page, "text": run.text[start:end], "box": box}
    return areas, values


def _text_lines(pdf_bytes: bytes) -> list[tuple[int, float, str, list[tuple]]]:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        heights, pages = _page_chars(pdf)
    finally:
        pdf.close()
    lines = []
    for number, chars in enumerate(pages, start=1):
        text, boxes = [], []
        for char, box in [*chars, ("\n", (0, 0, 0, 0))]:
            if char == "\n":
                lines.append((number, heights[number - 1], "".join(text), boxes))
                text, boxes = [], []
            elif char != "\r":
                text.append(char)
                boxes.append(box)
    return lines


def verify_by_search(pdf_bytes: bytes, highlights: list[dict[str, Any]]) -> list[str]:
    """Every stored value box must match an occurrence of its text found by plain search."""
    lines = _text_lines(pdf_bytes)
    problems = []
    for highlight in highlights:
        candidates = []
        for page, height, text, boxes in lines:
            if page != highlight["page"]:
                continue
            start = text.find(highlight["text"])
            while start >= 0:
                span = [
                    b
                    for c, b in zip(
                        text[start : start + len(highlight["text"])],
                        boxes[start : start + len(highlight["text"])],
                        strict=True,
                    )
                    if _ink(c, b)
                ]
                candidates.append(_union(span, height))
                start = text.find(highlight["text"], start + 1)
        if not any(
            all(abs(a - b) <= BOX_TOLERANCE for a, b in zip(c, highlight["box"], strict=True))
            for c in candidates
        ):
            problems.append(
                f"{highlight['ref']} {highlight['text']!r}: no printed match near "
                f"{highlight['box']} (found {candidates})"
            )
    return problems


def expected_case(
    document: dict[str, Any], dataset: dict[str, Any], values: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    def sourced(ref: str, value: Any) -> dict[str, Any]:
        found = values[ref]
        return {"value": value, "text": found["text"], "page": found["page"], "box": found["box"]}

    counterparty = next(
        c for c in dataset["counterparties"] if c["id"] == document["counterparty_id"]
    )
    expected: dict[str, Any] = {
        "doc_type": document["type"],
        "counterparty": {"value": counterparty["name"], "matched_id": counterparty["id"]},
        "date": sourced("date", document["date"]),
        "currency": {"value": document["currency"]},
    }
    if document["document_number"]:
        expected["document_number"] = sourced("document_number", document["document_number"])
    if document["rent_adjustment"]:
        fields = document["rent_adjustment"]
        expected |= {
            "effective_from": sourced("rent_adjustment.effective_from", fields["effective_from"]),
            "old_amount": sourced("rent_adjustment.old_amount", fields["old_amount"]),
            "new_amount": sourced("rent_adjustment.new_amount", fields["new_amount"]),
            "frequency": sourced("rent_adjustment.frequency", fields["frequency"]),
        }
    expected["lines"] = [
        {
            "description": sourced(f"lines.{line['id']}.description", line["description"]),
            "quantity": sourced(f"lines.{line['id']}.quantity", contract_decimal(line["quantity"])),
            "unit": sourced(f"lines.{line['id']}.unit", line["unit"]),
            "unit_price": sourced(
                f"lines.{line['id']}.unit_price", contract_decimal(line["unit_price"])
            ),
            "vat_rate": sourced(f"lines.{line['id']}.vat_rate", line["vat_rate"]),
            "net": sourced(f"lines.{line['id']}.net", line["net"]),
        }
        for line in document["lines"]
    ]
    if document["totals"]:
        expected["totals"] = {k: sourced(f"totals.{k}", v) for k, v in document["totals"].items()}
    return {
        "file": f"{document['id']}.pdf",
        "known_counterparties": dataset["counterparties"],
        "expected": expected,
    }


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def build(dataset: dict[str, Any]) -> tuple[dict[str, bytes], str, str, list[str]]:
    """All output files, the updated fixture and expected.json, and the search-check problems."""
    pdfs: dict[str, bytes] = {}
    cases = []
    problems = []
    for document in dataset["documents"]:
        rendered = render(document, dataset)
        areas, values = measure(rendered)
        pdfs[f"{document['id']}.pdf"] = rendered.pdf
        document["sha256"] = hashlib.sha256(rendered.pdf).hexdigest()
        for page in document["pages"]:
            for block in page["blocks"]:
                block["box"] = areas[f"blocks.{block['id']}"]
        for clause in document["clauses"]:
            clause["box"] = areas[f"clauses.{clause['id']}"]
        document["highlights"] = list(values.values())
        problems += [
            f"{document['id']}: {p}" for p in verify_by_search(rendered.pdf, document["highlights"])
        ]
        if document["type"] in BILLING_TYPES:
            cases.append(expected_case(document, dataset, values))
    return pdfs, dump(dataset), dump(cases), problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report changes, write nothing")
    check = parser.parse_args().check

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    pdfs, fixture_text, expected_text, problems = build(dataset)
    outputs = {PDF_DIR / name: data for name, data in pdfs.items()}
    outputs[DATASET] = fixture_text.encode("utf-8")
    outputs[EXPECTED] = expected_text.encode("utf-8")
    stale = sorted(p for p in PDF_DIR.glob("*.pdf") if p not in outputs)
    changed = [p for p, data in outputs.items() if not p.is_file() or p.read_bytes() != data]

    for problem in problems:
        print("box check failed:", problem, file=sys.stderr)
    for path in [*changed, *stale]:
        print(("would change " if check else "wrote ") + os.path.relpath(path))
    if not check:
        PDF_DIR.mkdir(exist_ok=True)
        for path in changed:
            path.write_bytes(outputs[path])
        for path in stale:
            path.unlink()
        print(f"{len(pdfs)} PDFs, {len(changed) + len(stale)} files changed")
    return 1 if problems or (check and (changed or stale)) else 0


if __name__ == "__main__":
    sys.exit(main())
