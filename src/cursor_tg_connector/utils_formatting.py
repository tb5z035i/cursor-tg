from __future__ import annotations

import html
import re
from collections.abc import Iterable

from cursor_tg_connector.cursor_api_models import Agent, Repository
from cursor_tg_connector.domain_types import AgentListItem
from cursor_tg_connector.github_api_models import GitHubPullRequest

TELEGRAM_MESSAGE_LIMIT = 4000

_PLACEHOLDER_CODEBLOCK = "\x00CB"
_PLACEHOLDER_INLINE = "\x00IC"
_PLACEHOLDER_LINK = "\x00LN"


def markdown_to_telegram_html(text: str) -> str:
    code_blocks: list[str] = []
    inline_codes: list[str] = []
    links: list[tuple[str, str]] = []

    def _save_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(1) or match.group(2))
        return f"{_PLACEHOLDER_CODEBLOCK}{len(code_blocks) - 1}\x00"

    def _save_inline_code(match: re.Match) -> str:
        inline_codes.append(match.group(1))
        return f"{_PLACEHOLDER_INLINE}{len(inline_codes) - 1}\x00"

    def _save_link(match: re.Match) -> str:
        links.append((match.group(1), match.group(2)))
        return f"{_PLACEHOLDER_LINK}{len(links) - 1}\x00"

    text = re.sub(r"```\w*\n(.*?)```", _save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"```(.*?)```", _save_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", _save_inline_code, text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", _save_link, text)

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
    for i, (label, url) in enumerate(links):
        escaped_label = html.escape(label)
        escaped_url = html.escape(url)
        text = text.replace(
            f"{_PLACEHOLDER_LINK}{i}\x00",
            f'<a href="{escaped_url}">{escaped_label}</a>',
        )
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
    parts = [agent.name.strip() or agent.id, agent.status, repo_name, branch, unread_suffix]
    return " · ".join(parts)


def build_agent_notice(agent: Agent, unread_count: int, *, threaded: bool = False) -> str:
    action = (
        "Tap below or use /agents to create or open its thread."
        if threaded
        else "Tap below or use /focus to switch."
    )
    return f"> **{agent.name or agent.id}**\n{unread_count} unread message(s). {action}"


def build_active_agent_message(agent: Agent, text: str) -> str:
    return f"> **{agent.name or agent.id}**\n{text}".strip()


def build_user_history_message(text: str) -> str:
    return f"> _You_\n{text}".strip()


def build_agent_thread_name(agent: Agent) -> str:
    repo_name = shorten_repository_name(agent.source.repository).split("/")[-1]
    branch = (agent.source.ref or "unknown")[:32]
    title = f"{agent.name or agent.id} · {repo_name} · {branch}"
    return title[:128]


def build_agent_info_message(
    agent: Agent,
    pull_request: GitHubPullRequest | None = None,
) -> str:
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
    if pull_request is not None:
        lines.extend(
            [
                f"PR status: {describe_pull_request_state(pull_request)}",
                f"PR title: {pull_request.title}",
            ]
        )
        if pull_request.mergeable_state:
            lines.append(f"PR mergeability: {pull_request.mergeable_state}")
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


def build_thread_mode_status(enabled: bool) -> str:
    status = "enabled" if enabled else "disabled"
    next_step = (
        "Use /agents in the root chat to create or open an agent thread."
        if enabled
        else "The bot will use the legacy single-active-agent chat flow."
    )
    return f"Thread mode is {status}.\n\n{next_step}"


def build_thread_mode_guidance() -> str:
    return (
        "Thread mode is on. Use /agents in the root chat to create or open an agent thread, "
        "then continue the conversation there."
    )


def build_thread_command_guidance() -> str:
    return (
        "This command only works inside a bound agent thread while thread mode is enabled. "
        "Use /agents in the root chat to create or open the correct thread."
    )


def build_thread_opened_message(agent: Agent, created: bool) -> str:
    action = "Created" if created else "Opened"
    return f"{action} thread for {agent.name or agent.id}. Continue in that thread."


def build_thread_ready_message(agent: Agent) -> str:
    return f"Thread ready for **{agent.name or agent.id}**. Send follow-ups here."


def build_reset_db_prompt() -> str:
    return (
        "Reset the local SQLite state?\n\n"
        "This clears session state, thread bindings, unread notices, delivery "
        "cursors, and any in-progress wizard."
    )


def build_reset_db_success() -> str:
    return "Local DB state reset and reinitialized."


def build_reset_db_cancelled() -> str:
    return "DB reset cancelled. No changes were made."


def describe_pull_request_state(pull_request: GitHubPullRequest) -> str:
    if pull_request.merged:
        return "merged"
    if pull_request.state.lower() != "open":
        return pull_request.state.lower()
    if pull_request.draft:
        return "draft"
    return "ready for review"


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


def build_agents_summary_message(items: Iterable[AgentListItem]) -> str:
    agent_items = list(items)
    if not agent_items:
        return "Agents\n(none)"

    table = format_text_table(
        ["Active", "Name", "Status", "Repository", "Branch", "Unread"],
        [
            [
                "yes" if item.is_active else "",
                item.name,
                item.status,
                item.repository,
                item.branch,
                str(item.unread_count),
            ]
            for item in agent_items
        ],
        max_widths=[6, 24, 10, 24, 18, 6],
    )
    return (
        "Agents\n"
        "<pre>"
        f"{html.escape(table)}"
        "</pre>\n"
        "Use /focus to switch the active agent."
    )


def format_text_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    max_widths: list[int] | None = None,
) -> str:
    normalized_rows = [[_normalize_table_cell(cell) for cell in row] for row in rows]
    normalized_headers = [_normalize_table_cell(header) for header in headers]
    widths: list[int] = []
    for index, header in enumerate(normalized_headers):
        values = [header, *[row[index] for row in normalized_rows]]
        width = max(len(value) for value in values)
        if max_widths is not None:
            width = min(width, max_widths[index])
        widths.append(width)

    rendered_headers = [
        _truncate_table_cell(header, widths[index]).ljust(widths[index])
        for index, header in enumerate(normalized_headers)
    ]
    rendered_rows = [
        " | ".join(
            _truncate_table_cell(cell, widths[index]).ljust(widths[index])
            for index, cell in enumerate(row)
        )
        for row in normalized_rows
    ]
    separator = "-+-".join("-" * width for width in widths)
    return "\n".join(
        [
            " | ".join(rendered_headers),
            separator,
            *rendered_rows,
        ]
    )


def _normalize_table_cell(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed or "-"


def _truncate_table_cell(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return f"{value[: width - 3]}..."
