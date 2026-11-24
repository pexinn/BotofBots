"""Tests for DealEngine processing pipeline: exclusions, price cap, dedup, multi-user and channels."""

import pytest

from app.config import Settings
from app.database import Database
from app.engine import DealEngine
from app.keywords_sync import KeywordsSyncManager


def _make_settings(tmp_path) -> Settings:
    return Settings(
        api_id=12345,
        api_hash="hash",
        string_session="session",
        alert_destination=-100999,
        bot_token="",
        admin_user_id=904378967,
        max_terms_per_user=5,
        dedup_window_minutes=30,
        require_price_for_cap=True,
        web_enabled=False,
        web_host="127.0.0.1",
        web_port=8086,
        web_password="",
        db_path=str(tmp_path / "deals.db"),
        keywords_file=str(tmp_path / "keywords.txt"),
        keywords_example_file=str(tmp_path / "keywords.example.txt"),
        channel_hardware=-100111,
        channel_perifericos=None,
        channel_games=None,
    )


@pytest.mark.asyncio
async def test_engine_kootion_exclusion_and_price_cap(tmp_path):
    cfg = _make_settings(tmp_path)
    db = Database(cfg.db_path)
    db.seed_thematic_channels({"hardware": cfg.channel_hardware})
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    db.add_keyword("ssd nvme 1tb -kootion <= 380")
    sync.export_to_file()

    sent_messages: list[tuple[int | str, str]] = []

    async def fake_send(dest, text):
        sent_messages.append((dest, text))
        return True

    engine = DealEngine(db, sync, cfg, fake_send)

    # 1. Blocked by brand exclusion '-kootion' on admin rule
    await engine.process_message(-1001, "PromoTech", "promotech", 10, "SSD NVMe 1TB Kootion por R$ 299,90")
    assert -100999 not in [d for d, _ in sent_messages]
    assert db.get_today_stats()["blacklist_blocked"] == 1

    # 2. Blocked by price cap (> 380) on admin rule
    sent_messages.clear()
    await engine.process_message(-1001, "PromoTech", "promotech", 11, "SSD Kingston NVMe 1TB por R$ 420,00")
    assert -100999 not in [d for d, _ in sent_messages]
    assert db.get_today_stats()["price_blocked"] == 1

    # 3. Global blacklist blocks both admin AND thematic channels
    sent_messages.clear()
    db.add_blacklist("leilao")
    await engine.process_message(-1001, "PromoTech", "promotech", 13, "Leilao SSD Kingston NVMe 1TB por R$ 250,00")
    assert sent_messages == []

    # 4. Passes both filters -> sends to admin channel AND thematic hardware channel
    sent_messages.clear()
    await engine.process_message(
        -1001,
        "PromoTech",
        "promotech",
        12,
        "SSD Kingston NV2 1TB NVMe M.2 R$ 349,90 https://www.amazon.com.br/dp/B0BBWH1R8H?tag=abc",
    )
    destinations = [d for d, _ in sent_messages]
    assert -100999 in destinations
    assert -100111 in destinations
    assert len(db.list_deals(status="sent")) == 1
    bl_deals = db.list_deals(status="blocked_blacklist")
    assert len(bl_deals) == 2
    assert any("kootion" in (d["block_reason"] or "") for d in bl_deals)
    price_deals = db.list_deals(status="blocked_price")
    assert len(price_deals) == 1
    assert "420.00" in (price_deals[0]["block_reason"] or "")

    # 5. Duplicate Amazon ASIN from another channel within 30 min -> blocked by dedup
    sent_messages.clear()
    await engine.process_message(
        -1002,
        "OutroCanal",
        "outrocanal",
        55,
        "Oferta SSD Kingston 1TB NVMe R$ 349,90 https://www.amazon.com.br/dp/B0BBWH1R8H?tag=xyz",
    )
    assert -100999 not in [d for d, _ in sent_messages]
    assert db.get_today_stats()["dedup_blocked"] == 1
    assert len(db.list_deals(status="blocked_dedup")) == 1
    assert len(db.list_deals(status="blocked")) == 4
    db.close()


@pytest.mark.asyncio
async def test_engine_loop_protection_and_pause_and_multiuser(tmp_path):
    cfg = _make_settings(tmp_path)
    db = Database(cfg.db_path)
    db.seed_thematic_channels({"hardware": cfg.channel_hardware})
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)
    db.add_user_term(77777, "rtx 4060 <= 1800")

    sent: list[tuple[int | str, str]] = []

    async def fake_send(dest, text):
        sent.append((dest, text))
        return True

    engine = DealEngine(db, sync, cfg, fake_send)

    # Loop protection: message coming from alert_destination or thematic channel is ignored
    res_loop = await engine.process_message(-100999, "Alertas", None, 1, "RTX 4060 R$ 1700")
    assert res_loop["ignored"] is True
    assert res_loop["reason"] == "loop_protection"

    # Pause mode
    db.set_paused(True)
    res_paused = await engine.process_message(-1001, "Promo", None, 2, "RTX 4060 R$ 1700")
    assert res_paused["ignored"] is True
    assert res_paused["reason"] == "paused"

    # Resume -> multi-user receives private alert + hardware thematic channel receives
    db.set_paused(False)
    await engine.process_message(-1001, "Promo", "promo", 3, "Placa RTX 4060 por R$ 1.750,00")
    dests = [d for d, _ in sent]
    assert 77777 in dests
    assert -100111 in dests
    db.close()


@pytest.mark.asyncio
async def test_engine_strict_price_cap_with_discount_and_upsert(tmp_path):
    cfg = _make_settings(tmp_path)
    cfg.channel_hardware = None
    db = Database(cfg.db_path)
    db.seed_thematic_channels({})
    sync = KeywordsSyncManager(db, cfg.keywords_file, cfg.keywords_example_file)

    # First add uncapped rule, then refine it to <= 900 (must replace, not duplicate!)
    db.add_keyword("ssd nvme 1tb")
    db.add_keyword("ssd nvme 1tb <= 900")
    sync.export_to_file()
    assert len(db.list_keywords()) == 1

    sent: list[tuple[int | str, str]] = []

    async def fake_send(dest, text):
        sent.append((dest, text))
        return True

    engine = DealEngine(db, sync, cfg, fake_send)

    # Discount from 2000 to 1200 (economia de 800) must STILL be blocked because 1200 > 900
    await engine.process_message(
        -1001,
        "PromoTech",
        "promotech",
        20,
        "SSD NVMe 1TB - De R$ 2.000,00 por R$ 1.200,00 (Economia de R$ 800,00)",
    )
    assert sent == []
    assert db.get_today_stats()["price_blocked"] == 1

    # Discount from 2000 to 899.90 (R$ 1.100 de desconto) -> <= 900, alert fires!
    await engine.process_message(
        -1001,
        "PromoTech",
        "promotech",
        21,
        "SSD NVMe 1TB - De R$ 2.000,00 por R$ 899,90 (R$ 1.100,00 de desconto)",
    )
    assert len(sent) == 1
    assert sent[0][0] == -100999
    db.close()
