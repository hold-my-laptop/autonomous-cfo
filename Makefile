# Recipe lines start with ">" instead of a tab (safe to paste anywhere).
.RECIPEPREFIX := >
COMPOSE := docker compose -f infra/docker-compose.yml
COMPOSE_APP := $(COMPOSE) --profile app
OC_NS := autonomous-cfo
STATEMENT := data/pizzeria-amalfi-bank-statement-2025-v6.csv

.PHONY: up up-app down logs ps seed demo-reset test eval e2e schema oc-api-build oc-api-deploy

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
> @echo "schema: not implemented yet (Backend)"

oc-api-build:
> oc apply -n $(OC_NS) -f deploy/openshift/api/01-imagestream.yaml -f deploy/openshift/api/02-buildconfig.yaml
> oc start-build api -n $(OC_NS) --follow

oc-api-deploy:
> oc create configmap api-statement -n $(OC_NS) --from-file=$(STATEMENT) --dry-run=client -o yaml | oc apply --server-side -n $(OC_NS) -f -
> oc apply -n $(OC_NS) -f deploy/openshift/api/03-deployment.yaml -f deploy/openshift/api/04-service.yaml
> oc rollout status deployment/api -n $(OC_NS) --timeout=180s
