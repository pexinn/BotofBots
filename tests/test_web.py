"""Tests for FastAPI Web Dashboard endpoints, authentication, keywords, analytics and channels."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.keywords_sync import KeywordsSyncManager
from app.web.server import create_app


def _make_settings(tmp_path, web_password: str = "") -> Settings:
    return Settings(
        api_id=123,
        api_hash="hash",
        string_session="sess",
        alert_destination=-100999,
        bot_token="token",
        admin_user_id=904378967,
        max_terms_per_user=5,
        dedup_window_minutes=30,
        require_price_for_cap=True,
        web_enabled=True,
        web_host="127.0.0.1",
        web_port=8086,
        web_password=web_password,
        db_path=str(tmp_path / "deals.db"),
        keywords_file=str(tmp_path / "keywords.txt"),
        keywords_example_file=str(tmp_path / "keywords.example.txt"),
        channel_hardware=None,
        channel_perifericos=None,
        channel_games=None,
    )


def test_web_dashboard_crud_and_analytics(tmp_path):
    cfg = _make_settings(tmp_path)
    db = Database(cfg.db_path)
    db.seed_thematic_channels({})
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    app = create_app(db, sync, cfg, get_monitored_chats_count=lambda: 7)
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/health").json()["status"] == "ok"

    # Add keyword via structured API
    res = client.post(
        "/api/keywords",
        json={"term": "ssd nvme 1tb", "negatives": ["kootion"], "max_price": 380},
    )
    assert res.status_code == 201
    kw_id = res.json()["keyword"]["id"]
    assert "ssd nvme 1tb -kootion <= 380" in open(cfg.keywords_file, encoding="utf-8").read()

    # Blacklist API
    assert client.post("/api/blacklist", json={"word": "leilao"}).status_code == 201
    assert "leilao" in client.get("/api/blacklist").json()["words"]

    # Pause / Resume
    assert client.post("/api/pause", json={"paused": True}).json()["paused"] is True
    assert client.get("/api/status").json()["paused"] is True

    # Deals & Analytics
    db.record_deal("ssd nvme 1tb", "ssd nvme 1tb -kootion <= 380", "Adrenaline", "SSD NVMe 1TB", price=349.9)
    assert len(client.get("/api/deals").json()["deals"]) == 1
    assert client.get("/api/analytics/prices").json()["summary"]["min_price"] == 349.9
    assert client.get("/api/analytics/channels").json()["channels"][0]["channel_name"] == "Adrenaline"

    # Thematic channel update
    ch_res = client.put("/api/channels/hardware", json={"chat_id": "-100123", "enabled": True})
    assert ch_res.status_code == 200
    assert ch_res.json()["channel"]["enabled"] is True

    # Delete keyword
    assert client.delete(f"/api/keywords/{kw_id}").status_code == 200
    assert client.get("/api/keywords").json()["keywords"] == []
    db.close()


def test_web_edit_keyword_guide_and_simulator(tmp_path):
    cfg = _make_settings(tmp_path)
    db = Database(cfg.db_path)
    db.seed_thematic_channels({})
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    app = create_app(db, sync, cfg)
    client = TestClient(app)

    # Interactive guide route
    assert client.get("/guia").status_code == 200

    # Create uncapped keyword and edit it in place to add max_price=1000 & -kootion
    created = client.post("/api/keywords", json={"term": "ssd nvme 1tb"}).json()["keyword"]
    patched = client.patch(
        f"/api/keywords/{created['id']}",
        json={"negatives": ["kootion"], "max_price": 1000},
    )
    assert patched.status_code == 200
    kw = patched.json()["keyword"]
    assert kw["max_price"] == 1000.0
    assert kw["negatives"] == ["kootion"]
    assert "ssd nvme 1tb -kootion <= 1000" in open(cfg.keywords_file, encoding="utf-8").read()

    # Clear max_price via PATCH
    cleared = client.patch(
        f"/api/keywords/{created['id']}",
        json={"clear_max_price": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["keyword"]["max_price"] is None

    # Simulator endpoint tests
    sim_ok = client.post(
        "/api/simulate",
        json={
            "rule": "ssd nvme 1tb -kootion <= 1000",
            "sample_message": "SSD Kingston NVMe 1TB por R$ 899,90",
        },
    ).json()
    assert sim_ok["would_alert"] is True
    assert sim_ok["reason"] == "matched"
    assert sim_ok["detected_price"] == 899.9

    sim_bl = client.post(
        "/api/simulate",
        json={
            "rule": "ssd nvme 1tb -kootion <= 1000",
            "sample_message": "SSD NVMe 1TB Kootion por R$ 289,90",
        },
    ).json()
    assert sim_bl["would_alert"] is False
    assert sim_bl["reason"] == "blacklist"

    sim_cap = client.post(
        "/api/simulate",
        json={
            "rule": "ssd nvme 1tb -kootion <= 1000",
            "sample_message": "SSD NVMe 1TB - De R$ 2.000,00 por R$ 1.200,00 (Economia de R$ 800,00)",
        },
    ).json()
    assert sim_cap["would_alert"] is False
    assert sim_cap["reason"] == "price_above_cap"
    assert sim_cap["detected_price"] == 1200.0
    db.close()


def test_web_dashboard_password_auth(tmp_path):
    cfg = _make_settings(tmp_path, web_password="secret-password")
    db = Database(cfg.db_path)
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    app = create_app(db, sync, cfg)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401

    login_res = client.post("/api/login", json={"password": "secret-password"})
    assert login_res.status_code == 200
    assert client.get("/api/status").status_code == 200
    db.close()
