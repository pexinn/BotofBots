"""Main entrypoint for Bot of Bots: Telethon listener, Bot API commands, and FastAPI Web Dashboard."""

from __future__ import annotations

import asyncio
import html
import time
from typing import Any

import uvicorn
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from app.commands import BOT_MENU_COMMANDS, CommandHandler
from app.config import Settings, load_settings
from app.database import Database
from app.engine import DealEngine
from app.keywords_sync import KeywordsSyncManager
from app.telegram_api import TelegramBotAPI
from app.web.server import create_app


async def _poll_bot_commands(
    bot_api: TelegramBotAPI,
    command_handler: CommandHandler,
    stop_event: asyncio.Event,
) -> None:
    """Long-poll Telegram Bot API getUpdates for admin and multi-user commands."""
    try:
        await bot_api.set_my_commands(BOT_MENU_COMMANDS)
    except Exception as exc:
        print(f"Aviso: Falha ao registrar menu de comandos no Bot API: {exc}", flush=True)

    offset: int | None = None

    async def _reply(chat_id: int, text: str) -> None:
        await bot_api.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)

    while not stop_event.is_set():
        try:
            updates = await bot_api.get_updates(offset=offset, timeout=25)
            for upd in updates:
                upd_id = upd.get("update_id")
                if upd_id is not None:
                    offset = int(upd_id) + 1
                try:
                    await command_handler.handle_update(upd, _reply)
                except Exception as exc:
                    print(f"Erro ao processar comando do Bot API: {exc}", flush=True)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"Aviso no polling do Bot API: {exc}", flush=True)
            await asyncio.sleep(3)


async def _run_web_server(app: Any, host: str, port: int) -> None:
    """Run FastAPI dashboard inside the shared asyncio event loop."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    await server.serve()


async def run_bot(settings: Settings | None = None) -> None:
    """Bootstrap all subsystems and run Telethon, Bot API polling, and Web UI."""
    cfg = settings or load_settings(require_telegram=True)
    start_time = time.time()

    db = Database(cfg.db_path)
    db.seed_thematic_channels(
        {
            "hardware": cfg.channel_hardware,
            "perifericos": cfg.channel_perifericos,
            "games": cfg.channel_games,
        }
    )

    sync_manager = KeywordsSyncManager(
        db=db,
        keywords_file=cfg.keywords_file,
        example_file=cfg.keywords_example_file,
    )
    sync_manager.bootstrap()

    client = TelegramClient(StringSession(cfg.string_session), cfg.api_id, cfg.api_hash)
    bot_api = TelegramBotAPI(cfg.bot_token) if cfg.bot_token else None
    monitored_chats_count = 0

    async def send_alert(destination: int | str, text_html: str) -> bool:
        if bot_api is not None:
            try:
                await bot_api.send_message(
                    destination, text_html, parse_mode="HTML", disable_web_page_preview=False
                )
                return True
            except Exception as exc:
                print(
                    f"Aviso: Falha ao enviar via Bot API ({exc}). Tentando fallback via Telethon...",
                    flush=True,
                )
        try:
            await client.send_message(destination, text_html, parse_mode="html")
            return True
        except Exception as exc:
            print(f"Erro ao enviar mensagem via Telethon para {destination}: {exc}", flush=True)
            return False

    engine = DealEngine(db=db, sync_manager=sync_manager, settings=cfg, send_fn=send_alert)
    command_handler = CommandHandler(
        db=db,
        sync_manager=sync_manager,
        settings=cfg,
        start_time=start_time,
        get_monitored_chats_count=lambda: monitored_chats_count,
    )

    @client.on(events.NewMessage(incoming=True))
    async def monitorar_ofertas(event: events.NewMessage.Event) -> None:
        try:
            texto_original = event.raw_text or ""
            if not texto_original.strip():
                return
            chat = await event.get_chat()
            nome_grupo = getattr(chat, "title", None) or getattr(chat, "username", None) or "Chat Privado"
            username = getattr(chat, "username", None)
            result = await engine.process_message(
                chat_id=event.chat_id,
                chat_name=str(nome_grupo),
                chat_username=username,
                message_id=event.id,
                raw_text=texto_original,
            )
            if not result.get("ignored") and result.get("admin_alerts_sent"):
                print(
                    f"[{time.strftime('%H:%M:%S')}] [{nome_grupo}] Alerta disparado!",
                    flush=True,
                )
        except Exception as exc:
            print(f"Erro ao processar mensagem recebida: {exc}", flush=True)

    stop_event = asyncio.Event()
    tasks: list[asyncio.Task[Any]] = []

    try:
        await client.start()
        me = await client.get_me()
        try:
            dialogs = await client.get_dialogs(limit=200)
            monitored_chats_count = len(dialogs)
        except Exception:
            monitored_chats_count = 0

        kws = db.list_keywords(only_enabled=True)
        bl = db.list_blacklist()
        termos = [kw["raw_line"] for kw in kws]
        modo_envio = "Bot API (Som & Push ativados)" if bot_api else "Telethon Userbot (Silencioso)"

        print("=" * 60)
        print(f"Conectado como: {me.first_name} (@{me.username})")
        print(f"Destino dos Alertas: {cfg.alert_destination}")
        print(f"Modo de Envio: {modo_envio}")
        print(f"Regras Ativas ({len(termos)}): {termos}")
        print(f"Blacklist Global ({len(bl)}): {bl}")
        if cfg.web_enabled:
            print(f"Dashboard Web: http://{cfg.web_host}:{cfg.web_port}")
        print("=" * 60, flush=True)

        termos_html = (
            "\n".join(f"• <code>{html.escape(t)}</code>" for t in termos)
            if termos
            else "<i>Nenhum termo cadastrado</i>"
        )
        web_line = f"\n🌐 <b>Dashboard Web:</b> porta <code>{cfg.web_port}</code>" if cfg.web_enabled else ""
        msg_inicial = (
            "🤖 <b>Bot de Monitoramento Ativado!</b>\n\n"
            f"<b>Conta Monitora:</b> {html.escape(me.first_name or '')} (@{html.escape(me.username or '')})\n"
            f"🎯 <b>Regras Monitoradas ({len(termos)}):</b>\n{termos_html}"
            f"\n🚫 <b>Blacklist Global:</b> <code>{len(bl)}</code> termos"
            f"{web_line}"
            f"\n\n🔔 <b>Notificações:</b> {modo_envio}"
        )
        await send_alert(cfg.alert_destination, msg_inicial)

        if bot_api is not None:
            tasks.append(asyncio.create_task(_poll_bot_commands(bot_api, command_handler, stop_event)))

        if cfg.web_enabled:
            web_app = create_app(
                db=db,
                sync_manager=sync_manager,
                settings=cfg,
                start_time=start_time,
                get_monitored_chats_count=lambda: monitored_chats_count,
            )
            tasks.append(asyncio.create_task(_run_web_server(web_app, cfg.web_host, cfg.web_port)))

        await client.run_until_disconnected()
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if bot_api is not None:
            await bot_api.close()
        await client.disconnect()
        db.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
