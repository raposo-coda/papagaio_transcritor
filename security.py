"""
security.py - Perimetro do servidor: origem, token opcional e cabecalhos.

A aplicacao foi desenhada para rodar em 127.0.0.1, para uma pessoa so. Este
modulo garante que essa premissa valha na pratica e da um caminho seguro para
quem precisar expor a ferramenta na rede.
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse

TOKEN_COOKIE = "papagaio_token"
TOKEN_HEADER = "x-papagaio-token"

# Metodos que alteram estado: sao os unicos que precisam de checagem de origem.
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_SECURITY_HEADERS = {
    # A interface e 100% local: nada de terceiros, nada inline em script.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def get_token() -> str:
    """Token de acesso opcional. Vazio (padrao) significa: sem autenticacao."""
    return (os.environ.get("PAPAGAIO_TOKEN") or "").strip()


def get_host() -> str:
    """Interface de escuta. Padrao 127.0.0.1: nao aceita conexao de fora."""
    return (os.environ.get("PAPAGAIO_HOST") or "127.0.0.1").strip()


def _allowed_hostnames() -> set[str]:
    permitidos = {"localhost", "127.0.0.1", "::1", "[::1]"}
    extra = os.environ.get("PAPAGAIO_ALLOWED_ORIGINS", "")
    for item in extra.split(","):
        item = item.strip()
        if not item:
            continue
        hostname = urlparse(item).hostname if "//" in item else item
        if hostname:
            permitidos.add(hostname.lower())
    return permitidos


def origin_is_allowed(request: Request) -> bool:
    """
    Barra CSRF: um POST vindo de outra pagina carrega Origin (ou ao menos
    Referer) apontando para o site do atacante.

    Requisicao sem Origin nem Referer e aceita: e o caso de curl e da CLI, que
    nao sofrem CSRF porque nao carregam credencial de navegador automaticamente.
    """
    origem = request.headers.get("origin") or request.headers.get("referer")
    if not origem:
        return True
    hostname = urlparse(origem).hostname
    if not hostname:
        return False
    return hostname.lower() in _allowed_hostnames()


def request_is_authorized(request: Request) -> bool:
    token = get_token()
    if not token:
        return True
    enviado = (
        request.headers.get(TOKEN_HEADER)
        or request.cookies.get(TOKEN_COOKIE)
        or request.query_params.get("token")
        or ""
    )
    # compare_digest evita vazar o tamanho do prefixo correto pelo tempo de resposta.
    # Comparado em bytes: com str, um token com acento levantaria TypeError.
    return hmac.compare_digest(enviado.encode("utf-8"), token.encode("utf-8"))


async def security_middleware(request: Request, call_next):
    """Aplica origem, token e cabecalhos de seguranca em toda resposta."""
    if request.method in _UNSAFE_METHODS and not origin_is_allowed(request):
        return JSONResponse(
            {"detail": "Origem nao permitida."},
            status_code=403,
            headers=_SECURITY_HEADERS,
        )

    if not request_is_authorized(request):
        return JSONResponse(
            {"detail": "Token de acesso ausente ou invalido."},
            status_code=401,
            headers=_SECURITY_HEADERS,
        )

    response = await call_next(request)

    for nome, valor in _SECURITY_HEADERS.items():
        response.headers.setdefault(nome, valor)

    # Quem chegou com ?token=... na URL leva um cookie e navega normalmente
    # depois disso, sem o token ficar no historico de cada requisicao.
    token = get_token()
    if token and request.query_params.get("token") == token:
        response.set_cookie(
            TOKEN_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=7 * 24 * 3600,
        )

    return response
