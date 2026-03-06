# Agents

## Cursor Cloud specific instructions

### Overview

This is a Python 3.12 async service (`cursor-tg-connector`) that bridges a Telegram bot with the Cursor Cloud Agents API. It is a single-process application with no external database servers — only an embedded SQLite file via `aiosqlite`.

### Development commands

See `README.md` for canonical setup. Quick reference:

- **Install**: `source .venv/bin/activate && pip install -e ".[dev]"`
- **Lint**: `ruff check .`
- **Test**: `pytest` (runs fully offline — all HTTP calls are mocked via `respx`)
- **Run**: `python -m cursor_tg_connector` (requires `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and `CURSOR_API_KEY` env vars or `.env` file)

### Non-obvious caveats

- The app validates the Cursor API key on startup by calling `GET /v0/me`. It will exit immediately if the key is invalid. For local testing without real credentials, rely on `pytest` which mocks all external calls.
- `pydantic-settings` reads from a `.env` file by default. Do not commit a `.env` file — use `.env.example` as a template.
- `pytest` is configured with `asyncio_mode = "auto"` in `pyproject.toml`, so all async test functions run automatically without explicit `@pytest.mark.asyncio`.
- The venv must be activated before running any commands: `source .venv/bin/activate`.
- System dependency `python3.12-venv` is required on Ubuntu if not already present (`sudo apt-get install -y python3.12-venv`).
