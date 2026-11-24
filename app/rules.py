"""Smart rule parsing, negative/brand blacklist evaluation, BRL price parsing and deduplication fingerprinting."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_UNIT_TOKEN_RE = re.compile(r"^(\d+)(tb|gb|mb|hz|w)$", re.IGNORECASE)
_PRICE_CAP_RE = re.compile(r"(<=|<)\s*(?:R\$\s*)?([\d.,]+)", re.IGNORECASE)

_INSTALLMENT_RE = re.compile(
    r"\b\d{1,2}\s*x\s*(?:(?:sem|com)\s+juros\s*)?(?:de\s*)?(?:R\$\s*)?[\d.,]+(?:\s*(?:sem\s+juros|com\s+juros|juros|no\s+cart[aã]o))?",
    re.IGNORECASE,
)
_PARCELA_RE = re.compile(
    r"\bparcela[sd]?\s*(?:de\s*)?(?:R\$\s*)?[\d.,]+",
    re.IGNORECASE,
)
_EXTRA_MONEY_RE = re.compile(
    r"(?:cupom|desconto|frete|cashback|economize|economia|baixou|caiu|menos|abate|ganhe|off)\s*(?:de\s*)?(?:-\s*)?(?:R\$\s*)?[\d.,]+",
    re.IGNORECASE,
)
_TRAILING_MONEY_RE = re.compile(
    r"R\$\s*[\d.,]+\s*(?:(?:de\s+)?(?:desconto|cashback|economia|cupom|frete|off)|a\s+menos|mais\s+barato)\b",
    re.IGNORECASE,
)
_BRL_PRICE_RE = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)
_POR_PRICE_RE = re.compile(
    r"\bpor\s+(?:apenas\s+)?([\d.,]+)(?!\s*(?:tb|gb|mb|hz|w|anos?|meses?|dias?|x\b|%|unidades?))\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_AMAZON_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)", re.IGNORECASE)
_ML_ID_RE = re.compile(r"(MLB-?\d+)", re.IGNORECASE)
_TRACKING_PARAM_PREFIXES = ("utm_", "matt_")
_TRACKING_PARAMS = {
    "ref",
    "ref_",
    "tag",
    "fbclid",
    "gclid",
    "aff_id",
    "source",
    "click_id",
    "gbraid",
    "wbraid",
    "spm",
    "aff_fcid",
    "aff_fsk",
    "aff_platform",
    "sk",
    "linkcode",
    "linkid",
    "camp",
    "creative",
    "creativeasin",
    "ascsubtag",
    "psc",
    "th",
}


@dataclass
class ParsedRule:
    """Structured representation of a keyword monitoring or global blacklist rule."""

    raw: str
    term: str
    positives: list[str]
    negatives: list[str]
    max_price: float | None
    strict_lt: bool
    is_global_blacklist: bool
    regex: re.Pattern[str] | None
    positive_regex: re.Pattern[str] | None


def parse_price_value(raw_num: str) -> float | None:
    """Parse Brazilian and international numeric strings (e.g. '1.750,00', '1.899', '379,90')."""
    if not raw_num:
        return None
    s = raw_num.strip().strip(".,")
    if not s:
        return None

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2:
            s = f"{parts[0]}.{parts[1]}"
        else:
            s = "".join(parts[:-1]) + "." + parts[-1]
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:
            if len(parts[-1]) == 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
            # e.g. '1.899' in BRL means 1899
            s = parts[0] + parts[1]

    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


def _build_positive_pattern(token: str) -> str:
    m_cap = _UNIT_TOKEN_RE.match(token)
    if m_cap:
        num, unit = m_cap.groups()
        return rf"(?=.*\b{re.escape(num)}\s*{re.escape(unit)}\b)"
    return rf"(?=.*\b{re.escape(token)}\b)"


def parse_rule(raw_line: str) -> ParsedRule | None:
    """Parse a keyword rule string into positive tokens, negative exclusions and price cap."""
    cleaned = raw_line.strip()
    if not cleaned or cleaned.startswith("#"):
        return None

    max_price: float | None = None
    strict_lt = False

    cap_match = _PRICE_CAP_RE.search(cleaned)
    if cap_match:
        op, num_str = cap_match.groups()
        parsed_cap = parse_price_value(num_str)
        if parsed_cap is not None:
            max_price = parsed_cap
            strict_lt = op == "<"
            cleaned = (cleaned[: cap_match.start()] + " " + cleaned[cap_match.end() :]).strip()

    positives: list[str] = []
    negatives: list[str] = []
    seen_neg: set[str] = set()

    for tok in cleaned.split():
        if len(tok) > 1 and tok[0] in {"-", "!"}:
            neg = tok[1:].strip().lower()
            if neg and neg not in seen_neg:
                seen_neg.add(neg)
                negatives.append(neg)
        else:
            positives.append(tok)

    if not positives and not negatives:
        return None

    if not positives:
        return ParsedRule(
            raw=raw_line.strip(),
            term=" ".join(f"-{n}" for n in negatives),
            positives=[],
            negatives=negatives,
            max_price=None,
            strict_lt=False,
            is_global_blacklist=True,
            regex=None,
            positive_regex=None,
        )

    term = " ".join(positives)
    pos_patterns = "".join(_build_positive_pattern(p) for p in positives)
    neg_patterns = "".join(rf"(?!.*\b{re.escape(n)}\b)" for n in negatives)

    try:
        positive_regex = re.compile(f"^{pos_patterns}", re.IGNORECASE | re.DOTALL)
        full_regex = re.compile(f"^{pos_patterns}{neg_patterns}", re.IGNORECASE | re.DOTALL)
    except re.error:
        return None

    return ParsedRule(
        raw=raw_line.strip(),
        term=term,
        positives=positives,
        negatives=negatives,
        max_price=max_price,
        strict_lt=strict_lt,
        is_global_blacklist=False,
        regex=full_regex,
        positive_regex=positive_regex,
    )


def format_rule_line(
    term: str,
    negatives: Iterable[str] = (),
    max_price: float | None = None,
    strict_lt: bool = False,
) -> str:
    """Format a rule back into canonical keywords.txt syntax."""
    parts = [term.strip()]
    for neg in negatives:
        clean_neg = neg.strip().lstrip("-!").lower()
        if clean_neg:
            parts.append(f"-{clean_neg}")
    if max_price is not None:
        op = "<" if strict_lt else "<="
        price_str = f"{int(max_price)}" if float(max_price).is_integer() else f"{max_price:.2f}"
        parts.append(f"{op} {price_str}")
    return " ".join(p for p in parts if p)


def evaluate_rule(
    rule: ParsedRule,
    text: str,
    global_blacklist: Iterable[str] = (),
) -> tuple[bool, str | None]:
    """
    Evaluate a rule against message text.
    Returns (matched, block_reason) where block_reason is 'blacklist' if positives matched
    but a rule-level negative or global blacklist word blocked it.
    """
    if rule.is_global_blacklist or rule.positive_regex is None or not text:
        return False, None

    if not rule.positive_regex.search(text):
        return False, None

    combined_negatives: list[str] = list(rule.negatives)
    for g in global_blacklist:
        clean_g = g.strip().lstrip("-!").lower()
        if clean_g and clean_g not in combined_negatives:
            combined_negatives.append(clean_g)

    for neg in combined_negatives:
        if re.search(rf"\b{re.escape(neg)}\b", text, re.IGNORECASE):
            return False, "blacklist"

    return True, None


def extract_price(text: str) -> float | None:
    """
    Extract the lowest main BRL price from a deal message, ignoring installments,
    coupons, shipping costs and cashback values.
    """
    if not text:
        return None

    cleaned = _INSTALLMENT_RE.sub(" ", text)
    cleaned = _PARCELA_RE.sub(" ", cleaned)
    cleaned = _TRAILING_MONEY_RE.sub(" ", cleaned)
    cleaned = _EXTRA_MONEY_RE.sub(" ", cleaned)

    values: list[float] = []
    for match in _BRL_PRICE_RE.findall(cleaned):
        val = parse_price_value(match)
        if val is not None:
            values.append(val)

    if not values:
        for match in _POR_PRICE_RE.findall(cleaned):
            val = parse_price_value(match)
            if val is not None and val >= 10:
                values.append(val)

    return min(values) if values else None


def canonicalize_url(raw_url: str) -> str:
    """Normalize a URL by removing tracking parameters and extracting canonical store IDs."""
    url = raw_url.strip().rstrip(".,;:!?)]}")
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path or ""

    if "amazon." in netloc:
        asin_match = _AMAZON_ASIN_RE.search(path)
        if asin_match:
            return f"amazon:{asin_match.group(1).upper()}"

    if "mercadolivre." in netloc or "meli.la" in netloc:
        ml_match = _ML_ID_RE.search(url)
        if ml_match:
            return f"ml:{ml_match.group(1).replace('-', '').upper()}"

    filtered_params = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
        and not any(k.lower().startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES)
    ]
    norm_path = path.rstrip("/") if path != "/" else "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            norm_path,
            "",
            urlencode(sorted(filtered_params)),
            "",
        )
    )


def extract_canonical_urls(text: str) -> list[str]:
    """Extract deduplicated canonical URLs from message text."""
    if not text:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.findall(text):
        canon = canonicalize_url(match)
        if canon and canon not in seen:
            seen.add(canon)
            urls.append(canon)
    return urls


def compute_deal_fingerprints(
    text: str,
    term: str,
    price: float | None,
    canonical_urls: list[str],
) -> list[str]:
    """Compute deduplication fingerprints for canonical URLs and normalized deal content."""
    fingerprints: list[str] = []
    for url in canonical_urls:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        fingerprints.append(f"url:{digest}")

    text_no_urls = _URL_RE.sub(" ", text or "").lower()
    tokens = re.findall(r"[a-z0-9]+", text_no_urls)
    stopwords = {"de", "da", "do", "em", "por", "no", "na", "com", "para", "r", "a", "o", "e", "ou", "via", "pix"}
    core_tokens = [t for t in tokens if len(t) >= 2 and t not in stopwords]

    if core_tokens:
        price_key = f"{price:.2f}" if price is not None else "na"
        sig_payload = f"{term.lower()}|{price_key}|{' '.join(core_tokens[:24])}"
        sig_digest = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()
        fingerprints.append(f"sig:{sig_digest}")

    return fingerprints
