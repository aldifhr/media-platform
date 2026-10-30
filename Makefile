.PHONY: help install lint format test smoke ci

help:
	@echo "targets: install lint format test smoke ci"

install:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run pytest

smoke:
	uv run pytest tests/test_smoke.py -q

ci: lint test
