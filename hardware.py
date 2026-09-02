"""
hardware.py - Detecta o hardware disponivel para escolher o modelo Whisper local
e estimar quanto tempo a transcricao vai levar.

Tudo aqui e local e barato de calcular: nenhuma chamada de rede.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

# Modelos Whisper suportados, do mais leve ao mais pesado.
# download_mb = tamanho aproximado baixado na primeira vez.
WHISPER_MODELS = {
    "tiny": {"label": "Tiny", "download_mb": 75, "quality": "Basico"},
    "base": {"label": "Base", "download_mb": 145, "quality": "Razoavel"},
    "small": {"label": "Small", "download_mb": 480, "quality": "Bom"},
    "medium": {"label": "Medium", "download_mb": 1530, "quality": "Muito bom"},
    "large-v3": {"label": "Large v3", "download_mb": 3090, "quality": "Melhor"},
}

MODEL_ORDER = ["tiny", "base", "small", "medium", "large-v3"]

# Quantos segundos de audio sao processados por segundo de relogio (fator "x tempo real").
# Numeros aproximados, medidos em maquinas comuns. Servem para dar ordem de grandeza,
# nao promessa de desempenho.
_SPEED_GPU = {"tiny": 60.0, "base": 50.0, "small": 35.0, "medium": 20.0, "large-v3": 12.0}
_SPEED_CPU_8_CORES = {"tiny": 12.0, "base": 8.0, "small": 3.5, "medium": 1.2, "large-v3": 0.5}

# Separacao de falantes (sherpa-onnx). Roda sempre em CPU, independente da GPU.
# Medido em 01/09/2026: 56 s de audio em ~10 s de relogio (Ryzen 1800X, 16 threads
# visiveis no container) = 5,6x tempo real. O chute inicial era 15x, quase o triplo.
DIARIZATION_SPEED = 5.5

# Os timestamps por palavra, necessarios para casar fala com falante, encarecem a
# transcricao em torno de 15%.
WORD_TIMESTAMPS_OVERHEAD = 1.15


def _run(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _detect_nvidia_gpu() -> dict | None:
    """Le nome, VRAM e capacidade de computacao da primeira GPU NVIDIA."""
    if not shutil.which("nvidia-smi"):
        return None

    # compute_cap so existe em drivers recentes; se falhar, consulta sem ele.
    saida = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"]
    )
    if not saida:
        saida = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not saida:
        return None

    partes = [parte.strip() for parte in saida.splitlines()[0].split(",")]
    if len(partes) < 2:
        return None
    try:
        vram_gb = round(float(partes[1]) / 1024, 1)
    except ValueError:
        return None

    capacidade = None
    if len(partes) >= 3:
        try:
            capacidade = float(partes[2])
        except ValueError:
            capacidade = None

    return {"name": partes[0], "vram_gb": vram_gb, "compute_cap": capacidade}


def _gpu_compute_type(gpu: dict | None) -> str:
    """
    float16 so vale a pena a partir de Volta (capacidade 7.0), quando existem
    Tensor Cores. Em Pascal - GTX 10xx - o FP16 roda a uma fracao da velocidade
    do FP32, e o CTranslate2 acaba caindo para float32 de qualquer jeito.
    Nessas placas, pesos em int8 com computo em float32 rendem bem mais.
    """
    if not gpu:
        return "int8"
    capacidade = gpu.get("compute_cap")
    if capacidade is None:
        return "float16"  # driver antigo demais para informar: mantem o padrao
    return "float16" if capacidade >= 7.0 else "int8_float32"


def _gpu_is_legacy(gpu: dict | None) -> bool:
    capacidade = (gpu or {}).get("compute_cap")
    return capacidade is not None and capacidade < 7.0


def _cuda_usable() -> bool:
    """
    O ctranslate2 (motor do faster-whisper) so usa a GPU se as bibliotecas CUDA
    estiverem presentes. Ter uma placa NVIDIA nao basta - dentro do Docker, por
    exemplo, a GPU so aparece se o container foi iniciado com acesso a ela.
    """
    try:
        import ctranslate2  # type: ignore
    except ImportError:
        return False
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _total_ram_gb() -> float | None:
    try:
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names and "SC_PHYS_PAGES" in os.sysconf_names:
            bytes_totais = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            return round(bytes_totais / (1024 ** 3), 1)
    except (ValueError, OSError):
        pass

    if platform.system() == "Windows":
        saida = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
        if saida:
            for linha in saida.splitlines():
                linha = linha.strip()
                if linha.isdigit():
                    return round(int(linha) / (1024 ** 3), 1)
    return None


def _recommend(gpu: dict | None, cuda_ok: bool, cores: int, ram_gb: float | None) -> tuple[str, list[str]]:
    """Escolhe o maior modelo que a maquina aguenta sem ficar insuportavelmente lenta."""
    notas: list[str] = []

    if cuda_ok and gpu:
        vram = gpu["vram_gb"]
        # Em placas sem Tensor Cores usamos pesos int8, que ocupam cerca de metade
        # da VRAM do float16 - da para subir um degrau de modelo com a mesma placa.
        if _gpu_is_legacy(gpu):
            # 7.5 e nao 8 porque placas de 8 GB costumam reportar um pouco menos
            # que 8192 MiB, e uma 1070 nao pode escorregar para o degrau de baixo.
            limites = [(7.5, "large-v3"), (4.5, "medium"), (3.0, "small")]
        else:
            limites = [(10, "large-v3"), (6, "medium"), (4, "small")]

        modelo = "base"
        for minimo, nome in limites:
            if vram >= minimo:
                modelo = nome
                break

        notas.append(f"GPU NVIDIA detectada e utilizavel ({gpu['name']}, {vram} GB de VRAM).")
        return modelo, notas

    if gpu and not cuda_ok:
        notas.append(
            f"Sua placa {gpu['name']} foi detectada, mas nao esta acessivel para o motor de "
            "transcricao (falta suporte CUDA neste ambiente). Sera usada a CPU."
        )

    # CPU
    if (cores >= 12 and (ram_gb is None or ram_gb >= 16)) or cores >= 8:
        modelo = "small"
    elif cores >= 4:
        modelo = "base"
    else:
        modelo = "tiny"

    ram_txt = f", {ram_gb} GB de RAM" if ram_gb else ""
    notas.append(f"Sem GPU utilizavel. Transcricao na CPU ({cores} nucleos{ram_txt}).")
    return modelo, notas


# A tabela _SPEED_GPU pressupoe Tensor Cores. Placas Pascal e anteriores rodam
# em float32 e ficam bem abaixo disso.
#
# Medido em 01/09/2026 numa GTX 1070 (capacidade 6.1):
#   small    ~18,7x contra 35x na tabela -> penalidade 0,53
#   large-v3  ~5,1x contra 12x na tabela -> penalidade 0,43
# A penalidade nao e constante: modelos maiores sao mais limitados por computo e
# sofrem mais sem Tensor Cores. Um numero so nao cobre os dois, entao fica alinhado
# ao large-v3, que e o recomendado nessas placas. O efeito colateral e subestimar a
# velocidade dos modelos pequenos - erro no sentido seguro, o de prometer mais tempo.
_LEGACY_GPU_PENALTY = 0.42


def speed_factor(model_size: str, device: str, cores: int, legacy_gpu: bool = False) -> float:
    """Segundos de audio processados por segundo de relogio."""
    if device == "cuda":
        base = _SPEED_GPU.get(model_size, 10.0)
        return base * _LEGACY_GPU_PENALTY if legacy_gpu else base
    base = _SPEED_CPU_8_CORES.get(model_size, 2.0)
    escala = max(0.3, min(2.0, cores / 8.0))
    return max(0.15, base * escala)


def estimate_seconds(
    audio_seconds: float,
    model_size: str,
    device: str,
    cores: int,
    diarize: bool = False,
    legacy_gpu: bool = False,
) -> float:
    """
    Estimativa grosseira do tempo de transcricao, incluindo a conversao com ffmpeg
    e, quando ligada, a separacao de falantes.

    A mesma formula esta em static/app.js (renderEstimate) para a estimativa
    aparecer sem ida ao servidor - mexeu aqui, mexa la.
    """
    if audio_seconds <= 0:
        return 0.0
    fator = speed_factor(model_size, device, cores, legacy_gpu=legacy_gpu)
    transcricao = audio_seconds / fator
    conversao = audio_seconds / 25.0  # ffmpeg costuma ser bem mais rapido que tempo real
    if diarize:
        transcricao *= WORD_TIMESTAMPS_OVERHEAD
        conversao += audio_seconds / DIARIZATION_SPEED
    return transcricao + conversao + 3.0


def detect() -> dict:
    """Retorna um retrato do hardware e a recomendacao de modelo."""
    cores = os.cpu_count() or 2
    ram_gb = _total_ram_gb()
    gpu = _detect_nvidia_gpu()
    cuda_ok = _cuda_usable()
    modelo, notas = _recommend(gpu, cuda_ok, cores, ram_gb)

    device = "cuda" if cuda_ok and gpu else "cpu"
    legacy = device == "cuda" and _gpu_is_legacy(gpu)
    compute_type = _gpu_compute_type(gpu) if device == "cuda" else "int8"

    if legacy:
        notas.append(
            f"Placa da geracao Pascal ou anterior (capacidade {gpu['compute_cap']}): sem Tensor Cores, "
            "o float16 nao compensa. Usando pesos int8 com computo em float32, que rende mais nessas placas."
        )

    velocidades = {
        nome: round(speed_factor(nome, device, cores, legacy_gpu=legacy), 1) for nome in MODEL_ORDER
    }

    return {
        "device": device,
        "device_label": f"GPU - {gpu['name']}" if device == "cuda" else f"CPU - {cores} nucleos",
        "gpu_name": gpu["name"] if gpu else None,
        "gpu_vram_gb": gpu["vram_gb"] if gpu else None,
        "gpu_compute_cap": gpu["compute_cap"] if gpu else None,
        "gpu_legacy": legacy,
        "gpu_detected": gpu is not None,
        "cuda_usable": cuda_ok,
        "cpu_cores": cores,
        "ram_gb": ram_gb,
        "compute_type": compute_type,
        "recommended_model": modelo,
        "notes": notas,
        "models": {
            nome: {
                **WHISPER_MODELS[nome],
                "speed_factor": velocidades[nome],
            }
            for nome in MODEL_ORDER
        },
    }


def resolve_model(escolha: str, info: dict) -> str:
    """
    Converte a escolha do usuario ('auto' ou um tamanho) no modelo efetivo.

    Fica aqui, e nao no server, porque o warmup.py precisa chegar exatamente ao
    mesmo modelo que a aplicacao usaria - duas copias da regra iriam divergir.
    """
    if not escolha or escolha == "auto" or escolha not in WHISPER_MODELS:
        return info["recommended_model"]
    return escolha
