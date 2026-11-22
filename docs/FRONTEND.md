# Vero – Frontend (Autonomous CFO challenge)

Code: `apps/web`.

React + TypeScript + Vite. Plain-language UI for business owners of every generation.

## Run

```bash
cd apps/web
npm install
VITE_MOCK=1 npm run dev    # in-browser mock backend, http://localhost:5173 (PowerShell: $env:VITE_MOCK="1"; npm run dev)
npm run dev                # real backend: start it first with `make up-app` (api on localhost:8000)
# presenter controls: http://localhost:5173/?demo=1
# api somewhere else: BACKEND_URL=http://10.0.0.5:8000 npm run dev
# chat needs services/ai too (default localhost:8001): AI_URL=http://localhost:8001 npm run dev
```

The client calls `/api/v1/...` on its own origin, the same as [`nginx.conf`](../apps/web/nginx.conf) does in the cluster, so no
CORS is needed. The dev server forwards `/api/v1/chat` and `/api/v1/documents/extract` to `$AI_URL` (as `/v1/...`)
and everything else under `/api/v1/*` to `$BACKEND_URL` unchanged.

In live mode the backend's shapes (cents as strings, snake_case) are mapped onto the UI types in
[`src/api/adapters.ts`](../apps/web/src/api/adapters.ts); the mapping is listed at the bottom of [`src/api/types.ts`](../apps/web/src/api/types.ts).
The backend has no event stream, so live updates come from polling the latest run (`GET /runs?limit=1`).

## Screens

| Route | What it shows |
|---|---|
| `/` Overview | One status sentence, 13-week cash chart (W1–W13), **Needs your OK**, **Suggestions**, **Done and checked** |
| `/contract` | **Upload a bill or notice** (POST /documents, PDF up to 5 MB): Vero checks it and the page follows the run. Demo button: `doc_rent_adjustment_2025_06.pdf` (served from `public/demo`). Mock mode also has "Test a customer contract" (cash impact, safer terms, email draft) |
| `/costs` | Vero, the cost check: digest counts, findings that need the owner with the investigation trace, evidence side by side, prepared actions (approve or decline each), **Handled quietly** (quiet findings, one tap to clear) and routine checks. `?doc=<id>` filters by document |
| `/activity` | Sealed (hash-chained) history of everything agents and the owner did |

Every item shows the challenge's four levels: **Checked on its own** (analysis), **Suggestion**,
**Needs your OK**, **Done for you**, plus **Simulated** vs real execution.
"How did you decide?" opens the business logic step by step.

Accessibility: Inter with tabular figures, fixed 16px base, keyboard focus, screen-reader labels,
reduced motion respected.

## Data

Frontend shapes: `src/api/types.ts`. Backend contract: [`docs/contracts/openapi.json`](contracts/openapi.json).

Mock mode replays **Pizzeria Amalfi's 2025 bank statement** (`data/pizzeria-amalfi-bank-statement-2025-v6.csv` at the repo root, gitignored, imported via the `@samples` alias).
The demo replays 2025 from Monday 14 April; agents only see transactions before that date.

- `src/api/mock/statement.ts`: CSV parser, daily balances, weekly forecast (mock forecast = 2025 actuals + approved decisions)
- `src/api/mock/index.ts`: CFO story (Cash, Currency, Tax agents), customer contract test, presenter events
- `src/api/mock/costs.ts`: Vero, the reference demo of [PROOF.md §7](PROOF.md): three changes handled quietly, three routine checks, and the rent adjustment (4 104 € billed, lease §4.2, VPI 117.6 → 121.2 = 3.06 % < 5 % → 3 800 € allowed, 304 €/month, 3 648 €/year, 912 € in the window), with `engine.ts` (the backend's formulas)

Presenter controls (`?demo=1`): **Landlord's rent adjustment arrives** (same as uploading it), **Fast-forward to 1 July**, **Start again**.

Live mode (`adapters.ts`): `GET /findings` + `GET /recommendations` + `GET /runs/{run_id}` → findings (quiet ones go to Handled quietly), `GET /digest` → the tiles (3 / 3 / 0 / 3 after a reset, 4 / 3 / 1 / 3 after the rent upload), `POST /recommendations/{id}/approve|reject` per action, `POST /findings/{id}/acknowledge` for a quiet one.

Real-backend builds do not include the mock (it is loaded with a dynamic import only when `VITE_MOCK=1`).

## Build and deploy

[`Dockerfile`](../apps/web/Dockerfile) (Red Hat UBI Node 22 build → UBI nginx 1.24, non-root, port 8080) and [`nginx.conf`](../apps/web/nginx.conf)
(`/api/v1/*` → `api:8080`, `/health`, SPA fallback) are owned by DevOps. The image is a real-backend build:
`VITE_MOCK` is not set, so the mock and the sample CSV are not part of the bundle.

```bash
make up-app     # db + api + web via infra/docker-compose.yml, web on http://localhost:8080
```
