"""Tests for Telegram Bot API CommandHandler (Admin vs Multi-User authorization and flows)."""

import pytest

from app.commands import CommandHandler
from app.config import Settings
from app.database import Database
from app.keywords_sync import KeywordsSyncManager


def _make_settings(tmp_path) -> Settings:
    return Settings(
        api_id=123,
        api_hash="hash",
        string_session="sess",
        alert_destination=-100999,
        bot_token="token",
        admin_user_id=904378967,
        max_terms_per_user=2,
        dedup_window_minutes=30,
        require_price_for_cap=True,
        web_enabled=False,
        web_host="127.0.0.1",
        web_port=8086,
        web_password="",
        db_path=str(tmp_path / "deals.db"),
        keywords_file=str(tmp_path / "keywords.txt"),
        keywords_example_file=str(tmp_path / "keywords.example.txt"),
        channel_hardware=None,
        channel_perifericos=None,
        channel_games=None,
    )


def _update(user_id: int, text: str, username: str = "tester") -> dict:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "username": username, "first_name": "Tester"},
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_admin_and_multiuser_commands(tmp_path):
    cfg = _make_settings(tmp_path)
    db = Database(cfg.db_path)
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    handler = CommandHandler(db, sync, cfg, get_monitored_chats_count=lambda: 12)

    replies: list[tuple[int, str]] = []

    async def fake_reply(chat_id: int, text: str) -> None:
        replies.append((chat_id, text))

    # Non-admin tries /add -> denied
    await handler.handle_update(_update(555, "/add ssd nvme 1tb"), fake_reply)
    assert "Acesso negado" in replies[-1][1]

    # Non-admin uses /alerta -> allowed up to max_terms_per_user=2
    await handler.handle_update(_update(555, "/alerta rtx 4060 <= 1800"), fake_reply)
    assert "Alerta pessoal adicionado" in replies[-1][1]
    await handler.handle_update(_update(555, "/meusalertas"), fake_reply)
    assert "rtx 4060" in replies[-1][1]
    await handler.handle_update(_update(555, "/remover 1"), fake_reply)
    assert "Alerta removido" in replies[-1][1]

    # Admin uses /add, /block, /list, /pause, /status
    await handler.handle_update(_update(904378967, "/add ssd nvme 1tb -kootion <= 380", "admin"), fake_reply)
    assert "Termo adicionado" in replies[-1][1]
    assert "ssd nvme 1tb -kootion <= 380" in open(cfg.keywords_file, encoding="utf-8").read()

    await handler.handle_update(_update(904378967, "/block leilao", "admin"), fake_reply)
    assert "leilao" in db.list_blacklist()

    await handler.handle_update(_update(904378967, "/pause", "admin"), fake_reply)
    assert db.is_paused() is True

    await handler.handle_update(_update(904378967, "/status", "admin"), fake_reply)
    assert "Pausado" in replies[-1][1]
    assert "12" in replies[-1][1]

    await handler.handle_update(_update(904378967, "/del 1", "admin"), fake_reply)
    assert db.list_keywords() == []
    db.close()
