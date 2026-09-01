#!/usr/bin/env python3
"""
Launcher local para subir o servidor web a partir da raiz do repositorio.
Uso:
    python main.py
"""

from __future__ import annotations

import os

import uvicorn

import security
from server import app


def main():
    port = int(os.environ.get("PORT", "8000"))
    # Padrao 127.0.0.1: a API nao tem login, entao nao pode ficar exposta na rede.
    # Para expor de proposito: PAPAGAIO_HOST=0.0.0.0 e PAPAGAIO_TOKEN=<segredo>.
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
