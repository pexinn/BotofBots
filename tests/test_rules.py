"""Tests for smart rule parsing, brand/negative blacklists, BRL price parsing and URL canonicalization."""

from app.rules import (
    canonicalize_url,
    compute_deal_fingerprints,
    evaluate_rule,
    extract_canonical_urls,
    extract_price,
    format_rule_line,
    parse_price_value,
    parse_rule,
)


def test_parse_rule_positives_negatives_and_price_cap():
    rule = parse_rule("ssd nvme 1tb -kootion !sata <= 380")
    assert rule is not None
    assert not rule.is_global_blacklist
    assert rule.term == "ssd nvme 1tb"
    assert rule.positives == ["ssd", "nvme", "1tb"]
    assert rule.negatives == ["kootion", "sata"]
    assert rule.max_price == 380.0
    assert rule.strict_lt is False


def test_parse_rule_global_blacklist_only():
    rule = parse_rule("-kootion")
    assert rule is not None
    assert rule.is_global_blacklist is True
    assert rule.negatives == ["kootion"]
    assert rule.positives == []


def test_evaluate_rule_ssd_kootion_exclusion_multiline():
    rule = parse_rule("ssd nvme 1tb -kootion -sata <= 380")
    assert rule is not None

    kingston_msg = "🔥 PROMOÇÃO\nSSD Kingston NV2 1 TB NVMe M.2\nR$ 349,90 no Pix"
    matched, reason = evaluate_rule(rule, kingston_msg)
    assert matched is True
    assert reason is None

    kootion_msg = "🔥 SSD NVMe 1TB Kootion X16\nApenas R$ 289,90!"
    matched_k, reason_k = evaluate_rule(rule, kootion_msg)
    assert matched_k is False
    assert reason_k == "blacklist"

    kootion_first_msg = "Kootion lançou SSD 1 TB NVMe por R$ 279,00"
    matched_kf, reason_kf = evaluate_rule(rule, kootion_first_msg)
    assert matched_kf is False
    assert reason_kf == "blacklist"


def test_evaluate_rule_global_blacklist():
    rule = parse_rule("rtx 4060")
    assert rule is not None
    matched, reason = evaluate_rule(rule, "Leilão placa RTX 4060 barata", global_blacklist=["leilao", "leilão"])
    assert matched is False
    assert reason == "blacklist"


def test_parse_price_value_formats():
    assert parse_price_value("1.750,00") == 1750.0
    assert parse_price_value("1.899") == 1899.0
    assert parse_price_value("379,90") == 379.9
    assert parse_price_value("379.90") == 379.9
    assert parse_price_value("380") == 380.0
    assert parse_price_value("abc") is None


def test_extract_price_ignores_installments_coupons_and_shipping():
    msg = (
        "SSD Kingston NV2 1TB NVMe\n"
        "De R$ 459,90 por R$ 349,90 no Pix!\n"
        "Ou 10x de R$ 42,90 sem juros\n"
        "Use cupom de R$ 20,00 | Frete R$ 15,00 | Cashback de R$ 10,00"
    )
    assert extract_price(msg) == 349.9

    msg_only_installment = "RTX 4060 em 12x de R$ 180,00 sem juros!"
    assert extract_price(msg_only_installment) is None

    msg_por = "RTX 4060 por 1750 no pix"
    assert extract_price(msg_por) == 1750.0

    msg_big_discount = (
        "SSD NVMe 1TB - De R$ 2.000,00 por R$ 1.200,00 "
        "(Economia de R$ 800,00 / 12x sem juros de R$ 100,00)"
    )
    assert extract_price(msg_big_discount) == 1200.0

    msg_por_plain = "SSD NVMe 1TB por 1200 (era 2000)"
    assert extract_price(msg_por_plain) == 1200.0


def test_canonicalize_urls_and_fingerprints():
    u1 = canonicalize_url("https://www.amazon.com.br/dp/B0BBWH1R8H?tag=canal1-20&utm_source=tg")
    u2 = canonicalize_url("https://www.amazon.com.br/SSD-Kingston/dp/B0BBWH1R8H/?ref=sr_1_1")
    assert u1 == "amazon:B0BBWH1R8H"
    assert u2 == "amazon:B0BBWH1R8H"

    ml1 = canonicalize_url("https://produto.mercadolivre.com.br/MLB-123456789-ssd?matt_tool=123")
    ml2 = canonicalize_url("https://www.mercadolivre.com.br/p/MLB123456789?utm_medium=social")
    assert ml1 == "ml:MLB123456789"
    assert ml2 == "ml:MLB123456789"

    urls = extract_canonical_urls("Confira: https://www.amazon.com.br/dp/B0BBWH1R8H)")
    assert urls == ["amazon:B0BBWH1R8H"]
    fps = compute_deal_fingerprints("SSD Kingston 1TB R$ 349,90", "ssd nvme 1tb", 349.9, urls)
    assert any(fp.startswith("url:") for fp in fps)
    assert any(fp.startswith("sig:") for fp in fps)


def test_format_rule_line_roundtrip():
    line = format_rule_line("ssd nvme 1tb", ["kootion", "sata"], 380.0, False)
    assert line == "ssd nvme 1tb -kootion -sata <= 380"


def test_accent_insensitive_matching():
    rule_accented = parse_rule("Tênis Vans")
    assert rule_accented is not None
    assert evaluate_rule(rule_accented, "Promocao Tenis Vans Old Skool")[0] is True
    assert evaluate_rule(rule_accented, "TÊNIS VANS")[0] is True

    rule_plain = parse_rule("tenis vans")
    assert rule_plain is not None
    assert evaluate_rule(rule_plain, "Tênis Vans Old Skool")[0] is True

    rule_rtx = parse_rule("rtx 4060")
    assert rule_rtx is not None
    matched, reason = evaluate_rule(rule_rtx, "Leilão RTX 4060", global_blacklist=["leilao"])
    assert matched is False
    assert reason == "blacklist"
