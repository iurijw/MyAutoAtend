"""Cliente HTTP para a Evolution API.

Duas frentes:
  - Painel /admin (sync): pareamento por QR, estado, foto de perfil.
  - Pipeline do agente (async): receber/enviar mensagens, mídia, presença —
    substitui os nodes Evolution do n8n.
  - Bootstrap: criação da instância + webhook no startup (substitui a parte
    Evolution do init-n8n.sh).

Config vem de `settings` (variáveis de ambiente injetadas pelo compose):
  EVOLUTION_API_URL · EVOLUTION_API_KEY · EVOLUTION_INSTANCE
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from .config import settings

log = logging.getLogger("evolution")

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


# ---------------------------------------------------------------------------
# Pipeline do agente (async) — substitui os nodes Evolution do n8n
# ---------------------------------------------------------------------------


def _async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.evolution_api_url.rstrip("/"),
        headers={"apikey": settings.evolution_api_key},
        timeout=30.0,
    )


async def obter_midia_base64(message_id: str) -> dict:
    """Base64 da mídia de uma mensagem: {"base64": ..., "mimetype": ...}."""
    async with _async_client() as c:
        r = await c.post(
            f"/chat/getBase64FromMediaMessage/{settings.evolution_instance}",
            json={"message": {"key": {"id": message_id}}, "convertToMp4": False},
        )
        r.raise_for_status()
        return r.json()


async def marcar_como_lida(remote_jid: str, from_me: bool, message_id: str) -> None:
    """Marca a mensagem como lida (✓✓ azul). Falha não interrompe o fluxo."""
    try:
        async with _async_client() as c:
            await c.post(
                f"/chat/markMessageAsRead/{settings.evolution_instance}",
                json={
                    "readMessages": [
                        {"remoteJid": remote_jid, "fromMe": from_me, "id": message_id}
                    ]
                },
            )
    except Exception as e:  # noqa: BLE001
        log.warning("markMessageAsRead falhou: %s", e)


async def enviar_texto(numero: str, texto: str, digitando_ms: int = 0) -> None:
    """Envia texto; `digitando_ms` > 0 mostra "digitando..." antes (delay)."""
    corpo: dict = {"number": numero, "text": texto}
    if digitando_ms > 0:
        corpo["delay"] = digitando_ms
    async with _async_client() as c:
        r = await c.post(
            f"/message/sendText/{settings.evolution_instance}", json=corpo
        )
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Bootstrap da instância (substitui a parte Evolution do init-n8n.sh)
# ---------------------------------------------------------------------------


async def garantir_instancia(webhook_url: str) -> None:
    """Aguarda a Evolution subir e garante instância + webhook apontando p/ cá.

    Roda como task de startup. Idempotente: instância existente não é recriada,
    mas o webhook é (re)configurado sempre — assim migração de ambiente antigo
    (webhook no n8n) passa a entregar aqui sem passo manual.
    """
    async with _async_client() as c:
        for tentativa in range(30):
            try:
                r = await c.get("/instance/fetchInstances")
                if r.status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            log.info("Aguardando Evolution API... tentativa %d", tentativa + 1)
            await asyncio.sleep(3)
        else:
            log.error("Evolution API não respondeu — instância não foi criada.")
            return

        nome = settings.evolution_instance
        existe = any(i.get("name") == nome for i in r.json())
        if not existe:
            cr = await c.post(
                "/instance/create",
                json={"instanceName": nome, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            )
            log.info("Criar instância %s → HTTP %s", nome, cr.status_code)
            if cr.status_code not in (200, 201):
                log.error("Falha ao criar instância: %s", cr.text[:300])
                return
            await c.post(
                f"/settings/set/{nome}",
                json={
                    "rejectCall": False,
                    "msgCall": "",
                    "groupsIgnore": False,
                    "alwaysOnline": False,
                    "readMessages": False,
                    "readStatus": False,
                    "syncFullHistory": False,
                },
            )

        wh = await c.post(
            f"/webhook/set/{nome}",
            json={
                "webhook": {
                    "url": webhook_url,
                    "enabled": True,
                    "webhookByEvents": False,
                    "base64": True,
                    "events": ["MESSAGES_UPSERT"],
                }
            },
        )
        log.info("Webhook → HTTP %s (%s)", wh.status_code, webhook_url)
        if not existe:
            log.info("AÇÃO NECESSÁRIA: conecte o WhatsApp — QR Code no painel /admin.")
