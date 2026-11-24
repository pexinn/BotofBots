"""Async HTTP client wrapper for Telegram Official Bot API."""

from __future__ import annotations

from typing import Any
import httpx


class TelegramBotAPI:
    """Thin async wrapper around Telegram Bot HTTP API using httpx."""

    def __init__(self, token: str, timeout: float = 40.0) -> None:
        self.token = token.strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        resp = await self._client.post(f"{self.base_url}/sendMessage", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram Bot API error: {data}")
        return data

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 25,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            params["offset"] = offset
        resp = await self._client.get(f"{self.base_url}/getUpdates", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return []
        return list(data.get("result") or [])

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        resp = await self._client.post(
            f"{self.base_url}/setMyCommands",
            json={"commands": commands},
        )
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("ok"))

    async def close(self) -> None:
        await self._client.aclose()
