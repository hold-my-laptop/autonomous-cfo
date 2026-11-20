# Recipe lines start with ">" instead of a tab (safe to paste anywhere).
.RECIPEPREFIX := >
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: up down logs ps seed demo-reset test eval e2e schema

up:
> test -f infra/.env || cp infra/.env.example infra/.env
> $(COMPOSE) up -d --build --wait
> $(COMPOSE) ps

down:
> $(COMPOSE) down

logs:
> $(COMPOSE) logs -f --tail=100

ps:
> $(COMPOSE) ps

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
