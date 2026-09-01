"""
config.py - Constantes, validacao de caminhos e persistencia.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

APP_NAME = "Papagaio Transcritor"
APP_VERSION = "4.1"
PACKAGE_NAME = "papagaio_transcritor"

# Nome da pasta criada em Documentos para guardar os relatorios.
OUTPUT_FOLDER_NAME = "Papagaio Transcritor"


def in_container() -> bool:
    """Dentro do Docker a pasta de saida e fixa (/app/output); o usuario nao escolhe."""
    return Path("/.dockerenv").exists() or os.environ.get("PAPAGAIO_IN_DOCKER") == "1"


def get_app_data_dir() -> Path:
    env_override = os.environ.get("PAPAGAIO_DATA_DIR")
    candidates = []
    if env_override:
        candidates.append(Path(env_override))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "PapagaioTranscritor")
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "PapagaioTranscritor")
    candidates.append(Path.cwd() / ".papagaio_transcritor_data")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return Path.cwd()


def get_models_dir() -> Path:
    """Onde os modelos Whisper do modo local ficam guardados."""
    env_override = os.environ.get("PAPAGAIO_MODELS_DIR")
    models_dir = Path(env_override) if env_override else (APP_DATA_DIR / "models")
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir


# ---------------------------------------------------------------- pasta de saida


def get_documents_dir() -> Path:
    """A pasta Documentos do usuario, com os nomes usados em pt-BR e no OneDrive."""
    home = Path.home()
    candidates = [
        home / "Documents",
        home / "Documentos",
        home / "OneDrive" / "Documents",
        home / "OneDrive" / "Documentos",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return home


def get_default_output_dir() -> Path:
    """Padrao: <Documentos>/Papagaio Transcritor. PAPAGAIO_OUTPUT_DIR tem prioridade."""
    env_override = os.environ.get("PAPAGAIO_OUTPUT_DIR")
    if env_override:
        return Path(env_override)
    return get_documents_dir() / OUTPUT_FOLDER_NAME


# Prefixos de sistema onde a aplicacao nunca deve escrever.
_FORBIDDEN_POSIX = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/dev", "/proc", "/sys", "/var",
)
_FORBIDDEN_WINDOWS = ("windows", "program files", "program files (x86)", "programdata", "system32")


def _is_forbidden(path: Path) -> bool:
    # Raiz do volume ("/" ou "C:\") nunca e destino valido.
    if path == path.parent:
        return True
    if os.name == "nt":
        parts = [part.lower().strip("\\/") for part in path.parts[1:]]
        return bool(parts) and parts[0] in _FORBIDDEN_WINDOWS
    texto = str(path)
    return any(texto == prefixo or texto.startswith(prefixo + "/") for prefixo in _FORBIDDEN_POSIX)


def validate_output_dir(raw: str) -> Path:
    """
    Valida um caminho de saida vindo do usuario (API/interface).

    Levanta ValueError com mensagem em portugues quando o caminho nao serve.
    Por padrao so aceita caminhos dentro da pasta do usuario; defina
    PAPAGAIO_ALLOW_ANY_OUTPUT_DIR=1 para liberar (uso consciente, fora do padrao).
    """
    texto = (raw or "").strip().strip('"').strip("'")
    if not texto:
        raise ValueError("Informe um caminho de pasta.")

    try:
        path = Path(texto).expanduser()
    except Exception as exc:
        raise ValueError("Caminho invalido.") from exc

    if not path.is_absolute():
        raise ValueError("Use um caminho absoluto, comecando pela raiz ou pela letra do disco.")

    try:
        path = path.resolve()
    except Exception as exc:
        raise ValueError("Caminho invalido.") from exc

    if _is_forbidden(path):
        raise ValueError("Essa pasta e do sistema operacional. Escolha uma pasta sua, como Documentos.")

    if os.environ.get("PAPAGAIO_ALLOW_ANY_OUTPUT_DIR") != "1":
        home = Path.home().resolve()
        if not (path == home or home in path.parents):
            raise ValueError(f"Escolha uma pasta dentro de {home}.")

    if path.exists() and not path.is_dir():
        raise ValueError("Ja existe um arquivo com esse nome. Escolha outra pasta.")

    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise ValueError("Nao foi possivel criar essa pasta. Verifique o caminho e as permissoes.") from exc

    if not os.access(path, os.W_OK):
        raise ValueError("Sem permissao para gravar nessa pasta.")

    return path


def get_output_dir() -> Path:
    """
    Pasta de saida em uso: a escolhida pelo usuario, senao o padrao.
    Resolvida a cada chamada, porque o usuario pode troca-la em execucao.
    """
    if not in_container():
        escolhida = load_config().get("output_dir")
        if escolhida:
            try:
                return validate_output_dir(escolhida)
            except ValueError:
                # Pasta salva ficou invalida (removida, pendrive desconectado):
                # cai no padrao em vez de derrubar a aplicacao.
                pass
    padrao = get_default_output_dir()
    padrao.mkdir(parents=True, exist_ok=True)
    return padrao


APP_DATA_DIR = get_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "config.json"
CACHE_FILE = "_cache.json"

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
SUPPORTED_EXTS = VIDEO_EXTS | AUDIO_EXTS

LANGS = {
    "Portugues (pt)": "pt",
    "English (en)": "en",
    "Espanol (es)": "es",
    "Francais (fr)": "fr",
    "Deutsch (de)": "de",
    "Italiano (it)": "it",
    "Japanese (ja)": "ja",
    "Korean (ko)": "ko",
    "Chinese (zh)": "zh",
}

GEMINI_DEFAULT_TRANSCRIPTION_MODEL = "gemini-flash-latest"
GEMINI_DEFAULT_SUMMARY_MODEL = "gemini-flash-latest"

# Modos de processamento.
MODE_CLOUD = "cloud"  # transcricao e resumo pelo Google Gemini (o arquivo sai da maquina)
MODE_LOCAL = "local"  # transcricao pelo Whisper na propria maquina (nada sai)
MODES = (MODE_CLOUD, MODE_LOCAL)
DEFAULT_MODE = MODE_CLOUD

# "auto" deixa o aplicativo escolher o modelo Whisper conforme o hardware detectado.
LOCAL_MODEL_AUTO = "auto"
DEFAULT_LOCAL_MODEL = LOCAL_MODEL_AUTO

# Separacao de falantes no modo local. Ligada por padrao: sem ela o relatorio de
# uma reuniao vira um bloco unico de texto sem dono.
DEFAULT_LOCAL_DIARIZE = True
LOCAL_SPEAKERS_AUTO = 0  # deixa o numero de falantes ser descoberto por agrupamento
MAX_SPEAKERS = 10

# Limites de entrada (aplicados em server.py).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # limite do File API do Gemini por arquivo
MAX_FILES_PER_JOB = 50
MAX_TITLE_LEN = 120
MAX_CONTEXT_PROMPT_LEN = 4000
MAX_MODEL_NAME_LEN = 80
MAX_API_KEY_LEN = 200

# Quantos jobs concluidos ficam na memoria antes de serem descartados.
JOB_RETENTION_SECONDS = int(os.environ.get("PAPAGAIO_JOB_RETENTION", str(6 * 3600)))
# Quantos arquivos de log de sessao manter em APP_DATA_DIR/logs.
LOG_RETENTION_FILES = int(os.environ.get("PAPAGAIO_LOG_RETENTION", "20"))


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict):
    """
    Grava a config. O arquivo guarda a chave da API, entao e criado com
    permissao 600 (so o dono le) antes de receber conteudo.
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        try:
            CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            print("[aviso] nao foi possivel restringir a permissao de config.json", file=sys.stderr)
    # Falha de escrita nao pode passar em silencio: a chave nao seria salva
    # e o usuario nao saberia o motivo.
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
