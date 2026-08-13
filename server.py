"""
server.py - API web (FastAPI) que substitui a GUI tkinter.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

try:
    from .config import (
        APP_NAME,
        APP_VERSION,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        LANGS,
        OUTPUT_DIR,
        SUPPORTED_EXTS,
        load_config,
        save_config,
    )
    from .gemini_client import validate_environment
    from .logger import log
    from .models import GeminiConfig, PipelineRequest
    from .pipeline import run_pipeline
except ImportError:
    from config import (  # type: ignore
        APP_NAME,
        APP_VERSION,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        LANGS,
        OUTPUT_DIR,
        SUPPORTED_EXTS,
        load_config,
        save_config,
    )
    from gemini_client import validate_environment  # type: ignore
    from logger import log  # type: ignore
    from models import GeminiConfig, PipelineRequest  # type: ignore
    from pipeline import run_pipeline  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = OUTPUT_DIR / "_uploads"

app = FastAPI(title=APP_NAME)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_run_lock = threading.Lock()
_current_job_id: Optional[str] = None


def _gemini_config_from_saved(cfg: dict) -> GeminiConfig:
    return GeminiConfig(
        api_key=cfg.get("gemini_api_key", ""),
        transcription_model=cfg.get("gemini_transcription_model") or GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        summary_model=cfg.get("gemini_summary_model") or GEMINI_DEFAULT_SUMMARY_MODEL,
    )


@app.get("/api/meta")
def get_meta():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "langs": LANGS,
        "supported_exts": sorted(SUPPORTED_EXTS),
    }


@app.get("/api/config")
def get_config():
    cfg = load_config()
    return {
        "has_api_key": bool(cfg.get("gemini_api_key")),
        "transcription_model": cfg.get("gemini_transcription_model") or GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        "summary_model": cfg.get("gemini_summary_model") or GEMINI_DEFAULT_SUMMARY_MODEL,
        "lang": cfg.get("lang", "Portugues (pt)"),
    }


@app.post("/api/config")
def set_config(payload: dict):
    cfg = load_config()
    if payload.get("gemini_api_key"):
        cfg["gemini_api_key"] = payload["gemini_api_key"].strip()
    if "transcription_model" in payload:
        cfg["gemini_transcription_model"] = (payload["transcription_model"] or "").strip()
    if "summary_model" in payload:
        cfg["gemini_summary_model"] = (payload["summary_model"] or "").strip()
    if "lang" in payload:
        cfg["lang"] = payload["lang"]
    save_config(cfg)
    return {"ok": True}


@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile],
    lang: str = Form("pt"),
    title: str = Form(""),
    context_prompt: str = Form(""),
):
    global _current_job_id

    if not files:
        raise HTTPException(400, "Nenhum arquivo enviado.")

    cfg = load_config()
    gemini_config = _gemini_config_from_saved(cfg)
    errors = validate_environment(gemini_config)
    if errors:
        raise HTTPException(400, "\n".join(errors))

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "Ja existe uma transcricao em andamento. Aguarde terminar.")

    job_id = uuid.uuid4().hex[:12]
    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for upload in files:
        ext = Path(upload.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTS:
            _run_lock.release()
            raise HTTPException(400, f"Extensao nao suportada: {upload.filename}")
        dest = job_upload_dir / Path(upload.filename).name
        with dest.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        saved_paths.append(dest)

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "log": [],
            "error": None,
            "session_dir": None,
            "files": [],
            "consolidated": None,
        }
        _current_job_id = job_id

    def gui_callback(msg: str, tag: str):
        with _jobs_lock:
            _jobs[job_id]["log"].append({"msg": msg, "tag": tag})

    log.set_gui_callback(gui_callback)

    request = PipelineRequest(
        file_paths=saved_paths,
        lang_code=LANGS.get(lang, lang),
        output_dir=OUTPUT_DIR,
        title=title,
        context_prompt=context_prompt,
        gemini=gemini_config,
    )

    def on_done(session_dir: Path, consolidated_path: Path):
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["session_dir"] = str(session_dir)
            _jobs[job_id]["files"] = sorted(p.name for p in session_dir.glob("*.md"))
            _jobs[job_id]["consolidated"] = consolidated_path.name
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        _run_lock.release()

    def on_error(message: str):
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = message
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        _run_lock.release()

    threading.Thread(
        target=run_pipeline,
        kwargs={"request": request, "on_done": on_done, "on_error": on_error},
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job nao encontrado.")
        return JSONResponse(dict(job))


@app.get("/api/jobs/{job_id}/download/{filename}")
def download_job_file(job_id: str, filename: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or not job.get("session_dir"):
            raise HTTPException(404, "Job nao encontrado.")
        session_dir = Path(job["session_dir"])

    safe_name = Path(filename).name
    file_path = session_dir / safe_name
    if not file_path.exists() or file_path.parent != session_dir:
        raise HTTPException(404, "Arquivo nao encontrado.")
    return FileResponse(file_path, filename=safe_name)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
