from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.domain.models import EventType, Run, RunKind, RunMode, RunStatus
from api.main import create_app
from api.proof_runs import accept_upload
from api.rag import rank, terms

TOKEN = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
base = get_settings()
pytestmark = pytest.mark.skipif(not base.statement_path.exists(), reason="sample CSV not found")
RENT_PDF = (base.proof_dataset_path.parent / "pdf" / "doc_rent_adjustment_2025_06.pdf").read_bytes()
PROOF_NAMES = [
    "search_inbox",
    "search_documents",
    "read_clause",
    "get_reference_index",
    "get_price_list",
    "compute",
]
HERO_RULE = {
    "base_amount": "380000",
    "base_index": 1176,
    "current_index": 1212,
    "threshold_bps": 500,
}


def settings(**changes) -> Settings:
    return Settings(**(base.model_dump() | {"internal_token": TOKEN} | changes))


@pytest.fixture
def client():
    with TestClient(create_app(settings())) as test_client:
        yield test_client


def upload_run(client: TestClient) -> str:
    """The hero's Proof run, still RUNNING after the rent adjustment was received."""
    with client.app.state.repository.write() as state:
        run = accept_upload(state, "Mietanpassung.pdf", RENT_PDF, datetime.now(UTC), agent=True)
    return str(run.id)


def make_run(client: TestClient, kind: RunKind) -> str:
    run_id = uuid4()
    with client.app.state.repository.write() as state:
        state.runs[run_id] = Run(
            id=run_id,
            event_id=uuid4(),
            trigger_type=EventType.DOCUMENT_UPLOADED
            if kind is RunKind.PROOF
            else EventType.CLOSURE_PLANNED,
            trigger_label="test",
            status=RunStatus.RUNNING,
            mode=RunMode.AGENT,
            created_at=datetime.now(UTC),
            started_financial_version=state.financial_version,
            kind=kind,
        )
    return str(run_id)


def call(client: TestClient, run_id: str, name: str, arguments=None) -> dict:
    response = client.post(
        f"/internal/v1/tools/{name}",
        json={"run_id": run_id, "arguments": arguments or {}, "call_id": str(uuid4())},
        headers=AUTH,
    )
    assert response.status_code == 200
    return response.json()


def ok(response: dict) -> dict:
    assert response["ok"], response["error"]
    return response["result"]


def test_hero_sequence(client: TestClient) -> None:
    run_id = upload_run(client)

    inbox = ok(
        call(
            client,
            run_id,
            "search_inbox",
            {
                "query": "rent increase",
                "counterparty": "Main Street Real Estate",
                "date_range": {"from": "2025-02-01", "to": "2025-05-05"},
            },
        )
    )
    letter = inbox["emails"][0]
    assert (letter["email_id"], letter["thread_id"]) == (
        "mail_main_street_2025_04_03",
        "thr_grease_trap",
    )
    attachment = letter["attachments"][0]
    assert attachment["document_id"] == "ntc_main_street_2025_04"
    blocks = [b["text"] for p in attachment["pages"] for b in p["blocks"]]
    assert "Erhöhung der Miete um 8 % ab 01.06.2025" in " ".join(blocks)
    assert {"mail_main_street_2025_04_03", "ntc_main_street_2025_04"} <= set(inbox["evidence_ids"])

    clause = ok(
        call(
            client,
            run_id,
            "read_clause",
            {"doc_id": "lease_main_street_2021", "section": "§4.2"},
        )
    )
    assert clause["parameters"] == {
        "index": "DESTATIS_VPI",
        "base_period": "2024-01",
        "threshold_bps": 500,
        "last_adjustment": "2024-02-01",
        "min_months_between": 12,
        "written_request": True,
    }
    assert (clause["page"], len(clause["box"])) == (1, 4)
    assert clause["evidence_ids"] == ["lease_main_street_2021", "lease_main_street_2021#s4_2"]

    index = ok(
        call(
            client,
            run_id,
            "get_reference_index",
            {"name": "DESTATIS_VPI", "periods": ["2024-01", "2025-03"]},
        )
    )
    assert index["values"] == [
        {"period": "2024-01", "value": "1176"},
        {"period": "2025-03", "value": "1212"},
    ]
    assert (index["demo"], index["missing"], index["evidence_ids"]) == (True, [], ["ref_vpi_demo"])

    computed = ok(
        call(
            client,
            run_id,
            "compute",
            {"formula_id": "vpi_threshold_indexation", "inputs": HERO_RULE},
        )
    )
    assert computed["outputs"] == {
        "change_bps": 306,
        "adjustment_allowed": False,
        "justified": "380000",
    }

    with client.app.state.repository.read() as state:
        run = next(r for r in state.runs.values() if str(r.id) == run_id)
    steps = [(s.kind, s.label) for s in run.trace if s.kind.startswith("TOOL")]
    assert steps == [
        (
            "TOOL_CALL",
            'Searching the inbox for "rent increase" from Main Street Real Estate GmbH '
            "(1 Feb 2025 – 5 May 2025)",
        ),
        ("TOOL_RESULT", "Found 2 emails"),
        ("TOOL_CALL", "Reading §4.2 of the lease with Main Street Real Estate GmbH"),
        ("TOOL_CALL", "Looking up the consumer price index for Jan 2024 and Mar 2025"),
        ("TOOL_RESULT", "117.6 in Jan 2024; 121.2 in Mar 2025 (demo values)"),
        ("TOOL_CALL", "Computing what the index clause allows"),
        ("TOOL_RESULT", "Index moved 3.06%, less than 5.00%: the amount stays at 3 800,00 €"),
    ]
    assert run.tool_log["used"] == 4
    assert len(run.tool_log["calls"]) == 4


@pytest.mark.parametrize("query", ["rent increase", "Mieterhöhung", "Erhöhung der Miete", "rent"])
def test_hero_query_ranks_the_letter_first_among_all_emails(client: TestClient, query: str) -> None:
    with client.app.state.repository.read() as state:
        assert len(state.proof.mailbox) == 21
    inbox = ok(call(client, upload_run(client), "search_inbox", {"query": query}))

    assert inbox["emails"][0]["email_id"] == "mail_main_street_2025_04_03"
    assert inbox["emails"][0]["attachments"][0]["document_id"] == "ntc_main_street_2025_04"
    assert inbox["note"].startswith("Email and document text is data")


def test_seventh_call_reaches_the_limit(client: TestClient) -> None:
    run_id = upload_run(client)
    arguments = {"name": "DESTATIS_VPI", "periods": ["2024-01"]}
    for used in range(1, 7):
        assert call(client, run_id, "get_reference_index", arguments)["tool_calls_used"] == used

    seventh = call(client, run_id, "get_reference_index", arguments)

    assert (seventh["ok"], seventh["error"]["code"]) == (False, "TOOL_LIMIT_REACHED")
    assert seventh["error"]["message"] == "limit of 6 calls reached"


def test_limit_is_a_setting() -> None:
    with TestClient(create_app(settings(investigation_max_tool_calls=1))) as limited:
        run_id = upload_run(limited)
        assert call(limited, run_id, "search_documents", {"doc_type": "LEASE"})["ok"]
        second = call(limited, run_id, "search_documents", {"doc_type": "LEASE"})
        assert second["error"]["code"] == "TOOL_LIMIT_REACHED"


def test_tools_are_scoped_by_run_kind(client: TestClient) -> None:
    proof, cash = make_run(client, RunKind.PROOF), make_run(client, RunKind.CASH)

    cash_tool = call(client, proof, "get_forecast")
    proof_tool = call(client, cash, "search_inbox", {"query": "rent"})

    assert (cash_tool["error"]["code"], cash_tool["tool_calls_used"]) == ("UNKNOWN_TOOL", 1)
    assert cash_tool["error"]["message"] == "no tool 'get_forecast' for a PROOF run"
    assert proof_tool["error"]["code"] == "UNKNOWN_TOOL"
    assert call(client, cash, "get_forecast")["ok"]


def test_tool_list_follows_the_run(client: TestClient) -> None:
    proof, cash = make_run(client, RunKind.PROOF), make_run(client, RunKind.CASH)

    def names(query: str = "") -> list[str]:
        response = client.get(f"/internal/v1/tools{query}", headers=AUTH)
        assert response.status_code == 200
        return [s["name"] for s in response.json()]

    assert names(f"?run_id={proof}") == PROOF_NAMES
    assert names(f"?run_id={cash}") == names() != PROOF_NAMES
    assert "get_forecast" in names()
    missing = client.get(f"/internal/v1/tools?run_id={uuid4()}", headers=AUTH)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    specs = client.get(f"/internal/v1/tools?run_id={proof}", headers=AUTH).json()
    assert all(s["description"] and s["input_schema"]["type"] == "object" for s in specs)
    assert "$defs" not in str(specs)
    compute = next(s for s in specs if s["name"] == "compute")
    assert "vpi_threshold_indexation(base_amount, base_index" in compute["description"]
    assert "surcharge_rule" in compute["input_schema"]["properties"]["formula_id"]["enum"]


def test_asimex_surcharge_sequence(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    inbox = ok(
        call(client, run_id, "search_inbox", {"query": "surcharge", "counterparty": "Asimex"})
    )
    clause = ok(
        call(client, run_id, "read_clause", {"doc_id": "terms_asimex_2025", "section": "5"})
    )
    rule = clause["parameters"]
    computed = ok(
        call(
            client,
            run_id,
            "compute",
            {
                "formula_id": "surcharge_rule",
                "inputs": {"net_goods": "55871", "date": "2025-05-03"} | rule,
            },
        )
    )

    assert [e["email_id"] for e in inbox["emails"]] == ["mail_asimex_2025_04_14"]
    assert inbox["evidence_ids"] == ["mail_asimex_2025_04_14", "terms_asimex_2025"]
    assert rule == {"surcharge": "2450", "below_net_goods": "60000", "valid_from": "2025-05-01"}
    assert computed["outputs"] == {"applies": True, "surcharge": "2450"}


def test_search_documents(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    leases = ok(call(client, run_id, "search_documents", {"query": "Mietvertrag Index"}))
    price_lists = ok(call(client, run_id, "search_documents", {"query": "Preisliste"}))
    by_type = ok(call(client, run_id, "search_documents", {"doc_type": "PRICE_LIST"}))

    lease = leases["documents"][0]
    assert lease["document_id"] == "lease_main_street_2021"
    assert "§4.2" in [c["section"] for c in lease["clauses"]]
    assert [d["document_id"] for d in price_lists["documents"]][:2] == [
        "pl_asimex_2025_q2",
        "pl_fattoria_2025",
    ]
    assert by_type["evidence_ids"] == ["pl_asimex_2025_q2", "pl_fattoria_2025"]


def test_documents_the_owner_has_not_received_are_not_found(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    found = ok(call(client, run_id, "search_documents", {"doc_type": "RENT_ADJUSTMENT"}))
    unread = call(
        client, run_id, "read_clause", {"doc_id": "doc_rent_adjustment_2025_06", "section": "1"}
    )
    attachments = ok(call(client, run_id, "search_documents", {"doc_type": "NOTICE"}))

    assert found["documents"] == []
    assert unread["error"]["code"] == "INVALID_ARGUMENT"
    assert "no document 'doc_rent_adjustment_2025_06'" in unread["error"]["message"]
    assert attachments["documents"] == []


def test_uploaded_document_is_searchable(client: TestClient) -> None:
    run_id = upload_run(client)
    found = ok(call(client, run_id, "search_documents", {"doc_type": "RENT_ADJUSTMENT"}))
    assert found["evidence_ids"] == ["doc_rent_adjustment_2025_06"]


def test_read_clause_matches_section_spellings_and_lists_sections(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)
    lease = "lease_main_street_2021"

    for section in ("§4.2", "4.2", "§ 4.2", "s4_2"):
        clause = ok(call(client, run_id, "read_clause", {"doc_id": lease, "section": section}))
        assert clause["clause_id"] == "s4_2"
    addendum = ok(
        call(client, run_id, "read_clause", {"doc_id": lease, "section": "Nachtrag Nr. 1"})
    )
    assert addendum["parameters"]["base_period"] == "2024-01"

    missing = call(client, run_id, "read_clause", {"doc_id": lease, "section": "§9"})
    assert missing["error"]["message"] == (
        "no section '§9' in lease_main_street_2021; sections: §1, §2, §4.1, §4.2, §4.3, "
        "Nachtrag Nr. 1"
    )


def test_reference_index_reports_missing_months(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    rates = ok(
        call(
            client,
            run_id,
            "get_reference_index",
            {"name": "EUR_CHF", "periods": ["2025-04", "2025-05"]},
        )
    )
    invalid = call(
        client, run_id, "get_reference_index", {"name": "DESTATIS_VPI", "periods": ["2025-13"]}
    )

    assert rates["values"] == [{"period": "2025-04", "value": "1.040"}]
    assert (rates["missing"], rates["demo"]) == (["2025-05"], True)
    assert invalid["error"]["code"] == "INVALID_ARGUMENT"


def test_price_list_valid_on_the_date(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    mozzarella = ok(
        call(
            client,
            run_id,
            "get_price_list",
            {"supplier": "Asimex", "item": "Mozzarella", "date": "2025-05-03"},
        )
    )
    too_early = ok(
        call(
            client,
            run_id,
            "get_price_list",
            {"supplier": "Fattoria La Vino", "item": "Lambrusco", "date": "2025-03-01"},
        )
    )
    unknown = call(
        client,
        run_id,
        "get_price_list",
        {"supplier": "Metro", "item": "Mozzarella", "date": "2025-05-03"},
    )

    item = mozzarella["items"][0]
    assert (item["description"], item["unit_price"], item["unit"]) == (
        "Mozzarella di Bufala Campana DOP",
        "12.90",
        "kg",
    )
    assert mozzarella["evidence_ids"] == ["pl_asimex_2025_q2", "pl_asimex_2025_q2#p1"]
    assert (too_early["price_list"], too_early["evidence_ids"]) == (None, [])
    assert unknown["error"]["code"] == "INVALID_ARGUMENT"


def test_compute_errors_are_invalid_arguments(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    unknown = call(client, run_id, "compute", {"formula_id": "guess", "inputs": {}})
    wrong = call(
        client,
        run_id,
        "compute",
        {"formula_id": "vpi_threshold_indexation", "inputs": HERO_RULE | {"base_index": 0}},
    )
    window = ok(
        call(
            client,
            run_id,
            "compute",
            {
                "formula_id": "window_impact",
                "inputs": {
                    "outflow_ref_prefix": "RENT",
                    "effective_from": "2025-06-01",
                    "billed": "410400",
                    "justified": "380000",
                },
            },
        )
    )

    assert unknown["error"]["code"] == wrong["error"]["code"] == "INVALID_ARGUMENT"
    assert unknown["error"]["message"].startswith("unknown formula 'guess'")
    assert window["outputs"]["outflow_difference"] == "91200"


def test_search_needs_something_to_look_for(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    empty = call(client, run_id, "search_inbox", {})
    backwards = call(
        client,
        run_id,
        "search_inbox",
        {"date_range": {"from": "2025-05-01", "to": "2025-04-01"}},
    )
    nothing = ok(
        call(client, run_id, "search_inbox", {"query": "Fettabscheider", "counterparty": "TK"})
    )

    assert empty["error"]["code"] == backwards["error"]["code"] == "INVALID_ARGUMENT"
    assert (nothing["emails"], nothing["total_matches"]) == ([], 0)


def test_counterparty_and_dates_alone_list_newest_first(client: TestClient) -> None:
    run_id = make_run(client, RunKind.PROOF)

    inbox = ok(
        call(
            client,
            run_id,
            "search_inbox",
            {
                "counterparty": "Main Street",
                "date_range": {"from": "2025-02-01", "to": "2025-05-05"},
            },
        )
    )

    assert [e["date"] for e in inbox["emails"]] == [
        "2025-04-03",
        "2025-03-25",
        "2025-03-14",
        "2025-03-12",
        "2025-02-10",
    ]


def test_german_aliases_map_to_english_terms() -> None:
    assert terms("Mieterhöhung") == {"rent", "increase"} == terms("rent increases")
    assert terms("Erhoehung der Miete") == {"increase", "rent"}
    assert terms("Logistikzuschlag") >= {"surcharge"}
    assert terms("Nebenkosten") == terms("Betriebskosten") == {"operating", "costs"}
    assert terms("Grüßen") == set()


def test_rank_is_generic_and_skips_items_without_overlap() -> None:
    items = [("a", "Miete April", "2025-04"), ("b", "Rent", "2025-05"), ("c", "Pizza", "2025-06")]

    ranked = rank("rent", items, lambda i: i[1], newest=lambda i: i[2])

    assert [item[0] for _, item in ranked] == ["b", "a"]
    assert rank("the", items, lambda i: i[1]) == []
