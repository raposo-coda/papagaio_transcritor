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
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


# ── Diretório de logs ─────────────────────────────────────────────────────────
LOG_DIR = Path.home() / ".transcritor_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

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
        self._gui_callback: Optional[Callable[[str, str], None]] = None
        self._session_file: Optional[Path] = None
        self._file_handler: Optional[logging.FileHandler] = None

        # Logger Python interno
        self._log = logging.getLogger("transcritor")
        self._log.setLevel(logging.DEBUG)

        # Handler de stdout (DEBUG+)
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(logging.DEBUG)
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

    def set_gui_callback(self, callback: Callable[[str, str], None]):
        """
        Registra função para enviar mensagens à GUI.
        Assinatura: callback(mensagem: str, tag: str)
        """
        self._gui_callback = callback

    @property
    def log_file(self) -> Optional[Path]:
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

    def exception(self, msg: str, exc: Optional[Exception] = None):
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
        """Envia mensagem ao callback da GUI se registrado."""
        if self._gui_callback:
            try:
                self._gui_callback(msg, tag)
            except Exception:
                pass


# Instância global — importar com: from transcritor.logger import log
log = TranscritorLogger()
