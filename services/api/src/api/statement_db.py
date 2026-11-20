"""Read the bank statement from PostgreSQL (STATEMENT_SOURCE=db).

The table holds one row per CSV line with the CSV's text unchanged, so the statement goes
through the same parsing as the CSV file and gives the same transactions and ids. The data
team creates and loads the table; the backend only reads it and never creates or drops it."""

from sqlalchemy import Column, Integer, MetaData, Table, Text, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from api.domain.importer import StatementImport, StatementImportError, parse_rows

STATEMENT_TABLE = "bank_statement_lines"

CSV_COLUMNS = {
    "account_number": "Account Number",
    "booking_date": "Booking Date",
    "value_date": "Value Date",
    "transaction_type": "Transaction Type",
    "payment_reference": "Payment Reference",
    "beneficiary_payer": "Beneficiary/Payer",
    "counterparty_account": "Counterparty Account",
    "bank_code": "Bank Code",
    "amount": "Amount",
    "currency": "Currency",
    "info": "Info",
}

statement_metadata = MetaData()

statement_lines = Table(
    STATEMENT_TABLE,
    statement_metadata,
    Column("line_no", Integer, primary_key=True),
    *(Column(name, Text) for name in CSV_COLUMNS),
)


def read_statement_table(engine: Engine) -> StatementImport:
    """`line_no` is the line in the CSV file (the header is line 1), which keeps the order of
    same-day bookings and the transaction ids identical to a CSV import. NULL reads as empty."""
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(statement_lines).order_by(statement_lines.c.line_no)
            ).all()
    except DBAPIError as exc:
        raise StatementImportError(f"cannot read table {STATEMENT_TABLE}: {exc.orig}") from exc
    return parse_rows(
        [
            (
                row.line_no,
                {csv_name: getattr(row, name) or "" for name, csv_name in CSV_COLUMNS.items()},
            )
            for row in rows
        ]
    )
