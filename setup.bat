@echo off
setlocal enabledelayedexpansion
echo ============================================================
echo    Bot of Bots - Assistente de Configuracao (Windows)
echo ============================================================

where python >nul 2>&1
if errorlevel 1 (
    echo Erro: Python nao encontrado. Instale Python 3.10+.
    exit /b 1
)

if not exist ".venv" (
    echo Criando ambiente virtual local (.venv)...
    python -m venv .venv
)

echo Instalando dependencias...
.venv\Scripts\pip.exe install --quiet -r requirements.txt

set /p API_ID="Informe seu API_ID (my.telegram.org): "
set /p API_HASH="Informe seu API_HASH (my.telegram.org): "
set /p TELEGRAM_BOT_TOKEN="Informe o TOKEN do bot (@BotFather): "
set /p ADMIN_USER_ID="Informe seu ADMIN_USER_ID (seu ID numerico do Telegram): "
set /p ALERT_DESTINATION="Informe o ALERT_DESTINATION (ex: -1001234567890 ou me) [me]: "
if "%ALERT_DESTINATION%"=="" set ALERT_DESTINATION=me
set /p WEB_PORT="Porta do Dashboard Web [8086]: "
if "%WEB_PORT%"=="" set WEB_PORT=8086
set /p WEB_PASSWORD="Senha opcional para o Dashboard Web (Enter = aberto): "

echo.
echo Gerando TELEGRAM_STRING_SESSION...
.venv\Scripts\python.exe scripts\gerar_sessao.py

set STRING_SESSION=
if exist "string_session.txt" (
    set /p STRING_SESSION=<string_session.txt
    del string_session.txt
)
if "%STRING_SESSION%"=="" (
    set /p STRING_SESSION="Cole a TELEGRAM_STRING_SESSION gerada acima: "
)

(
    echo API_ID=%API_ID%
    echo API_HASH=%API_HASH%
    echo TELEGRAM_STRING_SESSION=%STRING_SESSION%
    echo ALERT_DESTINATION=%ALERT_DESTINATION%
    echo TELEGRAM_BOT_TOKEN=%TELEGRAM_BOT_TOKEN%
    echo ADMIN_USER_ID=%ADMIN_USER_ID%
    echo MAX_TERMS_PER_USER=5
    echo DEDUP_WINDOW_MINUTES=30
    echo REQUIRE_PRICE_FOR_CAP=true
    echo WEB_ENABLED=true
    echo WEB_HOST=0.0.0.0
    echo WEB_PORT=%WEB_PORT%
    echo WEB_PASSWORD=%WEB_PASSWORD%
    echo DB_PATH=data/deals.db
    echo KEYWORDS_FILE=keywords.txt
) > .env

if not exist "keywords.txt" (
    copy keywords.example.txt keywords.txt >nul
)
if not exist "data" mkdir data

echo.
echo Configuracao concluida! Para iniciar localmente execute:
echo   .venv\Scripts\python.exe bot.py
echo Ou com Docker Compose:
echo   docker compose up -d --build
endlocal
