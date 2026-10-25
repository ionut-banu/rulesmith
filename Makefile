.PHONY: up down logs test test-integration test-all obs-up obs-down simulate-failures

# Integration tests run in their own compose project so they never touch the
# volumes (rulesmith_pgdata, rulesmith_uploads) of a developer's dev stack.
IT_PROJECT = rulesmith-it
IT_HEALTH_TIMEOUT = 90

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f app

test:
	uv run pytest

test-integration:
	@if uv run python -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', 8000)) == 0 else 1)"; then \
		echo "Port 8000 is already in use; refusing to test the wrong server. Free it (e.g. make down) and retry."; \
		exit 1; \
	fi
	@export COMPOSE_PROJECT_NAME=$(IT_PROJECT); \
	rc=0; \
	docker compose up --build -d || rc=$$?; \
	if [ $$rc -eq 0 ]; then \
		healthy=0; i=0; \
		while [ $$i -lt $(IT_HEALTH_TIMEOUT) ]; do \
			if curl -fs http://localhost:8000/health >/dev/null 2>&1; then healthy=1; break; fi; \
			i=$$((i+1)); sleep 1; \
		done; \
		if [ $$healthy -eq 1 ]; then \
			uv run pytest tests/integration || rc=$$?; \
		else \
			echo "App did not become healthy within $(IT_HEALTH_TIMEOUT)s; app logs:"; \
			docker compose logs app; \
			rc=1; \
		fi; \
	else \
		docker compose logs app; \
	fi; \
	docker compose down -v --remove-orphans; \
	exit $$rc

test-all: test test-integration

# Local Grafana/Tempo/Loki/Prometheus stack in its own Compose project
# (rulesmith-obs); see _docs/observability.md.
obs-up:
	docker compose -f observability/docker-compose.yaml up -d

obs-down:
	docker compose -f observability/docker-compose.yaml down

# Needs a local app started with RULESMITH_FAIL_UPLOADS=1; see
# scripts/simulate_failures.py and _docs/observability.md. Override the target
# with BASE_URL=http://host:port.
BASE_URL ?= http://localhost:8000
simulate-failures:
	uv run python scripts/simulate_failures.py --base-url $(BASE_URL)
