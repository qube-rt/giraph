.PHONY: sync lint format test check fix build examples clean

sync:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check

format:
	uv run ruff format .

test:
	uv run pytest

check: lint test

build:
	uv build

examples:
	uv run python scripts/generate_examples.py

fix:
	uv run ruff check --fix .
	uv run ruff format .

clean:
	rm -rf .pytest_cache .ruff_cache dist build
	fd -t d -I -E .venv __pycache__ -x rm -rf
