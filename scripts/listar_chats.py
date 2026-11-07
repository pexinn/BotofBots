import os
import sys
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("=" * 60)
print("Listador de Chats e Canais - Bot of Bots")
print("=" * 60)

api_id_raw = os.getenv('API_ID')
api_hash = (os.getenv('API_HASH') or '').strip()
session_str = (os.getenv('TELEGRAM_STRING_SESSION') or '').strip()

if not api_id_raw or not api_hash or not session_str:
    print("Aviso: Variáveis de ambiente não encontradas.")
    api_id_raw = input("Informe o API_ID: ").strip()
    api_hash = input("Informe o API_HASH: ").strip()
    session_str = input("Cole a TELEGRAM_STRING_SESSION: ").strip()

api_id = int(api_id_raw)

print("\nConectando...")
with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
    print("\nBuscando seus canais, grupos e conversas recentes:\n")
    print(f"{'ID':<18} | {'TIPO':<10} | {'TÍTULO'}")
    print("-" * 65)

    for dialog in client.get_dialogs(limit=50):
        tipo = "Canal" if dialog.is_channel else ("Grupo" if dialog.is_group else "Usuário")
        print(f"{dialog.id:<18} | {tipo:<10} | {dialog.title}")

print("\n" + "=" * 60)
print("Para usar um canal como destino dos alertas, copie o ID com o sinal de menos")
print("Exemplo: -1001234567890 e cole em ALERT_DESTINATION no seu .env")
print("=" * 60)

