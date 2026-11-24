"""SQLite persistence layer for keywords, blacklist, deals, dedup cache, multi-user alerts and channels."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.categories import DEFAULT_THEMATIC_CATEGORIES
from app.rules import format_rule_line, parse_rule


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    target = dt or _utc_now()
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return _utc_now().strftime("%Y-%m-%d")


class Database:
    """Thread-safe SQLite repository for all Bot of Bots state and analytics."""

    def __init__(self, db_path: str = "data/deals.db") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_line TEXT NOT NULL,
                term TEXT NOT NULL,
                negatives TEXT NOT NULL DEFAULT '[]',
                max_price REAL,
                strict_lt INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS global_blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                messages_processed INTEGER NOT NULL DEFAULT 0,
                alerts_sent INTEGER NOT NULL DEFAULT 0,
                dedup_blocked INTEGER NOT NULL DEFAULT 0,
                price_blocked INTEGER NOT NULL DEFAULT 0,
                blacklist_blocked INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                matched_rule TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                channel_username TEXT,
                message_id INTEGER,
                message_link TEXT,
                price REAL,
                category TEXT,
                raw_text TEXT NOT NULL,
                canonical_url TEXT,
                is_duplicate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dedup_cache (
                fingerprint TEXT NOT NULL,
                destination TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (fingerprint, destination)
            );

            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                max_terms INTEGER NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                raw_line TEXT NOT NULL,
                term TEXT NOT NULL,
                negatives TEXT NOT NULL DEFAULT '[]',
                max_price REAL,
                strict_lt INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS thematic_channels (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                chat_id TEXT,
                keywords TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_deals_created_at ON deals(created_at);
            CREATE INDEX IF NOT EXISTS idx_deals_term ON deals(term);
            CREATE INDEX IF NOT EXISTS idx_dedup_seen ON dedup_cache(first_seen_at);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Settings & Pause State
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    def is_paused(self) -> bool:
        return (self.get_setting("paused", "false") or "false").lower() == "true"

    def set_paused(self, paused: bool) -> None:
        self.set_setting("paused", "true" if paused else "false")

    # ------------------------------------------------------------------
    # Keywords & Global Blacklist
    # ------------------------------------------------------------------
    def _row_to_keyword(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "raw_line": row["raw_line"],
            "term": row["term"],
            "negatives": json.loads(row["negatives"] or "[]"),
            "max_price": row["max_price"],
            "strict_lt": bool(row["strict_lt"]),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        }

    def list_keywords(self, only_enabled: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM keywords"
            if only_enabled:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY id ASC"
            rows = self._conn.execute(sql).fetchall()
            return [self._row_to_keyword(r) for r in rows]

    def add_keyword(self, raw_line: str) -> dict[str, Any]:
        parsed = parse_rule(raw_line)
        if parsed is None or parsed.is_global_blacklist:
            raise ValueError("Regra inválida. Informe pelo menos uma palavra-chave positiva.")

        canonical_raw = format_rule_line(
            parsed.term, parsed.negatives, parsed.max_price, parsed.strict_lt
        )
        now = _utc_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM keywords WHERE LOWER(term) = LOWER(?) LIMIT 1",
                (parsed.term,),
            ).fetchone()
            if existing:
                kw_id = int(existing["id"])
                self._conn.execute(
                    "UPDATE keywords SET raw_line = ?, negatives = ?, max_price = ?, strict_lt = ?, enabled = 1 "
                    "WHERE id = ?",
                    (
                        canonical_raw,
                        json.dumps(parsed.negatives),
                        parsed.max_price,
                        1 if parsed.strict_lt else 0,
                        kw_id,
                    ),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO keywords (raw_line, term, negatives, max_price, strict_lt, enabled, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (
                        canonical_raw,
                        parsed.term,
                        json.dumps(parsed.negatives),
                        parsed.max_price,
                        1 if parsed.strict_lt else 0,
                        now,
                    ),
                )
                kw_id = int(cur.lastrowid)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM keywords WHERE id = ?", (kw_id,)).fetchone()
            return self._row_to_keyword(row)

    def delete_keyword(self, kw_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM keywords WHERE id = ?", (kw_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def update_keyword(
        self,
        kw_id: int,
        enabled: bool | None = None,
        max_price: float | None = None,
        clear_max_price: bool = False,
        negatives: list[str] | None = None,
        strict_lt: bool | None = None,
        term: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM keywords WHERE id = ?", (kw_id,)).fetchone()
            if not row:
                return None

            current = self._row_to_keyword(row)
            if term is not None:
                parsed_term = parse_rule(term)
                if parsed_term is None or parsed_term.is_global_blacklist or not parsed_term.term:
                    raise ValueError("Termo inválido. Informe pelo menos uma palavra-chave positiva.")
                new_term = parsed_term.term
            else:
                new_term = current["term"]

            new_enabled = current["enabled"] if enabled is None else enabled
            new_negatives = current["negatives"] if negatives is None else [
                n.strip().lstrip("-!").lower() for n in negatives if n.strip()
            ]
            new_strict = current["strict_lt"] if strict_lt is None else strict_lt
            if clear_max_price:
                new_max_price = None
            elif max_price is not None:
                new_max_price = float(max_price)
            else:
                new_max_price = current["max_price"]

            new_raw = format_rule_line(new_term, new_negatives, new_max_price, new_strict)
            self._conn.execute(
                "UPDATE keywords SET raw_line = ?, term = ?, negatives = ?, max_price = ?, strict_lt = ?, enabled = ? "
                "WHERE id = ?",
                (
                    new_raw,
                    new_term,
                    json.dumps(new_negatives),
                    new_max_price,
                    1 if new_strict else 0,
                    1 if new_enabled else 0,
                    kw_id,
                ),
            )
            self._conn.commit()
            updated = self._conn.execute("SELECT * FROM keywords WHERE id = ?", (kw_id,)).fetchone()
            return self._row_to_keyword(updated) if updated else None

    def toggle_keyword(self, kw_id: int, enabled: bool) -> dict[str, Any] | None:
        return self.update_keyword(kw_id, enabled=enabled)

    def delete_keyword_by_term_or_index(self, query: str) -> dict[str, Any] | None:
        q = query.strip()
        if not q:
            return None
        with self._lock:
            all_kws = self.list_keywords(only_enabled=False)
            target: dict[str, Any] | None = None
            if q.isdigit():
                idx = int(q)
                if 1 <= idx <= len(all_kws):
                    target = all_kws[idx - 1]
                else:
                    for kw in all_kws:
                        if kw["id"] == idx:
                            target = kw
                            break
            if target is None:
                q_lower = q.lower()
                for kw in all_kws:
                    if kw["raw_line"].lower() == q_lower or kw["term"].lower() == q_lower:
                        target = kw
                        break
            if target is None:
                return None
            self.delete_keyword(target["id"])
            return target

    def list_blacklist(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT word FROM global_blacklist ORDER BY word ASC").fetchall()
            return [str(r["word"]) for r in rows]

    def add_blacklist(self, word: str) -> str:
        clean = word.strip().lstrip("-!").lower()
        if not clean or " " in clean:
            raise ValueError("Informe uma única palavra válida para a blacklist.")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO global_blacklist (word, created_at) VALUES (?, ?)",
                (clean, _utc_iso()),
            )
            self._conn.commit()
            return clean

    def delete_blacklist(self, word: str) -> bool:
        clean = word.strip().lstrip("-!").lower()
        with self._lock:
            cur = self._conn.execute("DELETE FROM global_blacklist WHERE word = ?", (clean,))
            self._conn.commit()
            return cur.rowcount > 0

    def replace_all_from_lines(self, lines: list[str]) -> tuple[int, int]:
        """Replace keywords and global blacklist from parsed file lines."""
        parsed_rules = []
        blacklist_words: list[str] = []
        seen_bl: set[str] = set()

        for line in lines:
            parsed = parse_rule(line)
            if parsed is None:
                continue
            if parsed.is_global_blacklist:
                for neg in parsed.negatives:
                    if neg not in seen_bl:
                        seen_bl.add(neg)
                        blacklist_words.append(neg)
            else:
                parsed_rules.append(parsed)

        now = _utc_iso()
        with self._lock:
            self._conn.execute("DELETE FROM keywords")
            self._conn.execute("DELETE FROM global_blacklist")
            for neg in blacklist_words:
                self._conn.execute(
                    "INSERT OR IGNORE INTO global_blacklist (word, created_at) VALUES (?, ?)",
                    (neg, now),
                )
            for rule in parsed_rules:
                canonical_raw = format_rule_line(
                    rule.term, rule.negatives, rule.max_price, rule.strict_lt
                )
                self._conn.execute(
                    "INSERT INTO keywords (raw_line, term, negatives, max_price, strict_lt, enabled, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (
                        canonical_raw,
                        rule.term,
                        json.dumps(rule.negatives),
                        rule.max_price,
                        1 if rule.strict_lt else 0,
                        now,
                    ),
                )
            self._conn.commit()
        return len(parsed_rules), len(blacklist_words)

    # ------------------------------------------------------------------
    # Daily Stats
    # ------------------------------------------------------------------
    def increment_stat(self, field: str, delta: int = 1, date_str: str | None = None) -> None:
        allowed = {
            "messages_processed",
            "alerts_sent",
            "dedup_blocked",
            "price_blocked",
            "blacklist_blocked",
        }
        if field not in allowed:
            raise ValueError(f"Invalid stat field: {field}")
        day = date_str or _today_str()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO daily_stats (date) VALUES (?)",
                (day,),
            )
            self._conn.execute(
                f"UPDATE daily_stats SET {field} = {field} + ? WHERE date = ?",
                (delta, day),
            )
            self._conn.commit()

    def get_today_stats(self, date_str: str | None = None) -> dict[str, int]:
        day = date_str or _today_str()
        with self._lock:
            row = self._conn.execute("SELECT * FROM daily_stats WHERE date = ?", (day,)).fetchone()
            if not row:
                return {
                    "messages_processed": 0,
                    "alerts_sent": 0,
                    "dedup_blocked": 0,
                    "price_blocked": 0,
                    "blacklist_blocked": 0,
                }
            return {
                "messages_processed": int(row["messages_processed"]),
                "alerts_sent": int(row["alerts_sent"]),
                "dedup_blocked": int(row["dedup_blocked"]),
                "price_blocked": int(row["price_blocked"]),
                "blacklist_blocked": int(row["blacklist_blocked"]),
            }

    def get_total_stats(self) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COALESCE(SUM(messages_processed), 0) AS messages_processed,
                    COALESCE(SUM(alerts_sent), 0) AS alerts_sent,
                    COALESCE(SUM(dedup_blocked), 0) AS dedup_blocked,
                    COALESCE(SUM(price_blocked), 0) AS price_blocked,
                    COALESCE(SUM(blacklist_blocked), 0) AS blacklist_blocked
                FROM daily_stats
                """
            ).fetchone()
            return {
                "messages_processed": int(row["messages_processed"]),
                "alerts_sent": int(row["alerts_sent"]),
                "dedup_blocked": int(row["dedup_blocked"]),
                "price_blocked": int(row["price_blocked"]),
                "blacklist_blocked": int(row["blacklist_blocked"]),
            }

    # ------------------------------------------------------------------
    # Deduplication Cache
    # ------------------------------------------------------------------
    def check_and_record_dedup(
        self,
        fingerprints: list[str],
        destination: str,
        window_minutes: int = 30,
        now: datetime | None = None,
    ) -> tuple[bool, bool]:
        """
        Check if any fingerprint was seen for `destination` within `window_minutes`.
        Returns (is_duplicate_for_destination, is_first_overall).
        """
        if not fingerprints:
            return False, True

        current = now or _utc_now()
        cutoff_iso = _utc_iso(current - timedelta(minutes=max(1, window_minutes)))
        now_iso = _utc_iso(current)

        with self._lock:
            self._conn.execute("DELETE FROM dedup_cache WHERE first_seen_at < ?", (cutoff_iso,))
            placeholders = ",".join("?" for _ in fingerprints)

            dup_row = self._conn.execute(
                f"SELECT 1 FROM dedup_cache WHERE destination = ? AND fingerprint IN ({placeholders}) LIMIT 1",
                [destination, *fingerprints],
            ).fetchone()
            is_duplicate = dup_row is not None

            any_row = self._conn.execute(
                f"SELECT 1 FROM dedup_cache WHERE fingerprint IN ({placeholders}) LIMIT 1",
                list(fingerprints),
            ).fetchone()
            is_first_overall = any_row is None

            if not is_duplicate:
                for fp in fingerprints:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO dedup_cache (fingerprint, destination, first_seen_at) "
                        "VALUES (?, ?, ?)",
                        (fp, destination, now_iso),
                    )
            self._conn.commit()
            return is_duplicate, is_first_overall

    # ------------------------------------------------------------------
    # Deals & Analytics
    # ------------------------------------------------------------------
    def record_deal(
        self,
        term: str,
        matched_rule: str,
        channel_name: str,
        raw_text: str,
        channel_username: str | None = None,
        message_id: int | None = None,
        message_link: str | None = None,
        price: float | None = None,
        category: str | None = None,
        canonical_url: str | None = None,
        is_duplicate: bool = False,
        created_at: str | None = None,
    ) -> int:
        ts = created_at or _utc_iso()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO deals (
                    term, matched_rule, channel_name, channel_username, message_id,
                    message_link, price, category, raw_text, canonical_url, is_duplicate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    term,
                    matched_rule,
                    channel_name,
                    channel_username,
                    message_id,
                    message_link,
                    price,
                    category,
                    raw_text,
                    canonical_url,
                    1 if is_duplicate else 0,
                    ts,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_deals(
        self,
        limit: int = 50,
        term: str | None = None,
        category: str | None = None,
        channel: str | None = None,
        max_price: float | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if term:
            clauses.append("LOWER(term) = LOWER(?)")
            params.append(term.strip())
        if category:
            clauses.append("LOWER(category) = LOWER(?)")
            params.append(category.strip())
        if channel:
            clauses.append("LOWER(channel_name) LIKE LOWER(?)")
            params.append(f"%{channel.strip()}%")
        if max_price is not None:
            clauses.append("price IS NOT NULL AND price <= ?")
            params.append(max_price)
        if search:
            clauses.append("(LOWER(raw_text) LIKE LOWER(?) OR LOWER(term) LIKE LOWER(?))")
            like = f"%{search.strip()}%"
            params.extend([like, like])

        params.append(max(1, min(limit, 500)))
        sql = f"SELECT * FROM deals WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [
                {
                    "id": r["id"],
                    "term": r["term"],
                    "matched_rule": r["matched_rule"],
                    "channel_name": r["channel_name"],
                    "channel_username": r["channel_username"],
                    "message_id": r["message_id"],
                    "message_link": r["message_link"],
                    "price": r["price"],
                    "category": r["category"],
                    "raw_text": r["raw_text"],
                    "canonical_url": r["canonical_url"],
                    "is_duplicate": bool(r["is_duplicate"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def get_price_history(self, term: str | None = None, days: int = 30) -> dict[str, Any]:
        cutoff_iso = _utc_iso(_utc_now() - timedelta(days=max(1, days)))
        clauses = ["price IS NOT NULL", "created_at >= ?"]
        params: list[Any] = [cutoff_iso]
        if term:
            clauses.append("LOWER(term) = LOWER(?)")
            params.append(term.strip())

        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, term, price, channel_name, message_link, created_at "
                f"FROM deals WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                params,
            ).fetchall()

            terms_rows = self._conn.execute(
                "SELECT DISTINCT term FROM deals WHERE price IS NOT NULL ORDER BY term ASC"
            ).fetchall()

            points = [
                {
                    "id": r["id"],
                    "term": r["term"],
                    "price": float(r["price"]),
                    "channel_name": r["channel_name"],
                    "message_link": r["message_link"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            prices = [p["price"] for p in points]
            summary = {
                "count": len(prices),
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
            }
            return {
                "points": points,
                "summary": summary,
                "available_terms": [str(r["term"]) for r in terms_rows],
            }

    def get_channel_ranking(self, days: int = 30) -> list[dict[str, Any]]:
        cutoff_iso = _utc_iso(_utc_now() - timedelta(days=max(1, days)))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    channel_name,
                    COUNT(*) AS total_deals,
                    SUM(CASE WHEN is_duplicate = 0 THEN 1 ELSE 0 END) AS first_hits,
                    ROUND(AVG(price), 2) AS avg_price,
                    MIN(price) AS min_price,
                    MAX(created_at) AS last_deal_at
                FROM deals
                WHERE created_at >= ?
                GROUP BY channel_name
                ORDER BY first_hits DESC, total_deals DESC
                """,
                (cutoff_iso,),
            ).fetchall()
            return [
                {
                    "channel_name": r["channel_name"],
                    "total_deals": int(r["total_deals"]),
                    "first_hits": int(r["first_hits"] or 0),
                    "avg_price": float(r["avg_price"]) if r["avg_price"] is not None else None,
                    "min_price": float(r["min_price"]) if r["min_price"] is not None else None,
                    "last_deal_at": r["last_deal_at"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Multi-User Subscriptions
    # ------------------------------------------------------------------
    def upsert_user(
        self,
        chat_id: int,
        username: str | None = None,
        first_name: str | None = None,
        default_max_terms: int = 5,
    ) -> dict[str, Any]:
        now = _utc_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE users SET username = COALESCE(?, username), first_name = COALESCE(?, first_name) "
                    "WHERE chat_id = ?",
                    (username, first_name, chat_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO users (chat_id, username, first_name, active, max_terms, created_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (chat_id, username, first_name, default_max_terms, now),
                )
            self._conn.commit()
            return self.get_user(chat_id)  # type: ignore[return-value]

    def get_user(self, chat_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
            if not row:
                return None
            terms = self.list_user_terms(chat_id)
            return {
                "chat_id": int(row["chat_id"]),
                "username": row["username"],
                "first_name": row["first_name"],
                "active": bool(row["active"]),
                "max_terms": int(row["max_terms"]),
                "created_at": row["created_at"],
                "term_count": len(terms),
                "terms": terms,
            }

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT chat_id FROM users ORDER BY created_at DESC").fetchall()
            return [u for r in rows if (u := self.get_user(int(r["chat_id"]))) is not None]

    def update_user(
        self,
        chat_id: int,
        active: bool | None = None,
        max_terms: int | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            existing = self._conn.execute("SELECT 1 FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
            if not existing:
                return None
            if active is not None:
                self._conn.execute(
                    "UPDATE users SET active = ? WHERE chat_id = ?",
                    (1 if active else 0, chat_id),
                )
            if max_terms is not None:
                self._conn.execute(
                    "UPDATE users SET max_terms = ? WHERE chat_id = ?",
                    (max(1, int(max_terms)), chat_id),
                )
            self._conn.commit()
            return self.get_user(chat_id)

    def _row_to_user_term(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "chat_id": int(row["chat_id"]),
            "raw_line": row["raw_line"],
            "term": row["term"],
            "negatives": json.loads(row["negatives"] or "[]"),
            "max_price": row["max_price"],
            "strict_lt": bool(row["strict_lt"]),
            "created_at": row["created_at"],
        }

    def list_user_terms(self, chat_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM user_terms WHERE chat_id = ? ORDER BY id ASC",
                (chat_id,),
            ).fetchall()
            return [self._row_to_user_term(r) for r in rows]

    def list_all_active_user_terms(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ut.* FROM user_terms ut
                INNER JOIN users u ON u.chat_id = ut.chat_id
                WHERE u.active = 1
                ORDER BY ut.chat_id ASC, ut.id ASC
                """
            ).fetchall()
            return [self._row_to_user_term(r) for r in rows]

    def add_user_term(
        self,
        chat_id: int,
        raw_line: str,
        default_max_terms: int = 5,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        parsed = parse_rule(raw_line)
        if parsed is None or parsed.is_global_blacklist:
            raise ValueError("Regra inválida. Informe o produto que deseja monitorar.")

        with self._lock:
            user = self.upsert_user(chat_id, default_max_terms=default_max_terms)
            existing = self._conn.execute(
                "SELECT id FROM user_terms WHERE chat_id = ? AND LOWER(term) = LOWER(?) LIMIT 1",
                (chat_id, parsed.term),
            ).fetchone()
            canonical_raw = format_rule_line(
                parsed.term, parsed.negatives, parsed.max_price, parsed.strict_lt
            )
            now = _utc_iso()
            if existing:
                term_id = int(existing["id"])
                self._conn.execute(
                    "UPDATE user_terms SET raw_line = ?, negatives = ?, max_price = ?, strict_lt = ? "
                    "WHERE id = ?",
                    (
                        canonical_raw,
                        json.dumps(parsed.negatives),
                        parsed.max_price,
                        1 if parsed.strict_lt else 0,
                        term_id,
                    ),
                )
            else:
                current_terms = self.list_user_terms(chat_id)
                if not is_admin and len(current_terms) >= user["max_terms"]:
                    raise ValueError(
                        f"Limite de {user['max_terms']} alertas atingido. Remova um alerta antes de adicionar outro."
                    )
                cur = self._conn.execute(
                    "INSERT INTO user_terms (chat_id, raw_line, term, negatives, max_price, strict_lt, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chat_id,
                        canonical_raw,
                        parsed.term,
                        json.dumps(parsed.negatives),
                        parsed.max_price,
                        1 if parsed.strict_lt else 0,
                        now,
                    ),
                )
                term_id = int(cur.lastrowid)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM user_terms WHERE id = ?", (term_id,)).fetchone()
            return self._row_to_user_term(row)

    def remove_user_term(self, chat_id: int, query: str) -> dict[str, Any] | None:
        q = query.strip()
        if not q:
            return None
        with self._lock:
            terms = self.list_user_terms(chat_id)
            target: dict[str, Any] | None = None
            if q.isdigit():
                idx = int(q)
                if 1 <= idx <= len(terms):
                    target = terms[idx - 1]
                else:
                    for t in terms:
                        if t["id"] == idx:
                            target = t
                            break
            if target is None:
                q_lower = q.lower()
                for t in terms:
                    if t["raw_line"].lower() == q_lower or t["term"].lower() == q_lower:
                        target = t
                        break
            if target is None:
                return None
            self._conn.execute(
                "DELETE FROM user_terms WHERE id = ? AND chat_id = ?",
                (target["id"], chat_id),
            )
            self._conn.commit()
            return target

    def delete_user_term_by_id(self, chat_id: int, term_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM user_terms WHERE id = ? AND chat_id = ?",
                (term_id, chat_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Thematic Channels
    # ------------------------------------------------------------------
    def seed_thematic_channels(self, env_channels: dict[str, int | str | None]) -> None:
        with self._lock:
            for cat in DEFAULT_THEMATIC_CATEGORIES:
                slug = cat["slug"]
                existing = self._conn.execute(
                    "SELECT * FROM thematic_channels WHERE slug = ?", (slug,)
                ).fetchone()
                env_val = env_channels.get(slug)
                env_str = str(env_val) if env_val not in (None, "") else None
                if not existing:
                    self._conn.execute(
                        "INSERT INTO thematic_channels (slug, name, chat_id, keywords, enabled) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            slug,
                            cat["name"],
                            env_str,
                            json.dumps(cat["keywords"]),
                            1 if env_str else 0,
                        ),
                    )
                elif env_str and not existing["chat_id"]:
                    self._conn.execute(
                        "UPDATE thematic_channels SET chat_id = ?, enabled = 1 WHERE slug = ?",
                        (env_str, slug),
                    )
            self._conn.commit()

    def list_thematic_channels(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM thematic_channels ORDER BY slug ASC").fetchall()
            return [
                {
                    "slug": r["slug"],
                    "name": r["name"],
                    "chat_id": r["chat_id"],
                    "keywords": json.loads(r["keywords"] or "[]"),
                    "enabled": bool(r["enabled"]),
                }
                for r in rows
            ]

    def update_thematic_channel(
        self,
        slug: str,
        chat_id: str | int | None = None,
        enabled: bool | None = None,
        keywords: list[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM thematic_channels WHERE slug = ?", (slug,)
            ).fetchone()
            if not existing:
                return None

            new_name = name if name is not None else existing["name"]
            new_chat = str(chat_id).strip() if chat_id is not None and str(chat_id).strip() else (
                None if chat_id == "" else existing["chat_id"]
            )
            new_keywords = json.dumps(keywords) if keywords is not None else existing["keywords"]
            new_enabled = (1 if enabled else 0) if enabled is not None else existing["enabled"]

            self._conn.execute(
                "UPDATE thematic_channels SET name = ?, chat_id = ?, keywords = ?, enabled = ? WHERE slug = ?",
                (new_name, new_chat, new_keywords, new_enabled, slug),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM thematic_channels WHERE slug = ?", (slug,)).fetchone()
            return {
                "slug": row["slug"],
                "name": row["name"],
                "chat_id": row["chat_id"],
                "keywords": json.loads(row["keywords"] or "[]"),
                "enabled": bool(row["enabled"]),
            }
