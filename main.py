#!/usr/bin/env python3
"""
Launcher local para subir o servidor web a partir da raiz do repositorio.
Uso:
    python main.py
"""

from __future__ import annotations

import os

import uvicorn

from server import app


def main():
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
