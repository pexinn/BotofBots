# 📋 Roadmap & Backlog de Melhorias - Bot of Bots

Este documento reúne ideias, melhorias e evolução arquitetural para o **Bot of Bots**, com foco especial em tornar a **manutenção e gestão de palavras-chave o mais simples e rápida possível**, direto do celular ou de uma interface visual.

---

## 🎯 Prioridade Alta: Facilidade de Gestão de Palavras

### 1. Gestão Direta via Comandos no Telegram (A Mais Prática)
> **Objetivo:** Adicionar ou remover produtos monitorados sem precisar abrir terminal, SSH ou computador — direto pelo chat com o bot no Telegram.

- [ ] **Comando `/list`:**
  - Exibe a lista numerada de todas as palavras e termos atualmente monitorados.
- [ ] **Comando `/add <termo>`:**
  - Adiciona uma nova palavra-chave diretamente ao arquivo `keywords.txt`.
  - Exemplo: `/add rtx 4060 ti` ou `/add ryzen 7 5700x3d`.
  - Responde com confirmação imediata.
- [ ] **Comando `/del <número ou termo>`:**
  - Remove o termo da lista com base no índice ou nome.
- [ ] **Comando `/status`:**
  - Exibe métricas de funcionamento: uptime do container, total de mensagens processadas hoje, grupos monitorados e total de alertas disparados.
- [ ] **Segurança por `ADMIN_USER_ID`:**
  - O bot só aceita comandos de configuração se o `user_id` da mensagem for o seu ID do Telegram (`904378967`), ignorando qualquer outro usuário.

---

### 2. Mini Dashboard Web no Homelab (Web UI)
> **Objetivo:** Uma interface visual limpa, responsiva e moderna rodando em uma porta local do Homelab (ex: `http://192.168.68.88:8085`).

- [ ] **Stack recomendada:**
  - Backend leve em **FastAPI** ou **Flask** assíncrono.
  - Frontend moderno com **Tailwind CSS + Alpine.js** ou **Streamlit** (tudo empacotado no mesmo container ou via compose).
- [ ] **Recursos da Interface:**
  - Visualização de palavras ativas em formato de *tags/chips* coloridos.
  - Botão de adicionar nova palavra e excluir com um clique.
  - Histórico das últimas ofertas capturadas (com data, grupo de origem, preço detectado e link).
  - Toggle de "Pausar monitoramento" temporariamente.

---

## 🚀 Prioridade Média: Filtros Inteligentes e Qualidade dos Alertas

### 3. Filtro de Preço Máximo (Teto de Alerta)
> **Objetivo:** Evitar alertas de produtos com preço acima do valor de oportunidade desejado.

- [ ] **Sintaxe de Regras:**
  - Permitir definir teto na linha: `ssd nvme 1tb <= 380` ou `rtx 4060 <= 1750`.
- [ ] **Parser de Moeda:**
  - Expressão regular para extrair valores em Reais da mensagem (`R\$\s*(\d+[\.,]?\d*)`).
  - Se o preço for superior ao teto configurado, descarta o alerta.

### 4. Deduplicação e Cooldown Anti-Spam
> **Objetivo:** Quando uma promoção boa sai, 10 canais diferentes postam a mesma coisa em minutos. Evitar receber 10 apitos seguidos da mesma oferta.

- [ ] Cache em memória / SQLite das URLs das ofertas dos últimos 30 minutos.
- [ ] Se o mesmo link ou oferta similar for postada em outro canal em menos de $X$ minutos, o alerta não é reenviado.

### 5. Palavras Negativas / Blacklist
> **Objetivo:** Filtrar ruído (ex: não querer anúncios de produtos usados, cabos ou caixas).

- [ ] Suporte a termos negativos precedidos por `-` ou `!`.
- [ ] Exemplo: `ssd nvme 1tb -sata -usado -leilao`.

---

## 📊 Prioridade Baixa: Métricas e Histórico de Preços

### 6. Histórico de Preços e Estatísticas
- [ ] Registro das ofertas em banco SQLite leve (`deals.db`).
- [ ] Gráfico mostrando a variação de preço de um item ao longo das semanas.
- [ ] Ranking dos canais mais rápidos e com melhores descontos.

---

## 📌 Como Contribuir ou Priorizar
Para implementar qualquer uma dessas ideias, basta abrir uma branch a partir de `main` e submeter um Pull Request ou aplicar diretamente no Homelab!
