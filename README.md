# Cursor Cloud Telegram Connector

<table>
  <tr>
    <td width="58%" valign="top">
      <p>A single-process Python service that bridges a Telegram bot with the <a href="https://cursor.com/docs/cloud-agent/api/overview">Cursor Cloud Agents API</a>, so you can create agents, receive their responses, and send follow-ups directly from Telegram.</p>
      <p>Run Cursor Cloud workflows from chat, keep up with unread agent replies, and inspect or act on pull requests without leaving Telegram.</p>
    </td>
    <td width="42%" valign="top">
      <video src="./resources/demo.webm" controls muted playsinline></video>
      <p><a href="./resources/demo.webm">Open demo video</a></p>
    </td>
  </tr>
</table>

Issues or suggestions? Reach me on Telegram at [@tb5z035i](https://t.me/tb5z035i). <!-- pragma: allowlist secret -->

## Key functionalities

- Create and manage Cursor Cloud agents directly from Telegram.
- Send follow-up messages to the active agent and receive replies in chat.
- Monitor unread updates from multiple agents with configurable notification behavior.
- Inspect pull request status and diffs from Telegram.
- Mark pull requests ready for review or merge them when a GitHub token is configured.
- Route agent conversations into dedicated Telegram threads/topics when threaded mode is enabled.
- Persist agent state, unread tracking, and wizard progress locally with SQLite.

## Table of Contents

- [Key functionalities](#key-functionalities)
- [Setup Guide](#setup-guide)
- [Bot commands](#bot-commands)
- [Configuration Reference](#configuration-reference)
- [Q&A](#qa)
- [Development](#development)
- [Architecture](#architecture)
- [License](#license)

## Setup Guide

### Prerequisites

| Requirement | Where to get it |
|---|---|
| **Python 3.12+** | [python.org](https://www.python.org/downloads/) or your system package manager |
| **Telegram Bot Token** | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram (see [step-by-step below](#1-create-a-telegram-bot)) |
| **Your Telegram User ID** | Send `/start` to [@userinfobot](https://t.me/userinfobot) — the numeric ID it returns is your user ID |
| **Cursor API Key** | Generate one at [Cursor Dashboard → My User API Keys](https://cursor.com/cn/dashboard?tab=cloud-agents#my-user-api-keys) |
| **GitHub token** *(optional)* | Create one in [GitHub fine-grained PAT settings](https://github.com/settings/personal-access-tokens/new) if you want Telegram to inspect PR diffs, mark PRs ready for review, or merge them |

### 1. Create a Telegram bot

1. Open Telegram and search for [**@BotFather**](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to choose a name and username.
3. BotFather will reply with a token like `123456:ABC-DEF...`. Save this as `TELEGRAM_BOT_TOKEN`.

### 2. Get your Telegram user ID

Send `/start` to [@userinfobot](https://t.me/userinfobot). It replies with your numeric user ID. Save this as `TELEGRAM_ALLOWED_USER_ID`. Only this user will be allowed to interact with the bot.

### 3. Create a Cursor API key

1. Go to [Cursor Dashboard → My User API Keys](https://cursor.com/cn/dashboard?tab=cloud-agents#my-user-api-keys).
2. Create a new user API key.
3. Copy the key and save it as `CURSOR_API_KEY`.

### 4. Create an optional GitHub token for PR actions

You only need this if you want the bot to inspect PR diffs, mark PRs ready for review, or merge them from Telegram.

**Fine-grained PAT (recommended):**

1. Open GitHub → **Settings** → **Developer settings** → **Personal access tokens** → [**Fine-grained tokens**](https://github.com/settings/personal-access-tokens).
2. Click [**Generate new token**](https://github.com/settings/personal-access-tokens/new).
3. Restrict it to the repository (or org repositories) the Cursor agent works on.
4. Under **Repository permissions**, grant at least:
   - **Pull requests: Read and write** for PR status reads and the `/ready` action
   - **Contents: Read and write** for the `/merge` action
5. Copy the token and use it as `GITHUB_TOKEN` (or `GITHUB_PAT`).

**Classic PAT (alternative):**

1. Open GitHub → **Settings** → **Developer settings** → **Personal access tokens** → [**Tokens (classic)**](https://github.com/settings/tokens).
2. Generate a token with the `repo` scope from the [classic token creation page](https://github.com/settings/tokens/new).
3. Copy it into `GITHUB_TOKEN`.

### 5. Configure the service

```bash
cp .env.example .env
```

Open `.env` and start with this minimal copy-paste configuration:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_ALLOWED_USER_ID=987654321
CURSOR_API_KEY=cur_...
```

If you want PR actions from Telegram, also add:

```env
GITHUB_TOKEN=github_pat_...
GITHUB_DEFAULT_MERGE_METHOD=merge
```

See full [Configuration Reference](#configuration-reference) for details.

By default the service reads `.env` from the working directory. To load from a different path (useful for container deployments), use either:

```bash
# CLI argument
python -m cursor_tg_connector --env-file /path/to/secrets.env

# or environment variable
ENV_FILE=/path/to/secrets.env python -m cursor_tg_connector
```

Environment variables always take precedence over values in the env file.

### 6. Run

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
| `/history <count>` | Replay the last N conversation messages for the current agent, including prior user prompts |
| `/agents` | List running and finished agents; tap one to select it, or create/open its thread when thread mode is enabled |
| `/focus` | Show clickable agent options to choose the active agent |
| `/configure_unread` | Configure how unread messages from unselected agents are shown: `full`, `count`, or `none` |
| `/unfocus` | Clear the currently selected active agent |
| `/stop` | Stop the currently selected running agent and clear the active selection |
| `/clear` | Mark all unread messages as read for the active agent |
| `/close` | Close the current bound Telegram thread/topic in threaded mode without deleting the Cursor agent |
| `/threadmode` | Show status or toggle per-agent Telegram thread routing with `/threadmode on|off|status` (requires bot-level Threaded Mode in @BotFather) |
| `/newagent` | Create a new agent with a 4-step wizard (model → repo → branch → prompt) |
| `/pr` | Show the active agent PR status and action buttons |
| `/diff` | Show the active agent PR diff in a Telegram code block |
| `/ready` | Mark the active agent PR ready for review |
| `/merge` | Merge the active agent PR (or `/merge merge|squash|rebase`) |
| `/cancel` | Abort the in-progress `/newagent` wizard |
| `/resetdb` | Show a confirmation prompt before wiping and reinitializing local SQLite state |
| `/help` | Show available commands |

Any other text message is forwarded as a follow-up to the active agent. When thread mode is enabled, follow-ups must be sent from the bound agent thread.

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *required* | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | *required* | Your Telegram numeric user ID |
| `CURSOR_API_KEY` | *required* | Cursor Cloud API key |
| `GITHUB_TOKEN` / `GITHUB_PAT` | optional | GitHub token used for PR inspection and actions (`/pr`, `/diff`, `/ready`, `/merge`) |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | GitHub API base URL |
| `GITHUB_DEFAULT_MERGE_METHOD` | `merge` | Merge method used by the inline merge button (`merge`, `squash`, or `rebase`) |
| `TELEGRAM_CHAT_ID` | auto-detected | Override the chat ID; normally discovered from your first message to the bot |
| `CURSOR_API_BASE_URL` | `https://api.cursor.com` | Cursor API base URL |
| `CURSOR_API_MAX_RETRIES` | `3` | Max retries on transient API errors (429, 5xx) |
| `CURSOR_API_RETRY_BACKOFF_SECONDS` | `1` | Base backoff between retries (doubled each attempt) |
| `SQLITE_PATH` | `/data/connector.db` | Path to the SQLite database file |
| `POLL_INTERVAL_SECONDS` | `10` | Seconds between background polling cycles |
| `FOLLOWUP_POLL_INTERVAL_SECONDS` | `5` | Seconds between checks for agent response after a follow-up |
| `FOLLOWUP_POLL_TIMEOUT_SECONDS` | `180` | Max seconds to wait for an agent response inline |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Q&A

### Pull request actions

Cursor's public Cloud Agent API currently exposes the PR URL (`target.prUrl`), but it does not expose public endpoints to mark that PR ready for review or merge it. To cover that gap, this connector can optionally call the GitHub REST API directly when a GitHub token is configured.

When `GITHUB_TOKEN` (or `GITHUB_PAT`) is set:

- `/current` and `/pr` show the latest GitHub PR status.
- `/diff` shows the current PR diff in Telegram code blocks.
- `/pr` adds inline buttons for:
  - **Ready for review** (only while the PR is still draft)
  - **Merge (`GITHUB_DEFAULT_MERGE_METHOD`)**
  - **Refresh PR**
- `/ready` marks the current agent PR ready for review.
- `/merge [merge|squash|rebase]` merges the current agent PR.

Recommended GitHub token choices:

- **Fine-grained PAT** scoped to the target repository, with at least:
  - **Pull requests: Read and write** for PR status reads and ready-for-review actions
  - **Contents: Read and write** (needed for merging)
- **Classic PAT** with `repo` scope also works.

If no GitHub token is configured, the bot still shows the PR link, but PR state changes remain read-only.

### How it works

- The service polls the Cursor API every 10 seconds (configurable) for running agents.
- **Active agent**: unread assistant messages are delivered as Telegram messages with Markdown rendering, up to 10 per poll cycle.
- **Other agents**: unread behavior is configurable with `/configure_unread`:
  - `count` (default) sends a summary notice when new unread messages appear, with a button to switch to that agent.
  - `full` delivers unread assistant messages in full, like the active agent, with a switch button on the first message in the batch.
  - `none` suppresses notifications until you switch to that agent.
- Use `/focus` for the button-based active-agent picker, and `/agents` for a summarized read-only agent list.
- When you switch agents via `/focus` or a notice button, unread messages are delivered immediately.
- Messages from Cursor agents are converted from Markdown to Telegram HTML (bold, italic, code blocks, blockquotes, lists).
- Follow-up messages you send are forwarded to the Cursor agent; the service polls for a response for up to 3 minutes.
- All state (active agent, unread display preference, delivery cursors, wizard progress, and thread bindings) is stored in a local SQLite database.

### Threaded mode

If the bot itself has **Threaded Mode** enabled in
[@BotFather](https://t.me/BotFather) (Telegram `getMe.has_topics_enabled = true`),
the connector automatically turns on per-agent thread mode by default for new
sessions.

Use `/threadmode off` if you want to stay in the legacy single-chat flow, or
use `/threadmode on` later to turn per-agent Telegram threads/topics back on.

- In threaded mode, `/agents` becomes the button-based thread opener for agents.
- `/focus` remains the non-thread-mode active-agent picker.
- Bound agents receive their unread assistant messages directly inside their own thread.
- Agents without a bound thread still use the configured root-chat unread policy from `/configure_unread`.
- Notice buttons open/create the agent thread instead of switching the root-chat active selection.
- Follow-ups must be sent from the correct bound thread.
- `/current`, `/history`, `/clear`, `/stop`, and `/close` only work inside a bound thread while thread mode is enabled.
- `/close` closes the current Telegram topic and removes its local binding. It does not delete the Cursor agent.
- `/newagent` must be started from the root chat, not from inside an existing agent thread.

Existing thread bindings are preserved when you toggle thread mode off and back on.

### Resetting local state

Use `/resetdb` to wipe the bot's local SQLite state and recreate the schema. The bot will ask for inline-button confirmation before doing anything destructive.

This clears local session data, wizard state, unread notices, delivery cursors, and stored agent/thread bindings. It does not stop or delete Cursor agents in Cursor Cloud.

## Development

### Local development

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

### Docker deployment

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

### Docker image releases

GitHub Actions publishes Docker images to Docker Hub in two cases:

- Every push to `main` publishes:
  - `DOCKER_HUB_USER/cursor-tg:latest`

- Pushing a new Git tag whose commit is reachable from `main` publishes:
  - `DOCKER_HUB_USER/cursor-tg:<tag>`
  - `DOCKER_HUB_USER/cursor-tg:latest`

The workflow uses the `DOCKER_HUB_USER` and `DOCKER_HUB_PAT` GitHub secrets for authentication.

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

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE) for the full text.
