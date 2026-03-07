# Cursor Cloud Telegram Connector

A single-process Python service that bridges a Telegram bot with the [Cursor Cloud Agents API](https://cursor.com/docs/cloud-agent/api/overview), letting you create agents, receive their responses, and send follow-ups — all from Telegram.

## Prerequisites

| Requirement | Where to get it |
|---|---|
| **Python 3.12+** | [python.org](https://www.python.org/downloads/) or your system package manager |
| **Telegram Bot Token** | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram (see [step-by-step below](#1-create-a-telegram-bot)) |
| **Your Telegram User ID** | Send `/start` to [@userinfobot](https://t.me/userinfobot) — the numeric ID it returns is your user ID |
| **Cursor API Key** | Generate one at [Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations) |

## Setup guide

### 1. Create a Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts to choose a name and username.
3. BotFather will reply with a **token** like `123456:ABC-DEF...`. Save this — it is your `TELEGRAM_BOT_TOKEN`.

### 2. Get your Telegram user ID

Send `/start` to [@userinfobot](https://t.me/userinfobot). It replies with your numeric user ID. Save this as `TELEGRAM_ALLOWED_USER_ID`. Only this user will be allowed to interact with the bot.

### 3. Create a Cursor API key

1. Go to [cursor.com/dashboard/integrations](https://cursor.com/dashboard/integrations).
2. Create a new Cloud Agent API key.
3. Copy the key — it is your `CURSOR_API_KEY`.

### 4. Configure the service

```bash
cp .env.example .env
```

Open `.env` and fill in the three required values:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_ALLOWED_USER_ID=987654321
CURSOR_API_KEY=cur_...
```

All other settings have sensible defaults. See [Configuration reference](#configuration-reference) for details.

By default the service reads `.env` from the working directory. To load from a different path (useful for container deployments), use either:

```bash
# CLI argument
python -m cursor_tg_connector --env-file /path/to/secrets.env

# or environment variable
ENV_FILE=/path/to/secrets.env python -m cursor_tg_connector
```

Environment variables always take precedence over values in the env file.

### 5. Run

**Local:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m cursor_tg_connector
```

**Docker:**

```bash
docker build -t cursor-tg-connector .
docker run --rm --env-file .env -v "$(pwd)/data:/data" cursor-tg-connector
```

The SQLite database defaults to `/data/connector.db`. Mount `/data` to persistent storage so state survives container restarts.

## Bot commands

| Command | Description |
|---|---|
| `/current` | Show info about the active agent (name, status, repo, branches, PR link) |
| `/agents` | List running and finished agents; tap one to select it, or create/open its thread when thread mode is enabled |
| `/stop` | Stop the currently selected running agent and clear the active selection |
| `/clear` | Mark all unread messages as read for the active agent |
| `/threadmode` | Show status or toggle per-agent Telegram thread routing with `/threadmode on|off|status` |
| `/newagent` | Create a new agent with a 4-step wizard (model → repo → branch → prompt) |
| `/cancel` | Abort the in-progress `/newagent` wizard |
| `/resetdb` | Show a confirmation prompt before wiping and reinitializing local SQLite state |
| `/help` | Show available commands |

Any other text message is forwarded as a follow-up to the active agent. When thread mode is
enabled, follow-ups must be sent from the bound agent thread.

## How it works

- The service polls the Cursor API every 10 seconds (configurable) for running agents.
- **Active agent**: unread assistant messages are delivered as Telegram messages with Markdown rendering, up to 10 per poll cycle.
- **Other agents**: a summary notice is sent when new unread messages appear (e.g. "3 unread message(s)").
- When you use `/agents`, unread messages are delivered immediately.
- Messages from Cursor agents are converted from Markdown to Telegram HTML (bold, italic, code blocks, blockquotes, lists).
- Follow-up messages you send are forwarded to the Cursor agent; the service polls for a response for up to 3 minutes.
- All state (active agent, delivery cursors, wizard progress) is stored in a local SQLite database.

## Threaded mode

Use `/threadmode on` if you want one Telegram thread/topic per Cursor agent.

- In threaded mode, `/agents` creates or reopens the selected agent's Telegram thread.
- Bound agents receive their unread assistant messages directly inside their own thread.
- Agents without a bound thread still produce lightweight unread notices in the root chat.
- Follow-ups must be sent from the correct bound thread.
- `/current`, `/clear`, and `/stop` only work inside a bound thread while thread mode is enabled.
- `/newagent` must be started from the root chat, not from inside an existing agent thread.

Use `/threadmode off` to return to the legacy single-active-agent chat flow. Existing thread
bindings are preserved.

## Resetting local state

Use `/resetdb` to wipe the bot's local SQLite state and recreate the schema. The bot will ask
for inline-button confirmation before doing anything destructive.

This clears local session data, wizard state, unread notices, delivery cursors, and stored
agent/thread bindings. It does not stop or delete Cursor agents in Cursor Cloud.

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *required* | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | *required* | Your Telegram numeric user ID |
| `CURSOR_API_KEY` | *required* | Cursor Cloud API key |
| `TELEGRAM_CHAT_ID` | auto-detected | Override the chat ID; normally discovered from your first message to the bot |
| `CURSOR_API_BASE_URL` | `https://api.cursor.com` | Cursor API base URL |
| `CURSOR_API_MAX_RETRIES` | `3` | Max retries on transient API errors (429, 5xx) |
| `CURSOR_API_RETRY_BACKOFF_SECONDS` | `1` | Base backoff between retries (doubled each attempt) |
| `SQLITE_PATH` | `/data/connector.db` | Path to the SQLite database file |
| `POLL_INTERVAL_SECONDS` | `10` | Seconds between background polling cycles |
| `FOLLOWUP_POLL_INTERVAL_SECONDS` | `5` | Seconds between checks for agent response after a follow-up |
| `FOLLOWUP_POLL_TIMEOUT_SECONDS` | `180` | Max seconds to wait for an agent response inline |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run tests (fully offline — all HTTP calls are mocked):

```bash
pytest
ruff check .
```

## Docker deployment

**Option A — pass env vars directly (works with `docker run` and ECI):**

```bash
docker build -t cursor-tg-connector .
docker run -d \
  --name cursor-tg \
  --restart unless-stopped \
  --env-file .env \
  -v /path/to/persistent/data:/data \
  cursor-tg-connector
```

**Option B — mount an env file inside the container (useful for ECI / Kubernetes):**

```bash
docker run -d \
  --name cursor-tg \
  --restart unless-stopped \
  -v /path/to/persistent/data:/data \
  -v /path/to/secrets.env:/data/.env:ro \
  -e ENV_FILE=/data/.env \
  cursor-tg-connector
```

- Mount `/data` to persistent storage so the SQLite database survives restarts.
- Keep to a **single replica** — SQLite is local and not shared.
- Store credentials as environment variables or container secrets.

## Architecture

```
Telegram ←→ python-telegram-bot ←→ Service layer ←→ Cursor Cloud API
                                        ↕
                                  SQLite (aiosqlite)
```

Single async Python process. No external database servers. Key components:

- **PollingService** — periodic background job that fetches agent conversations and delivers new messages
- **FollowupService** — sends user text to the Cursor agent and waits inline for a response
- **CreateAgentService** — multi-step wizard for creating new agents
- **AgentService** — conversation snapshots, unread tracking via delivery cursors
- **TelegramNotifier** — converts Markdown to Telegram HTML, sends with plain-text fallback
