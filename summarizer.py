"""
summarizer.py — Geração de resumo via provedores de IA
=======================================================
Cada provider tem um _call_* isolado. generate_summary() despacha
para o correto e faz fallback local se todos falharem.
"""

import json
import urllib.request
from typing import Optional

import assemblyai as aai

from .logger import log


# ── Construtores de prompt ────────────────────────────────────────────────────

def _build_instruction(context_prompt: str) -> str:
    """
    Prompt CURTO com apenas a instrução.
    Usado pelo LeMUR — o conteúdo é acessado internamente via transcript_ids,
    não precisa ser embutido no prompt.
    """
    ctx_block = ""
    if context_prompt.strip():
        ctx_block = f"**Contexto fornecido:**\n{context_prompt.strip()}\n\n"

    return (
        f"{ctx_block}"
        "Analise a(s) transcricao(oes) e produza um relatorio estruturado em Markdown.\n\n"
        "Se o contexto indicar um formato especifico (ex: por ambiente, por topico), "
        "siga-o fielmente.\nCaso contrario, use:\n\n"
        "1. **Resumo Executivo** - paragrafo com o tema central\n"
        "2. **Pontos-chave** - principais assuntos discutidos\n"
        "3. **Decisoes e Acoes** - compromissos e proximos passos\n"
        "4. **Participantes** - perfil de cada falante (se houver)\n"
        "5. **Conclusao** - encerramento sintetico\n\n"
        "Use Markdown limpo. Seja direto e objetivo."
    )


def _build_prompt_with_text(transcripts: list, context_prompt: str) -> str:
    """
    Prompt COMPLETO com texto embutido.
    Necessário para providers externos que não têm acesso aos transcript_ids.
    """
    n = len(transcripts)
    file_note = (
        f"A seguir estao as transcricoes de {n} arquivos de um mesmo contexto. "
        "Trate-os como partes de uma mesma sessao.\n\n"
        if n > 1 else ""
    )

    texts = []
    for i, t in enumerate(transcripts, 1):
        text = (t.text or "").strip()
        if text:
            header = f"--- Arquivo {i} ---" if n > 1 else "--- Transcricao ---"
            texts.append(f"{header}\n{text}")

    body = "\n\n".join(texts) if texts else "_Sem texto disponivel._"
    instruction = _build_instruction(context_prompt)
    return f"{file_note}{body}\n\n{instruction}"


# ── Callers por provider ──────────────────────────────────────────────────────

def _call_lemur(transcripts: list, context_prompt: str) -> Optional[str]:
    """
    LeMUR usa transcript_ids — NÃO embute o texto no prompt.
    O conteúdo é recuperado internamente pela AssemblyAI.
    """
    transcript_ids = [t.id for t in transcripts if t and t.id]
    if not transcript_ids:
        log.warning("  [lemur] Nenhum transcript_id valido encontrado")
        return None

    log.info(f"  [lemur] Enviando {len(transcript_ids)} transcript_id(s): {transcript_ids}")
    instruction = _build_instruction(context_prompt)
    log.debug(f"  [lemur] Instrucao ({len(instruction)} chars):\n{instruction[:300]}...")

    result = aai.Lemur().task(
        prompt=instruction,
        transcript_ids=transcript_ids,
        max_output_size=4000,
    )

    log.debug(f"  [lemur] Resposta bruta: {repr((result.response or '')[:200])}")
    response = (result.response or "").strip()
    if not response:
        log.warning("  [lemur] Resposta vazia")
    return response or None


def _call_anthropic(prompt: str, model: str, api_key: str) -> Optional[str]:
    try:
        import anthropic
        log.info(f"  [anthropic] Modelo: {model or 'claude-haiku-4-5'}")
        client = anthropic.Anthropic(api_key=api_key or None)
        msg = client.messages.create(
            model=model or "claude-haiku-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        log.debug(f"  [anthropic] stop_reason={msg.stop_reason}, "
                  f"tokens_in={msg.usage.input_tokens}, out={msg.usage.output_tokens}")
        return msg.content[0].text.strip() if msg.content else None
    except ImportError:
        log.warning("  [anthropic] Pacote nao instalado: pip install anthropic")
    except Exception as e:
        log.exception("  [anthropic] Falhou", exc=e)
    return None


def _call_openai(prompt: str, model: str, api_key: str, base_url: str) -> Optional[str]:
    try:
        import openai
        kwargs: dict = {"api_key": api_key or None}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        log.info(f"  [openai] Modelo: {model or 'gpt-4o-mini'} | url: {base_url or 'default'}")
        client = openai.OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        log.debug(f"  [openai] finish_reason={resp.choices[0].finish_reason if resp.choices else '?'}, "
                  f"tokens={resp.usage.total_tokens if resp.usage else '?'}")
        return resp.choices[0].message.content.strip() if resp.choices else None
    except ImportError:
        log.warning("  [openai] Pacote nao instalado: pip install openai")
    except Exception as e:
        log.exception("  [openai] Falhou", exc=e)
    return None


def _call_ollama(prompt: str, model: str, base_url: str) -> Optional[str]:
    """Ollama via /api/generate — sem dependência extra (urllib stdlib)."""
    url = (base_url.rstrip("/") if base_url else "http://localhost:11434") + "/api/generate"
    body = json.dumps({
        "model": model or "llama3",
        "prompt": prompt,
        "stream": False,
    }).encode()

    log.info(f"  [ollama] url={url} modelo={model or 'llama3'}")
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        log.debug(f"  [ollama] eval_count={data.get('eval_count','?')}, "
                  f"done={data.get('done','?')}")
        return (data.get("response") or "").strip() or None
    except Exception as e:
        log.exception("  [ollama] Falhou", exc=e)
    return None


# ── Dispatcher principal ──────────────────────────────────────────────────────

def generate_summary(
    transcripts: list,
    context_prompt: str,
    provider_id: str = "lemur",
    ai_model: str = "",
    ai_key: str = "",
    ai_url: str = "",
) -> str:
    """
    Gera resumo via provider escolhido.
    Fallback automático para resumo local se o provider falhar.

    LeMUR: instrução curta + transcript_ids (sem texto embutido)
    Demais: texto completo das transcrições embutido no prompt
    """
    log.info(f"\n[ia] Provider={provider_id} | Modelo={ai_model or 'default'}")
    result: Optional[str] = None

    try:
        if provider_id == "lemur":
            result = _call_lemur(transcripts, context_prompt)

        else:
            prompt = _build_prompt_with_text(transcripts, context_prompt)
            log.info(f"  [ia] Prompt com texto: ~{len(prompt.split())} palavras")

            if provider_id == "anthropic":
                result = _call_anthropic(prompt, ai_model, ai_key)
            elif provider_id in ("openai", "openai_compat"):
                result = _call_openai(prompt, ai_model, ai_key, ai_url)
            elif provider_id == "ollama":
                result = _call_ollama(prompt, ai_model, ai_url)
            else:
                log.warning(f"  [ia] Provider desconhecido: '{provider_id}'")

    except Exception as e:
        log.exception(f"  [ia] Excecao inesperada no provider '{provider_id}'", exc=e)

    if result:
        log.ok(f"  [ia] Resumo gerado com sucesso via {provider_id} ({len(result)} chars)")
        return result

    log.warning("  [ia] Todos os providers falharam — usando fallback local")
    return _fallback_summary_all(transcripts)


# ── Fallbacks locais ──────────────────────────────────────────────────────────

def _fallback_summary_all(transcripts: list) -> str:
    """Constrói resumo a partir dos capítulos/texto sem chamar nenhuma API."""
    from .transcriber import fmt_time  # import local para evitar circular

    if not transcripts:
        return "_Resumo indisponivel: nenhuma transcricao encontrada._"

    lines = [
        "## Resumo Consolidado (gerado localmente)",
        "",
        "> **Nota:** Nenhum provider de IA disponivel. "
        "Resumo gerado a partir dos topicos detectados automaticamente.",
        "",
    ]

    for i, t in enumerate(transcripts, 1):
        chapters = t.chapters or []
        if len(transcripts) > 1:
            lines += [f"### Arquivo {i}", ""]

        if chapters:
            lines += ["**Topicos identificados:**", ""]
            for ch in chapters:
                ts = fmt_time(ch.start / 1000)
                lines.append(f"- `{ts}` **{ch.headline}** — {ch.summary}")
            lines.append("")
        else:
            text = (t.text or "").strip()
            if text:
                snippet = " ".join(text.split()[:300])
                if len(text.split()) > 300:
                    snippet += "..."
                lines += ["**Trecho inicial:**", "", f"> {snippet}", ""]
            else:
                lines += ["_Sem conteudo disponivel._", ""]

    return "\n".join(lines)
