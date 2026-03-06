from __future__ import annotations

from collections.abc import Iterable

from cursor_tg_connector.cursor_api_models import Agent, Repository

TELEGRAM_MESSAGE_LIMIT = 4000


def shorten_repository_name(repository_url: str) -> str:
    trimmed = repository_url.rstrip("/")
    if "github.com/" in trimmed:
        return trimmed.split("github.com/", 1)[1]
    return trimmed


def build_agent_label(agent: Agent, unread_count: int) -> str:
    repo_name = shorten_repository_name(agent.source.repository)
    branch = agent.source.ref or "unknown-branch"
    unread_suffix = f"unread:{unread_count}"
    parts = [agent.name.strip() or agent.id, repo_name, branch, unread_suffix]
    return " · ".join(parts)


def build_agent_notice(agent: Agent, unread_count: int) -> str:
    return (
        f"Agent {agent.name or agent.id} has {unread_count} unread message(s). "
        "Use /agents to switch."
    )


def build_active_agent_message(agent: Agent, text: str) -> str:
    header = f"[{agent.name or agent.id}]"
    return f"{header}\n{text}".strip()


def build_agent_created_message(agent: Agent) -> str:
    repo_name = shorten_repository_name(agent.source.repository)
    target_branch = agent.target.branch_name or "pending-branch"
    return (
        f"Created agent {agent.name or agent.id}\n"
        f"Repository: {repo_name}\n"
        f"Base branch: {agent.source.ref or 'unknown'}\n"
        f"Working branch: {target_branch}\n"
        f"Status: {agent.status}"
    )


def build_repository_label(repository: Repository) -> str:
    return f"{repository.owner}/{repository.name}"


def paginate(items: list[str], page: int, per_page: int) -> tuple[list[str], int, int]:
    total_pages = max((len(items) - 1) // per_page + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = start + per_page
    return items[start:end], page, total_pages


def chunk_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


def format_command_list(title: str, lines: Iterable[str]) -> str:
    items = list(lines)
    if not items:
        return f"{title}\n(none)"
    return f"{title}\n" + "\n".join(f"- {line}" for line in items)
