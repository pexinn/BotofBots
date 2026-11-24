"""Configuration loader for Bot of Bots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _parse_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _parse_destination(value: str | None, default: int | str | None = None) -> int | str | None:
    if value is None or not value.strip():
        return default
    raw = value.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


@dataclass
class Settings:
    """Runtime configuration settings loaded from environment variables."""

    api_id: int
    api_hash: str
    string_session: str
    alert_destination: int | str
    bot_token: str
    admin_user_id: int | None
    max_terms_per_user: int
    dedup_window_minutes: int
    require_price_for_cap: bool
    web_enabled: bool
    web_host: str
    web_port: int
    web_password: str
    db_path: str
    keywords_file: str
    keywords_example_file: str
    channel_hardware: int | str | None
    channel_perifericos: int | str | None
    channel_games: int | str | None


def load_settings(require_telegram: bool = True) -> Settings:
    """Load settings from `.env` and OS environment variables."""
    load_dotenv()

    api_id_raw = (os.getenv("API_ID") or "").strip()
    api_hash = (os.getenv("API_HASH") or "").strip()
    string_session = (os.getenv("TELEGRAM_STRING_SESSION") or "").strip()

    if require_telegram and (not api_id_raw or not api_hash or not string_session):
        raise ValueError(
            "Credenciais ausentes! Certifique-se de configurar API_ID, "
            "API_HASH e TELEGRAM_STRING_SESSION no seu arquivo .env"
        )

    api_id = _parse_int(api_id_raw, 0) or 0
    alert_destination = _parse_destination(os.getenv("ALERT_DESTINATION"), "me")
    if alert_destination is None:
        alert_destination = "me"

    return Settings(
        api_id=api_id,
        api_hash=api_hash,
        string_session=string_session,
        alert_destination=alert_destination,
        bot_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
        admin_user_id=_parse_int(os.getenv("ADMIN_USER_ID"), None),
        max_terms_per_user=_parse_int(os.getenv("MAX_TERMS_PER_USER"), 5) or 5,
        dedup_window_minutes=_parse_int(os.getenv("DEDUP_WINDOW_MINUTES"), 30) or 30,
        require_price_for_cap=_parse_bool(os.getenv("REQUIRE_PRICE_FOR_CAP"), True),
        web_enabled=_parse_bool(os.getenv("WEB_ENABLED"), True),
        web_host=(os.getenv("WEB_HOST") or "0.0.0.0").strip(),
        web_port=_parse_int(os.getenv("WEB_PORT"), 8086) or 8086,
        web_password=(os.getenv("WEB_PASSWORD") or "").strip(),
        db_path=(os.getenv("DB_PATH") or "data/deals.db").strip(),
        keywords_file=(os.getenv("KEYWORDS_FILE") or "keywords.txt").strip(),
        keywords_example_file=(os.getenv("KEYWORDS_EXAMPLE_FILE") or "keywords.example.txt").strip(),
        channel_hardware=_parse_destination(os.getenv("CHANNEL_HARDWARE"), None),
        channel_perifericos=_parse_destination(os.getenv("CHANNEL_PERIFERICOS"), None),
        channel_games=_parse_destination(os.getenv("CHANNEL_GAMES"), None),
    )
