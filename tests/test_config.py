from __future__ import annotations

from cursor_tg_connector.config import Settings


def test_github_default_merge_method_defaults_to_merge(tmp_path) -> None:
    settings = Settings.model_validate(
        {
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_ALLOWED_USER_ID": 1234,
            "CURSOR_API_KEY": "cursor-key",
            "SQLITE_PATH": str(tmp_path / "connector.db"),
            "POLL_INTERVAL_SECONDS": 1,
            "FOLLOWUP_POLL_INTERVAL_SECONDS": 0.01,
            "FOLLOWUP_POLL_TIMEOUT_SECONDS": 0.05,
        }
    )

    assert settings.github_default_merge_method == "merge"
