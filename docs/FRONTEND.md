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
| `/contract` | The only upload. Customer contract → cash impact → safer terms → email draft. Lease → registered for Vero |
| `/costs` | Vero, the cost check: findings with a live investigation trace, evidence, prepared actions. `?doc=<id>` filters by document |
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
- `src/api/mock/index.ts`: CFO story (Cash, Currency, Tax agents), contract check, presenter events
- `src/api/mock/costs.ts`: Vero (rent notice → lease §5 → `engine.ts`)

Presenter controls (`?demo=1`): **Rent increase notice arrives**, **Fast-forward to 1 July**, **Start again**.
Upload the lease on Contract check first (demo button "Lease (Mietvertrag)") for the full rent story;
without it Vero stops at "Needs evidence" and asks for the lease.

Real-backend builds do not include the mock (it is loaded with a dynamic import only when `VITE_MOCK=1`).

## Build and deploy

[`Dockerfile`](../apps/web/Dockerfile) (Red Hat UBI Node 22 build → UBI nginx 1.24, non-root, port 8080) and [`nginx.conf`](../apps/web/nginx.conf)
(`/api/v1/*` → `api:8080`, `/health`, SPA fallback) are owned by DevOps. The image is a real-backend build:
`VITE_MOCK` is not set, so the mock and the sample CSV are not part of the bundle.

```bash
make up-app     # db + api + web via infra/docker-compose.yml, web on http://localhost:8080
```
