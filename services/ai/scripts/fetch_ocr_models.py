"""Fetch the OCR models used by src/ai/documents.py (PP-OCRv6 small, Apache-2.0).

The models come from the `onnxocr` wheel on PyPI (no model hub needed, so it works
wherever pip works: laptop, OpenShift build). Only the text detector, the recogniser
and its character list are kept; the character list is embedded into the recogniser
so RapidOCR can load it like its own models.

    pip install onnx
    python scripts/fetch_ocr_models.py            # writes models/ocr/det.onnx and rec.onnx

Without these files the service still reads PDFs: pages with a text layer are exact,
scans fall back to RapidOCR's bundled models (much weaker on Latin text).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WHEEL = "onnxocr==4.0.0"
PREFIX = "onnxocr/models/ppocrv6/"
DET = PREFIX + "small/det/det.onnx"
REC = PREFIX + "small/rec/rec.onnx"
DICT = PREFIX + "ppocrv6_dict.txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "models" / "ocr"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        import onnx
    except ImportError:
        sys.exit("Needs the onnx package for this step only: pip install onnx")

    with tempfile.TemporaryDirectory() as tmp:
        print(f"Downloading {WHEEL} (models only are kept)...")
        subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--quiet", "-d", tmp, WHEEL],
                       check=True)
        wheel = next(Path(tmp).glob("onnxocr-*.whl"))
        with zipfile.ZipFile(wheel) as z:
            (out / "det.onnx").write_bytes(z.read(DET))
            chars = z.read(DICT).decode("utf-8").splitlines()
            rec = onnx.load_from_string(z.read(REC))

    meta = {p.key: p for p in rec.metadata_props}
    if "character" in meta:
        meta["character"].value = "\n".join(chars)
    else:
        p = rec.metadata_props.add()
        p.key, p.value = "character", "\n".join(chars)
    onnx.save(rec, str(out / "rec.onnx"))
    for f in ("det.onnx", "rec.onnx"):
        print(f"  {out / f}  {(out / f).stat().st_size / 1e6:.1f} MB")
    print(f"OK: {len(chars)} characters (incl. ä ö ü ß € §).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
