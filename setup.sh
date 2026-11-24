#!/usr/bin/env bash
set -euo pipefail

echo "============================================================"
echo "   Bot of Bots - Assistente de Configuracao (1-Click Setup)"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Erro: python3 nao encontrado. Instale Python 3.10+."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Criando ambiente virtual local (.venv)..."
  python3 -m venv .venv
fi

echo "Instalando dependencias..."
.venv/bin/pip install --quiet -r requirements.txt

read -rp "Informe seu API_ID (my.telegram.org): " API_ID
read -rp "Informe seu API_HASH (my.telegram.org): " API_HASH
read -rp "Informe o TOKEN do bot (@BotFather): " TELEGRAM_BOT_TOKEN
read -rp "Informe seu ADMIN_USER_ID (seu ID numerico do Telegram): " ADMIN_USER_ID
read -rp "Informe o ALERT_DESTINATION (ex: -1001234567890 ou 'me') [me]: " ALERT_DESTINATION
ALERT_DESTINATION=${ALERT_DESTINATION:-me}
read -rp "Porta do Dashboard Web [8086]: " WEB_PORT
WEB_PORT=${WEB_PORT:-8086}
read -rp "Senha opcional para o Dashboard Web (vazio = aberto na LAN): " WEB_PASSWORD

export API_ID API_HASH
echo ""
echo "Gerando TELEGRAM_STRING_SESSION..."
.venv/bin/python scripts/gerar_sessao.py

STRING_SESSION=""
if [ -f "string_session.txt" ]; then
  STRING_SESSION=$(cat string_session.txt)
  rm -f string_session.txt
fi

if [ -z "$STRING_SESSION" ]; then
  read -rp "Cole a TELEGRAM_STRING_SESSION gerada acima: " STRING_SESSION
fi

cat > .env <<EOF
API_ID=$API_ID
API_HASH=$API_HASH
TELEGRAM_STRING_SESSION=$STRING_SESSION
ALERT_DESTINATION=$ALERT_DESTINATION
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
ADMIN_USER_ID=$ADMIN_USER_ID
MAX_TERMS_PER_USER=5
DEDUP_WINDOW_MINUTES=30
REQUIRE_PRICE_FOR_CAP=true
WEB_ENABLED=true
WEB_HOST=0.0.0.0
WEB_PORT=$WEB_PORT
WEB_PASSWORD=$WEB_PASSWORD
DB_PATH=data/deals.db
KEYWORDS_FILE=keywords.txt
EOF

if [ ! -f "keywords.txt" ]; then
  cp keywords.example.txt keywords.txt
fi

mkdir -p data

if command -v docker >/dev/null 2>&1; then
  read -rp "Deseja subir o container com Docker Compose agora? (s/N): " START_DOCKER
  if [[ "$START_DOCKER" =~ ^[sS]$ ]]; then
    docker compose up -d --build
    echo "Bot iniciado! Acesse o dashboard em http://localhost:$WEB_PORT"
  fi
fi

echo "Configuracao concluida!"
