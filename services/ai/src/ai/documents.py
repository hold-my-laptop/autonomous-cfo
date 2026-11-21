"""Turn an uploaded document (PDF, image or text) into plain text for the contract check.

PDF pages with a text layer are read directly (pypdfium2). Pages without one (scans,
photos saved as PDF) are rendered to an image and read with OCR: RapidOCR running the
PP-OCRv6 small models from OCR_MODEL_DIR (scripts/fetch_ocr_models.py puts them in
models/ocr; the image build does the same). Without them it falls back to RapidOCR's
bundled models, which are much weaker on Latin text, and says so in the result.
Images (PNG/JPEG) go straight to OCR. Everything runs on CPU inside the ai pod; no
document leaves the cluster.

OCR is loaded lazily on first use and runs in a worker thread, so the event loop (agent
runs, chat) keeps working while a scan is read.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("ai.documents")

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES = 20
MIN_TEXT_CHARS = 40       # a page with less text than this is treated as a scan
OCR_TARGET_WIDTH = 1700   # px; ~200 dpi on A4
OCR_DET_LIMIT = 2000      # longest side the detector sees. Measured on our fixtures: all key
                          # phrases read, peak ~780 MB (1600 misread small amounts; "min/736" ~1 GB)
OCR_MIN_SCORE = 0.5

OCR_MODEL_DIR = Path(os.environ.get("OCR_MODEL_DIR") or Path(__file__).resolve().parents[2] / "models" / "ocr")

Method = Literal["text", "ocr", "empty"]


class DocumentError(ValueError):
    """The upload cannot be read (wrong type, too big, encrypted, no text)."""


@dataclass
class PageText:
    page: int
    method: Method
    text: str
    confidence: float | None = None  # mean OCR score, OCR pages only


@dataclass
class DocumentText:
    kind: Literal["pdf", "image", "text"]
    pages: list[PageText] = field(default_factory=list)
    seconds: float = 0.0
    ocr_model: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text).strip()

    @property
    def ocr_pages(self) -> int:
        return sum(p.method == "ocr" for p in self.pages)

    def summary(self) -> dict[str, Any]:
        return {"kind": self.kind, "pages": len(self.pages), "ocr_pages": self.ocr_pages,
                "ocr_model": self.ocr_model if self.ocr_pages else None,
                "chars": len(self.text), "seconds": round(self.seconds, 2),
                "per_page": [{"page": p.page, "method": p.method, "chars": len(p.text),
                              "confidence": None if p.confidence is None else round(p.confidence, 3)}
                             for p in self.pages]}


# ---------- OCR engine (lazy, one per process) ----------
_engine: Any = None
_engine_name: str | None = None
_engine_lock = threading.Lock()


def _ocr_engine() -> Any:
    global _engine, _engine_name
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            t0 = time.perf_counter()
            det, rec = OCR_MODEL_DIR / "det.onnx", OCR_MODEL_DIR / "rec.onnx"
            limits = {"det_limit_type": "max", "det_limit_side_len": OCR_DET_LIMIT}
            if det.is_file() and rec.is_file():
                _engine = RapidOCR(det_model_path=str(det), rec_model_path=str(rec), **limits)
                _engine_name = "pp-ocrv6-small"
            else:
                log.warning("OCR models not found in %s: using RapidOCR's bundled models (weak on Latin "
                            "text). Run scripts/fetch_ocr_models.py.", OCR_MODEL_DIR)
                _engine, _engine_name = RapidOCR(**limits), "bundled-v3 (low accuracy)"
            log.info("OCR engine %s loaded in %.1fs", _engine_name, time.perf_counter() - t0)
    return _engine


def ocr_model_name() -> str | None:
    return _engine_name


def ocr_image(image: Any) -> tuple[str, float | None]:
    """OCR a PIL image. Returns the text with reading order restored, and the mean score."""
    import numpy as np

    img = image.convert("RGB")
    if img.width > OCR_TARGET_WIDTH * 1.3:  # very large photos: shrink for speed
        ratio = OCR_TARGET_WIDTH / img.width
        img = img.resize((OCR_TARGET_WIDTH, int(img.height * ratio)))
    result, _ = _ocr_engine()(np.asarray(img))
    boxes = [(b, t, s) for b, t, s in (result or []) if t and float(s) >= OCR_MIN_SCORE]
    if not boxes:
        return "", None
    return _reading_order(boxes), sum(float(s) for _, _, s in boxes) / len(boxes)


def _reading_order(boxes: list[tuple[Any, str, Any]]) -> str:
    """Groups OCR boxes into lines (by vertical centre) and sorts each line left to right."""
    items = []
    for box, text, _ in boxes:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((sum(ys) / len(ys), min(xs), max(ys) - min(ys), text.strip()))
    items.sort()
    heights = sorted(h for _, _, h, _ in items)
    tol = max(4.0, heights[len(heights) // 2] * 0.55)
    lines: list[list[tuple[float, float, float, str]]] = []
    for it in items:
        if lines and abs(it[0] - lines[-1][-1][0]) <= tol:
            lines[-1].append(it)
        else:
            lines.append([it])
    out, prev_y, line_gap = [], None, heights[len(heights) // 2] * 1.9
    for line in lines:
        y = sum(i[0] for i in line) / len(line)
        if prev_y is not None and y - prev_y > line_gap:
            out.append("")  # keep paragraph breaks
        out.append(" ".join(t for _, _, _, t in sorted(line, key=lambda i: i[1])))
        prev_y = y
    return "\n".join(out)


# ---------- readers ----------
def _sniff(data: bytes, filename: str, content_type: str) -> Literal["pdf", "image", "text"]:
    name = (filename or "").lower()
    if data[:5] == b"%PDF-" or name.endswith(".pdf"):
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or name.endswith((".png", ".jpg", ".jpeg")):
        return "image"
    if (content_type or "").startswith("text/") or name.endswith((".txt", ".md")):
        return "text"
    raise DocumentError("Unsupported file type. Upload a PDF, PNG, JPEG or TXT file.")


def read_pdf(data: bytes, force_ocr: bool = False) -> list[PageText]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise DocumentError(f"Cannot open the PDF ({e}). Is it encrypted or damaged?") from e
    try:
        if len(pdf) > MAX_PAGES:
            raise DocumentError(f"The PDF has {len(pdf)} pages; at most {MAX_PAGES} are supported.")
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            text = ""
            if not force_ocr:
                tp = page.get_textpage()
                text = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()
                tp.close()
            if len(text) >= MIN_TEXT_CHARS:
                pages.append(PageText(i + 1, "text", text))
            else:
                scale = OCR_TARGET_WIDTH / max(1.0, page.get_width())
                image = page.render(scale=scale).to_pil()
                ocr_text, conf = ocr_image(image)
                pages.append(PageText(i + 1, "ocr" if ocr_text else "empty", ocr_text, conf))
            page.close()
        return pages
    finally:
        pdf.close()


def read_image(data: bytes) -> list[PageText]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except UnidentifiedImageError as e:
        raise DocumentError("Cannot read the image.") from e
    text, conf = ocr_image(image)
    return [PageText(1, "ocr" if text else "empty", text, conf)]


def read_document(data: bytes, filename: str = "", content_type: str = "", force_ocr: bool = False) -> DocumentText:
    """Synchronous: PDF text layer first, OCR for pages without one."""
    if not data:
        raise DocumentError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise DocumentError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    t0 = time.perf_counter()
    kind = _sniff(data, filename, content_type)
    if kind == "pdf":
        pages = read_pdf(data, force_ocr)
    elif kind == "image":
        pages = read_image(data)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        pages = [PageText(1, "text", text.strip())]
    doc = DocumentText(kind, pages, time.perf_counter() - t0, ocr_model_name())
    if not doc.text:
        raise DocumentError("No readable text found in the document.")
    log.info("document read: %s", {k: v for k, v in doc.summary().items() if k != "per_page"})
    return doc


_ocr_slot = asyncio.Semaphore(1)  # OCR is CPU heavy: one document at a time


async def read_document_async(data: bytes, filename: str = "", content_type: str = "",
                              force_ocr: bool = False, timeout_s: float = 90.0) -> DocumentText:
    async with _ocr_slot:
        return await asyncio.wait_for(
            asyncio.to_thread(read_document, data, filename, content_type, force_ocr), timeout=timeout_s)
