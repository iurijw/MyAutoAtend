"""Mídia da conversa: o que o WhatsApp manda além de texto.

Duas responsabilidades:

1. **Ler o payload do Baileys** (o `message` que a Evolution repassa no
   webhook) e dizer de que tipo é aquela mensagem, mesmo quando ela vem
   embrulhada (mensagem efêmera, "ver uma vez", documento com legenda).
2. **Guardar o arquivo** em disco, ao lado do banco, para o painel poder
   mostrar a imagem/vídeo/figurinha em vez de um "[Imagem]" seco.

O que vai para a MEMÓRIA do agente continua sendo só texto — um marcador
("[Imagem enviada pelo cliente] ..."). A tabela `Midia` guarda esse mesmo
marcador em `texto`, e é assim que o painel reencontra o arquivo da bolha.

Retenção: nada é apagado automaticamente. O volume é o mesmo do banco
(`mcp_data`), então a mídia sobrevive a rebuild; limpar é decisão manual.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import uuid
from pathlib import Path

from .config import settings

log = logging.getLogger("midia")

# Pasta dos arquivos: ao lado do SQLite (no container, /data/midia).
PASTA = Path(settings.db_path).resolve().parent / "midia"

# Teto por arquivo. Vídeo de WhatsApp costuma ficar bem abaixo disso; o limite
# existe para um envio absurdo não encher o volume do container.
LIMITE_BYTES = 25 * 1024 * 1024

# Chave do Baileys → (tipo interno, rótulo usado no marcador da memória).
TIPOS = {
    "imageMessage": ("imagem", "Imagem"),
    "videoMessage": ("video", "Vídeo"),
    "audioMessage": ("audio", "Áudio"),
    "stickerMessage": ("figurinha", "Figurinha"),
    "documentMessage": ("documento", "Documento"),
}

# Embrulhos que escondem a mensagem real um nível abaixo.
_EMBRULHOS = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
    "editedMessage",
)

_EXTENSOES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "video/quicktime": ".mov",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/amr": ".amr",
    "audio/wav": ".wav",
    "application/pdf": ".pdf",
}


def desembrulhar(message: dict | None) -> dict:
    """Tira as camadas de embrulho (efêmera, ver-uma-vez, doc com legenda).

    O Baileys aninha a mensagem real dentro delas; sem isso um áudio efêmero
    vira "tipo desconhecido" e some da conversa.
    """
    atual = message or {}
    for _ in range(4):  # trava de segurança: embrulho dentro de embrulho
        chave = next((k for k in _EMBRULHOS if isinstance(atual.get(k), dict)), None)
        if not chave:
            break
        dentro = atual[chave].get("message")
        if not isinstance(dentro, dict):
            break
        atual = dentro
    return atual


def tipo_de(message: dict | None) -> tuple[str, dict] | None:
    """(chave Baileys, conteúdo) da primeira parte de mídia reconhecida."""
    msg = desembrulhar(message)
    for chave in TIPOS:
        if isinstance(msg.get(chave), dict):
            return chave, msg[chave]
    return None


def extensao(mime: str, nome: str = "") -> str:
    """Extensão a partir do mime; cai no sufixo do nome original, senão .bin."""
    limpo = (mime or "").split(";")[0].strip().lower()
    if limpo in _EXTENSOES:
        return _EXTENSOES[limpo]
    sufixo = Path(nome or "").suffix
    if 1 < len(sufixo) <= 6 and re.fullmatch(r"\.[A-Za-z0-9]+", sufixo):
        return sufixo.lower()
    return ".bin"


def guardar(b64: str | None, mime: str, nome: str = "") -> str | None:
    """Grava o base64 num arquivo novo e devolve o nome dele (None se falhar).

    Nunca levanta: mídia é enfeite da conversa — falhar em salvar não pode
    derrubar o atendimento, só deixa a bolha sem o arquivo.
    """
    if not b64:
        return None
    try:
        bruto = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as e:
        log.warning("base64 inválido (%s): %s", mime, e)
        return None
    if not bruto:
        return None
    if len(bruto) > LIMITE_BYTES:
        log.warning("mídia de %d bytes acima do limite — não guardada", len(bruto))
        return None
    try:
        PASTA.mkdir(parents=True, exist_ok=True)
        arquivo = f"{uuid.uuid4().hex}{extensao(mime, nome)}"
        (PASTA / arquivo).write_bytes(bruto)
        return arquivo
    except OSError as e:
        log.warning("não deu para guardar a mídia: %s", e)
        return None


def caminho(arquivo: str) -> Path | None:
    """Caminho absoluto de um arquivo guardado, se ele ainda existir.

    `Path(...).name` corta qualquer travessia de diretório vinda do banco —
    o que é servido nunca sai da pasta de mídia.
    """
    if not arquivo:
        return None
    alvo = PASTA / Path(arquivo).name
    return alvo if alvo.is_file() else None
