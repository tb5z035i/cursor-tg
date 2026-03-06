# Cursor Cloud Telegram Connector

Python service that bridges a Telegram bot with Cursor Cloud Agents.

## Features

- `/agents` lists running Cursor agents as inline buttons
- switching the active agent from Telegram
- unread message tracking by Cursor conversation message ID
- unread counts shown in `/agents`
- full unread contents sent only for the active agent
- non-active agents generate unread notices without leaking contents
- `/newagent` wizard:
  1. model selection from `GET /v0/models`
  2. repository selection from `GET /v0/repositories`
  3. base branch input
  4. prompt input
- `/cancel` aborts the create-agent wizard
- one-minute cooldown on starting `/newagent`
- Telegram access restricted to one allowed user ID
- Docker image for VPS / Aliyun ECI deployment

## Requirements

- Python 3.12+
- a Telegram bot token
- a Cursor Cloud API key

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

Important settings:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_ID`
- `CURSOR_API_KEY`
- `CURSOR_API_MAX_RETRIES`
- `CURSOR_API_RETRY_BACKOFF_SECONDS`
- `SQLITE_PATH`
- `POLL_INTERVAL_SECONDS`
- `FOLLOWUP_POLL_INTERVAL_SECONDS`
- `FOLLOWUP_POLL_TIMEOUT_SECONDS`

`TELEGRAM_CHAT_ID` is optional. If omitted, the connector will use the private chat ID discovered from your first authorized interaction with the bot.

The Cursor API client retries transient network failures, HTTP `429`, and HTTP `5xx` responses with exponential backoff.

## Local development

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

Run:

```bash
python -m cursor_tg_connector
```

## Tests

```bash
pytest
ruff check .
```

## Docker

Build:

```bash
docker build -t cursor-tg-connector .
```

Run:

```bash
docker run --rm \
  --env-file .env \
  -v "$(pwd)/data:/data" \
  cursor-tg-connector
```

The SQLite database path defaults to `/data/connector.db`, so mount `/data` to persistent storage in Aliyun ECI.

## Aliyun ECI deployment notes

- Use the provided Dockerfile as the container image build source.
- Mount persistent storage to `/data` so the SQLite database survives restarts.
- Store Telegram/Cursor credentials as container environment variables or secrets.
- Keep the deployment on a single replica, since SQLite is local and not shared.

## Telegram behavior summary

- `/agents`
  - shows running agents
  - shows unread counts
  - lets you switch the active agent
- active agent
  - unread assistant messages are sent with contents
  - capped to 10 messages per polling cycle
- non-active agents
  - only unread notices are sent
- free-text messages
  - if wizard is active, they are interpreted as wizard input
  - otherwise, they are sent as follow-ups to the active agent
