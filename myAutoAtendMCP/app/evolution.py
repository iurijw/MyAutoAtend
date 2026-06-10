"""Cliente HTTP para a Evolution API — pareamento do WhatsApp pelo /admin.

O painel roda no mesmo processo/rede docker que a Evolution API, então fala
direto com ela (`http://evolution_api:9090`) usando a `apikey`. Assim o QR Code
fica dentro do próprio /admin, sem precisar abrir o manager da Evolution.

Config vem de `settings` (variáveis de ambiente injetadas pelo compose):
  EVOLUTION_API_URL · EVOLUTION_API_KEY · EVOLUTION_INSTANCE
"""

from __future__ import annotations

import re
import time

import httpx

from .config import settings

# Foto de perfil raramente muda — cache em memória evita bater na Evolution
# (e no WhatsApp) a cada recarga do painel. numero → (expira_em, url|None).
# Resultado vazio expira rápido: instância pode estar só desconectada.
_FOTO_TTL_S = 3600.0
_FOTO_TTL_VAZIO_S = 300.0
_foto_cache: dict[str, tuple[float, str | None]] = {}


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


def foto_perfil(numero: str) -> str | None:
    """URL da foto de perfil do WhatsApp de um número (None se sem foto,
    privada ou número fora do WhatsApp). Resultado cacheado por 1h."""
    digitos = re.sub(r"\D", "", numero or "")
    if not digitos:
        return None

    agora = time.monotonic()
    em_cache = _foto_cache.get(digitos)
    if em_cache and em_cache[0] > agora:
        return em_cache[1]

    with _client() as c:
        # Timeout curto: com a instância desconectada a Evolution trava a
        # chamada — o painel cai rápido no fallback de inicial.
        r = c.post(
            f"/chat/fetchProfilePictureUrl/{settings.evolution_instance}",
            json={"number": digitos},
            timeout=5.0,
        )
        # 4xx = sem foto / número inexistente — não é erro do painel.
        url = None
        if r.status_code < 400:
            url = r.json().get("profilePictureUrl")
        elif r.status_code >= 500:
            r.raise_for_status()

    ttl = _FOTO_TTL_S if url else _FOTO_TTL_VAZIO_S
    _foto_cache[digitos] = (agora + ttl, url)
    return url
