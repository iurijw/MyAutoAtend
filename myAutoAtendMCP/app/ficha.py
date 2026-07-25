"""Ficha de cadastro do cliente — tipos de campo, validação e montagem.

Feature OPCIONAL: só vale quando `Config.ficha_ativa` está ligada no painel
(seção "Ficha do cliente"). O dono define os campos (rótulo + tipo + dica) e
tanto ele (pelo painel) quanto o agente (durante a conversa, conforme a
instrução que o dono escreve) preenchem os valores.

Os valores são guardados SEMPRE como texto já normalizado por esta camada —
data em YYYY-MM-DD, número com ponto decimal, booleano em "sim"/"nao" — para
que painel e agente leiam o mesmo formato. Valor que não passa na validação
levanta ValueError com uma frase pronta para mostrar a quem preencheu.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from . import db
from .phone import formatar_internacional, normalizar

# Tipos suportados: chave → (rótulo no painel, dica de formato p/ o agente).
TIPOS: dict[str, dict[str, str]] = {
    "texto": {"rotulo": "Texto", "formato": "texto curto"},
    "texto_longo": {"rotulo": "Texto longo", "formato": "texto livre"},
    "numero": {"rotulo": "Número", "formato": "número (ex.: 42 ou 1250.50)"},
    "data": {"rotulo": "Data", "formato": "data no formato YYYY-MM-DD"},
    "hora": {"rotulo": "Hora", "formato": "hora no formato HH:MM"},
    "telefone": {"rotulo": "Telefone", "formato": "telefone com DDD"},
    "email": {"rotulo": "E-mail", "formato": "e-mail"},
    "booleano": {"rotulo": "Sim / Não", "formato": '"sim" ou "nao"'},
    "selecao": {"rotulo": "Seleção", "formato": "uma das opções listadas"},
}

MAX_TEXTO = 300
MAX_TEXTO_LONGO = 2000

_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def _slug(texto: str) -> str:
    """Rótulo → chave estável (sem acento, minúscula, separada por _)."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    return base[:40] or "campo"


def chave_livre(rotulo: str) -> str:
    """Chave única para um rótulo novo (sufixo numérico se já existir)."""
    base = _slug(rotulo)
    chave = base
    n = 2
    while db.get_campo_ficha_por_chave(chave):
        chave = f"{base}_{n}"
        n += 1
    return chave


def opcoes_de(campo) -> list[str]:
    """Alternativas de um campo de seleção (guardadas separadas por ;)."""
    return [o.strip() for o in (campo.opcoes or "").split(";") if o.strip()]


# ---------------------------------------------------------------------------
# Validação por tipo
# ---------------------------------------------------------------------------


def _normalizar_numero(bruto: str) -> str:
    texto = bruto.replace(" ", "")
    # "1.234,56" (pt-BR) → "1234.56"; "1234,56" → "1234.56"
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        raise ValueError("Informe um número (ex.: 42 ou 1250.50).") from None
    return str(int(valor)) if valor == int(valor) else str(valor)


def _normalizar_data(bruto: str) -> str:
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(bruto, formato).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Informe a data no formato YYYY-MM-DD (ex.: 1990-04-25).")


def _normalizar_hora(bruto: str) -> str:
    texto = bruto.replace("h", ":").rstrip(":")
    for formato in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(texto, formato).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError("Informe a hora no formato HH:MM (ex.: 14:30).")


_SIM = {"sim", "s", "true", "1", "yes", "y", "verdadeiro"}
_NAO = {"nao", "não", "n", "false", "0", "no", "falso"}


def normalizar_valor(campo, bruto: str) -> str:
    """Valor cru → forma canônica do tipo. Vazio devolve "" (apaga o valor).

    Levanta ValueError com mensagem pronta para o painel ou para o agente
    repassar ao cliente.
    """
    texto = (bruto or "").strip()
    if not texto:
        return ""

    tipo = campo.tipo
    if tipo == "texto":
        if len(texto) > MAX_TEXTO:
            raise ValueError(f"Texto longo demais (máximo {MAX_TEXTO} caracteres).")
        return texto
    if tipo == "texto_longo":
        return texto[:MAX_TEXTO_LONGO]
    if tipo == "numero":
        return _normalizar_numero(texto)
    if tipo == "data":
        return _normalizar_data(texto)
    if tipo == "hora":
        return _normalizar_hora(texto)
    if tipo == "telefone":
        num = normalizar(texto)
        if not num:
            raise ValueError("Telefone inválido — informe com DDD.")
        return num
    if tipo == "email":
        if not _RE_EMAIL.match(texto):
            raise ValueError("E-mail inválido.")
        return texto.lower()
    if tipo == "booleano":
        chave = texto.lower()
        if chave in _SIM:
            return "sim"
        if chave in _NAO:
            return "nao"
        raise ValueError('Responda "sim" ou "nao".')
    if tipo == "selecao":
        alternativas = opcoes_de(campo)
        if not alternativas:
            return texto
        for alt in alternativas:
            if alt.lower() == texto.lower():
                return alt
        raise ValueError("Opções válidas: " + ", ".join(alternativas) + ".")
    # tipo desconhecido (campo criado por versão futura) → texto simples
    return texto[:MAX_TEXTO]


def exibir(campo, valor: str) -> str:
    """Valor canônico → forma legível no painel (data BR, telefone formatado)."""
    if not valor:
        return ""
    if campo.tipo == "data":
        try:
            return date.fromisoformat(valor).strftime("%d/%m/%Y")
        except ValueError:
            return valor
    if campo.tipo == "telefone":
        return formatar_internacional(valor) or valor
    if campo.tipo == "booleano":
        return "Sim" if valor == "sim" else "Não"
    return valor


# ---------------------------------------------------------------------------
# Montagem e escrita da ficha de um contato
# ---------------------------------------------------------------------------


def ativa() -> bool:
    return bool(db.get_config().ficha_ativa)


def ficha_de(telefone: str, apenas_ativos: bool = True) -> dict:
    """Ficha completa de um contato: identificação + campos com valor.

    Serve o painel (modal) e a tool `ver_ficha` do agente — mesma fonte, então
    os dois enxergam exatamente a mesma coisa.
    """
    chave = normalizar(telefone) or telefone
    cliente = db.get_cliente(chave)
    valores = db.valores_ficha(chave)
    campos = []
    for c in db.listar_campos_ficha(apenas_ativos=apenas_ativos):
        v = valores.get(c.id)
        campos.append(
            {
                "id": c.id,
                "chave": c.chave,
                "rotulo": c.rotulo,
                "tipo": c.tipo,
                "opcoes": opcoes_de(c),
                "descricao": c.descricao,
                "obrigatorio": c.obrigatorio,
                "valor": v.valor if v else "",
                "valor_exibicao": exibir(c, v.valor if v else ""),
                "origem": v.origem if v else "",
                "atualizado_em": v.atualizado_em if v else "",
            }
        )
    return {
        "telefone": chave,
        "telefone_fmt": formatar_internacional(chave) or chave,
        "nome": (cliente.nome if cliente else "") or "",
        "campos": campos,
        "preenchidos": sum(1 for c in campos if c["valor"]),
        "total": len(campos),
    }


def preencher(telefone: str, dados: dict[str, str], origem: str) -> dict:
    """Grava vários campos de uma vez. Chaves desconhecidas e valores fora do
    formato NÃO derrubam o lote: voltam em `erros` para quem chamou decidir
    (o agente pergunta de novo; o painel mostra o aviso)."""
    chave = normalizar(telefone) or telefone
    salvos: dict[str, str] = {}
    erros: dict[str, str] = {}
    for nome_campo, bruto in (dados or {}).items():
        campo = db.get_campo_ficha_por_chave(nome_campo)
        if not campo or not campo.ativo:
            erros[nome_campo] = "Campo inexistente na ficha."
            continue
        try:
            valor = normalizar_valor(campo, str(bruto) if bruto is not None else "")
        except ValueError as e:
            erros[campo.chave] = str(e)
            continue
        db.set_valor_ficha(chave, campo.id, valor, origem=origem)
        salvos[campo.chave] = valor
    return {"salvos": salvos, "erros": erros}
