"""
logger.py — Sistema de log centralizado do TranscritorIA
=========================================================
Todos os módulos importam `get_logger()` e usam a mesma instância.
O log é escrito simultaneamente em:
  - arquivo  : ~/.transcritor_logs/YYYY-MM-DD_HH-MM-SS.log
  - stdout   : via print (útil em terminal / debug)
  - callback : função opcional para atualizar a GUI em tempo real
"""

import logging
import os
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

try:
    from .config import APP_DATA_DIR, LOG_RETENTION_FILES
except ImportError:
    from config import APP_DATA_DIR, LOG_RETENTION_FILES  # type: ignore


# ── Diretório de logs ─────────────────────────────────────────────────────────
LOG_DIR = APP_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Nível do que vai para o stdout. DEBUG despeja caminhos e metadados no terminal
# (e em `docker compose logs`), então o padrão é INFO.
STDOUT_LEVEL = getattr(logging, os.environ.get("PAPAGAIO_LOG_LEVEL", "INFO").upper(), logging.INFO)


def prune_old_logs(keep: int = LOG_RETENTION_FILES):
    """
    Mantém só os `keep` logs de sessão mais recentes.

    Os logs registram nomes de arquivo e títulos de sessão — que identificam de
    quem é a gravação — então acumulá-los para sempre é retenção de dado pessoal
    sem motivo. PAPAGAIO_LOG_RETENTION ajusta o limite.
    """
    if keep <= 0:
        return
    try:
        arquivos = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for antigo in arquivos[keep:]:
            antigo.unlink(missing_ok=True)
    except OSError:
        pass  # limpeza é oportunista: nunca deve derrubar uma transcrição

# ── Níveis customizados para exibição na GUI ──────────────────────────────────
LOG_TAGS = {
    "DEBUG":    "dim",
    "INFO":     "",
    "OK":       "ok",
    "WARNING":  "warn",
    "ERROR":    "err",
    "CRITICAL": "err",
}

# Nível OK (entre INFO e WARNING, para mensagens de sucesso)
OK_LEVEL = 25
logging.addLevelName(OK_LEVEL, "OK")


class TranscritorLogger:
    """
    Logger wrapper que:
    - Grava em arquivo com timestamp e nível
    - Chama callback da GUI (thread-safe via queue)
    - Expõe .debug / .info / .ok / .warning / .error / .exception
    """

    def __init__(self):
        # O callback é por thread: cada job roda na sua, e o log de um nunca
        # pode cair no registro de outro.
        self._local = threading.local()
        self._session_file: Path | None = None
        self._file_handler: logging.FileHandler | None = None

        # Logger Python interno
        self._log = logging.getLogger("transcritor")
        self._log.setLevel(logging.DEBUG)

        # Handler de stdout
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(STDOUT_LEVEL)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        self._log.addHandler(sh)

    # ── Setup de sessão ───────────────────────────────────────────────────────

    def start_session(self, label: str = ""):
        """Abre arquivo de log para a sessão atual."""
        if self._file_handler:
            self._log.removeHandler(self._file_handler)
            self._file_handler.close()

        prune_old_logs()

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)[:40]
        fname = f"{ts}_{safe}.log" if safe else f"{ts}.log"
        self._session_file = LOG_DIR / fname

        fh = logging.FileHandler(self._session_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._log.addHandler(fh)
        self._file_handler = fh
        self.info(f"=== Sessao iniciada: {label or 'sem titulo'} ===")
        self.info(f"Log em: {self._session_file}")

    def set_gui_callback(self, callback: Callable[[str, str], None] | None):
        """
        Registra função para enviar mensagens à GUI, só para a thread atual.
        Assinatura: callback(mensagem: str, tag: str). Passe None para limpar.
        """
        self._local.gui_callback = callback

    @property
    def log_file(self) -> Path | None:
        return self._session_file

    # ── Métodos de log ────────────────────────────────────────────────────────

    def debug(self, msg: str):
        self._log.debug(msg)
        self._emit(msg, "dim")

    def info(self, msg: str):
        self._log.info(msg)
        self._emit(msg, "")

    def ok(self, msg: str):
        self._log.log(OK_LEVEL, msg)
        self._emit(msg, "ok")

    def warning(self, msg: str):
        self._log.warning(msg)
        self._emit(msg, "warn")

    def error(self, msg: str):
        self._log.error(msg)
        self._emit(msg, "err")

    def exception(self, msg: str, exc: Exception | None = None):
        """Loga erro + traceback completo no arquivo, mensagem curta na GUI."""
        tb = traceback.format_exc() if exc is None else (
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        self._log.error(f"{msg}\n{tb}")
        # GUI recebe só a linha principal, sem traceback
        short = f"{msg} — {type(exc).__name__}: {exc}" if exc else msg
        self._emit(short, "err")

    # ── Compat: aceita chamada como função (log("msg")) para código legado ────

    def __call__(self, msg: str, tag: str = ""):
        """Permite usar o logger como callable: log('mensagem')."""
        level = {
            "ok":   self.ok,
            "warn": self.warning,
            "err":  self.error,
            "dim":  self.debug,
        }.get(tag, self.info)
        level(msg)

    # ── Interno ───────────────────────────────────────────────────────────────

    def _emit(self, msg: str, tag: str):
        """Envia mensagem ao callback da GUI registrado por esta thread, se houver."""
        callback = getattr(self._local, "gui_callback", None)
        if callback:
            try:
                callback(msg, tag)
            except Exception:
                pass


# Instância global — importar com: from transcritor.logger import log
log = TranscritorLogger()
