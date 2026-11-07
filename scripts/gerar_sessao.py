import os
import sys
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

print("=" * 60)
print("Gerador de StringSession Telethon - Bot of Bots")
print("=" * 60)

api_id_env = os.getenv('API_ID')
api_hash_env = os.getenv('API_HASH')

if api_id_env and api_hash_env:
    print(f"Usando credenciais detectadas do ambiente:")
    print(f"API_ID: {api_id_env}")
    api_id = int(api_id_env)
    api_hash = api_hash_env.strip()
else:
    api_id_input = input("Informe o seu API_ID (de my.telegram.org): ").strip()
    api_hash = input("Informe o seu API_HASH (de my.telegram.org): ").strip()
    if not api_id_input or not api_hash:
        print("Erro: API_ID e API_HASH são obrigatórios!")
        sys.exit(1)
    api_id = int(api_id_input)

print("\nIniciando conexão segura com os servidores do Telegram...")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    me = client.get_me()
    session_string = client.session.save()

    print("\n" + "=" * 60)
    print(f"Sucesso! Autenticado como: {me.first_name} (@{me.username})")
    print("=" * 60)
    print("\nSua TELEGRAM_STRING_SESSION gerada é:\n")
    print(session_string)
    print("\n" + "=" * 60)
    print("Copie o texto acima e cole na variável TELEGRAM_STRING_SESSION do seu arquivo .env")
    print("=" * 60)

    try:
        with open("string_session.txt", "w", encoding="utf-8") as f:
            f.write(session_string)
        print("\nSessão também salva temporariamente no arquivo 'string_session.txt'.")
    except Exception:
        pass
