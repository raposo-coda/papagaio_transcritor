#!/usr/bin/env python3
"""
Entry point do pacote papagaio_transcritor.
"""

from __future__ import annotations

import os

import uvicorn

from . import security
from .server import app


def main():
    port = int(os.environ.get("PORT", "8000"))
    # Mesma regra do main.py: sem login, so escuta em localhost por padrao.
    host = security.get_host()
    if host not in ("127.0.0.1", "localhost", "::1") and not security.get_token():
        raise SystemExit(
            f"Recusando escutar em {host} sem PAPAGAIO_TOKEN definido.\n"
            "A aplicacao nao tem autenticacao propria: exposta na rede, qualquer um\n"
            "poderia enviar arquivos, ler transcricoes e trocar a sua chave da API.\n"
            "Defina PAPAGAIO_TOKEN=<um segredo> para liberar."
        )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
