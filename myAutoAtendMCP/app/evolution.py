"""Cliente HTTP para a Evolution API — pareamento do WhatsApp pelo /admin.

O painel roda no mesmo processo/rede docker que a Evolution API, então fala
direto com ela (`http://evolution_api:9090`) usando a `apikey`. Assim o QR Code
fica dentro do próprio /admin, sem precisar abrir o manager da Evolution.

Config vem de `settings` (variáveis de ambiente injetadas pelo compose):
  EVOLUTION_API_URL · EVOLUTION_API_KEY · EVOLUTION_INSTANCE
"""

from __future__ import annotations

import httpx

from .config import settings


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.evolution_api_url.rstrip("/"),
        headers={"apikey": settings.evolution_api_key},
        timeout=15.0,
    )


def estado() -> dict:
    """Estado da conexão da instância: open | connecting | close."""
    with _client() as c:
        r = c.get(f"/instance/connectionState/{settings.evolution_instance}")
        r.raise_for_status()
        return r.json()


def conectar() -> dict:
    """Inicia o pareamento. Retorna QR (`base64`/`pairingCode`) ou, se já
    conectado, o estado atual da instância."""
    with _client() as c:
        r = c.get(f"/instance/connect/{settings.evolution_instance}")
        r.raise_for_status()
        return r.json()


def desconectar() -> dict:
    """Faz logout da sessão atual para permitir parear outro número."""
    with _client() as c:
        r = c.delete(f"/instance/logout/{settings.evolution_instance}")
        r.raise_for_status()
        return r.json()
