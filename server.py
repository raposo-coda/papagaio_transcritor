"""
server.py - API web (FastAPI) que substitui a GUI tkinter.
"""

from __future__ import annotations

import io
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from . import diarizer, hardware, local_client, security
    from .config import (
        APP_DATA_DIR,
        APP_NAME,
        APP_VERSION,
        DEFAULT_LOCAL_DIARIZE,
        DEFAULT_LOCAL_MODEL,
        DEFAULT_MODE,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        JOB_RETENTION_SECONDS,
        LANGS,
        LOCAL_MODEL_AUTO,
        LOCAL_SPEAKERS_AUTO,
        MAX_API_KEY_LEN,
        MAX_CONTEXT_PROMPT_LEN,
        MAX_FILES_PER_JOB,
        MAX_MODEL_NAME_LEN,
        MAX_SPEAKERS,
        MAX_TITLE_LEN,
        MAX_UPLOAD_BYTES,
        MODE_CLOUD,
        MODE_LOCAL,
        MODES,
        SUPPORTED_EXTS,
        get_default_output_dir,
        get_output_dir,
        in_container,
        load_config,
        save_config,
        validate_output_dir,
    )
    from .gemini_client import validate_environment
    from .logger import log
    from .models import GeminiConfig, LocalConfig, PipelineRequest
    from .pipeline import run_pipeline
except ImportError:
    import diarizer  # type: ignore
    import hardware  # type: ignore
    import local_client  # type: ignore
    import security  # type: ignore
    from config import (  # type: ignore
        APP_DATA_DIR,
        APP_NAME,
        APP_VERSION,
        DEFAULT_LOCAL_DIARIZE,
        DEFAULT_LOCAL_MODEL,
        DEFAULT_MODE,
        GEMINI_DEFAULT_SUMMARY_MODEL,
        GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        JOB_RETENTION_SECONDS,
        LANGS,
        LOCAL_MODEL_AUTO,
        LOCAL_SPEAKERS_AUTO,
        MAX_API_KEY_LEN,
        MAX_CONTEXT_PROMPT_LEN,
        MAX_FILES_PER_JOB,
        MAX_MODEL_NAME_LEN,
        MAX_SPEAKERS,
        MAX_TITLE_LEN,
        MAX_UPLOAD_BYTES,
        MODE_CLOUD,
        MODE_LOCAL,
        MODES,
        SUPPORTED_EXTS,
        get_default_output_dir,
        get_output_dir,
        in_container,
        load_config,
        save_config,
        validate_output_dir,
    )
    from gemini_client import validate_environment  # type: ignore
    from logger import log  # type: ignore
    from models import GeminiConfig, LocalConfig, PipelineRequest  # type: ignore
    from pipeline import run_pipeline  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Os uploads sao temporarios (apagados ao fim do job): ficam na pasta de dados
# do aplicativo, nao na pasta de relatorios do usuario.
UPLOAD_DIR = APP_DATA_DIR / "_uploads"

# Extensoes que o endpoint de download pode servir. O cache de transcricao
# (_cache.json) fica de fora de proposito: e texto verbatim, nao relatorio.
DOWNLOADABLE_EXTS = {".md"}

# Le o upload em blocos para abortar assim que passar do limite, em vez de
# descobrir o tamanho depois de ja ter gravado o arquivo inteiro em disco.
UPLOAD_CHUNK_BYTES = 1024 * 1024

app = FastAPI(title=APP_NAME)
app.middleware("http")(security.security_middleware)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_run_lock = threading.Lock()


_hardware_cache: dict | None = None


def _hardware_info() -> dict:
    """Detecta o hardware uma vez e reaproveita (nao muda durante a execucao)."""
    global _hardware_cache
    if _hardware_cache is None:
        _hardware_cache = hardware.detect()
    return _hardware_cache


def _gemini_config_from_saved(cfg: dict) -> GeminiConfig:
    return GeminiConfig(
        api_key=cfg.get("gemini_api_key", ""),
        transcription_model=cfg.get("gemini_transcription_model") or GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        summary_model=cfg.get("gemini_summary_model") or GEMINI_DEFAULT_SUMMARY_MODEL,
    )


def _resolve_local_model(escolha: str) -> str:
    """Converte 'auto' no modelo recomendado para este hardware."""
    return hardware.resolve_model(escolha, _hardware_info())


def _local_config_from_saved(cfg: dict) -> LocalConfig:
    info = _hardware_info()
    return LocalConfig(
        model_size=_resolve_local_model(cfg.get("local_model") or DEFAULT_LOCAL_MODEL),
        device=info["device"],
        compute_type=info["compute_type"],
        diarize=_diarize_from_saved(cfg),
        num_speakers=_num_speakers_from_saved(cfg),
    )


def _diarize_from_saved(cfg: dict) -> bool:
    if "local_diarize" not in cfg:
        return DEFAULT_LOCAL_DIARIZE
    return bool(cfg["local_diarize"])


def _num_speakers_from_saved(cfg: dict) -> int:
    try:
        quantos = int(cfg.get("local_num_speakers", LOCAL_SPEAKERS_AUTO))
    except (TypeError, ValueError):
        return LOCAL_SPEAKERS_AUTO
    if quantos < 0 or quantos > MAX_SPEAKERS:
        return LOCAL_SPEAKERS_AUTO
    return quantos


def _mode_from_saved(cfg: dict) -> str:
    modo = cfg.get("mode")
    if not modo:
        # Sem escolha explicita do usuario: quem ja tem chave salva continua na nuvem
        # (era o comportamento anterior); quem esta chegando agora comeca no modo local,
        # que nao envia nada para lugar nenhum e nao exige configuracao.
        local_ok = local_client.validate_environment(
            LocalConfig(model_size=_resolve_local_model(DEFAULT_LOCAL_MODEL))
        ) == []
        modo = MODE_CLOUD if (cfg.get("gemini_api_key") or not local_ok) else MODE_LOCAL
    return modo if modo in MODES else DEFAULT_MODE


def _purge_old_jobs():
    """Descarta jobs terminados ha tempo suficiente, para _jobs nao crescer sem fim."""
    limite = time.monotonic() - JOB_RETENTION_SECONDS
    for job_id, job in list(_jobs.items()):
        if job["status"] != "running" and job.get("finished_at", 0) < limite:
            _jobs.pop(job_id, None)


def _get_job(job_id: str) -> dict:
    """Copia do registro do job. A lista de log e copiada tambem: a thread do
    pipeline continua acrescentando itens nela enquanto a resposta e serializada."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job nao encontrado.")
        snapshot = dict(job)
        snapshot["log"] = list(job["log"])
        return snapshot


@app.get("/api/meta")
def get_meta():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "langs": LANGS,
        "supported_exts": sorted(SUPPORTED_EXTS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "max_files_per_job": MAX_FILES_PER_JOB,
    }


@app.get("/api/config")
def get_config():
    cfg = load_config()
    escolha_local = cfg.get("local_model") or DEFAULT_LOCAL_MODEL
    modelo_local = _resolve_local_model(escolha_local)
    return {
        "has_api_key": bool(cfg.get("gemini_api_key")),
        "transcription_model": cfg.get("gemini_transcription_model") or GEMINI_DEFAULT_TRANSCRIPTION_MODEL,
        "summary_model": cfg.get("gemini_summary_model") or GEMINI_DEFAULT_SUMMARY_MODEL,
        "lang": cfg.get("lang", "Portugues (pt)"),
        "mode": _mode_from_saved(cfg),
        "local_model_choice": escolha_local,
        "local_model_resolved": modelo_local,
        "local_model_downloaded": local_client.is_model_downloaded(modelo_local),
        "local_diarize": _diarize_from_saved(cfg),
        "local_num_speakers": _num_speakers_from_saved(cfg),
        "max_speakers": MAX_SPEAKERS,
        "local_available": local_client.validate_environment(LocalConfig(model_size=modelo_local)) == [],
        "output_dir": str(get_output_dir()),
        "default_output_dir": str(get_default_output_dir()),
        "output_dir_editable": not in_container(),
    }


class ConfigPayload(BaseModel):
    """
    Esquema explicito do /api/config. Sem ele, qualquer chave entrava na
    config.json sem validacao e sem limite de tamanho.
    """

    model_config = {"extra": "forbid"}

    gemini_api_key: str | None = Field(default=None, max_length=MAX_API_KEY_LEN)
    transcription_model: str | None = Field(default=None, max_length=MAX_MODEL_NAME_LEN)
    summary_model: str | None = Field(default=None, max_length=MAX_MODEL_NAME_LEN)
    lang: str | None = None
    mode: str | None = None
    local_model: str | None = None
    local_diarize: bool | None = None
    local_num_speakers: int | None = Field(default=None, ge=0, le=MAX_SPEAKERS)
    output_dir: str | None = Field(default=None, max_length=4096)


@app.post("/api/config")
def set_config(payload: ConfigPayload):
    cfg = load_config()

    if payload.gemini_api_key:
        cfg["gemini_api_key"] = payload.gemini_api_key.strip()
    if payload.transcription_model is not None:
        cfg["gemini_transcription_model"] = payload.transcription_model.strip()
    if payload.summary_model is not None:
        cfg["gemini_summary_model"] = payload.summary_model.strip()
    if payload.lang is not None:
        if payload.lang not in LANGS:
            raise HTTPException(400, f"Idioma invalido: {payload.lang}")
        cfg["lang"] = payload.lang
    if payload.mode is not None:
        if payload.mode not in MODES:
            raise HTTPException(400, f"Modo invalido: {payload.mode}")
        cfg["mode"] = payload.mode
    if payload.local_model is not None:
        escolha = payload.local_model or LOCAL_MODEL_AUTO
        if escolha != LOCAL_MODEL_AUTO and escolha not in hardware.WHISPER_MODELS:
            raise HTTPException(400, f"Modelo local invalido: {escolha}")
        cfg["local_model"] = escolha
    if payload.local_diarize is not None:
        cfg["local_diarize"] = bool(payload.local_diarize)
    if payload.local_num_speakers is not None:
        cfg["local_num_speakers"] = int(payload.local_num_speakers)
    if payload.output_dir is not None:
        if in_container():
            raise HTTPException(400, "No Docker a pasta de saida e fixa (volume ./output).")
        if payload.output_dir.strip():
            try:
                cfg["output_dir"] = str(validate_output_dir(payload.output_dir))
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            cfg.pop("output_dir", None)  # vazio = voltar ao padrao (Documentos)

    save_config(cfg)
    return {"ok": True, "output_dir": str(get_output_dir())}


class OutputDirPayload(BaseModel):
    model_config = {"extra": "forbid"}

    output_dir: str = Field(max_length=4096)


@app.post("/api/config/validate-output-dir")
def check_output_dir(payload: OutputDirPayload):
    """Confere o caminho enquanto o usuario digita, sem salvar nada."""
    if in_container():
        raise HTTPException(400, "No Docker a pasta de saida e fixa (volume ./output).")
    try:
        resolvido = validate_output_dir(payload.output_dir)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "resolved": str(resolvido)}


@app.get("/api/hardware")
def get_hardware():
    """
    Retrato do hardware desta maquina, usado para dimensionar o modelo local
    e estimar o tempo de transcricao. Nenhuma informacao sai daqui.
    """
    info = dict(_hardware_info())
    info["models"] = {
        nome: {**dados, "downloaded": local_client.is_model_downloaded(nome)}
        for nome, dados in info["models"].items()
    }
    info["local_available"] = local_client.validate_environment(
        LocalConfig(model_size=info["recommended_model"])
    ) == []
    info["diarization"] = {
        "available": diarizer.validate_environment() == [],
        "downloaded": diarizer.is_model_downloaded(),
        "download_mb": diarizer.DOWNLOAD_MB,
        "backend": diarizer.BACKEND_LABEL,
        "speed_factor": hardware.DIARIZATION_SPEED,
        "word_timestamps_overhead": hardware.WORD_TIMESTAMPS_OVERHEAD,
        "max_speakers": MAX_SPEAKERS,
    }
    return info


@app.get("/api/audit")
def get_audit():
    """
    Declara, por modo, exatamente o que sai deste computador. Serve ao painel
    de transparencia da interface.
    """
    pasta = str(get_output_dir())
    return {
        "cloud": {
            "mode": "cloud",
            "label": "Modo nuvem (Google Gemini)",
            "leaves_machine": True,
            "sends": [
                "O arquivo de audio/video inteiro, convertido para mp4, e enviado aos servidores do Google.",
                "O texto transcrito volta e e reenviado ao Google para gerar o resumo.",
                "O contexto que voce escreveu no passo 3 acompanha o pedido de resumo.",
                "Sua chave de API identifica sua conta Google em cada chamada.",
            ],
            "stays": [
                f"Os relatorios .md finais, salvos em {pasta}.",
                "O cache de transcricoes, na mesma pasta.",
                "Sua chave de API, guardada em disco nesta maquina, legivel so por voce.",
                "Os logs de execucao.",
            ],
            "destinations": ["generativelanguage.googleapis.com (Google Gemini API)"],
        },
        "local": {
            "mode": "local",
            "label": "Modo local (Whisper offline)",
            "leaves_machine": False,
            "sends": [],
            "stays": [
                "O audio e o video nunca saem da maquina: sao lidos do disco e processados na sua CPU/GPU.",
                "A transcricao e feita pelo modelo Whisper rodando aqui dentro.",
                "A separacao de falantes tambem roda aqui: o que e comparado sao caracteristicas "
                "da voz, calculadas nesta maquina e descartadas ao fim do processamento.",
                "O panorama consolidado e calculado a partir do proprio texto, sem IA e sem rede.",
                f"Relatorios, cache e logs ficam so no seu computador, em {pasta}.",
            ],
            "destinations": [],
            "one_time_download": (
                "Excecao unica: os modelos sao baixados na primeira vez que voce os usa. O de "
                "transcricao vem do repositorio publico Hugging Face (huggingface.co) e os da "
                "separacao de falantes vem das releases do projeto sherpa-onnx no GitHub "
                f"(github.com) - cerca de {diarizer.DOWNLOAD_MB} MB. Nenhum dos dois exige conta "
                "ou token, e sao downloads de mao unica: nada seu vai junto. Depois disso o modo "
                "local funciona com a internet desligada."
            ),
        },
    }


def _save_upload(upload: UploadFile, dest: Path):
    """Grava um upload cortando em MAX_UPLOAD_BYTES."""
    total = 0
    try:
        with dest.open("wb") as handle:
            while True:
                bloco = upload.file.read(UPLOAD_CHUNK_BYTES)
                if not bloco:
                    break
                total += len(bloco)
                if total > MAX_UPLOAD_BYTES:
                    limite_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                    raise HTTPException(413, f"Arquivo acima do limite de {limite_mb} MB: {dest.name}")
                handle.write(bloco)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


@app.post("/api/jobs")
async def create_job(
    files: list[UploadFile],
    lang: str = Form("Portugues (pt)"),
    title: str = Form(""),
    context_prompt: str = Form(""),
):
    if not files:
        raise HTTPException(400, "Nenhum arquivo enviado.")
    if len(files) > MAX_FILES_PER_JOB:
        raise HTTPException(400, f"Maximo de {MAX_FILES_PER_JOB} arquivos por transcricao.")
    if lang not in LANGS:
        raise HTTPException(400, f"Idioma invalido: {lang}")

    title = title.strip()[:MAX_TITLE_LEN]
    context_prompt = context_prompt[:MAX_CONTEXT_PROMPT_LEN]

    cfg = load_config()
    modo = _mode_from_saved(cfg)
    gemini_config = _gemini_config_from_saved(cfg)
    local_config = _local_config_from_saved(cfg)
    output_dir = get_output_dir()

    if modo == MODE_LOCAL:
        errors = local_client.validate_environment(local_config)
    else:
        errors = validate_environment(gemini_config)
    if errors:
        raise HTTPException(400, "\n".join(errors))

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "Ja existe uma transcricao em andamento. Aguarde terminar.")

    # A partir daqui o lock esta nas nossas maos. Qualquer saida antes de a thread
    # comecar precisa devolve-lo, senao o servidor responde 409 ate ser reiniciado.
    job_id = uuid.uuid4().hex[:12]
    job_upload_dir = UPLOAD_DIR / job_id
    try:
        job_upload_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[Path] = []
        for upload in files:
            ext = Path(upload.filename or "").suffix.lower()
            if ext not in SUPPORTED_EXTS:
                raise HTTPException(400, f"Extensao nao suportada: {upload.filename}")
            dest = job_upload_dir / Path(upload.filename or "").name
            _save_upload(upload, dest)
            saved_paths.append(dest)

        with _jobs_lock:
            _purge_old_jobs()
            _jobs[job_id] = {
                "status": "running",
                "mode": modo,
                "log": [],
                "error": None,
                "session_dir": None,
                "output_dir": str(output_dir),
                "files": [],
                "consolidated": None,
                "finished_at": 0.0,
            }

        request = PipelineRequest(
            file_paths=saved_paths,
            lang_code=LANGS[lang],
            output_dir=output_dir,
            title=title,
            context_prompt=context_prompt,
            gemini=gemini_config,
            mode=modo,
            local=local_config,
            job_id=job_id,
        )
    except BaseException:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        with _jobs_lock:
            _jobs.pop(job_id, None)
        _run_lock.release()
        raise

    def on_done(session_dir: Path, consolidated_path: Path):
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["session_dir"] = str(session_dir)
            _jobs[job_id]["files"] = sorted(p.name for p in session_dir.glob("*.md"))
            _jobs[job_id]["consolidated"] = consolidated_path.name
            _jobs[job_id]["finished_at"] = time.monotonic()
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        _run_lock.release()

    def on_error(message: str):
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = message
            _jobs[job_id]["finished_at"] = time.monotonic()
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        _run_lock.release()

    def worker():
        # O callback e registrado por thread: assim o log de um job nunca cai no
        # registro de outro, mesmo que a thread anterior ainda esteja viva.
        def gui_callback(msg: str, tag: str):
            with _jobs_lock:
                registro = _jobs.get(job_id)
                if registro is not None:
                    registro["log"].append({"msg": msg, "tag": tag})

        log.set_gui_callback(gui_callback)
        try:
            run_pipeline(request=request, on_done=on_done, on_error=on_error)
        finally:
            log.set_gui_callback(None)

    threading.Thread(target=worker, daemon=True).start()

    return {"job_id": job_id, "output_dir": str(output_dir)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return JSONResponse(_get_job(job_id))


def _session_dir_of(job_id: str) -> Path:
    job = _get_job(job_id)
    if not job.get("session_dir"):
        raise HTTPException(404, "Job ainda nao produziu relatorios.")
    return Path(job["session_dir"])


@app.get("/api/jobs/{job_id}/download/{filename}")
def download_job_file(job_id: str, filename: str):
    session_dir = _session_dir_of(job_id)

    safe_name = Path(filename).name
    if Path(safe_name).suffix.lower() not in DOWNLOADABLE_EXTS:
        raise HTTPException(404, "Arquivo nao encontrado.")

    file_path = session_dir / safe_name
    if not file_path.is_file() or file_path.parent != session_dir:
        raise HTTPException(404, "Arquivo nao encontrado.")
    return FileResponse(file_path, filename=safe_name)


@app.get("/api/jobs/{job_id}/download-all")
def download_job_zip(job_id: str):
    """Todos os relatorios da sessao em um zip - so os .md, nunca o cache."""
    session_dir = _session_dir_of(job_id)

    relatorios = sorted(p for p in session_dir.glob("*.md") if p.is_file())
    if not relatorios:
        raise HTTPException(404, "Nenhum relatorio para baixar.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for caminho in relatorios:
            zf.write(caminho, arcname=caminho.name)

    nome_zip = f"{session_dir.name}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_zip}"'},
    )


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
