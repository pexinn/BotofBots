"""Telegram Bot command router for both Admin management and Multi-User subscriptions."""

from __future__ import annotations

import html
import time
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.database import Database
from app.keywords_sync import KeywordsSyncManager
from app.rules import parse_rule


BOT_MENU_COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Iniciar e ver instruções"},
    {"command": "alerta", "description": "Criar alerta pessoal (ex: /alerta rtx 4060 <= 1800)"},
    {"command": "meusalertas", "description": "Listar seus alertas cadastrados"},
    {"command": "remover", "description": "Remover um alerta pessoal"},
    {"command": "help", "description": "Ver ajuda e sintaxe de filtros"},
]


def _format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


class CommandHandler:
    """Processes Telegram Bot commands with role-based separation (Admin vs User)."""

    def __init__(
        self,
        db: Database,
        sync_manager: KeywordsSyncManager,
        settings: Settings,
        start_time: float | None = None,
        get_monitored_chats_count: Callable[[], int] | None = None,
    ) -> None:
        self.db = db
        self.sync_manager = sync_manager
        self.settings = settings
        self.start_time = start_time or time.time()
        self.get_monitored_chats_count = get_monitored_chats_count or (lambda: 0)

    def is_admin(self, user_id: int | None) -> bool:
        return (
            self.settings.admin_user_id is not None
            and user_id is not None
            and int(user_id) == int(self.settings.admin_user_id)
        )

    async def handle_update(
        self,
        update: dict[str, Any],
        send_reply: Callable[[int, str], Awaitable[None]],
    ) -> str | None:
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return None

        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if chat_id is None:
            return None

        chat_id = int(chat_id)
        user_id_int = int(user_id) if user_id is not None else None
        username = sender.get("username")
        first_name = sender.get("first_name")

        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        reply = self._dispatch(cmd, arg, chat_id, user_id_int, username, first_name)
        if reply:
            await send_reply(chat_id, reply)
        return reply

    def _dispatch(
        self,
        cmd: str,
        arg: str,
        chat_id: int,
        user_id: int | None,
        username: str | None,
        first_name: str | None,
    ) -> str:
        # Public / Multi-user commands
        if cmd == "/start":
            self.db.upsert_user(
                chat_id,
                username=username,
                first_name=first_name,
                default_max_terms=self.settings.max_terms_per_user,
            )
            return self._help_text(is_admin=self.is_admin(user_id), welcome=True)

        if cmd == "/help":
            return self._help_text(is_admin=self.is_admin(user_id), welcome=False)

        if cmd == "/alerta":
            self.db.upsert_user(
                chat_id,
                username=username,
                first_name=first_name,
                default_max_terms=self.settings.max_terms_per_user,
            )
            return self._cmd_user_add(chat_id, arg, is_admin=self.is_admin(user_id))

        if cmd == "/meusalertas":
            self.db.upsert_user(
                chat_id,
                username=username,
                first_name=first_name,
                default_max_terms=self.settings.max_terms_per_user,
            )
            return self._cmd_user_list(chat_id)

        if cmd == "/remover":
            return self._cmd_user_remove(chat_id, arg)

        # Admin-only commands
        admin_cmds = {
            "/add",
            "/del",
            "/list",
            "/block",
            "/unblock",
            "/blacklist",
            "/pause",
            "/resume",
            "/status",
            "/usuarios",
            "/limite",
            "/canais",
        }
        if cmd in admin_cmds:
            if not self.is_admin(user_id):
                return "⛔ <b>Acesso negado.</b> Este comando é restrito ao administrador do bot."
            return self._dispatch_admin(cmd, arg)

        return "❓ Comando não reconhecido. Envie /help para ver os comandos disponíveis."

    def _dispatch_admin(self, cmd: str, arg: str) -> str:
        if cmd == "/list":
            return self._cmd_admin_list()
        if cmd == "/add":
            return self._cmd_admin_add(arg)
        if cmd == "/del":
            return self._cmd_admin_del(arg)
        if cmd == "/block":
            return self._cmd_admin_block(arg)
        if cmd == "/unblock":
            return self._cmd_admin_unblock(arg)
        if cmd == "/blacklist":
            return self._cmd_admin_blacklist()
        if cmd == "/pause":
            self.db.set_paused(True)
            return "⏸️ <b>Monitoramento pausado.</b> Os alertas estão suspensos até você enviar /resume."
        if cmd == "/resume":
            self.db.set_paused(False)
            return "▶️ <b>Monitoramento retomado!</b> Os alertas voltaram a ser disparados em tempo real."
        if cmd == "/status":
            return self._cmd_admin_status()
        if cmd == "/usuarios":
            return self._cmd_admin_users()
        if cmd == "/limite":
            return self._cmd_admin_limit(arg)
        if cmd == "/canais":
            return self._cmd_admin_channels()
        return "❓ Comando inválido."

    def _help_text(self, is_admin: bool, welcome: bool) -> str:
        header = (
            "🤖 <b>Bem-vindo ao Bot of Bots!</b>\n"
            "Eu monitoro dezenas de canais de promoções em tempo real e aviso você no privado quando surgir o produto certo.\n\n"
            if welcome
            else "📖 <b>Ajuda — Bot of Bots</b>\n\n"
        )
        user_section = (
            "🎯 <b>Seus Alertas Pessoais:</b>\n"
            "• <code>/alerta &lt;produto&gt;</code> — Cadastra um alerta\n"
            "• <code>/meusalertas</code> — Lista seus alertas ativos\n"
            "• <code>/remover &lt;número ou nome&gt;</code> — Remove um alerta\n\n"
            "💡 <b>Filtros inteligentes suportados:</b>\n"
            "• <b>Exclusão de marcas/termos:</b> <code>/alerta ssd nvme 1tb -kootion -sata</code>\n"
            "• <b>Preço máximo (teto):</b> <code>/alerta rtx 4060 &lt;= 1750</code>\n"
            "• <b>Combinado:</b> <code>/alerta ssd nvme 1tb -kootion &lt;= 380</code>\n"
        )
        if not is_admin:
            return header + user_section

        admin_section = (
            "\n🛡️ <b>Comandos de Administrador (Canal Principal):</b>\n"
            "• <code>/add &lt;regra&gt;</code> — Adiciona regra global + keywords.txt\n"
            "• <code>/del &lt;número ou termo&gt;</code> — Remove regra global\n"
            "• <code>/list</code> — Lista regras e blacklist global\n"
            "• <code>/block &lt;palavra&gt;</code> / <code>/unblock &lt;palavra&gt;</code> — Blacklist global\n"
            "• <code>/blacklist</code> — Lista palavras bloqueadas globalmente\n"
            "• <code>/pause</code> / <code>/resume</code> — Pausa ou retoma monitoramento\n"
            "• <code>/status</code> — Métricas e saúde do bot\n"
            "• <code>/usuarios</code> — Lista inscritos multi-usuário\n"
            "• <code>/limite &lt;chat_id&gt; &lt;n&gt;</code> — Ajusta limite de alertas de um usuário\n"
            "• <code>/canais</code> — Status dos canais temáticos\n"
        )
        return header + user_section + admin_section

    def _format_rule_details(self, rule: dict[str, Any]) -> str:
        details: list[str] = []
        if rule.get("negatives"):
            negs = ", ".join(f"-{html.escape(n)}" for n in rule["negatives"])
            details.append(f"bloqueando <code>{negs}</code>")
        if rule.get("max_price") is not None:
            op = "&lt;" if rule.get("strict_lt") else "&lt;="
            details.append(f"teto <code>{op} R$ {rule['max_price']:.2f}</code>")
        return f" ({'; '.join(details)})" if details else ""

    def _cmd_user_add(self, chat_id: int, arg: str, is_admin: bool) -> str:
        if not arg:
            return (
                "ℹ️ Use: <code>/alerta ssd nvme 1tb -kootion &lt;= 380</code>\n"
                "Você pode combinar palavras, exclusões com <code>-</code> e preço máximo com <code>&lt;=</code>."
            )
        try:
            term = self.db.add_user_term(
                chat_id,
                arg,
                default_max_terms=self.settings.max_terms_per_user,
                is_admin=is_admin,
            )
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}"

        extra = self._format_rule_details(term)
        return f"✅ Alerta pessoal adicionado: <code>{html.escape(term['raw_line'])}</code>{extra}"

    def _cmd_user_list(self, chat_id: int) -> str:
        user = self.db.get_user(chat_id)
        terms = user["terms"] if user else []
        max_terms = user["max_terms"] if user else self.settings.max_terms_per_user
        if not terms:
            return (
                "📭 Você ainda não tem alertas pessoais cadastrados.\n"
                "Envie <code>/alerta ssd nvme 1tb</code> para começar!"
            )
        lines = [f"🎯 <b>Seus Alertas ({len(terms)}/{max_terms}):</b>\n"]
        for i, t in enumerate(terms, start=1):
            lines.append(f"{i}. <code>{html.escape(t['raw_line'])}</code>")
        lines.append("\nPara remover, envie <code>/remover &lt;número&gt;</code>.")
        return "\n".join(lines)

    def _cmd_user_remove(self, chat_id: int, arg: str) -> str:
        if not arg:
            return "ℹ️ Informe o número ou nome do alerta: <code>/remover 1</code>"
        removed = self.db.remove_user_term(chat_id, arg)
        if not removed:
            return f"❌ Alerta <code>{html.escape(arg)}</code> não encontrado na sua lista."
        return f"🗑️ Alerta removido: <code>{html.escape(removed['raw_line'])}</code>"

    def _cmd_admin_list(self) -> str:
        kws = self.db.list_keywords(only_enabled=False)
        bl = self.db.list_blacklist()
        lines: list[str] = []
        if kws:
            lines.append(f"📋 <b>Palavras Monitoradas ({len(kws)}):</b>\n")
            for i, kw in enumerate(kws, start=1):
                status_icon = "✅" if kw["enabled"] else "⏸️"
                lines.append(f"{i}. {status_icon} <code>{html.escape(kw['raw_line'])}</code>")
        else:
            lines.append("📭 Nenhuma palavra monitorada no canal principal.")

        if bl:
            bl_text = ", ".join(f"<code>-{html.escape(w)}</code>" for w in bl)
            lines.append(f"\n🚫 <b>Blacklist Global:</b> {bl_text}")
        return "\n".join(lines)

    def _cmd_admin_add(self, arg: str) -> str:
        if not arg:
            return "ℹ️ Use: <code>/add ssd nvme 1tb -kootion &lt;= 380</code>"
        parsed = parse_rule(arg)
        if parsed and parsed.is_global_blacklist:
            added: list[str] = []
            for neg in parsed.negatives:
                added.append(self.db.add_blacklist(neg))
            self.sync_manager.export_to_file()
            return f"🚫 Adicionado à blacklist global: <code>{', '.join(added)}</code>"

        try:
            kw = self.db.add_keyword(arg)
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}"
        self.sync_manager.export_to_file()
        extra = self._format_rule_details(kw)
        return f"✅ Termo adicionado e salvo em <code>keywords.txt</code>: <code>{html.escape(kw['raw_line'])}</code>{extra}"

    def _cmd_admin_del(self, arg: str) -> str:
        if not arg:
            return "ℹ️ Informe o número ou termo: <code>/del 1</code>"
        removed = self.db.delete_keyword_by_term_or_index(arg)
        if not removed:
            return f"❌ Termo <code>{html.escape(arg)}</code> não encontrado."
        self.sync_manager.export_to_file()
        return f"🗑️ Termo removido de <code>keywords.txt</code>: <code>{html.escape(removed['raw_line'])}</code>"

    def _cmd_admin_block(self, arg: str) -> str:
        if not arg:
            return "ℹ️ Use: <code>/block kootion</code>"
        try:
            word = self.db.add_blacklist(arg)
        except ValueError as exc:
            return f"⚠️ {html.escape(str(exc))}"
        self.sync_manager.export_to_file()
        return f"🚫 Palavra <code>{html.escape(word)}</code> adicionada à blacklist global."

    def _cmd_admin_unblock(self, arg: str) -> str:
        if not arg:
            return "ℹ️ Use: <code>/unblock kootion</code>"
        ok = self.db.delete_blacklist(arg)
        if not ok:
            return f"❌ Palavra <code>{html.escape(arg)}</code> não estava na blacklist global."
        self.sync_manager.export_to_file()
        return f"✅ Palavra <code>{html.escape(arg.strip().lstrip('-!').lower())}</code> removida da blacklist global."

    def _cmd_admin_blacklist(self) -> str:
        bl = self.db.list_blacklist()
        if not bl:
            return "✅ A blacklist global está vazia."
        items = "\n".join(f"• <code>-{html.escape(w)}</code>" for w in bl)
        return f"🚫 <b>Blacklist Global ({len(bl)}):</b>\n{items}"

    def _cmd_admin_status(self) -> str:
        today = self.db.get_today_stats()
        paused = self.db.is_paused()
        kws = self.db.list_keywords(only_enabled=True)
        bl = self.db.list_blacklist()
        users = self.db.list_users()
        chats_count = self.get_monitored_chats_count()
        uptime = _format_uptime(time.time() - self.start_time)

        return (
            "📊 <b>Status — Bot of Bots</b>\n\n"
            f"• <b>Estado:</b> {'⏸️ Pausado' if paused else '🟢 Ativo'}\n"
            f"• <b>Uptime:</b> <code>{uptime}</code>\n"
            f"• <b>Chats/Canais monitorados:</b> <code>{chats_count}</code>\n"
            f"• <b>Regras ativas:</b> <code>{len(kws)}</code> | <b>Blacklist global:</b> <code>{len(bl)}</code>\n"
            f"• <b>Usuários inscritos:</b> <code>{len(users)}</code>\n\n"
            "📈 <b>Métricas de Hoje:</b>\n"
            f"• Mensagens lidas: <code>{today['messages_processed']}</code>\n"
            f"• Alertas enviados: <code>{today['alerts_sent']}</code>\n"
            f"• Bloqueios (Blacklist/Marca): <code>{today['blacklist_blocked']}</code>\n"
            f"• Bloqueios (Preço teto): <code>{today['price_blocked']}</code>\n"
            f"• Bloqueios (Duplicadas): <code>{today['dedup_blocked']}</code>"
        )

    def _cmd_admin_users(self) -> str:
        users = self.db.list_users()
        if not users:
            return "👥 Nenhum usuário inscrito ainda."
        lines = [f"👥 <b>Usuários Multi-Usuário ({len(users)}):</b>\n"]
        for u in users:
            uname = f"@{u['username']}" if u.get("username") else (u.get("first_name") or "Sem nome")
            status = "🟢" if u["active"] else "🔴"
            lines.append(
                f"{status} <code>{u['chat_id']}</code> ({html.escape(uname)}) — "
                f"<b>{u['term_count']}/{u['max_terms']}</b> alertas"
            )
        return "\n".join(lines)

    def _cmd_admin_limit(self, arg: str) -> str:
        parts = arg.split()
        if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].isdigit():
            return "ℹ️ Use: <code>/limite &lt;chat_id&gt; &lt;quantidade&gt;</code>"
        target_id = int(parts[0])
        new_limit = int(parts[1])
        updated = self.db.update_user(target_id, max_terms=new_limit)
        if not updated:
            return f"❌ Usuário <code>{target_id}</code> não encontrado."
        return f"✅ Limite de alertas do usuário <code>{target_id}</code> atualizado para <b>{updated['max_terms']}</b>."

    def _cmd_admin_channels(self) -> str:
        channels = self.db.list_thematic_channels()
        if not channels:
            return "📢 Nenhum canal temático configurado."
        lines = ["📢 <b>Canais Temáticos:</b>\n"]
        for ch in channels:
            status = "🟢 Ativo" if (ch["enabled"] and ch["chat_id"]) else "⚪ Inativo"
            dest = f"<code>{html.escape(str(ch['chat_id']))}</code>" if ch["chat_id"] else "<i>não configurado</i>"
            lines.append(f"• <b>{html.escape(ch['name'])}</b> ({status}) — {dest}")
        return "\n".join(lines)
