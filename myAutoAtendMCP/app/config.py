"""Configurações de processo (lidas de variáveis de ambiente).

Tudo que muda entre ambientes (caminho do banco, credenciais do painel,
telefone do dono, fuso) vem daqui. No Docker são injetadas pelo compose.
"""

from __future__ import annotations

import os


class Settings:
    # SQLite: caminho do arquivo. No container aponta para um volume (/data).
    db_path: str = os.getenv("MCP_DB_PATH", "agendamentos.db")

    # Painel /admin (HTTP Basic). Trocar em produção.
    admin_user: str = os.getenv("ADMIN_USER", "admin")
    admin_pass: str = os.getenv("ADMIN_PASS", "admin123")

    # Telefone do dono — autoriza ações restritas. Usado como seed da Config.
    owner_phone: str = os.getenv("OWNER_PHONE", "5545999990000")

    # Fuso usado nas conversões de data/hora (validação de passado, slots).
    timezone: str = os.getenv("MCP_TZ", "America/Sao_Paulo")

    # Evolution API — pareamento do WhatsApp pelo painel (rede docker interna).
    evolution_api_url: str = os.getenv("EVOLUTION_API_URL", "http://evolution_api:9090")
    evolution_api_key: str = os.getenv("EVOLUTION_API_KEY", "")
    evolution_instance: str = os.getenv("EVOLUTION_INSTANCE", "evo_n8n")

    # n8n — API interna (rede docker) p/ atualizar credenciais de IA pelo painel.
    # Mesmo owner usado pelo init-n8n.sh; a chave dos provedores só ENTRA no n8n,
    # nunca volta para o painel.
    n8n_api_url: str = os.getenv("N8N_API_URL", "http://n8n:5678")
    n8n_owner_email: str = os.getenv("N8N_OWNER_EMAIL", "")
    n8n_owner_password: str = os.getenv("N8N_OWNER_PASSWORD", "")

    # System prompt inicial do agente (mesmo valor passado ao n8n). Usado só
    # como SEED da instrução geral no primeiro acesso ao card do painel —
    # depois a fonte de verdade é o SQLite (tabela Prompt).
    agent_system_prompt: str = os.getenv("AGENT_SYSTEM_PROMPT", "")

    # URLs externas (acessadas pelo browser do host) p/ atalhos no painel.
    n8n_external_url: str = os.getenv("N8N_EXTERNAL_URL", "http://localhost:5678")
    evolution_external_url: str = os.getenv(
        "EVOLUTION_EXTERNAL_URL", "http://localhost:9090"
    )


settings = Settings()
