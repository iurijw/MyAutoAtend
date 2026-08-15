"""Sessão do painel: JWT (HS256) em cookie httpOnly.

Substitui o HTTP Basic — a caixa do navegador saiu, quem autentica é a página
`/login`. O token é um JWT compacto assinado com HMAC-SHA256 (stdlib: hmac +
hashlib + base64), no formato padrão `header.payload.assinatura`, então trocar
por PyJWT depois é só substituir estas duas funções.

Segredo: derivado da SENHA do painel (`settings.session_secret`). Consequência
desejada — trocar a SENHA invalida todas as sessões abertas, sem tabela nova
nem estado em banco (o token carrega a validade).

Sem lista de revogação: a expiração (12 h) é o limite. Para "deslogar de todo
lugar" agora, troque a SENHA no .env e suba de novo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Response

from .config import settings

COOKIE = "maa_sessao"
TTL_HORAS = 12

_CABECALHO = {"alg": "HS256", "typ": "JWT"}

# Tentativas de login falhas por IP: {ip: [timestamps]}. Em memória mesmo —
# o painel roda em um processo local; reiniciar o container zera (aceitável).
_FALHAS: dict[str, list[float]] = {}
_LIMITE = 8  # falhas na janela até bloquear
_JANELA = 300.0  # segundos observados
_CASTIGO = 120.0  # segundos de bloqueio ao estourar o limite


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _b64(bruto: bytes) -> str:
    return base64.urlsafe_b64encode(bruto).decode().rstrip("=")


def _deb64(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _json_b64(obj: dict) -> str:
    return _b64(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode())


def _assinar(corpo: str) -> str:
    mac = hmac.new(settings.session_secret.encode(), corpo.encode(), hashlib.sha256)
    return _b64(mac.digest())


def criar_token(usuario: str, horas: int = TTL_HORAS) -> str:
    agora = int(time.time())
    corpo = (
        f"{_json_b64(_CABECALHO)}."
        f"{_json_b64({'sub': usuario, 'iat': agora, 'exp': agora + horas * 3600})}"
    )
    return f"{corpo}.{_assinar(corpo)}"


def ler_token(token: str | None) -> str | None:
    """Devolve o usuário do token válido, ou None (ausente/adulterado/expirado)."""
    if not token:
        return None
    partes = token.split(".")
    if len(partes) != 3:
        return None
    cabecalho_b64, payload_b64, assinatura = partes
    corpo = f"{cabecalho_b64}.{payload_b64}"
    # compare_digest: comparação em tempo constante (não vaza o prefixo certo).
    if not hmac.compare_digest(_assinar(corpo), assinatura):
        return None
    try:
        cabecalho = json.loads(_deb64(cabecalho_b64))
        payload = json.loads(_deb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    # alg fixo: recusa o clássico "alg": "none" e troca de algoritmo.
    if cabecalho.get("alg") != "HS256":
        return None
    if not isinstance(payload.get("exp"), int) or payload["exp"] <= time.time():
        return None
    usuario = payload.get("sub")
    return usuario if isinstance(usuario, str) and usuario else None


# ---------------------------------------------------------------------------
# Cookie
# ---------------------------------------------------------------------------


def definir_cookie(resposta: Response, token: str) -> None:
    resposta.set_cookie(
        COOKIE,
        token,
        max_age=TTL_HORAS * 3600,
        httponly=True,  # fora do alcance de JS: XSS no painel não rouba a sessão
        samesite="lax",
        path="/",
        # secure fica False de propósito: o painel roda em http://localhost.
        # Ao publicar atrás de HTTPS, ligar aqui (cookie some em http puro).
        secure=False,
    )


def limpar_cookie(resposta: Response) -> None:
    resposta.delete_cookie(COOKIE, path="/")


# ---------------------------------------------------------------------------
# Freio de força bruta (por IP)
# ---------------------------------------------------------------------------


def bloqueio_restante(ip: str) -> int:
    """Segundos que faltam de bloqueio para este IP (0 = pode tentar)."""
    tentativas = [t for t in _FALHAS.get(ip, []) if time.time() - t < _JANELA]
    _FALHAS[ip] = tentativas
    if len(tentativas) < _LIMITE:
        return 0
    return max(0, int(_CASTIGO - (time.time() - tentativas[-1])))


def registrar_falha(ip: str) -> None:
    _FALHAS.setdefault(ip, []).append(time.time())


def limpar_falhas(ip: str) -> None:
    _FALHAS.pop(ip, None)


class SessaoInvalida(Exception):
    """Sem cookie válido. O handler em main.py decide o que devolver:
    redirect p/ /login em navegação HTML, 401 JSON no fetch do painel."""
