# autonomous-cfo
AI CFO – Commerzbank Hackathon 2026 (Red Hat challenge)

## Financial question answering

AI teammate handoff: [`docs/RAG_HANDOFF.md`](docs/RAG_HANDOFF.md).

`POST /api/v1/assistant/ask` takes a question, retrieves matching booked transactions and
future payments from the backend's loaded financial state, and asks the internal LLM service
to answer using only those records. The response includes the source records used and
whether the text came from the model (`MODEL`) or is a plain list of the records (`TEMPLATE`,
when the model is unavailable or its answer fails the checks). No model-generated SQL or
direct model database access is involved; it works with the CSV and the PostgreSQL
`bank_statement_lines` statement source alike.

```http
POST /api/v1/assistant/ask
Content-Type: application/json

{"question":"When we will pay invoices?"}
```

The API and AI service share `INTERNAL_TOKEN`; `AI_URL` points to the AI service. For local
Docker Compose, copy `infra/.env.example` to `infra/.env`, fill the model URL and database
settings, then run `docker compose --profile app up --build` from `infra/`.

Retrieval is lexical with a small Turkish finance synonym list and returns up to 12 records,
so totals across the full statement should use deterministic backend analytics instead.
