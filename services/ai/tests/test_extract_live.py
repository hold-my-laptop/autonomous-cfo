"""Extraction against the REAL model (vLLM on OpenShift AI), compared with expected values.

Skipped unless LLM_BASE_URL is set. Fixtures: a folder with PDFs and expected.json
(default tests/fixtures/extract; set EXTRACT_FIXTURES for the backend's demo PDFs):

    [{"file": "invoice.pdf",
      "known_counterparties": [{"id": "...", "name": "...", "aliases": []}],
      "expected": {
        "doc_type": "INVOICE",
        "counterparty": {"value": "Metzgerei Rossi GmbH", "matched_id": "cp-rossi"},
        "date": {"value": "2025-06-02", "text": "02.06.2025", "page": 1, "box": [x0, y0, x1, y1]},
        "lines": [{"net": {"value": "50000"}, ...}, ...],
        "totals": {"gross": {"value": "93089", "text": "930,89 €"}}}}]

Only what `expected` lists is compared: value always; text, page, matched_id when given;
box within BOX_TOL points (PDF points, top-left origin). `lines` must have the same count.

Run it from a laptop with the model port-forwarded:
    set LLM_BASE_URL=http://localhost:8080/v1
    set LLM_MODEL=vero-llm
    python -m pytest -q tests/test_extract_live.py -s
"""

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest

from ai.config import Settings
from ai.extraction import ExtractRequest, extract
from ai.llm import LiveLLM

BOX_TOL = 2.0
FIX = Path(os.environ.get("EXTRACT_FIXTURES") or Path(__file__).parent / "fixtures" / "extract")
CASES = json.loads((FIX / "expected.json").read_text(encoding="utf-8")) if (FIX / "expected.json").is_file() else []

pytestmark = pytest.mark.skipif(not os.environ.get("LLM_BASE_URL"), reason="live model test: set LLM_BASE_URL")


def compare(expected, actual, path, problems):
    if isinstance(expected, list):
        actual = actual or []
        if len(expected) != len(actual):
            problems.append(f"{path}: expected {len(expected)} items, got {len(actual)}")
        for i, (e, a) in enumerate(zip(expected, actual)):
            compare(e, a, f"{path}[{i}]", problems)
    elif isinstance(expected, dict) and ({"value", "text", "box", "page", "matched_id"} & expected.keys()):
        if actual is None:
            problems.append(f"{path}: missing (expected {expected.get('value')!r})")
            return
        src = actual.get("source") or {}
        for k in ("value", "matched_id"):
            if k in expected and expected[k] != actual.get(k):
                problems.append(f"{path}.{k}: expected {expected[k]!r}, got {actual.get(k)!r}")
        for k in ("text", "page"):
            if k in expected and expected[k] != src.get(k):
                problems.append(f"{path}.{k}: expected {expected[k]!r}, got {src.get(k)!r}")
        if "box" in expected:
            box = src.get("box")
            if not box or any(abs(e - a) > BOX_TOL for e, a in zip(expected["box"], box)):
                problems.append(f"{path}.box: expected {expected['box']}, got {box}")
    elif isinstance(expected, dict):
        for k, e in expected.items():
            compare(e, (actual or {}).get(k), f"{path}.{k}" if path else k, problems)
    elif expected != (actual.get("value") if isinstance(actual, dict) else actual):
        problems.append(f"{path}: expected {expected!r}, got {actual!r}")


@pytest.mark.parametrize("case", CASES, ids=[c["file"] for c in CASES])
def test_extraction_matches_expected(case):
    s = Settings(llm_mode="live", llm_base_url=os.environ["LLM_BASE_URL"],
                 llm_model=os.environ.get("LLM_MODEL", ""), llm_timeout_s=60, extract_timeout_s=60)
    req = ExtractRequest(document_id=case["file"], filename=case["file"],
                         known_counterparties=case.get("known_counterparties", []),
                         content_base64=base64.b64encode((FIX / case["file"]).read_bytes()).decode())
    resp = asyncio.run(extract(req, LiveLLM(s), s))
    body = resp.model_dump(by_alias=True)
    print(f"\n{case['file']}: {resp.status} in {resp.seconds}s, errors={resp.errors}")
    problems: list[str] = []
    compare(case["expected"], body, "", problems)
    assert resp.status == "OK", resp.errors
    assert not problems, "\n".join(problems)
