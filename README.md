# Vero — Autonomous CFO

Vero helps small business owners forecast 13 weeks of cash flow and check unexpected bills against their agreements before deciding what to pay.

Built for the Collabothon 2026 Autonomous CFO challenge. [Source code repository](https://github.com/hold-my-laptop/autonomous-cfo)

## Technology stack

| Component | Technology |
| --- | --- |
| Owner interface | TypeScript, React, Vite, TanStack Query, Tailwind CSS |
| Financial engine and storage | Python, FastAPI, Pydantic, PostgreSQL 16 |
| Document and model services | PDFium via pypdfium2, OpenAI-compatible model API, Qwen3-8B FP8 served by vLLM on one NVIDIA GPU in the OpenShift configuration |
| Delivery and evaluation | Docker Compose, Red Hat OpenShift and OpenShift AI, Tekton, GitHub Actions, pytest, MLflow |

## The problem

A restaurant can have enough cash today and still struggle to cover payroll during a planned closure. An unexpected rent increase changes that forecast. To decide what to pay, the owner needs the original lease, the landlord's notice and the index named in the lease. The bank statement alone shows only what the restaurant paid before.

## Your solution

Vero connects two decisions that the owner would otherwise make separately. **Vero Cash** projects the next 13 weeks from statement history and scheduled payments. **Vero Proof** checks a bill against the expected amount, investigates differences and carries the supported amount into a payment recommendation and its forecast impact. The owner can inspect the evidence and decide on the proposed actions.

## The working prototype

1. Open **Overview** to inspect Pizzeria Amalfi's cash forecast and the decisions awaiting approval.
2. Open **Upload a bill** and select **Rent adjustment from the landlord**. This uses the bundled [demo PDF](apps/web/public/demo/doc_rent_adjustment_2025_06.pdf). The lease is already on file.
3. Open **Cost changes**. The landlord asks for €4,104 per month. Lease §4.2 requires a 5% index movement before rent can increase. The demo index rose from 117.6 to 121.2, a 3.06% change. The supported rent stays at €3,800. The disputed €304 per month amounts to €3,648 per year and €912 within this forecast window.
4. Inspect the clause, notice and calculation. Review the prepared actions and German letter draft, then approve or reject the proposed actions. **Activity** records decisions and simulated execution. Bank transfers and credit arrangements are simulated throughout the prototype.

## Behind the build

The React app submits a PDF to the API and follows its run ID. The Python AI service extracts fields and their source locations. The API validates those fields, compares them with cost history and starts an investigation when the change needs evidence. The agent can search mail and documents, read a clause, look up an index and call a calculation tool.

The team built the forecasting and cost-checking rules, investigation tools, evidence checks and approval workflow on top of FastAPI, PostgreSQL and the model server. Keeping the arithmetic in Python lets the backend reproduce a proposed finding from the cited values. If the agent exhausts its tool budget, the backend can run a scripted investigation. The trace records which path produced the result. [Architecture](docs/ARCHITECTURE_OVERVIEW.md) · [AI service](docs/AI_SERVICE.md)

## Challenges and discoveries

The clause that sets today's rent and the clause that permits a rent increase answer different questions. In live-model trials the agent followed the current-amount reference to §4.1 and spent its lookup budget before computing the adjustment. This led to a prompt change that asks for the adjustment clause, uses index periods supported by evidence and reserves a lookup for the deciding calculation. A correct result from the fallback validates the business rule, while the run trace reveals where the model still needs improvement.

## Results and next steps

The end-to-end rent scenario ties a specific clause and index to a monthly dispute, a forecast impact and an owner decision. The [API investigation tests](services/api/tests/test_investigations.py) check the classification and fallback. The [upload tests](services/api/tests/test_document_upload.py) cover duplicate files, size limits and extraction failures. The [AI investigation tests](services/ai/tests/test_investigation.py) cover tool budgets and evidence-grounded submissions.

The live bill flow accepts PDFs up to 5 MB and currently needs a text layer for structured invoice extraction. The separate document-text service supports OCR. The demo checks invoices and rent notices against records already on file. Next steps are to measure agent completion versus fallback across repeated live-model uploads and validate scanned-invoice extraction against the same evidence checks.

## Run locally

From the repository root, use Docker Compose and place the organizer-provided statement at `data/pizzeria-amalfi-bank-statement-2025-v6.csv`. The CSV is excluded from Git. Copy [the environment template](infra/.env.example) to `infra/.env`. For the local fixture demo set `LLM_MODE=stub`. For live inference set `LLM_MODE=live` and supply an OpenAI-compatible `LLM_BASE_URL` reachable from the AI container, plus credentials if required.

```sh
docker compose -f infra/docker-compose.yml --profile app up -d --build --wait
```

Open [Vero](http://localhost:8080) and follow the rent example above. The live backend's default demo date is 5 May 2025. The bundled example can use stored demo values when extraction is unavailable. Uploading the same file twice returns a duplicate-document response.

## Repository guide

| Path | What to look for |
| --- | --- |
| [apps/web](apps/web) | Owner interface, upload screen, API adapters and browser mocks |
| [services/api](services/api) | Forecasts, financial rules, proof tools, persistence and API tests |
| [services/ai](services/ai) | Model client, prompts, PDF processing and agent evaluation |
| [infra](infra) | Local Compose stack and environment template |
| [deploy/openshift](deploy/openshift) | Service deployments, GPU model serving, Tekton pipelines and cluster checks |
| [notebooks](notebooks) | Cash-flow analysis, model benchmarks and agent evaluation |
| [docs](docs) | [Frontend guide](docs/FRONTEND.md), [OpenAPI contract](docs/contracts/openapi.json), [Proof design](docs/PROOF.md) and [OpenShift guide](docs/OPENSHIFT.md) |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | API and AI test commands, frontend lint and production build |
