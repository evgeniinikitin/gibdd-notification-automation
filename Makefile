.PHONY: setup up down logs test lint build api-test web-test

setup:
	@test -f .env || cp .env.example .env

up: setup
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=150

api-test:
	docker compose run --rm api pytest

web-test:
	docker compose run --rm web npm test -- --run

test: api-test web-test

lint:
	docker compose run --rm api ruff check app tests
	docker compose run --rm web npm run lint

build:
	docker compose build

