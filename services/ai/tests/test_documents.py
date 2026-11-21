"""Document reading: PDF text layer, OCR for scans and images, mixed PDFs, limits, endpoint."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai import documents as D
from ai.config import Settings
from ai.llm import StubLLM
from ai.main import create_app

from conftest import FakeBackend

pytest.importorskip("pypdfium2")
pytest.importorskip("rapidocr_onnxruntime")

FIX = Path(__file__).parent / "fixtures"
V6 = (D.OCR_MODEL_DIR / "rec.onnx").is_file()
needs_v6 = pytest.mark.skipif(not V6, reason="PP-OCRv6 models missing: python scripts/fetch_ocr_models.py")


def test_text_pdf_is_read_without_ocr():
    doc = D.read_document((FIX / "lease_text.pdf").read_bytes(), "lease.pdf")
    assert doc.kind == "pdf" and doc.ocr_pages == 0 and doc.pages[0].method == "text"
    assert "EUR 3,500.00" in doc.text and "Main Street Real Estate GmbH" in doc.text
    assert doc.summary()["ocr_model"] is None


@needs_v6
def test_scanned_image_is_read_with_ocr():
    doc = D.read_document((FIX / "lease_scan.png").read_bytes(), "scan.png")
    assert doc.kind == "image" and doc.ocr_pages == 1 and doc.ocr_model == "pp-ocrv6-small"
    for phrase in ("EUR 3,500.00", "Main Street Real Estate GmbH", "Hauptstraße 14", "1 January 2024"):
        assert phrase in doc.text, phrase
    assert "§3Rent" in doc.text.replace(" ", "")  # OCR spacing around § varies
    assert doc.pages[0].confidence > 0.8


@needs_v6
def test_mixed_pdf_uses_text_layer_and_ocr_per_page():
    doc = D.read_document((FIX / "mixed.pdf").read_bytes(), "mixed.pdf")
    assert [p.method for p in doc.pages] == ["text", "ocr"]
    assert "9.75 % per annum" in doc.pages[0].text  # exact from the text layer
    assert "EUR 3,500.00" in doc.pages[1].text      # read from the scanned page


def test_force_ocr_on_text_pdf():
    doc = D.read_document((FIX / "lease_text.pdf").read_bytes(), "lease.pdf", force_ocr=True)
    assert doc.pages[0].method == "ocr" and doc.text


def test_reading_order_groups_boxes_into_lines():
    boxes = [([[200, 10], [260, 10], [260, 30], [200, 30]], "world", 0.9),
             ([[10, 12], [80, 12], [80, 32], [10, 32]], "Hello", 0.9),
             ([[10, 120], [90, 120], [90, 140], [10, 140]], "Next", 0.9)]
    assert D._reading_order(boxes) == "Hello world\n\nNext"


@pytest.mark.parametrize("data,name,msg", [
    (b"", "a.pdf", "empty"),
    (b"PK\x03\x04zip", "a.docx", "Unsupported"),
    (b"%PDF-1.4 broken", "a.pdf", "Cannot open"),
])
def test_bad_uploads_are_rejected(data, name, msg):
    with pytest.raises(D.DocumentError, match=msg):
        D.read_document(data, name)


def test_too_big_is_rejected(monkeypatch):
    monkeypatch.setattr(D, "MAX_BYTES", 10)
    with pytest.raises(D.DocumentError, match="larger"):
        D.read_document(b"x" * 11, "a.txt")


def test_plain_text_upload():
    doc = D.read_document("Miete: 3.500,00 € monatlich".encode(), "c.txt", "text/plain")
    assert doc.kind == "text" and "3.500,00 €" in doc.text


def test_extract_endpoint():
    app = create_app(Settings(llm_mode="stub"), llm=StubLLM(), backend=FakeBackend())
    with TestClient(app) as c:
        r = c.post("/v1/documents/extract",
                   files={"file": ("lease.pdf", (FIX / "lease_text.pdf").read_bytes(), "application/pdf")})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "pdf" and body["ocr_pages"] == 0 and "EUR 3,500.00" in body["text"]
        bad = c.post("/v1/documents/extract", files={"file": ("x.docx", b"PK\x03\x04", "application/zip")})
        assert bad.status_code == 422 and "Unsupported" in bad.json()["detail"]
