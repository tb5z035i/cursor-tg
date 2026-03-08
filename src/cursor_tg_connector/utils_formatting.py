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
        f"<blockquote><b>{html.escape(agent.name or agent.id)}</b></blockquote>",
        f"Status: {html.escape(agent.status)}",
        f"Repository: {html.escape(repo_name)}",
        f"Base branch: {html.escape(agent.source.ref or 'unknown')}",
        f"Working branch: {html.escape(target_branch)}",
        f"PR: {_html_link_or_text(agent.target.pr_url, pr_url)}",
        f"URL: {_html_link_or_text(agent.target.url, agent.target.url)}",
    ]
    if pull_request is not None:
        lines.extend(
            [
                f"PR status: {html.escape(describe_pull_request_state(pull_request))}",
                f"PR title: {html.escape(pull_request.title)}",
            ]
        )
        if pull_request.mergeable_state:
            lines.append(f"PR mergeability: {html.escape(pull_request.mergeable_state)}")
    if agent.summary:
        lines.append(f"\n{markdown_to_telegram_html(agent.summary)}")
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


def build_pull_request_diff_messages(
    pull_request: GitHubPullRequest,
    diff_text: str,
) -> list[str]:
    escaped_url = html.escape(pull_request.html_url, quote=True)
    escaped_title = html.escape(pull_request.title)
    first_prefix = (
        f'PR diff for <a href="{escaped_url}">#{pull_request.number}</a>: '
        f"{escaped_title}\n<pre>"
    )
    continuation_prefix = "PR diff (continued):\n<pre>"
    suffix = "</pre>"

    normalized_diff = diff_text.rstrip("\n") or "(empty diff)"
    inner_limit = max(
        1,
        min(
            TELEGRAM_MESSAGE_LIMIT - len(first_prefix) - len(suffix),
            TELEGRAM_MESSAGE_LIMIT - len(continuation_prefix) - len(suffix),
        ),
    )
    chunks = _split_preformatted_text_chunks(normalized_diff, inner_limit)

    messages: list[str] = []
    for index, chunk in enumerate(chunks):
        prefix = first_prefix if index == 0 else continuation_prefix
        messages.append(f"{prefix}{html.escape(chunk)}</pre>")
    return messages


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


def _split_preformatted_text_chunks(text: str, max_escaped_len: int) -> list[str]:
    chunks: list[str] = []
    current_parts: list[str] = []
    current_escaped_len = 0

    for line in text.splitlines(keepends=True) or [""]:
        for piece in _split_text_by_escaped_length(line, max_escaped_len):
            piece_escaped_len = len(html.escape(piece))
            if current_parts and current_escaped_len + piece_escaped_len > max_escaped_len:
                chunks.append("".join(current_parts).rstrip("\n"))
                current_parts = [piece]
                current_escaped_len = piece_escaped_len
                continue
            current_parts.append(piece)
            current_escaped_len += piece_escaped_len

    if current_parts or not chunks:
        chunks.append("".join(current_parts).rstrip("\n"))
    return chunks


def _split_text_by_escaped_length(text: str, max_escaped_len: int) -> list[str]:
    pieces: list[str] = []
    current_chars: list[str] = []
    current_escaped_len = 0

    for char in text:
        char_escaped_len = len(html.escape(char))
        if current_chars and current_escaped_len + char_escaped_len > max_escaped_len:
            pieces.append("".join(current_chars))
            current_chars = [char]
            current_escaped_len = char_escaped_len
            continue
        current_chars.append(char)
        current_escaped_len += char_escaped_len

    if current_chars or not pieces:
        pieces.append("".join(current_chars))
    return pieces


def format_command_list(title: str, lines: Iterable[str]) -> str:
    items = list(lines)
    if not items:
        return f"{title}\n(none)"
    return f"{title}\n" + "\n".join(f"- {line}" for line in items)


def build_agents_summary_message(
    items: Iterable[AgentListItem],
    *,
    threaded: bool,
) -> str:
    agent_items = list(items)
    if not agent_items:
        return "Agents\n(none)"

    lines = ["Agents", ""]
    for item in agent_items:
        lines.extend(_build_agent_summary_lines(item, threaded=threaded))
        lines.append("")
    lines.pop()
    lines.append(
        "Tap a button below to create or open a thread."
        if threaded
        else "Use /focus to switch the active agent."
    )
    return "\n".join(lines)


def _build_agent_summary_lines(item: AgentListItem, *, threaded: bool) -> list[str]:
    lines = [
        f"• <b>{html.escape(item.name)}</b>",
        f"  ◦ Status: {html.escape(item.status)}",
        f"  ◦ Unread messages: {item.unread_count}",
        f"  ◦ Repository: {html.escape(item.repository)}",
        f"  ◦ Branch: {html.escape(item.branch)}",
    ]
    if not threaded:
        lines.append(f"  ◦ Active: {'yes' if item.is_active else 'no'}")
    return lines


def _html_link_or_text(url: str | None, fallback_text: str) -> str:
    if not url:
        return html.escape(fallback_text)
    escaped_url = html.escape(url, quote=True)
    escaped_label = html.escape(fallback_text)
    return f'<a href="{escaped_url}">{escaped_label}</a>'
