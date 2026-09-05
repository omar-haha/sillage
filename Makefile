.PHONY: help install fmt lint type test check clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## create the venv and install everything
	uv sync

fmt:      ## auto-format and auto-fix
	uv run ruff format src tests
	uv run ruff check --fix src tests

lint:     ## lint without changing anything
	uv run ruff format --check src tests
	uv run ruff check src tests

type:     ## type-check (strict on core/engine/portfolio/risk)
	uv run mypy

test:     ## run the test suite
	uv run pytest

check: lint type test  ## everything CI runs

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
