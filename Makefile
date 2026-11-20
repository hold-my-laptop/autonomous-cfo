# Recipe lines start with ">" instead of a tab (safe to paste anywhere).
.RECIPEPREFIX := >
COMPOSE := docker compose -f infra/docker-compose.yml
COMPOSE_APP := $(COMPOSE) --profile app

.PHONY: up up-app down logs ps seed demo-reset test eval e2e schema

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
