"""Read-only retrieval over the financial facts already loaded by the backend.

Retrieval is deliberately deterministic and local. The model sees only the selected
records; it cannot query the database or run SQL supplied by a user. The same term handling
and ranking serve the RAG assistant and Vero Proof's inbox and document search.
"""

import re
from collections.abc import Callable, Iterable
from typing import Any

from api.state import DemoState

_STOP_WORDS = """
a an and are for how in is of on the to what when which with did do does i my me we our much
many pay paid was were it payment payments from about any by der die das den dem des und oder
um ab zu zum zur mit von fuer im ein eine einen einer ist sind wir sie ihr ihre ihren ihnen ich
es auf am bei bitte sehr geehrter geehrte herr frau gruessen gruesse freundlichen viele hallo
guten tag nicht auch als wie wird werden
"""
STOP = set(_STOP_WORDS.split())
_SPELLINGS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
ALIASES = {
    "salary": "payroll",
    "salaries": "payroll",
    "wages": "payroll",
    "taxes": "tax",
    "electricity": "utilities",
    "landlord": "rent",
    "transfers": "transfer",
    "rents": "rent",
    "rental": "rent",
    "increases": "increase",
    "increased": "increase",
    "raise": "increase",
    "rise": "increase",
    "hike": "increase",
    "adjustments": "adjustment",
    "surcharges": "surcharge",
    "fee": "surcharge",
    "invoices": "invoice",
    "prices": "price",
    "miete": "rent",
    "mieten": "rent",
    "mieterhoehung": "rent increase",
    "mietanpassung": "rent adjustment",
    "mietvertrag": "rent lease",
    "mietverhaeltnis": "rent lease",
    "mietobjekt": "rent premises",
    "vermieter": "rent",
    "vermieterin": "rent",
    "erhoehung": "increase",
    "erhoehen": "increase",
    "anpassung": "adjustment",
    "nebenkosten": "operating costs",
    "betriebskosten": "operating costs",
    "bewirtschaftungskosten": "operating costs",
    "logistikzuschlag": "surcharge logistics",
    "zuschlag": "surcharge",
    "preisliste": "price list",
    "preise": "price",
    "preis": "price",
    "rechnung": "invoice",
    "lieferbedingungen": "terms delivery",
    "bedingungen": "terms",
    "wertsicherung": "index indexation",
    "verbraucherpreisindex": "index",
    "indexmiete": "rent index",
    "strom": "utilities",
    "abschlag": "utilities advance",
    "abschlagsplan": "utilities advance",
}


def terms(text: str) -> set[str]:
    """Lower-case words without stop words, umlauts spelled out, aliases mapped to their
    English terms, so an English question finds German text and the reverse."""
    words = re.findall(r"[^\W_]+", text.casefold().translate(_SPELLINGS))
    found: set[str] = set()
    for word in words:
        if len(word) > 1 and word not in STOP:
            found.update(ALIASES.get(word, word).split())
    return found


def rank[T](
    query: str | set[str],
    items: Iterable[T],
    text_of: Callable[[T], str],
    *,
    newest: Callable[[T], str] = lambda item: "",
    bonus: Callable[[set[str], T], int] = lambda query, item: 0,
    weight: int = 3,
) -> list[tuple[int, T]]:
    """Items sharing at least one term with the query, best first: `weight` points per shared
    term plus `bonus`, ties broken by `newest` (descending). An item with no overlap is never
    returned, however recent."""
    wanted = terms(query) if isinstance(query, str) else query
    if not wanted:
        return []
    ranked = []
    for item in items:
        score = weight * len(wanted & terms(text_of(item))) + bonus(wanted, item)
        if score:
            ranked.append((score, newest(item), item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [(score, item) for score, _, item in ranked]


def _record(
    kind: str,
    identifier: Any,
    date: Any,
    amount: int,
    category: str,
    counterparty: str,
    description: str,
) -> dict[str, str]:
    return {
        "id": str(identifier),
        "kind": kind,
        "date": str(date),
        "amount_cents": str(amount),
        "currency": "EUR",
        "category": category,
        "counterparty": counterparty,
        "description": description[:500],
    }


def _date_and_amount(query: set[str], record: dict[str, str]) -> int:
    """Exact money amounts (EUR or cents) and dates match without descriptive words."""
    amount = abs(int(record["amount_cents"]))
    amount_terms = {str(amount), str(amount // 100)}
    return 2 * len(query & terms(record["date"])) + 4 * len(query & amount_terms)


def retrieve(state: DemoState, question: str, limit: int = 12) -> list[dict[str, str]]:
    """Booked transactions and future payments ranked by lexical overlap."""
    records = [
        _record(
            "transaction",
            t.id,
            t.booking_date,
            t.amount,
            t.category.value,
            t.counterparty,
            f"{t.type} {t.reference}",
        )
        for t in state.history
    ]
    records += [
        _record(
            "outflow",
            o.id,
            o.scheduled_date,
            -o.amount,
            o.category.value,
            o.counterparty,
            f"{o.ref} {o.description or ''}",
        )
        for o in state.outflows
    ]
    records += [
        _record("inflow", i.id, i.expected_date, i.amount, "INFLOW", i.counterparty, i.ref)
        for i in state.inflows
    ]
    ranked = rank(
        question,
        records,
        lambda r: " ".join((r["category"], r["counterparty"], r["description"])),
        newest=lambda r: r["date"],
        bonus=_date_and_amount,
    )
    return [record for _, record in ranked[:limit]]
