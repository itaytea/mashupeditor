.PHONY: install lint test typecheck frontend

install:
	cd backend && pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-dev.txt
	pip install ruff mypy
	cd frontend && npm ci

lint:
	cd backend && ruff check .

test:
	cd backend && python -m pytest -m "not slow" --cov --cov-report=term-missing

typecheck:
	cd backend && mypy

frontend:
	cd frontend && npm run lint && npm test && npm run build
