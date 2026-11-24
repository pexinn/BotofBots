# 📋 Roadmap & Backlog de Melhorias - Bot of Bots

Este documento reúne ideias, melhorias e evolução arquitetural para o **Bot of Bots**, com foco especial em tornar a **manutenção e gestão de palavras-chave o mais simples e rápida possível**, direto do celular ou de uma interface visual.

---

## 🎯 Prioridade Alta: Facilidade de Gestão de Palavras

### 1. Gestão Direta via Comandos no Telegram (A Mais Prática)
> **Objetivo:** Adicionar ou remover produtos monitorados sem precisar abrir terminal, SSH ou computador — direto pelo chat com o bot no Telegram.

- [x] **Comando `/list`:**
  - Exibe a lista numerada de todas as palavras e termos atualmente monitorados.
- [x] **Comando `/add <termo>`:**
  - Adiciona uma nova palavra-chave diretamente ao arquivo `keywords.txt`.
  - Exemplo: `/add rtx 4060 ti` ou `/add ryzen 7 5700x3d`.
  - Responde com confirmação imediata.
- [x] **Comando `/del <número ou termo>`:**
  - Remove o termo da lista com base no índice ou nome.
- [x] **Comando `/status`:**
  - Exibe métricas de funcionamento: uptime do container, total de mensagens processadas hoje, grupos monitorados e total de alertas disparados.
- [x] **Segurança por `ADMIN_USER_ID`:**
  - O bot só aceita comandos de configuração se o `user_id` da mensagem for o seu ID do Telegram (`904378967`), ignorando qualquer outro usuário.

---

### 2. Mini Dashboard Web no Homelab (Web UI)
> **Objetivo:** Uma interface visual limpa, responsiva e moderna rodando em uma porta local do Homelab (ex: `http://192.168.68.88:8086`).

- [x] **Stack recomendada:**
  - Backend leve em **FastAPI** assíncrono.
  - Frontend moderno com **Tailwind CSS + Alpine.js + Chart.js** empacotado no mesmo container.
- [x] **Recursos da Interface:**
  - Visualização de palavras ativas em formato de *tags/chips* coloridos.
  - Botão de adicionar nova palavra e excluir com um clique.
  - Histórico das últimas ofertas capturadas (com data, grupo de origem, preço detectado e link).
  - Toggle de "Pausar monitoramento" temporariamente.

---

## 🚀 Prioridade Média: Filtros Inteligentes e Qualidade dos Alertas

### 3. Filtro de Preço Máximo (Teto de Alerta)
> **Objetivo:** Evitar alertas de produtos com preço acima do valor de oportunidade desejado.

- [x] **Sintaxe de Regras:**
  - Permitir definir teto na linha: `ssd nvme 1tb <= 380` ou `rtx 4060 <= 1750`.
- [x] **Parser de Moeda:**
  - Expressão regular para extrair valores em Reais da mensagem (`R\$\s*(\d+[\.,]?\d*)`), ignorando parcelas e cupons.
  - Se o preço for superior ao teto configurado, descarta o alerta.

### 4. Deduplicação e Cooldown Anti-Spam
> **Objetivo:** Quando uma promoção boa sai, 10 canais diferentes postam a mesma coisa em minutos. Evitar receber 10 apitos seguidos da mesma oferta.

- [x] Cache em SQLite (`dedup_cache`) das URLs canônicas e assinaturas das ofertas dos últimos 30 minutos.
- [x] Se o mesmo link ou oferta similar for postada em outro canal em menos de $X$ minutos, o alerta não é reenviado.

### 5. Palavras Negativas / Blacklist de Marcas e Termos
> **Objetivo:** Filtrar marcas indesejadas ou ruído (ex: monitorar `ssd nvme 1tb`, mas **não** receber alerta se for da marca `"kootion"`, ou ignorar produtos usados, cabos e caixas).

- [x] **Exclusão por Linha (Termos Negativos com `-` ou `!`):**
  - Permitir adicionar palavras negativas na mesma linha do produto para barrar marcas ou características específicas.
  - Exemplo prático: `ssd nvme 1tb -kootion` (ou `ssd nvme 1tb -kootion -sata -usado`).
  - Se a mensagem contiver `ssd`, `nvme` e `1tb`, mas também contiver `kootion`, o alerta é **descartado silenciosamente**.
- [x] **Blacklist Global (Opcional):**
  - Permitir definir linhas apenas com termos negativos (ex: `-kootion`) no `keywords.txt` para bloquear aquela marca em **qualquer** busca do bot.
- [x] **Implementação no Motor de Regex (`app/rules.py`):**
  - Separar palavras normais de palavras iniciadas por `-` ou `!`.
  - Converter termos negativos em *Negative Lookaheads* no Regex: `(?!.*\bkootion\b)` com âncora `^` e flag `re.DOTALL`.

---

## 📊 Prioridade Baixa: Métricas e Histórico de Preços

### 6. Histórico de Preços e Estatísticas
- [x] Registro das ofertas em banco SQLite leve (`data/deals.db`).
- [x] Gráfico mostrando a variação de preço de um item ao longo das semanas.
- [x] Ranking dos canais mais rápidos e com melhores descontos.

---

## 👥 Prioridade Alta: Como Disponibilizar para Outras Pessoas

Atualmente o bot suporta **3 caminhos principais** para disponibilizar essa solução para amigos, família ou para o público em geral:

---

### 1. Modelo "Bot Centralizado Multi-Usuário" (Recomendado ⭐⭐⭐⭐⭐)
> **Como funciona:** O seu Homelab/VPS continua sendo a "central" que escuta todos os canais de ofertas com o Telethon. Qualquer pessoa simplesmente abre o bot no Telegram e escolhe o que quer receber no privado dela!

- **Vantagem para os outros:** Ninguém precisa de API do Telegram, nem de servidor, nem de Docker. Eles só dão `/start` no bot e pronto.
- [x] **Banco de Dados de Inscrições (SQLite):**
  - Tabela `users`: `chat_id`, `username`, `first_name`, `created_at`, `active`, `max_terms`.
  - Tabela `user_terms`: `id`, `chat_id`, `raw_line`, `term`, `negatives`, `max_price`, `strict_lt`.
- [x] **Fluxo do Usuário no Telegram:**
  - Usuário manda `/start` para o bot.
  - Comandos disponíveis para qualquer usuário:
    - `/alerta <termo>`: Adiciona um produto que ele quer vigiar (ex: `/alerta rtx 4060`).
    - `/meusalertas`: Lista os termos que ele cadastrou com opção de apagar.
    - `/remover <termo>`: Remove um alerta dele.
- [x] **Motor de Distribuição Segmentada:**
  - Quando uma oferta cai na central, o bot varre quais usuários têm interesse naquele termo e despacha o alerta no privado de cada um individualmente!
- [x] **Controle de Limites:**
  - Limite de $X$ alertas por usuário para evitar abusos (padrão `MAX_TERMS_PER_USER=5`, ajustável via `/limite` ou Web UI).

---

### 2. Modelo "Canais Temáticos por Categoria" (Zero Configuração para Amigos)
> **Como funciona:** Você cria canais públicos ou privados temáticos e qualquer pessoa apenas entra no canal de interesse dela.

- [x] **Exemplo de Canais:**
  - `🚨 Alertas - Hardware & PC` (SSD, GPU, CPU, RAM, Fontes)
  - `🚨 Alertas - Periféricos` (Mouses, Teclados, Headsets, Monitores)
  - `🚨 Alertas - Games & Consoles` (PS5, Xbox, Switch, Jogos)
- [x] O bot classifica as ofertas por categoria e posta no canal correspondente (`CHANNEL_HARDWARE`, `CHANNEL_PERIFERICOS`, `CHANNEL_GAMES` ou via Dashboard Web).
- [x] Amigos só precisam clicar no link de convite do canal que quiserem acompanhar.

---

### 3. Modelo "Self-Hosted 1-Click" (Para outros Devs / Homelabbers)
> **Como funciona:** Permitir que qualquer pessoa que tenha um Homelab ou VPS rode a sua própria instância do projeto em 1 minuto.

- [x] **GitHub Container Registry (GHCR):**
  - Configurar GitHub Actions (`.github/workflows/docker-publish.yml`) para testar, gerar e publicar a imagem automaticamente: `ghcr.io/pexinn/botofbots:latest`.
- [x] **Template 1-Click para Portainer / CasaOS / Unraid:**
  - `docker-compose.ghcr.yml` e `portainer-template.json` com campos de formulário para preencher `.env`.
- [x] **Script de Inicialização Automática (`setup.sh` / `setup.bat`):**
  - Assistente interativo que pergunta `API_ID`, `API_HASH`, gera a `StringSession` e configura `.env` + Docker.

---

## 📌 Como Contribuir ou Priorizar
Para implementar novas evoluções, basta abrir uma branch a partir de `main` e submeter um Pull Request ou aplicar diretamente no Homelab!
