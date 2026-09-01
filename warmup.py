#!/usr/bin/env python3
"""
warmup.py - Baixa os modelos do modo local antes do primeiro uso.

Sem isso, a primeira transcricao de verdade para por varios minutos baixando
alguns GB, sem o usuario entender o que esta acontecendo. O instalador roda este
script no fim para deixar tudo pronto.

Uso (dentro do container):
    python warmup.py
"""

from __future__ import annotations

import sys

try:
    from . import diarizer, hardware, local_client
    from .config import DEFAULT_LOCAL_DIARIZE, DEFAULT_LOCAL_MODEL, load_config
    from .models import LocalConfig
except ImportError:
    import diarizer  # type: ignore
    import hardware  # type: ignore
    import local_client  # type: ignore
    from config import DEFAULT_LOCAL_DIARIZE, DEFAULT_LOCAL_MODEL, load_config  # type: ignore
    from models import LocalConfig  # type: ignore


def main() -> int:
    cfg = load_config()
    info = hardware.detect()
    modelo = hardware.resolve_model(cfg.get("local_model") or DEFAULT_LOCAL_MODEL, info)
    diarizar = bool(cfg.get("local_diarize", DEFAULT_LOCAL_DIARIZE))

    tamanho = hardware.WHISPER_MODELS.get(modelo, {}).get("download_mb", 0)
    print(f"[warmup] Hardware: {info['device_label']} ({info['compute_type']})")
    print(f"[warmup] Modelo de transcricao: {modelo} (~{tamanho} MB)")
    print(f"[warmup] Separacao de falantes: {'sim' if diarizar else 'nao'}")
    print()

    falhas = []

    if diarizar:
        erros = diarizer.validate_environment()
        if erros:
            for erro in erros:
                print(f"[warmup] AVISO: {erro}")
        else:
            try:
                diarizer.ensure_models()
                print("[warmup] Modelos de separacao de falantes: prontos.")
            except Exception as exc:
                falhas.append(f"separacao de falantes: {exc}")
                print(f"[warmup] AVISO: falhou ao baixar os modelos de voz: {exc}")

    erros = local_client.validate_environment(LocalConfig(model_size=modelo))
    if erros:
        for erro in erros:
            print(f"[warmup] AVISO: {erro}")
        falhas.append("motor de transcricao indisponivel")
    else:
        try:
            local_client.ensure_model(
                LocalConfig(
                    model_size=modelo,
                    device=info["device"],
                    compute_type=info["compute_type"],
                )
            )
            print("[warmup] Modelo de transcricao: pronto.")
        except Exception as exc:
            falhas.append(f"modelo de transcricao: {exc}")
            print(f"[warmup] AVISO: falhou ao baixar o modelo de transcricao: {exc}")

    print()
    if falhas:
        # Nao e erro fatal: o aplicativo baixa sob demanda na primeira transcricao.
        # O instalador so avisa que a primeira execucao vai demorar mais.
        print("[warmup] Terminou com pendencias. O aplicativo funciona mesmo assim,")
        print("[warmup] mas a primeira transcricao vai baixar o que faltou.")
        return 1

    print("[warmup] Tudo pronto. O modo local ja funciona sem internet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
