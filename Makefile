.PHONY: setup web check docs-check test serve tui tmux clean
setup:
	uv sync --frozen
	cd web && npm ci
	$(MAKE) web

web:
	cd web && npm run build
	uv run python scripts/build_web.py

docs-check:
	uv run python scripts/check_docs.py

check: docs-check
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run mypy src/cvals
	cd web && npm run typecheck
	cd web && npm test -- --run

test:
	uv run pytest

serve:
	uv run everval serve

clean:
	uv run python -c 'import shutil; shutil.rmtree("web/dist", ignore_errors=True); shutil.rmtree("src/cvals/static", ignore_errors=True)'

tui:
	uv run everval tui

tmux:
	uv run everval tmux
