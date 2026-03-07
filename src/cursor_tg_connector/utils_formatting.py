from __future__ import annotations

import html
import re
from collections.abc import Iterable

from cursor_tg_connector.cursor_api_models import Agent, Repository

TELEGRAM_MESSAGE_LIMIT = 4000

_PLACEHOLDER_CODEBLOCK = "\x00CB"
_PLACEHOLDER_INLINE = "\x00IC"


def markdown_to_telegram_html(text: str) -> str:
    code_blocks: list[str] = []
    inline_codes: list[str] = []

    def _save_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(1) or match.group(2))
        return f"{_PLACEHOLDER_CODEBLOCK}{len(code_blocks) - 1}\x00"

    def _save_inline_code(match: re.Match) -> str:
        inline_codes.append(match.group(1))
        return f"{_PLACEHOLDER_INLINE}{len(inline_codes) - 1}\x00"

    text = re.sub(r"```\w*\n(.*?)```", _save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"```(.*?)```", _save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", _save_inline_code, text)

    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    def _blockquote(match: re.Match) -> str:
        content = re.sub(r"^&gt;\s?", "", match.group(0), flags=re.MULTILINE)
        return f"<blockquote>{content.strip()}</blockquote>"

    text = re.sub(
        r"^&gt;\s?.+(?:\n&gt;\s?.+)*", _blockquote, text, flags=re.MULTILINE
    )

    for i, code in enumerate(code_blocks):
        escaped = html.escape(code.strip())
        text = text.replace(f"{_PLACEHOLDER_CODEBLOCK}{i}\x00", f"<pre>{escaped}</pre>")
    for i, code in enumerate(inline_codes):
        escaped = html.escape(code)
        text = text.replace(f"{_PLACEHOLDER_INLINE}{i}\x00", f"<code>{escaped}</code>")
    return text.strip()


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
        f"> **{agent.name or agent.id}**\n"
        f"{unread_count} unread message(s). Tap below or use /focus to switch."
    )


def build_active_agent_message(agent: Agent, text: str) -> str:
    return f"> **{agent.name or agent.id}**\n{text}".strip()


def build_agent_info_message(agent: Agent) -> str:
    repo_name = shorten_repository_name(agent.source.repository)
    target_branch = agent.target.branch_name or "—"
    pr_url = agent.target.pr_url or "—"
    lines = [
        f"> **{agent.name or agent.id}**",
        f"Status: {agent.status}",
        f"Repository: {repo_name}",
        f"Base branch: {agent.source.ref or 'unknown'}",
        f"Working branch: {target_branch}",
        f"PR: {pr_url}",
        f"URL: {agent.target.url}",
    ]
    if agent.summary:
        lines.append(f"\n{agent.summary}")
    return "\n".join(lines)


def build_agent_created_message(agent: Agent) -> str:
    repo_name = shorten_repository_name(agent.source.repository)
    target_branch = agent.target.branch_name or "pending-branch"
    return (
        f"> **{agent.name or agent.id}** — created\n"
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
