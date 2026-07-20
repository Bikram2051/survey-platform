# Run from the repo root, inside WSL2.
.PHONY: up down migrate test run shell

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd backend && python3 manage.py migrate

test:
	cd backend && python3 -m pytest -q

run:
	cd backend && python3 manage.py runserver

shell:
	cd backend && python3 manage.py shell
