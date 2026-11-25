"""FastAPI Web UI and REST API server for Bot of Bots."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.categories import detect_category_name
from app.config import Settings
from app.database import Database
from app.engine import DealEngine
from app.keywords_sync import KeywordsSyncManager
from app.rules import (
    evaluate_rule,
    extract_canonical_urls,
    extract_price,
    format_rule_line,
    parse_rule,
)


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
_STATIC_DIR = Path(__file__).parent / "static"
_COOKIE_NAME = "botofbots_session"


class LoginPayload(BaseModel):
    password: str


class PausePayload(BaseModel):
    paused: bool | None = None


class KeywordCreatePayload(BaseModel):
    raw_line: str | None = None
    term: str | None = None
    negatives: list[str] = []
    max_price: float | None = None
    strict_lt: bool = False


class KeywordUpdatePayload(BaseModel):
    term: str | None = None
    enabled: bool | None = None
    max_price: float | None = None
    clear_max_price: bool = False
    negatives: list[str] | None = None
    strict_lt: bool | None = None


class SimulatePayload(BaseModel):
    rule: str
    sample_message: str
    use_global_blacklist: bool = True


class BlacklistCreatePayload(BaseModel):
    word: str


class UserUpdatePayload(BaseModel):
    active: bool | None = None
    max_terms: int | None = None


class ChannelUpdatePayload(BaseModel):
    chat_id: str | None = None
    enabled: bool | None = None
    keywords: list[str] | None = None
    name: str | None = None


class RuntimeSettingsPayload(BaseModel):
    dedup_window_minutes: int | None = None
    require_price_for_cap: bool | None = None


def _session_token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"botofbots-web-ui", hashlib.sha256).hexdigest()


def create_app(
    db: Database,
    sync_manager: KeywordsSyncManager,
    settings: Settings,
    start_time: float | None = None,
    get_monitored_chats_count: Callable[[], int] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    app = FastAPI(title="Bot of Bots Dashboard", version="2.0.0")
    boot_time = start_time or time.time()
    chats_count_fn = get_monitored_chats_count or (lambda: 0)

    def _is_authenticated(request: Request) -> bool:
        pwd = settings.web_password.strip()
        if not pwd:
            return True
        api_key = request.headers.get("X-API-Key", "").strip()
        if api_key and hmac.compare_digest(api_key, pwd):
            return True
        cookie_val = request.cookies.get(_COOKIE_NAME, "")
        expected = _session_token(pwd)
        return bool(cookie_val) and hmac.compare_digest(cookie_val, expected)

    async def require_auth(request: Request) -> None:
        if not _is_authenticated(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Autenticação necessária",
            )

    @app.get("/", response_class=HTMLResponse)
    @app.get("/guia", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if not _TEMPLATE_PATH.exists():
            return HTMLResponse("<h1>Dashboard template not found</h1>", status_code=500)
        return HTMLResponse(_TEMPLATE_PATH.read_text(encoding="utf-8"))

    @app.get("/manifest.json")
    async def pwa_manifest() -> FileResponse:
        return FileResponse(
            _STATIC_DIR / "manifest.json",
            media_type="application/json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/sw.js")
    async def pwa_service_worker() -> FileResponse:
        return FileResponse(
            _STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/pwa-192x192.png")
    async def pwa_icon_192() -> FileResponse:
        return FileResponse(_STATIC_DIR / "pwa-192x192.png", media_type="image/png")

    @app.get("/pwa-512x512.png")
    async def pwa_icon_512() -> FileResponse:
        return FileResponse(_STATIC_DIR / "pwa-512x512.png", media_type="image/png")

    @app.get("/favicon.svg")
    async def favicon_svg() -> FileResponse:
        return FileResponse(_STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/favicon.ico")
    async def favicon_ico() -> FileResponse:
        return FileResponse(_STATIC_DIR / "pwa-192x192.png", media_type="image/png")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "paused": db.is_paused(),
            "uptime_seconds": max(0, int(time.time() - boot_time)),
        }

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, bool]:
        return {
            "auth_required": bool(settings.web_password.strip()),
            "authenticated": _is_authenticated(request),
        }

    @app.post("/api/login")
    async def login(payload: LoginPayload, response: Response) -> dict[str, bool]:
        pwd = settings.web_password.strip()
        if not pwd:
            return {"ok": True}
        if not hmac.compare_digest(payload.password, pwd):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Senha inválida")
        response.set_cookie(
            key=_COOKIE_NAME,
            value=_session_token(pwd),
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,
        )
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(_COOKIE_NAME)
        return {"ok": True}

    @app.get("/api/status", dependencies=[Depends(require_auth)])
    async def get_status() -> dict[str, Any]:
        today = db.get_today_stats()
        totals = db.get_total_stats()
        kws = db.list_keywords(only_enabled=True)
        bl = db.list_blacklist()
        users = db.list_users()
        dedup_win = int(db.get_setting("dedup_window_minutes") or settings.dedup_window_minutes)
        req_price = (
            db.get_setting("require_price_for_cap") or str(settings.require_price_for_cap)
        ).lower() == "true"

        return {
            "paused": db.is_paused(),
            "uptime_seconds": max(0, int(time.time() - boot_time)),
            "monitored_chats": chats_count_fn(),
            "today": today,
            "totals": totals,
            "active_keywords_count": len(kws),
            "blacklist_count": len(bl),
            "users_count": len(users),
            "dedup_window_minutes": dedup_win,
            "require_price_for_cap": req_price,
        }

    @app.post("/api/pause", dependencies=[Depends(require_auth)])
    async def toggle_pause(payload: PausePayload | None = None) -> dict[str, bool]:
        if payload is not None and payload.paused is not None:
            new_state = payload.paused
        else:
            new_state = not db.is_paused()
        db.set_paused(new_state)
        return {"paused": db.is_paused()}

    @app.get("/api/keywords", dependencies=[Depends(require_auth)])
    async def list_keywords() -> dict[str, Any]:
        return {"keywords": db.list_keywords(only_enabled=False)}

    @app.post("/api/keywords", status_code=201, dependencies=[Depends(require_auth)])
    async def create_keyword(payload: KeywordCreatePayload) -> dict[str, Any]:
        if payload.raw_line and payload.raw_line.strip():
            raw = payload.raw_line.strip()
        elif payload.term and payload.term.strip():
            raw = format_rule_line(
                payload.term, payload.negatives, payload.max_price, payload.strict_lt
            )
        else:
            raise HTTPException(status_code=400, detail="Informe 'raw_line' ou 'term'.")

        try:
            kw = db.add_keyword(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_manager.export_to_file()
        return {"keyword": kw}

    @app.patch("/api/keywords/{kw_id}", dependencies=[Depends(require_auth)])
    async def patch_keyword(kw_id: int, payload: KeywordUpdatePayload) -> dict[str, Any]:
        try:
            kw = db.update_keyword(
                kw_id,
                enabled=payload.enabled,
                max_price=payload.max_price,
                clear_max_price=payload.clear_max_price,
                negatives=payload.negatives,
                strict_lt=payload.strict_lt,
                term=payload.term,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not kw:
            raise HTTPException(status_code=404, detail="Palavra-chave não encontrada")
        sync_manager.export_to_file()
        return {"keyword": kw}

    @app.post("/api/simulate", dependencies=[Depends(require_auth)])
    async def simulate_rule(payload: SimulatePayload) -> dict[str, Any]:
        parsed = parse_rule(payload.rule)
        detected_price = extract_price(payload.sample_message)
        canonical_urls = extract_canonical_urls(payload.sample_message)
        channels = db.list_thematic_channels()
        detected_category = detect_category_name(payload.sample_message, channels)

        if parsed is None:
            return {
                "valid": False,
                "parsed": None,
                "detected_price": detected_price,
                "detected_category": detected_category,
                "canonical_urls": canonical_urls,
                "would_alert": False,
                "reason": "invalid_rule",
                "explanation": "Regra vazia ou inválida. Informe pelo menos uma palavra positiva (ex: ssd nvme 1tb <= 1000).",
            }

        parsed_info = {
            "raw": parsed.raw,
            "term": parsed.term,
            "positives": parsed.positives,
            "negatives": parsed.negatives,
            "max_price": parsed.max_price,
            "strict_lt": parsed.strict_lt,
            "is_global_blacklist": parsed.is_global_blacklist,
        }

        if parsed.is_global_blacklist:
            return {
                "valid": True,
                "parsed": parsed_info,
                "detected_price": detected_price,
                "detected_category": detected_category,
                "canonical_urls": canonical_urls,
                "would_alert": False,
                "reason": "global_blacklist_rule",
                "explanation": "Esta sintaxe define uma palavra de Blacklist Global (bloqueia outras regras), não um alerta positivo.",
            }

        global_bl = db.list_blacklist() if payload.use_global_blacklist else []
        matched, block_reason = evaluate_rule(parsed, payload.sample_message, global_bl)

        if block_reason == "blacklist":
            return {
                "valid": True,
                "parsed": parsed_info,
                "detected_price": detected_price,
                "detected_category": detected_category,
                "canonical_urls": canonical_urls,
                "would_alert": False,
                "reason": "blacklist",
                "explanation": "As palavras positivas casaram, mas o alerta foi BLOQUEADO por uma marca/termo negativo ou pela Blacklist Global.",
            }

        if not matched:
            return {
                "valid": True,
                "parsed": parsed_info,
                "detected_price": detected_price,
                "detected_category": detected_category,
                "canonical_urls": canonical_urls,
                "would_alert": False,
                "reason": "no_match",
                "explanation": "A mensagem não contém todas as palavras positivas exigidas pela regra.",
            }

        req_price = (
            db.get_setting("require_price_for_cap") or str(settings.require_price_for_cap)
        ).lower() == "true"

        if not DealEngine._check_price_cap(
            parsed.max_price, parsed.strict_lt, detected_price, req_price
        ):
            if detected_price is None:
                return {
                    "valid": True,
                    "parsed": parsed_info,
                    "detected_price": None,
                    "detected_category": detected_category,
                    "canonical_urls": canonical_urls,
                    "would_alert": False,
                    "reason": "missing_price",
                    "explanation": "A regra define um preço máximo, mas nenhum preço à vista válido foi detectado na mensagem.",
                }
            op = "<" if parsed.strict_lt else "<="
            return {
                "valid": True,
                "parsed": parsed_info,
                "detected_price": detected_price,
                "detected_category": detected_category,
                "canonical_urls": canonical_urls,
                "would_alert": False,
                "reason": "price_above_cap",
                "explanation": f"Preço detectado (R$ {detected_price:.2f}) está acima do teto configurado ({op} R$ {parsed.max_price:.2f}). Alerta descartado!",
            }

        return {
            "valid": True,
            "parsed": parsed_info,
            "detected_price": detected_price,
            "detected_category": detected_category,
            "canonical_urls": canonical_urls,
            "would_alert": True,
            "reason": "matched",
            "explanation": "Sucesso! Todas as palavras casaram, nenhuma exclusão foi ativada e o preço respeita o teto configurado.",
        }

    @app.delete("/api/keywords/{kw_id}", dependencies=[Depends(require_auth)])
    async def remove_keyword(kw_id: int) -> dict[str, bool]:
        ok = db.delete_keyword(kw_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Palavra-chave não encontrada")
        sync_manager.export_to_file()
        return {"deleted": True}

    @app.get("/api/blacklist", dependencies=[Depends(require_auth)])
    async def list_blacklist() -> dict[str, list[str]]:
        return {"words": db.list_blacklist()}

    @app.post("/api/blacklist", status_code=201, dependencies=[Depends(require_auth)])
    async def add_blacklist(payload: BlacklistCreatePayload) -> dict[str, str]:
        try:
            word = db.add_blacklist(payload.word)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_manager.export_to_file()
        return {"word": word}

    @app.delete("/api/blacklist/{word}", dependencies=[Depends(require_auth)])
    async def remove_blacklist(word: str) -> dict[str, bool]:
        ok = db.delete_blacklist(word)
        if not ok:
            raise HTTPException(status_code=404, detail="Palavra não encontrada na blacklist")
        sync_manager.export_to_file()
        return {"deleted": True}

    @app.get("/api/deals", dependencies=[Depends(require_auth)])
    async def list_deals(
        limit: int = 50,
        term: str | None = None,
        category: str | None = None,
        channel: str | None = None,
        max_price: float | None = None,
        search: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        deals = db.list_deals(
            limit=limit,
            term=term,
            category=category,
            channel=channel,
            max_price=max_price,
            search=search,
            status=status,
        )
        return {"deals": deals}

    @app.get("/api/analytics/prices", dependencies=[Depends(require_auth)])
    async def analytics_prices(term: str | None = None, days: int = 30) -> dict[str, Any]:
        return db.get_price_history(term=term, days=days)

    @app.get("/api/analytics/channels", dependencies=[Depends(require_auth)])
    async def analytics_channels(days: int = 30) -> dict[str, Any]:
        return {"channels": db.get_channel_ranking(days=days)}

    @app.get("/api/users", dependencies=[Depends(require_auth)])
    async def list_users() -> dict[str, Any]:
        return {"users": db.list_users()}

    @app.patch("/api/users/{chat_id}", dependencies=[Depends(require_auth)])
    async def patch_user(chat_id: int, payload: UserUpdatePayload) -> dict[str, Any]:
        user = db.update_user(chat_id, active=payload.active, max_terms=payload.max_terms)
        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        return {"user": user}

    @app.delete("/api/users/{chat_id}/terms/{term_id}", dependencies=[Depends(require_auth)])
    async def delete_user_term(chat_id: int, term_id: int) -> dict[str, bool]:
        ok = db.delete_user_term_by_id(chat_id, term_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Alerta de usuário não encontrado")
        return {"deleted": True}

    @app.get("/api/channels", dependencies=[Depends(require_auth)])
    async def list_channels() -> dict[str, Any]:
        return {"channels": db.list_thematic_channels()}

    @app.put("/api/channels/{slug}", dependencies=[Depends(require_auth)])
    async def update_channel(slug: str, payload: ChannelUpdatePayload) -> dict[str, Any]:
        ch = db.update_thematic_channel(
            slug=slug,
            chat_id=payload.chat_id,
            enabled=payload.enabled,
            keywords=payload.keywords,
            name=payload.name,
        )
        if not ch:
            raise HTTPException(status_code=404, detail="Canal temático não encontrado")
        return {"channel": ch}

    @app.get("/api/settings", dependencies=[Depends(require_auth)])
    async def get_runtime_settings() -> dict[str, Any]:
        return {
            "dedup_window_minutes": int(
                db.get_setting("dedup_window_minutes") or settings.dedup_window_minutes
            ),
            "require_price_for_cap": (
                db.get_setting("require_price_for_cap") or str(settings.require_price_for_cap)
            ).lower()
            == "true",
        }

    @app.patch("/api/settings", dependencies=[Depends(require_auth)])
    async def patch_runtime_settings(payload: RuntimeSettingsPayload) -> dict[str, Any]:
        if payload.dedup_window_minutes is not None:
            db.set_setting("dedup_window_minutes", str(max(1, payload.dedup_window_minutes)))
        if payload.require_price_for_cap is not None:
            db.set_setting(
                "require_price_for_cap",
                "true" if payload.require_price_for_cap else "false",
            )
        return await get_runtime_settings()

    return app
