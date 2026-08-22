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
    from . import whisper_client
    from .config import (
        APP_NAME,
        APP_VERSION,
        DEFAULT_PROVIDER,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        LANGS,
        OUTPUT_DIR,
        PROVIDER_WHISPER,
        PROVIDERS,
        SUPPORTED_EXTS,
        WHISPER_DEFAULT_DEVICE,
        WHISPER_DEFAULT_MODEL,
        WHISPER_MODELS,
        load_config,
        save_config,
    )
    from .gemini_client import validate_environment
    from .logger import log
    from .models import GeminiConfig, PipelineRequest, WhisperConfig
    from .pipeline import run_pipeline
except ImportError:
    import whisper_client  # type: ignore
    from config import (  # type: ignore
        APP_NAME,
        APP_VERSION,
        DEFAULT_PROVIDER,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        LANGS,
        OUTPUT_DIR,
        PROVIDER_WHISPER,
        PROVIDERS,
        SUPPORTED_EXTS,
        WHISPER_DEFAULT_DEVICE,
        WHISPER_DEFAULT_MODEL,
        WHISPER_MODELS,
        load_config,
        save_config,
    )
    from gemini_client import validate_environment  # type: ignore
    from logger import log  # type: ignore
    from models import GeminiConfig, PipelineRequest, WhisperConfig  # type: ignore
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


def _whisper_config_from_saved(cfg: dict) -> WhisperConfig:
    return WhisperConfig(
        model=cfg.get("whisper_model") or WHISPER_DEFAULT_MODEL,
        device=cfg.get("whisper_device") or WHISPER_DEFAULT_DEVICE,
        compute_type=cfg.get("whisper_compute_type", ""),
        diarization=bool(cfg.get("whisper_diarization", True)),
        hf_token=cfg.get("hf_token", ""),
        num_speakers=int(cfg.get("whisper_num_speakers") or 0),
        min_speakers=int(cfg.get("whisper_min_speakers") or 0),
        max_speakers=int(cfg.get("whisper_max_speakers") or 0),
    )


def _provider_from_saved(cfg: dict) -> str:
    provider = cfg.get("provider") or DEFAULT_PROVIDER
    return provider if provider in PROVIDERS else DEFAULT_PROVIDER


@app.get("/api/meta")
def get_meta():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "langs": LANGS,
        "supported_exts": sorted(SUPPORTED_EXTS),
        "providers": PROVIDERS,
        "whisper_models": WHISPER_MODELS,
        "whisper_devices": ["auto", "cpu", "cuda"],
    }


@app.get("/api/config")
def get_config():
    cfg = load_config()
    whisper = _whisper_config_from_saved(cfg)
    whisper_issues = whisper_client.validate_environment(whisper)
    return {
        "has_api_key": bool(cfg.get("gemini_api_key")),
        "transcription_model": cfg.get("gemini_transcription_model") or GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        "summary_model": cfg.get("gemini_summary_model") or GEMINI_DEFAULT_SUMMARY_MODEL,
        "lang": cfg.get("lang", "Portugues (pt)"),
        "provider": _provider_from_saved(cfg),
        "whisper_model": whisper.model,
        "whisper_device": whisper.device,
        "whisper_diarization": whisper.diarization,
        "whisper_num_speakers": whisper.num_speakers,
        "has_hf_token": bool(whisper.hf_token),
        "whisper_ready": not whisper_issues,
        "whisper_issues": whisper_issues,
        "resolved_device": whisper_client.resolve_device(whisper.device),
    }


@app.post("/api/config")
def set_config(payload: dict):
    cfg = load_config()
    if payload.get("gemini_api_key"):
        cfg["gemini_api_key"] = payload["gemini_api_key"].strip()
    if payload.get("hf_token"):
        cfg["hf_token"] = payload["hf_token"].strip()
    if "transcription_model" in payload:
        cfg["gemini_transcription_model"] = (payload["transcription_model"] or "").strip()
    if "summary_model" in payload:
        cfg["gemini_summary_model"] = (payload["summary_model"] or "").strip()
    if "lang" in payload:
        cfg["lang"] = payload["lang"]
    if payload.get("provider") in PROVIDERS:
        cfg["provider"] = payload["provider"]
    if "whisper_model" in payload:
        cfg["whisper_model"] = (payload["whisper_model"] or "").strip()
    if "whisper_device" in payload:
        cfg["whisper_device"] = (payload["whisper_device"] or "").strip()
    if "whisper_diarization" in payload:
        cfg["whisper_diarization"] = bool(payload["whisper_diarization"])
    if "whisper_num_speakers" in payload:
        try:
            cfg["whisper_num_speakers"] = max(0, int(payload["whisper_num_speakers"] or 0))
        except (TypeError, ValueError):
            cfg["whisper_num_speakers"] = 0
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
    whisper_config = _whisper_config_from_saved(cfg)
    provider = _provider_from_saved(cfg)

    if provider == PROVIDER_WHISPER:
        errors = whisper_client.validate_environment(whisper_config)
    else:
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
        provider=provider,
        whisper=whisper_config,
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
