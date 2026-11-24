"""Tests for SQLite repository, deduplication cache, analytics and multi-user limits."""

from datetime import datetime, timedelta, timezone

import pytest

from app.database import Database


def test_keywords_and_blacklist_crud(tmp_path):
    db = Database(str(tmp_path / "deals.db"))
    kw = db.add_keyword("ssd nvme 1tb -kootion <= 380")
    assert kw["term"] == "ssd nvme 1tb"
    assert kw["negatives"] == ["kootion"]
    assert kw["max_price"] == 380.0

    bl = db.add_blacklist("-leilao")
    assert bl == "leilao"
    assert db.list_blacklist() == ["leilao"]

    removed = db.delete_keyword_by_term_or_index("1")
    assert removed is not None
    assert db.list_keywords() == []
    assert db.delete_blacklist("leilao") is True
    db.close()


def test_dedup_cache_window_expiration(tmp_path):
    db = Database(str(tmp_path / "deals.db"))
    t0 = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    fps = ["url:abc123"]

    is_dup, is_first = db.check_and_record_dedup(fps, "admin:-100", window_minutes=30, now=t0)
    assert is_dup is False
    assert is_first is True

    # Same destination within 10 min -> duplicate
    is_dup2, is_first2 = db.check_and_record_dedup(
        fps, "admin:-100", window_minutes=30, now=t0 + timedelta(minutes=10)
    )
    assert is_dup2 is True
    assert is_first2 is False

    # Different destination within 10 min -> not duplicate for user, but not first overall
    is_dup3, is_first3 = db.check_and_record_dedup(
        fps, "user:555", window_minutes=30, now=t0 + timedelta(minutes=10)
    )
    assert is_dup3 is False
    assert is_first3 is False

    # After 31 min -> admin:-100 expired (allowed again), though user:555 (t0+10) is still active
    is_dup4, is_first4 = db.check_and_record_dedup(
        fps, "admin:-100", window_minutes=30, now=t0 + timedelta(minutes=31)
    )
    assert is_dup4 is False
    assert is_first4 is False

    # After 62 min -> both user:555 (t0+10) and admin:-100 (t0+31) expired, fresh overall hit again
    is_dup5, is_first5 = db.check_and_record_dedup(
        fps, "admin:-100", window_minutes=30, now=t0 + timedelta(minutes=62)
    )
    assert is_dup5 is False
    assert is_first5 is True
    db.close()


def test_price_history_and_channel_ranking(tmp_path):
    db = Database(str(tmp_path / "deals.db"))
    db.record_deal("ssd nvme 1tb", "ssd nvme 1tb", "Canal Rapido", "msg1", price=350.0, is_duplicate=False)
    db.record_deal("ssd nvme 1tb", "ssd nvme 1tb", "Canal Lento", "msg2", price=370.0, is_duplicate=True)
    db.record_deal("ssd nvme 1tb", "ssd nvme 1tb", "Canal Rapido", "msg3", price=330.0, is_duplicate=False)

    history = db.get_price_history(term="ssd nvme 1tb", days=30)
    assert history["summary"]["count"] == 3
    assert history["summary"]["min_price"] == 330.0
    assert history["summary"]["max_price"] == 370.0
    assert history["summary"]["avg_price"] == 350.0

    ranking = db.get_channel_ranking(days=30)
    assert ranking[0]["channel_name"] == "Canal Rapido"
    assert ranking[0]["first_hits"] == 2
    assert ranking[1]["channel_name"] == "Canal Lento"
    assert ranking[1]["first_hits"] == 0
    db.close()


def test_multi_user_limit_enforcement(tmp_path):
    db = Database(str(tmp_path / "deals.db"))
    db.add_user_term(111, "rtx 4060", default_max_terms=2, is_admin=False)
    db.add_user_term(111, "ssd nvme 1tb", default_max_terms=2, is_admin=False)

    with pytest.raises(ValueError, match="Limite de 2"):
        db.add_user_term(111, "monitor 144hz", default_max_terms=2, is_admin=False)

    removed = db.remove_user_term(111, "1")
    assert removed is not None
    assert len(db.list_user_terms(111)) == 1
    db.close()


def test_deals_schema_migration_preserves_rows(tmp_path):
    import sqlite3

    db_file = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE deals (
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
        )
        """
    )
    conn.execute(
        "INSERT INTO deals (term, matched_rule, channel_name, raw_text, is_duplicate, created_at) "
        "VALUES ('ssd', 'ssd', 'Canal A', 'SSD 1TB', 0, '2026-11-24T10:00:00Z')"
    )
    conn.execute(
        "INSERT INTO deals (term, matched_rule, channel_name, raw_text, is_duplicate, created_at) "
        "VALUES ('ssd', 'ssd', 'Canal B', 'SSD 1TB dup', 1, '2026-11-24T10:05:00Z')"
    )
    conn.commit()
    conn.close()

    db = Database(db_file)
    sent = db.list_deals(status="sent")
    blocked = db.list_deals(status="blocked")
    assert len(sent) == 1
    assert sent[0]["channel_name"] == "Canal A"
    assert len(blocked) == 1
    assert blocked[0]["status"] == "blocked_dedup"
    db.close()
