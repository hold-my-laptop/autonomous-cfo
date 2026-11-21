# Recipe lines start with ">" instead of a tab (safe to paste anywhere).
.RECIPEPREFIX := >
COMPOSE := docker compose -f infra/docker-compose.yml
COMPOSE_APP := $(COMPOSE) --profile app
OC_NS := autonomous-cfo
STATEMENT := data/pizzeria-amalfi-bank-statement-2025-v6.csv

.PHONY: up up-app down logs ps seed demo-reset test eval e2e schema oc-api-build oc-api-deploy oc-db-secret oc-db-deploy oc-ai-secret oc-ai-build oc-ai-deploy oc-network oc-monitoring oc-check

up:
> test -f infra/.env || cp infra/.env.example infra/.env
> $(COMPOSE) up -d --build --wait
> $(COMPOSE) ps

up-app:
> test -f infra/.env || cp infra/.env.example infra/.env
> $(COMPOSE_APP) up -d --build --wait
> $(COMPOSE_APP) ps

down:
> $(COMPOSE_APP) down

logs:
> $(COMPOSE_APP) logs -f --tail=100

ps:
> $(COMPOSE_APP) ps

seed:
> @echo "seed: not implemented yet (Backend)"

demo-reset:
> @echo "demo-reset: not implemented yet (Backend)"

test:
> @echo "test: not implemented yet"

eval:
> @echo "eval: not implemented yet"

e2e:
> @echo "e2e: not implemented yet"

schema:
> cd services/api && uv run python -m api.openapi

oc-api-build:
> oc apply -n $(OC_NS) -f deploy/openshift/api/01-imagestream.yaml -f deploy/openshift/api/02-buildconfig.yaml
> oc start-build api -n $(OC_NS) --follow

oc-api-deploy: oc-db-secret oc-ai-secret
> oc create configmap api-statement -n $(OC_NS) --from-file=$(STATEMENT) --dry-run=client -o yaml | oc apply --server-side -n $(OC_NS) -f -
> oc apply -n $(OC_NS) -f deploy/openshift/api/03-deployment.yaml -f deploy/openshift/api/04-service.yaml
> oc rollout status deployment/api -n $(OC_NS) --timeout=180s

oc-db-secret:
> @oc get secret db -n $(OC_NS) >/dev/null 2>&1 || { pw=$$(openssl rand -hex 16); oc create secret generic db -n $(OC_NS) --from-literal=POSTGRESQL_USER=app --from-literal=POSTGRESQL_DATABASE=app --from-literal=POSTGRESQL_PASSWORD=$$pw --from-literal=DATABASE_URL=postgresql://app:$$pw@db:5432/app; }

oc-db-deploy: oc-db-secret
> oc apply -n $(OC_NS) -f deploy/openshift/db/
> oc rollout status deployment/db -n $(OC_NS) --timeout=180s

oc-ai-secret:
> @oc get secret internal-token -n $(OC_NS) >/dev/null 2>&1 || oc create secret generic internal-token -n $(OC_NS) --from-literal=INTERNAL_TOKEN=$$(openssl rand -hex 24)

oc-ai-build:
> oc apply -n $(OC_NS) -f deploy/openshift/ai/01-imagestream.yaml -f deploy/openshift/ai/02-buildconfig.yaml
> oc start-build ai -n $(OC_NS) --follow

oc-ai-deploy: oc-ai-secret
> oc apply -n $(OC_NS) -f deploy/openshift/ai/03-deployment.yaml -f deploy/openshift/ai/04-service.yaml
> oc rollout status deployment/ai -n $(OC_NS) --timeout=180s

oc-network:
> oc apply -n $(OC_NS) -f deploy/openshift/network/
> oc get networkpolicy -n $(OC_NS) -l app.kubernetes.io/part-of=vero

oc-monitoring:
> oc apply -f deploy/openshift/monitoring/cluster-monitoring-config.yaml
> for i in $$(seq 60); do oc get statefulset prometheus-user-workload -n openshift-user-workload-monitoring >/dev/null 2>&1 && break; sleep 5; done
> oc rollout status statefulset/prometheus-user-workload -n openshift-user-workload-monitoring --timeout=300s
