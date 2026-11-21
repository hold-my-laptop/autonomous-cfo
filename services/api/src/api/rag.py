"""Read-only retrieval over the financial facts already loaded by the backend.

Retrieval is deliberately deterministic and local. The model sees only the selected
records; it cannot query the database or run SQL supplied by a user.

The term handling and `rank()` are shared with Vero Proof's investigation tools
(`search_inbox`, `search_documents`): German words in letters and emails map to the same
English terms as the owner's questions, so an English query finds a German email.
"""

import re
from collections.abc import Callable, Iterable
from typing import Any

from api.state import DemoState

STOP = {"a", "an", "and", "are", "for", "how", "in", "is", "of", "on", "the", "to",
        "what", "when", "which", "with", "did", "do", "does", "i", "my", "me", "we", "our",
        "much", "many", "pay", "paid", "was", "were", "it", "payment", "payments",
        # German function words, so they never make an email match on their own
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einer", "und",
        "oder", "zu", "zum", "zur", "im", "am", "auf", "aus", "bei", "mit", "von",
        "vom", "für", "fuer", "ab", "bis", "ist", "sind", "wir", "sie", "ihr", "ihre",
        "ihnen", "ich", "es", "nicht", "auch", "wie", "dass", "als", "sehr", "geehrter",
        "geehrte", "herr", "frau", "grüße", "gruesse", "viele", "freundlichen"}
ALIASES = {
    "salary": "payroll", "salaries": "payroll", "wages": "payroll", "taxes": "tax",
    "electricity": "utilities", "landlord": "rent", "transfers": "transfer",
    "increase": "increase", "increased": "increase", "raise": "increase",
    "surcharges": "surcharge", "fee": "surcharge", "prices": "price", "letters": "letter",
    # German → the same English terms
    "miete": "rent", "mieten": "rent", "mieterhöhung": "rent", "mieterhoehung": "rent",
    "mietanpassung": "rent", "mietvertrag": "lease", "gewerbemietvertrag": "lease",
    "erhöhung": "increase", "erhoehung": "increase", "anpassung": "increase",
    "nebenkosten": "utilities", "betriebskosten": "utilities", "abschlag": "utilities",
    "logistikzuschlag": "surcharge", "zuschlag": "surcharge",
    "preisliste": "price", "preis": "price", "preise": "price",
    "lieferbedingungen": "terms", "bedingungen": "terms",
    "schreiben": "letter", "brief": "letter", "rechnung": "invoice",
}
_WORD = re.compile(r"[^\W_]+")


def terms(text: str) -> set[str]:
    """Lower-cased words without stop words, with aliases mapped to one English term."""
    return {ALIASES.get(word, word) for word in _WORD.findall(text.casefold())
            if len(word) > 1 and word not in STOP}


def rank[T](
    query: str | set[str],
    items: Iterable[T],
    text_of: Callable[[T], str],
    *,
    bonus: Callable[[T, set[str]], int] | None = None,
    order: Callable[[T], Any] | None = None,
    limit: int | None = None,
) -> list[T]:
    """Items that share at least one term with the query, best first.

    Score: 3 per shared term of `text_of(item)`, plus an optional `bonus` (exact amounts,
    dates). Ties are broken by `order` (higher first), then by input order. An item with no
    overlap is never returned."""
    wanted = query if isinstance(query, set) else terms(query)
    if not wanted:
        return []
    scored = []
    for index, item in enumerate(items):
        score = 3 * len(wanted & terms(text_of(item)))
        if bonus is not None:
            score += bonus(item, wanted)
        if score:
            scored.append((score, order(item) if order else 0, -index, item))
    scored.sort(key=lambda row: row[:3], reverse=True)
    picked = [row[3] for row in scored]
    return picked[:limit] if limit is not None else picked


def _record(kind: str, identifier: Any, date: Any, amount: int, category: str,
            counterparty: str, description: str) -> dict[str, str]:
    return {"id": str(identifier), "kind": kind, "date": str(date),
            "amount_cents": str(amount), "currency": "EUR", "category": category,
            "counterparty": counterparty, "description": description[:500]}


def _money_and_date_bonus(record: dict[str, str], query: set[str]) -> int:
    amount = abs(int(record["amount_cents"]))
    return 2 * len(query & terms(record["date"])) + 4 * len(query & {str(amount),
                                                                      str(amount // 100)})


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
    return rank(
        question,
        records,
        lambda r: " ".join((r["category"], r["counterparty"], r["description"])),
        bonus=_money_and_date_bonus,
        order=lambda r: r["date"],
        limit=limit,
    )
