"""Core deal evaluation, filtering, deduplication and multi-target alerting engine."""

from __future__ import annotations

import html
import re
from typing import Any, Awaitable, Callable

from app.categories import detect_category_name, match_thematic_channels
from app.config import Settings
from app.database import Database
from app.keywords_sync import KeywordsSyncManager
from app.rules import (
    compute_deal_fingerprints,
    evaluate_rule,
    extract_canonical_urls,
    extract_price,
    parse_rule,
    strip_accents,
)


class DealEngine:
    """Evaluates incoming Telegram messages against admin rules, user alerts and thematic channels."""

    def __init__(
        self,
        db: Database,
        sync_manager: KeywordsSyncManager,
        settings: Settings,
        send_fn: Callable[[int | str, str], Awaitable[bool]],
    ) -> None:
        self.db = db
        self.sync_manager = sync_manager
        self.settings = settings
        self.send_fn = send_fn

    @staticmethod
    def _check_price_cap(
        max_price: float | None,
        strict_lt: bool,
        detected_price: float | None,
        require_price_for_cap: bool,
    ) -> bool:
        if max_price is None:
            return True
        if detected_price is None:
            return not require_price_for_cap
        return detected_price < max_price if strict_lt else detected_price <= max_price

    @staticmethod
    def _parse_dest(raw_dest: int | str) -> int | str:
        if isinstance(raw_dest, int):
            return raw_dest
        s = str(raw_dest).strip()
        if s.lstrip("-").isdigit():
            return int(s)
        return s

    @staticmethod
    def _format_alert_html(
        label: str,
        chat_name: str,
        message_link: str | None,
        raw_text: str,
        price: float | None,
        category: str | None,
        header_prefix: str = "🚨 <b>OFERTA ENCONTRADA:</b>",
    ) -> str:
        label_safe = html.escape(label.upper())
        chat_safe = html.escape(chat_name)
        text_safe = html.escape(raw_text[:1200])

        meta_lines: list[str] = []
        if price is not None:
            price_fmt = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            meta_lines.append(f"💰 <b>Preço Detectado:</b> <code>R$ {price_fmt}</code>")
        if category:
            meta_lines.append(f"🏷️ <b>Categoria:</b> {html.escape(category)}")
        meta_lines.append(f"📢 <b>Grupo/Canal:</b> {chat_safe}")
        if message_link:
            meta_lines.append(f'🔗 <b>Link da Oferta:</b> <a href="{message_link}">{message_link}</a>')

        meta_block = "\n".join(meta_lines)
        return (
            f"{header_prefix} <code>{label_safe}</code>\n\n"
            f"{meta_block}\n"
            f"💬 <b>Mensagem:</b>\n{text_safe}"
        )

    def _protected_destinations(self, channels: list[dict[str, Any]]) -> set[str]:
        protected = {str(self.settings.alert_destination)}
        for ch in channels:
            if ch.get("chat_id"):
                protected.add(str(ch["chat_id"]).strip())
        return protected

    async def process_message(
        self,
        chat_id: int | str | None,
        chat_name: str,
        chat_username: str | None,
        message_id: int | None,
        raw_text: str,
    ) -> dict[str, Any]:
        if not raw_text or not raw_text.strip():
            return {"ignored": True, "reason": "empty"}

        channels = self.db.list_thematic_channels()
        if chat_id is not None and str(chat_id) in self._protected_destinations(channels):
            return {"ignored": True, "reason": "loop_protection"}

        self.db.increment_stat("messages_processed")
        self.sync_manager.sync_from_file_if_changed()

        if self.db.is_paused():
            return {"ignored": True, "reason": "paused"}

        price = extract_price(raw_text)
        canonical_urls = extract_canonical_urls(raw_text)
        category = detect_category_name(raw_text, channels)
        message_link = (
            f"https://t.me/{chat_username}/{message_id}"
            if chat_username and message_id
            else None
        )

        global_blacklist = self.db.list_blacklist()
        require_price_cap = (
            self.db.get_setting("require_price_for_cap")
            or str(self.settings.require_price_for_cap)
        ).lower() == "true"
        dedup_window = int(
            self.db.get_setting("dedup_window_minutes") or self.settings.dedup_window_minutes
        )

        admin_alerts_sent = 0
        user_alerts_sent = 0
        channel_alerts_sent = 0

        # 1. Admin / Main Channel Rules
        admin_keywords = self.db.list_keywords(only_enabled=True)
        for kw in admin_keywords:
            rule = parse_rule(kw["raw_line"])
            if rule is None:
                continue

            matched, block_reason = evaluate_rule(rule, raw_text, global_blacklist)
            if block_reason == "blacklist":
                self.db.increment_stat("blacklist_blocked")
                continue
            if not matched:
                continue

            if not self._check_price_cap(rule.max_price, rule.strict_lt, price, require_price_cap):
                self.db.increment_stat("price_blocked")
                continue

            fps = compute_deal_fingerprints(raw_text, rule.term, price, canonical_urls)
            dest_key = f"admin:{self.settings.alert_destination}"
            is_dup, is_first = self.db.check_and_record_dedup(fps, dest_key, dedup_window)
            if is_dup:
                self.db.increment_stat("dedup_blocked")
                self.db.record_deal(
                    term=rule.term,
                    matched_rule=kw["raw_line"],
                    channel_name=chat_name,
                    channel_username=chat_username,
                    message_id=message_id,
                    message_link=message_link,
                    price=price,
                    category=category,
                    raw_text=raw_text,
                    canonical_url=canonical_urls[0] if canonical_urls else None,
                    is_duplicate=True,
                )
                break

            alert_html = self._format_alert_html(
                label=rule.term,
                chat_name=chat_name,
                message_link=message_link,
                raw_text=raw_text,
                price=price,
                category=category,
            )
            sent = await self.send_fn(self.settings.alert_destination, alert_html)
            if sent:
                admin_alerts_sent += 1
                self.db.increment_stat("alerts_sent")
            self.db.record_deal(
                term=rule.term,
                matched_rule=kw["raw_line"],
                channel_name=chat_name,
                channel_username=chat_username,
                message_id=message_id,
                message_link=message_link,
                price=price,
                category=category,
                raw_text=raw_text,
                canonical_url=canonical_urls[0] if canonical_urls else None,
                is_duplicate=not is_first,
            )
            break

        # 2. Multi-User Personal Subscriptions
        terms_by_user: dict[int, list[dict[str, Any]]] = {}
        for ut in self.db.list_all_active_user_terms():
            terms_by_user.setdefault(int(ut["chat_id"]), []).append(ut)

        for user_chat_id, user_terms in terms_by_user.items():
            for ut in user_terms:
                rule = parse_rule(ut["raw_line"])
                if rule is None:
                    continue
                matched, _ = evaluate_rule(rule, raw_text, ())
                if not matched:
                    continue
                if not self._check_price_cap(rule.max_price, rule.strict_lt, price, require_price_cap):
                    continue

                fps = compute_deal_fingerprints(raw_text, rule.term, price, canonical_urls)
                dest_key = f"user:{user_chat_id}"
                is_dup, _ = self.db.check_and_record_dedup(fps, dest_key, dedup_window)
                if is_dup:
                    break

                alert_html = self._format_alert_html(
                    label=rule.term,
                    chat_name=chat_name,
                    message_link=message_link,
                    raw_text=raw_text,
                    price=price,
                    category=category,
                    header_prefix="🎯 <b>SEU ALERTA DISPAROU:</b>",
                )
                if await self.send_fn(user_chat_id, alert_html):
                    user_alerts_sent += 1
                    self.db.increment_stat("alerts_sent")
                break

        # 3. Thematic Channels
        matched_channels = match_thematic_channels(raw_text, channels)
        if matched_channels:
            norm_text = strip_accents(raw_text)
            blocked_by_global = any(
                re.search(rf"\b{re.escape(strip_accents(g))}\b", norm_text, re.IGNORECASE)
                for g in global_blacklist
                if g
            )
            if not blocked_by_global:
                for ch in matched_channels:
                    ch_dest = self._parse_dest(ch["chat_id"])
                    fps = compute_deal_fingerprints(raw_text, ch["slug"], price, canonical_urls)
                    dest_key = f"channel:{ch_dest}"
                    is_dup, _ = self.db.check_and_record_dedup(fps, dest_key, dedup_window)
                    if is_dup:
                        continue

                    alert_html = self._format_alert_html(
                        label=ch["name"],
                        chat_name=chat_name,
                        message_link=message_link,
                        raw_text=raw_text,
                        price=price,
                        category=ch["name"],
                        header_prefix="📢 <b>CATEGORIA:</b>",
                    )
                    if await self.send_fn(ch_dest, alert_html):
                        channel_alerts_sent += 1
                        self.db.increment_stat("alerts_sent")

        return {
            "ignored": False,
            "price": price,
            "category": category,
            "admin_alerts_sent": admin_alerts_sent,
            "user_alerts_sent": user_alerts_sent,
            "channel_alerts_sent": channel_alerts_sent,
        }
