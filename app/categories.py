"""Thematic category definitions and channel matching helpers."""

from __future__ import annotations

import re
from typing import Any

from app.rules import strip_accents

DEFAULT_THEMATIC_CATEGORIES: list[dict[str, Any]] = [
    {
        "slug": "hardware",
        "name": "Hardware & PC",
        "keywords": [
            "ssd",
            "nvme",
            "rtx",
            "rx",
            "gtx",
            "ryzen",
            "intel",
            "placa de video",
            "placa mae",
            "memoria ram",
            "ddr4",
            "ddr5",
            "fonte",
            "gabinete",
            "water cooler",
            "air cooler",
            "processador",
            "hd",
        ],
    },
    {
        "slug": "perifericos",
        "name": "Periféricos",
        "keywords": [
            "mouse",
            "teclado",
            "headset",
            "monitor",
            "webcam",
            "microfone",
            "mousepad",
            "cadeira gamer",
            "144hz",
            "165hz",
            "240hz",
            "oled",
            "fone",
        ],
    },
    {
        "slug": "games",
        "name": "Games & Consoles",
        "keywords": [
            "ps5",
            "playstation",
            "xbox",
            "nintendo",
            "switch",
            "steam deck",
            "rog ally",
            "dualsense",
            "jogo",
            "game pass",
            "midia fisica",
            "console",
        ],
    },
]


def _matches_any_keyword(text: str, keywords: list[str]) -> bool:
    if not text or not keywords:
        return False
    norm_text = strip_accents(text)
    for kw in keywords:
        clean_kw = strip_accents(kw.strip())
        if not clean_kw:
            continue
        if re.search(rf"\b{re.escape(clean_kw)}\b", norm_text, re.IGNORECASE):
            return True
    return False


def match_thematic_channels(text: str, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return enabled thematic channels whose keywords match the message text."""
    matched: list[dict[str, Any]] = []
    for ch in channels:
        if not ch.get("enabled"):
            continue
        chat_id = (ch.get("chat_id") or "").strip() if isinstance(ch.get("chat_id"), str) else ch.get("chat_id")
        if not chat_id:
            continue
        if _matches_any_keyword(text, ch.get("keywords") or []):
            matched.append(ch)
    return matched


def detect_category_name(text: str, channels: list[dict[str, Any]] | None = None) -> str | None:
    """Detect the display category name for a message regardless of channel publishing status."""
    source = channels if channels else DEFAULT_THEMATIC_CATEGORIES
    for ch in source:
        if _matches_any_keyword(text, ch.get("keywords") or []):
            return str(ch["name"])
    return None
