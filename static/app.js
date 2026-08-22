(() => {
  const state = {
    files: [],
    langs: {},
    jobId: null,
    polling: null,
    renderedLogCount: 0,
  };

  const el = (id) => document.getElementById(id);

  async function loadMeta() {
    const res = await fetch("/api/meta");
    const meta = await res.json();
    el("app-title").textContent = meta.app_name;
    el("app-version").textContent = `v${meta.app_version}`;
    state.langs = meta.langs;

    fillSelect(el("lang-select"), Object.keys(meta.langs));
    fillSelect(el("whisper-model"), meta.whisper_models || []);
    fillSelect(el("whisper-device"), meta.whisper_devices || []);
    fillSelect(
      el("provider-select"),
      Object.keys(meta.providers || {}),
      (key) => meta.providers[key]
    );
  }

  function fillSelect(select, values, labelFor) {
    select.innerHTML = "";
    values.forEach((value) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = labelFor ? labelFor(value) : value;
      select.appendChild(opt);
    });
  }

  function applyProviderVisibility() {
    const isWhisper = el("provider-select").value === "whisper";
    el("whisper-block").style.display = isWhisper ? "" : "none";
    el("transcription-model-field").style.display = isWhisper ? "none" : "";
    el("provider-hint").textContent = isWhisper
      ? "Transcricao 100% local (nada sai da maquina). O resumo consolidado ainda usa o Gemini se houver API key; sem ela, cai num resumo local simples."
      : "Transcricao na nuvem via Gemini. Requer API key e ffmpeg instalado.";
    el("gemini-hint").textContent = isWhisper
      ? "Com o Whisper selecionado, a API key e usada apenas para o resumo consolidado (opcional)."
      : "Gere uma API key gratuita em aistudio.google.com com sua conta Google.";
  }

  function applySummaryHint() {
    el("summary-hint").textContent = el("summary-enabled").checked
      ? "O _consolidado.md traz a analise unificada gerada pelo Gemini."
      : "So as transcricoes. O _consolidado.md sai com metadados e indice dos arquivos, sem analise.";
  }

  async function loadConfig() {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    el("transcription-model").value = cfg.transcription_model || "";
    el("summary-model").value = cfg.summary_model || "";
    if (state.langs[cfg.lang]) {
      el("lang-select").value = cfg.lang;
    }
    el("summary-enabled").checked = cfg.summary_enabled !== false;
    applySummaryHint();
    el("provider-select").value = cfg.provider || "gemini";
    el("whisper-model").value = cfg.whisper_model || "small";
    el("whisper-device").value = cfg.whisper_device || "auto";
    el("whisper-diarization").checked = !!cfg.whisper_diarization;
    el("whisper-num-speakers").value = cfg.whisper_num_speakers || 0;
    applyProviderVisibility();

    const status = el("api-status");
    if (cfg.has_api_key) {
      status.textContent = "API key configurada.";
      status.className = "api-status ok";
    } else {
      status.textContent = "Nenhuma API key salva ainda.";
      status.className = "api-status warn";
    }

    const whisperStatus = el("whisper-status");
    if (cfg.whisper_ready) {
      whisperStatus.textContent = `Whisper pronto (dispositivo: ${cfg.resolved_device}).`;
      whisperStatus.className = "api-status ok";
    } else {
      whisperStatus.textContent = (cfg.whisper_issues || []).join(" ");
      whisperStatus.className = "api-status warn";
    }
  }

  async function saveConfig() {
    const payload = {
      transcription_model: el("transcription-model").value.trim(),
      summary_model: el("summary-model").value.trim(),
      lang: el("lang-select").value,
      summary_enabled: el("summary-enabled").checked,
      provider: el("provider-select").value,
      whisper_model: el("whisper-model").value,
      whisper_device: el("whisper-device").value,
      whisper_diarization: el("whisper-diarization").checked,
      whisper_num_speakers: parseInt(el("whisper-num-speakers").value, 10) || 0,
    };
    const apiKey = el("api-key").value.trim();
    if (apiKey) {
      payload.gemini_api_key = apiKey;
    }
    const hfToken = el("hf-token").value.trim();
    if (hfToken) {
      payload.hf_token = hfToken;
    }
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    el("api-key").value = "";
    el("hf-token").value = "";
    setStatus("Configuracao salva.");
    loadConfig();
  }

  function renderFileList() {
    const list = el("file-list");
    list.innerHTML = "";
    state.files.forEach((file, index) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${file.name} (${(file.size / 1_048_576).toFixed(1)} MB)`;
      const remove = document.createElement("span");
      remove.textContent = "x";
      remove.className = "remove";
      remove.addEventListener("click", () => {
        state.files.splice(index, 1);
        renderFileList();
      });
      li.appendChild(label);
      li.appendChild(remove);
      list.appendChild(li);
    });
  }

  function setStatus(msg) {
    el("status-bar").textContent = msg;
  }

  function appendLogEntries(entries) {
    const box = el("log-box");
    entries.forEach((entry) => {
      const line = document.createElement("div");
      line.className = entry.tag || "";
      line.textContent = entry.msg;
      box.appendChild(line);
    });
    box.scrollTop = box.scrollHeight;
  }

  async function startJob() {
    if (!state.files.length) {
      alert("Adicione pelo menos um arquivo.");
      return;
    }

    const formData = new FormData();
    state.files.forEach((file) => formData.append("files", file));
    formData.append("lang", el("lang-select").value);
    formData.append("title", el("title-input").value.trim());
    formData.append("context_prompt", el("context-input").value);
    formData.append("summary_enabled", el("summary-enabled").checked);

    el("start-btn").disabled = true;
    el("start-btn").textContent = "Processando...";
    el("results").innerHTML = "";
    state.renderedLogCount = 0;

    let res;
    try {
      res = await fetch("/api/jobs", { method: "POST", body: formData });
    } catch (err) {
      resetStartButton();
      setStatus(`Erro de rede: ${err}`);
      return;
    }

    if (!res.ok) {
      const detail = await res.text();
      resetStartButton();
      setStatus(`Erro: ${detail}`);
      alert(detail);
      return;
    }

    const data = await res.json();
    state.jobId = data.job_id;
    setStatus("Transcricao iniciada...");
    state.polling = setInterval(pollJob, 1500);
  }

  function resetStartButton() {
    el("start-btn").disabled = false;
    el("start-btn").textContent = "Iniciar transcricao";
  }

  async function pollJob() {
    if (!state.jobId) return;
    const res = await fetch(`/api/jobs/${state.jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    if (job.log.length > state.renderedLogCount) {
      appendLogEntries(job.log.slice(state.renderedLogCount));
      state.renderedLogCount = job.log.length;
      const last = job.log[job.log.length - 1];
      if (last) setStatus(last.msg.slice(0, 140));
    }

    if (job.status === "done") {
      clearInterval(state.polling);
      resetStartButton();
      setStatus("Concluido!");
      renderResults(job);
    } else if (job.status === "error") {
      clearInterval(state.polling);
      resetStartButton();
      setStatus(`Erro: ${job.error}`);
      alert(`Erro na transcricao:\n\n${job.error}`);
    }
  }

  function renderResults(job) {
    const container = el("results");
    container.innerHTML = "<strong>Arquivos gerados:</strong>";
    job.files.forEach((filename) => {
      const link = document.createElement("a");
      link.href = `/api/jobs/${state.jobId}/download/${encodeURIComponent(filename)}`;
      link.textContent = filename;
      link.setAttribute("download", filename);
      container.appendChild(link);
    });
  }

  el("add-files-btn").addEventListener("click", () => el("file-input").click());
  el("file-input").addEventListener("change", (event) => {
    state.files.push(...Array.from(event.target.files));
    renderFileList();
    event.target.value = "";
  });
  el("save-config-btn").addEventListener("click", saveConfig);
  el("provider-select").addEventListener("change", applyProviderVisibility);
  el("summary-enabled").addEventListener("change", applySummaryHint);
  el("start-btn").addEventListener("click", startJob);
  el("clear-log-btn").addEventListener("click", () => {
    el("log-box").innerHTML = "";
  });

  loadMeta().then(loadConfig);
})();
