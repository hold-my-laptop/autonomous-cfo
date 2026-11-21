"""Read-only retrieval over the financial facts already loaded by the backend.

Retrieval is deliberately deterministic and local. The model sees only the selected
records; it cannot query the database or run SQL supplied by a user.
"""

import re
from typing import Any

from api.state import DemoState

STOP = {"a", "an", "and", "are", "for", "how", "in", "is", "of", "on", "the", "to",
        "what", "when", "which", "with", "did", "do", "does", "i", "my", "me", "we", "our",
        "much", "many", "pay", "paid", "was", "were", "it", "ve", "ile", "icin", "için", "ne", "nedir",
        "kaç", "kac", "hangi", "bu", "bir", "mi", "mu", "mı", "mü", "ödemeler", "ödeme"}
ALIASES = {"kira": "rent", "kiralar": "rent", "maaş": "payroll", "maas": "payroll",
           "vergi": "tax", "vergiler": "tax", "elektrik": "utilities",
           "gelir": "revenue", "gider": "cost", "transferler": "transfer"}


def _terms(text: str) -> set[str]:
    return {ALIASES.get(word, word) for word in re.findall(r"[^\W_]+", text.casefold())
            if len(word) > 1 and word not in STOP}


def _record(kind: str, identifier: Any, date: Any, amount: int, category: str,
            counterparty: str, description: str) -> dict[str, str]:
    return {"id": str(identifier), "kind": kind, "date": str(date),
            "amount_cents": str(amount), "currency": "EUR", "category": category,
            "counterparty": counterparty, "description": description[:500]}


def retrieve(state: DemoState, question: str, limit: int = 12) -> list[dict[str, str]]:
    """Rank booked transactions and future payments by lexical overlap.

    A zero-overlap result is never returned merely because it is recent. Exact money
    amounts (EUR or cents) and dates can also match without descriptive words.
    """
    records = [
        _record("transaction", t.id, t.booking_date, t.amount, t.category.value,
                t.counterparty, f"{t.type} {t.reference}")
        for t in state.history
    ]
    records += [
        _record("outflow", o.id, o.scheduled_date, -o.amount, o.category.value,
                o.counterparty, f"{o.ref} {o.description or ''}")
        for o in state.outflows
    ]
    records += [
        _record("inflow", i.id, i.expected_date, i.amount, "INFLOW",
                i.counterparty, i.ref)
        for i in state.inflows
    ]
    query = _terms(question)
    if not query:
        return []
    ranked = []
    for record in records:
        subject = _terms(" ".join((record["category"], record["counterparty"],
                                   record["description"])))
        date_terms = _terms(record["date"])
        amount = abs(int(record["amount_cents"]))
        amount_terms = {str(amount), str(amount // 100)}
        score = 3 * len(query & subject) + 2 * len(query & date_terms)
        score += 4 * len(query & amount_terms)
        if score:
            ranked.append((score, record["date"], record))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [record for _, _, record in ranked[:limit]]
