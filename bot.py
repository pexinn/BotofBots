import os
import re
import time
import html
import asyncio
import urllib.request
import urllib.parse
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# CONFIGURAÇÕES E CREDENCIAIS
# ==========================================
api_id_raw = os.getenv('API_ID')
api_hash = (os.getenv('API_HASH') or '').strip()
session_str = (os.getenv('TELEGRAM_STRING_SESSION') or '').strip()
destino_alerta_raw = (os.getenv('ALERT_DESTINATION') or 'me').strip()
bot_token = (os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()

if not api_id_raw or not api_hash or not session_str:
    raise ValueError(
        "Credenciais ausentes! Certifique-se de configurar API_ID, "
        "API_HASH e TELEGRAM_STRING_SESSION no seu arquivo .env"
    )

api_id = int(api_id_raw)

# Se for ID numérico de chat/canal (ex: -100xxxxxxx), converte para int
if destino_alerta_raw.lstrip('-').isdigit():
    destino_alerta = int(destino_alerta_raw)
else:
    destino_alerta = destino_alerta_raw

client = TelegramClient(StringSession(session_str), api_id, api_hash)

# ==========================================
# DISPARO DE NOTIFICAÇÃO (BOT API / TELETHON)
# ==========================================
async def enviar_alerta(texto_html):
    """
    Envia a mensagem prioritariamente através do Bot oficial (@BotFather)
    para garantir que o celular do usuário emita som e notificação push.
    Caso o token não esteja configurado, utiliza o próprio cliente Telethon.
    """
    if bot_token:
        try:
            def _post():
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": destino_alerta,
                    "text": texto_html,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False
                }
                data = urllib.parse.urlencode(payload).encode('utf-8')
                req = urllib.request.Request(url, data=data)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read().decode('utf-8')

            await asyncio.to_thread(_post)
            return
        except Exception as e:
            print(f"Aviso: Falha ao enviar via Bot API ({e}). Tentando fallback via Telethon...", flush=True)

    # Fallback para envio direto pela conta Telethon
    await client.send_message(destino_alerta, texto_html, parse_mode='html')

# ==========================================
# MOTOR DINÂMICO DE PALAVRAS-CHAVE
# ==========================================
KEYWORDS_FILE = 'keywords.txt'
KEYWORDS_EXAMPLE_FILE = 'keywords.example.txt'
_last_keywords_mtime = 0
_compiled_rules = []

def compilar_regra_termo(termo_bruto):
    """
    Compila um termo de busca em uma expressão regular inteligente:
    - Normaliza unidades de hardware: '1tb' casa com '1tb' e '1 tb'.
    - Exige que todas as palavras do termo estejam no texto, em qualquer ordem.
    """
    termo = termo_bruto.strip()
    if not termo:
        return None

    palavras = termo.split()
    padroes = []
    for p in palavras:
        m_cap = re.match(r'^(\d+)(tb|gb)$', p.lower())
        if m_cap:
            num, unit = m_cap.groups()
            padroes.append(rf'(?=.*\b{num}\s*{unit}\b)')
        else:
            p_escaped = re.escape(p)
            padroes.append(rf'(?=.*\b{p_escaped}\b)')

    regex_str = ''.join(padroes)
    try:
        return {
            'termo': termo,
            'regex': re.compile(regex_str, re.IGNORECASE)
        }
    except Exception as e:
        print(f"Erro ao compilar regex para '{termo}': {e}", flush=True)
        return None

def obter_regras_monitoradas():
    """
    Recarrega as regras em tempo real se o arquivo keywords.txt tiver sido alterado.
    """
    global _last_keywords_mtime, _compiled_rules
    arquivo_alvo = KEYWORDS_FILE if os.path.exists(KEYWORDS_FILE) else KEYWORDS_EXAMPLE_FILE

    try:
        if os.path.exists(arquivo_alvo):
            mtime = os.path.getmtime(arquivo_alvo)
            if mtime != _last_keywords_mtime:
                _last_keywords_mtime = mtime
                novas_regras = []
                with open(arquivo_alvo, 'r', encoding='utf-8') as f:
                    for line in f:
                        linha = line.strip()
                        if linha and not linha.startswith('#'):
                            regra = compilar_regra_termo(linha)
                            if regra:
                                novas_regras.append(regra)
                _compiled_rules = novas_regras
                termos = [r['termo'] for r in _compiled_rules]
                print(f"[{time.strftime('%H:%M:%S')}] Palavras-chave recarregadas ({len(termos)}): {termos}", flush=True)
        elif not _compiled_rules:
            env_kw = os.getenv('KEYWORDS', 'ssd nvme 1tb')
            novas_regras = []
            for item in env_kw.split(','):
                item = item.strip()
                if item:
                    regra = compilar_regra_termo(item)
                    if regra:
                        novas_regras.append(regra)
            _compiled_rules = novas_regras
            print(f"Palavras-chave carregadas da variável KEYWORDS: {[r['termo'] for r in _compiled_rules]}", flush=True)
    except Exception as e:
        print(f"Erro ao atualizar palavras-chave: {e}", flush=True)

    return _compiled_rules

# ==========================================
# MONITORAMENTO DE MENSAGENS EM TEMPO REAL
# ==========================================
@client.on(events.NewMessage(incoming=True))
async def monitorar_ofertas(event):
    try:
        texto_original = event.raw_text or ''
        if not texto_original:
            return

        # Ignora mensagens geradas no próprio canal/chat de alertas para evitar loops
        if event.chat_id == destino_alerta:
            return

        regras = obter_regras_monitoradas()
        for regra in regras:
            if regra['regex'].search(texto_original):
                termo_encontrado = regra['termo']
                chat = await event.get_chat()
                nome_grupo = getattr(chat, 'title', getattr(chat, 'username', 'Chat Privado'))

                link_html = ''
                if getattr(chat, 'username', None):
                    link_url = f'https://t.me/{chat.username}/{event.id}'
                    link_html = f'\n🔗 <b>Link da Oferta:</b> <a href="{link_url}">{link_url}</a>'

                texto_seguro = html.escape(texto_original[:1200])
                nome_grupo_seguro = html.escape(nome_grupo)
                termo_seguro = html.escape(termo_encontrado.upper())

                alerta_html = (
                    f"🚨 <b>OFERTA ENCONTRADA:</b> <code>{termo_seguro}</code>\n\n"
                    f"📢 <b>Grupo/Canal:</b> {nome_grupo_seguro}{link_html}\n"
                    f"💬 <b>Mensagem:</b>\n{texto_seguro}"
                )

                await enviar_alerta(alerta_html)
                print(f"[{time.strftime('%H:%M:%S')}] [{nome_grupo}] Alerta disparado para: '{termo_encontrado}'", flush=True)
                break  # Evita envio duplicado se casar com múltiplos termos

    except Exception as e:
        print(f"Erro ao processar mensagem recebida: {e}", flush=True)

# ==========================================
# INICIALIZAÇÃO
# ==========================================
async def main():
    me = await client.get_me()
    regras = obter_regras_monitoradas()
    termos = [r['termo'] for r in regras]

    modo_envio = "Bot API (Som & Push ativados)" if bot_token else "Telethon Userbot (Silencioso)"

    print("=" * 55)
    print(f"Conectado como: {me.first_name} (@{me.username})")
    print(f"Destino dos Alertas: {destino_alerta}")
    print(f"Modo de Envio: {modo_envio}")
    print(f"Termos Ativos ({len(termos)}): {termos}")
    print("=" * 55, flush=True)

    msg_inicial = (
        "🤖 <b>Bot de Monitoramento Ativado!</b>\n\n"
        f"<b>Conta Monitora:</b> {html.escape(me.first_name)} (@{html.escape(me.username or '')})\n"
        f"🎯 <b>Termos Monitorados ({len(termos)}):</b>\n" +
        "\n".join([f"• <code>{html.escape(t)}</code>" for t in termos]) +
        f"\n\n🔔 <b>Notificações:</b> {modo_envio}\n"
        "Monitorando todos os grupos e canais em tempo real!"
    )
    await enviar_alerta(msg_inicial)
    print(f"Notificação de inicialização enviada para '{destino_alerta}'!", flush=True)

if __name__ == '__main__':
    with client:
        client.loop.run_until_complete(main())
        client.run_until_disconnected()

