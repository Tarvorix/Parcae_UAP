UV ?= uv
.PHONY: setup fixtures demo test browser-test
setup:
	$(UV) sync --project backend --locked
	npm --prefix frontend ci
fixtures:
	$(UV) run --project backend --no-sync python scripts/create_test_videos.py
demo: fixtures
	npm --prefix frontend run build
	$(UV) run --project backend --no-sync uvicorn uap_observer.main:create_app --factory --host 127.0.0.1 --port 8000
test:
	$(UV) run --project backend --no-sync pytest -q tests/unit tests/integration
	$(UV) run --project backend --no-sync ruff check --config backend/pyproject.toml backend/src scripts tests
	$(UV) run --project backend --no-sync mypy --config-file backend/pyproject.toml backend/src
	npm --prefix frontend test
	npm --prefix frontend run build
browser-test:
	npm --prefix frontend run test:e2e
