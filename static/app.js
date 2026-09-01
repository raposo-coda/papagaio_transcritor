(() => {
  const state = {
    entries: [], // { file, duration } - duration em segundos ou null se o navegador nao souber ler
    langs: {},
    supportedExts: [],
    hasApiKey: false,
    mode: "cloud",
    hardware: null,
    audit: null,
    localModelChoice: "auto",
    localModelResolved: "",
    localAvailable: false,
    diarize: true,
    numSpeakers: 0,
    maxSpeakers: 10,
    jobId: null,
    outputDir: "",
    defaultOutputDir: "",
    outputDirTimer: null,
    polling: null,
    renderedLogCount: 0,
    logCleared: false,
  };

  const el = (id) => document.getElementById(id);
  const GUIDE_SEEN_KEY = "papagaio_guide_seen";

  // ---------------------------------------------------------------- guia
  function openGuide(anchorId) {
    el("guide-overlay").classList.remove("hidden");
    document.body.classList.add("no-scroll");
    if (anchorId) {
      const alvo = document.querySelector(anchorId);
      if (alvo) alvo.scrollIntoView({ block: "start" });
    }
  }

  function closeGuide() {
    el("guide-overlay").classList.add("hidden");
    document.body.classList.remove("no-scroll");
    try {
      localStorage.setItem(GUIDE_SEEN_KEY, "1");
    } catch (err) {
      /* modo privativo: apenas nao lembra */
    }
  }

  function maybeShowGuideOnFirstVisit() {
    let seen = null;
    try {
      seen = localStorage.getItem(GUIDE_SEEN_KEY);
    } catch (err) {
      seen = null;
    }
    if (!seen) openGuide();
  }

  // ---------------------------------------------------------------- formatacao
  function fmtDuration(segundos) {
    if (!segundos || segundos < 1) return "menos de 1 min";
    const totalMin = Math.round(segundos / 60);
    if (totalMin < 1) return "menos de 1 min";
    if (totalMin < 60) return `${totalMin} min`;
    const horas = Math.floor(totalMin / 60);
    const minutos = totalMin % 60;
    return minutos ? `${horas} h ${minutos} min` : `${horas} h`;
  }

  // ---------------------------------------------------------------- carga
  async function loadMeta() {
    const res = await fetch("/api/meta");
    const meta = await res.json();
    el("app-title").textContent = meta.app_name;
    el("app-version").textContent = `v${meta.app_version}`;
    state.langs = meta.langs;
    state.supportedExts = meta.supported_exts || [];

    const select = el("lang-select");
    select.innerHTML = "";
    Object.keys(meta.langs).forEach((label) => {
      const opt = document.createElement("option");
      opt.value = label;
      opt.textContent = label;
      select.appendChild(opt);
    });

    const formatos = state.supportedExts.map((ext) => ext.replace(".", "")).join(", ");
    el("formats-hint").textContent = `Formatos aceitos: ${formatos}`;
    el("guide-formats").textContent =
      `Audio e video nestes formatos: ${formatos}. Nao precisa converter nada antes - ` +
      `o aplicativo prepara o arquivo sozinho.`;
    el("file-input").setAttribute("accept", state.supportedExts.join(","));
  }

  async function loadHardware() {
    const res = await fetch("/api/hardware");
    state.hardware = await res.json();
    renderHardware();
    renderDiarizeState();
  }

  async function loadAudit() {
    const res = await fetch("/api/audit");
    state.audit = await res.json();
    renderAudit();
  }

  async function loadConfig() {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    el("transcription-model").value = cfg.transcription_model || "";
    el("summary-model").value = cfg.summary_model || "";
    if (state.langs[cfg.lang]) {
      el("lang-select").value = cfg.lang;
    }
    state.hasApiKey = Boolean(cfg.has_api_key);
    state.mode = cfg.mode || "cloud";
    state.localModelChoice = cfg.local_model_choice || "auto";
    state.localModelResolved = cfg.local_model_resolved || "";
    state.localAvailable = Boolean(cfg.local_available);
    state.diarize = cfg.local_diarize !== false;
    state.numSpeakers = Number(cfg.local_num_speakers) || 0;
    state.maxSpeakers = Number(cfg.max_speakers) || 10;
    state.outputDir = cfg.output_dir || "";
    state.defaultOutputDir = cfg.default_output_dir || "";
    renderOutputDir(Boolean(cfg.output_dir_editable));

    const status = el("api-status");
    if (state.hasApiKey) {
      status.textContent = "Chave salva. O modo nuvem esta pronto.";
      status.className = "api-status ok";
    } else {
      status.textContent = "Nenhuma chave salva ainda - cole sua API key acima e clique em Salvar configuracao.";
      status.className = "api-status warn";
    }

    renderModelSelect();
    renderSpeakersSelect();
    applyMode();
  }

  // ---------------------------------------------------------------- pasta de saida
  function renderOutputDir(editavel) {
    el("output-dir").value = state.outputDir;
    const hint = el("output-dir-hint");

    if (!editavel) {
      // No Docker a pasta e o volume ./output; deixar o campo editavel so
      // criaria uma expectativa que o container nao consegue cumprir.
      el("output-dir").disabled = true;
      el("save-output-dir-btn").disabled = true;
      el("reset-output-dir-btn").disabled = true;
      hint.textContent =
        "Rodando no Docker: os relatorios ficam na pasta output ao lado do aplicativo.";
      hint.className = "hint";
      return;
    }
    hint.textContent = `Pasta padrao: ${state.defaultOutputDir}`;
    hint.className = "hint";
  }

  async function checkOutputDir(caminho) {
    const hint = el("output-dir-hint");
    if (!caminho.trim()) {
      hint.textContent = `Vazio = usar a pasta padrao: ${state.defaultOutputDir}`;
      hint.className = "hint";
      return;
    }
    let dados;
    try {
      const res = await fetch("/api/config/validate-output-dir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ output_dir: caminho }),
      });
      dados = await res.json();
    } catch (err) {
      return; // sem rede para validar: o Salvar ainda vai dizer o que houve
    }
    if (dados.ok) {
      hint.textContent = `Pasta valida: ${dados.resolved}`;
      hint.className = "hint ok-hint";
    } else {
      hint.textContent = dados.error || dados.detail || "Caminho invalido.";
      hint.className = "hint warn-hint";
    }
  }

  async function saveOutputDir(caminho) {
    try {
      const dados = await postConfig({ output_dir: caminho }, { silent: true });
      state.outputDir = dados.output_dir || "";
      el("output-dir").value = state.outputDir;
      const hint = el("output-dir-hint");
      hint.textContent = `Salvo. Os relatorios vao para ${state.outputDir}`;
      hint.className = "hint ok-hint";
      loadAudit(); // o painel de transparencia cita a pasta
    } catch (err) {
      const hint = el("output-dir-hint");
      hint.textContent = String(err.message || err);
      hint.className = "hint warn-hint";
    }
  }

  async function postConfig(payload, opcoes = {}) {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detalhe = await res.text();
      try {
        // FastAPI devolve {"detail": "..."}; mostrar so a mensagem.
        const corpo = JSON.parse(detalhe);
        if (corpo && corpo.detail) detalhe = corpo.detail;
      } catch (err) {
        /* resposta nao era JSON: usa o texto cru */
      }
      if (opcoes.silent) throw new Error(detalhe);
      alert(`Nao foi possivel salvar: ${detalhe}`);
      return false;
    }
    return (await res.json()) || true;
  }

  async function saveConfig() {
    const payload = {
      transcription_model: el("transcription-model").value.trim(),
      summary_model: el("summary-model").value.trim(),
      lang: el("lang-select").value,
    };
    const apiKey = el("api-key").value.trim();
    if (apiKey) {
      payload.gemini_api_key = apiKey;
    }
    if (await postConfig(payload)) {
      el("api-key").value = "";
      setStatus("Configuracao salva.");
      loadConfig();
    }
  }

  // ---------------------------------------------------------------- modo
  async function setMode(novoModo) {
    if (novoModo === state.mode) return;
    if (novoModo === "local" && !state.localAvailable) {
      alert(
        "O motor de transcricao local nao esta instalado nesta copia do aplicativo.\n\n" +
          "Se voce usa Docker, refaca a imagem com:\n    docker compose up -d --build\n\n" +
          "Sem Docker:\n    pip install faster-whisper"
      );
      return;
    }
    if (await postConfig({ mode: novoModo })) {
      state.mode = novoModo;
      applyMode();
      setStatus(
        novoModo === "local"
          ? "Modo local ativado. Nada sai do seu computador."
          : "Modo nuvem ativado. Os arquivos serao enviados ao Google Gemini."
      );
    }
  }

  function applyMode() {
    const local = state.mode === "local";

    el("mode-local").classList.toggle("active", local);
    el("mode-cloud").classList.toggle("active", !local);
    el("mode-local").classList.toggle("unavailable", !state.localAvailable);

    document.querySelectorAll(".mode-only-local").forEach((node) => {
      node.classList.toggle("hidden", !local);
    });
    document.querySelectorAll(".mode-only-cloud").forEach((node) => {
      node.classList.toggle("hidden", local);
    });

    const badge = el("mode-badge");
    badge.textContent = local ? "LOCAL - OFFLINE" : "NUVEM - GOOGLE";
    badge.className = local ? "mode-badge safe" : "mode-badge net";

    el("mode-note").textContent = local
      ? "Modo local ativo: o audio e o video sao lidos do disco e processados aqui mesmo. Nenhum byte do seu conteudo vai para a internet."
      : "Modo nuvem ativo: cada arquivo sera enviado aos servidores do Google para ser transcrito.";
    el("mode-note").className = local ? "mode-note safe" : "mode-note net";

    el("context-hint").textContent = local
      ? "Aparece no cabecalho do relatorio. No modo local ele nao alimenta nenhuma IA - o panorama e estatistico."
      : "Diga quem fala, do que se trata e o que voce quer destacar. Isso melhora a identificacao dos falantes e o foco do resumo.";

    el("run-explain").textContent = local
      ? "Cada arquivo e preparado e transcrito aqui dentro. E mais lento que a nuvem, entao acompanhe a estimativa acima e o painel Log ao lado."
      : "Cada arquivo e preparado, enviado ao Gemini, transcrito e resumido. Acompanhe pelo painel Log ao lado.";

    renderAudit();
    renderEstimate();
    updateReadiness();
  }

  // ---------------------------------------------------------------- hardware
  function renderHardware() {
    const info = state.hardware;
    const caixa = el("hardware-box");
    if (!info) return;

    const linhas = [];
    if (!info.local_available) {
      caixa.innerHTML =
        '<div class="hw-note warn">O motor de transcricao local (faster-whisper) nao esta instalado ' +
        "nesta copia do aplicativo. Com Docker, refaca a imagem: " +
        "<code>docker compose up -d --build</code>. Sem Docker: <code>pip install faster-whisper</code>.</div>";
      return;
    }
    linhas.push(
      `<div class="hw-line"><span class="hw-key">Processamento</span><span class="hw-val ${
        info.device === "cuda" ? "good" : ""
      }">${info.device_label}</span></div>`
    );
    if (info.gpu_detected) {
      linhas.push(
        `<div class="hw-line"><span class="hw-key">Placa de video</span><span class="hw-val">${info.gpu_name} (${info.gpu_vram_gb} GB)${
          info.cuda_usable ? "" : " - nao acessivel"
        }</span></div>`
      );
    }
    if (info.ram_gb) {
      linhas.push(`<div class="hw-line"><span class="hw-key">Memoria</span><span class="hw-val">${info.ram_gb} GB</span></div>`);
    }
    linhas.push(
      `<div class="hw-line"><span class="hw-key">Modelo escolhido</span><span class="hw-val good">${
        (info.models[info.recommended_model] || {}).label || info.recommended_model
      }</span></div>`
    );

    (info.notes || []).forEach((nota) => {
      linhas.push(`<div class="hw-note">${nota}</div>`);
    });

    if (!info.cuda_usable && info.gpu_detected) {
      linhas.push(
        `<div class="hw-note">Para usar a GPU dentro do Docker, suba com: ` +
          `<code>docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build</code></div>`
      );
    }

    caixa.innerHTML = linhas.join("");
  }

  function renderModelSelect() {
    const info = state.hardware;
    const select = el("local-model-select");
    if (!info) return;

    select.innerHTML = "";

    const recomendado = info.models[info.recommended_model] || {};
    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = `Automatico - ${recomendado.label || info.recommended_model} (recomendado para esta maquina)`;
    select.appendChild(auto);

    Object.entries(info.models).forEach(([nome, dados]) => {
      const opt = document.createElement("option");
      opt.value = nome;
      const baixado = dados.downloaded ? "ja baixado" : `baixa ~${dados.download_mb} MB`;
      opt.textContent = `${dados.label} - qualidade ${dados.quality}, ~${dados.speed_factor}x tempo real, ${baixado}`;
      select.appendChild(opt);
    });

    select.value = state.localModelChoice || "auto";
    renderModelHint();
  }

  function currentLocalModel() {
    if (!state.hardware) return null;
    const escolha = el("local-model-select").value || "auto";
    return escolha === "auto" ? state.hardware.recommended_model : escolha;
  }

  function renderModelHint() {
    const info = state.hardware;
    if (!info) return;
    const nome = currentLocalModel();
    const dados = info.models[nome] || {};
    const partes = [
      `Processa cerca de ${dados.speed_factor}x mais rapido que o tempo real nesta maquina.`,
    ];
    if (dados.downloaded) {
      partes.push("Ja esta baixado - funciona sem internet.");
    } else {
      partes.push(
        `Ainda nao esta na maquina: na primeira transcricao serao baixados ~${dados.download_mb} MB ` +
          `(so o modelo, nenhum dado seu). Depois disso funciona offline.`
      );
    }
    el("local-model-hint").textContent = partes.join(" ");
  }

  async function onModelChange() {
    const escolha = el("local-model-select").value;
    state.localModelChoice = escolha;
    if (await postConfig({ local_model: escolha })) {
      renderModelHint();
      renderEstimate();
    }
  }

  // ------------------------------------------------------ separacao de falantes
  function diarizationInfo() {
    return (state.hardware && state.hardware.diarization) || null;
  }

  function renderSpeakersSelect() {
    const select = el("local-speakers-select");
    select.innerHTML = "";

    const auto = document.createElement("option");
    auto.value = "0";
    auto.textContent = "Detectar automaticamente";
    select.appendChild(auto);

    for (let n = 2; n <= state.maxSpeakers; n += 1) {
      const opt = document.createElement("option");
      opt.value = String(n);
      opt.textContent = `${n} pessoas`;
      select.appendChild(opt);
    }

    select.value = String(state.numSpeakers || 0);
    el("local-diarize-toggle").checked = state.diarize;
    renderDiarizeState();
  }

  function renderDiarizeState() {
    const info = diarizationInfo();
    const dica = el("local-diarize-hint");
    const toggle = el("local-diarize-toggle");
    const disponivel = Boolean(info && info.available);

    toggle.disabled = !disponivel;
    el("speakers-field").classList.toggle("hidden", !state.diarize || !disponivel);

    if (!disponivel) {
      dica.textContent =
        "A separacao de falantes nao esta instalada nesta copia. Com Docker, refaca a imagem " +
        "com: docker compose up -d --build";
      dica.className = "hint warn-hint";
      return;
    }

    if (!state.diarize) {
      dica.textContent =
        "Desligada: a transcricao sai em ordem cronologica, sob um rotulo unico, e fica um pouco mais rapida.";
      dica.className = "hint";
      return;
    }

    if (!info.downloaded) {
      dica.textContent =
        `Na primeira transcricao serao baixados ~${info.download_mb} MB de modelos de voz, ` +
        "de repositorios publicos, sem conta e sem token. Depois disso funciona offline.";
      dica.className = "hint warn-hint";
      return;
    }

    dica.textContent =
      "Modelos ja baixados - funciona sem internet. Funciona melhor com audio limpo; vozes muito " +
      "parecidas ou pessoas falando por cima uma da outra podem ser confundidas.";
    dica.className = "hint";
  }

  async function onDiarizeChange() {
    const ligado = el("local-diarize-toggle").checked;
    state.diarize = ligado;
    if (await postConfig({ local_diarize: ligado })) {
      renderDiarizeState();
      renderEstimate();
    }
  }

  async function onSpeakersChange() {
    const quantos = Number(el("local-speakers-select").value) || 0;
    state.numSpeakers = quantos;
    if (await postConfig({ local_num_speakers: quantos })) {
      renderEstimate();
    }
  }

  // ---------------------------------------------------------------- auditoria
  function renderAudit() {
    if (!state.audit) return;
    const dados = state.audit[state.mode];
    const corpo = el("audit-body");
    const partes = [];

    partes.push(
      `<div class="audit-verdict ${dados.leaves_machine ? "net" : "safe"}">${
        dados.leaves_machine ? "Neste modo, seu conteudo SAI do computador" : "Neste modo, seu conteudo NAO sai do computador"
      }</div>`
    );

    if (dados.sends.length) {
      partes.push('<div class="audit-title">Sai da maquina</div><ul class="audit-list net">');
      dados.sends.forEach((item) => partes.push(`<li>${item}</li>`));
      partes.push("</ul>");
    }

    partes.push('<div class="audit-title">Fica so aqui</div><ul class="audit-list safe">');
    dados.stays.forEach((item) => partes.push(`<li>${item}</li>`));
    partes.push("</ul>");

    if (dados.destinations && dados.destinations.length) {
      partes.push(
        `<div class="audit-title">Destino das conexoes</div><div class="audit-dest">${dados.destinations.join("<br>")}</div>`
      );
    }

    if (dados.one_time_download) {
      partes.push(`<div class="audit-note">${dados.one_time_download}</div>`);
    }

    corpo.innerHTML = partes.join("");
  }

  // ---------------------------------------------------------------- arquivos
  function isSupported(file) {
    const name = file.name || "";
    const dot = name.lastIndexOf(".");
    if (dot < 0) return false;
    return state.supportedExts.includes(name.slice(dot).toLowerCase());
  }

  function probeDuration(file) {
    return new Promise((resolve) => {
      let url;
      try {
        url = URL.createObjectURL(file);
      } catch (err) {
        resolve(null);
        return;
      }
      const media = document.createElement("video");
      media.preload = "metadata";
      const encerrar = (valor) => {
        URL.revokeObjectURL(url);
        media.removeAttribute("src");
        resolve(valor);
      };
      media.onloadedmetadata = () => {
        encerrar(Number.isFinite(media.duration) && media.duration > 0 ? media.duration : null);
      };
      media.onerror = () => encerrar(null);
      // Alguns formatos nunca disparam evento algum: nao espera para sempre.
      setTimeout(() => encerrar(null), 8000);
      media.src = url;
    });
  }

  async function addFiles(fileList) {
    const incoming = Array.from(fileList);
    const aceitos = incoming.filter(isSupported);
    const recusados = incoming.filter((file) => !isSupported(file));

    aceitos.forEach((file) => state.entries.push({ file, duration: null }));
    renderFileList();

    if (recusados.length) {
      const nomes = recusados.map((file) => file.name).join(", ");
      alert(
        `Estes arquivos nao sao audio nem video e foram ignorados:\n\n${nomes}\n\n` +
          `Formatos aceitos: ${state.supportedExts.join(", ")}`
      );
    }

    // Le a duracao de cada arquivo em segundo plano para estimar o tempo.
    for (const entrada of state.entries) {
      if (entrada.duration === null && !entrada.probed) {
        entrada.probed = true;
        entrada.duration = await probeDuration(entrada.file);
        renderFileList();
      }
    }
  }

  function renderFileList() {
    const list = el("file-list");
    list.innerHTML = "";
    state.entries.forEach((entrada, index) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      const tamanho = `${(entrada.file.size / 1_048_576).toFixed(1)} MB`;
      const duracao = entrada.duration ? ` - ${fmtDuration(entrada.duration)}` : "";
      label.textContent = `${entrada.file.name} (${tamanho}${duracao})`;
      const remove = document.createElement("span");
      remove.textContent = "x";
      remove.className = "remove";
      remove.title = "Tirar este arquivo da lista";
      remove.addEventListener("click", () => {
        state.entries.splice(index, 1);
        renderFileList();
      });
      li.appendChild(label);
      li.appendChild(remove);
      list.appendChild(li);
    });

    const resumo = el("file-summary");
    if (!state.entries.length) {
      resumo.textContent = "Nenhum arquivo escolhido ainda.";
    } else {
      const totalMb = state.entries.reduce((soma, item) => soma + item.file.size, 0) / 1_048_576;
      const plural = state.entries.length === 1 ? "arquivo" : "arquivos";
      resumo.textContent = `${state.entries.length} ${plural} na fila (${totalMb.toFixed(1)} MB no total).`;
    }
    renderEstimate();
    updateReadiness();
  }

  // ---------------------------------------------------------------- estimativa
  function renderEstimate() {
    const caixa = el("estimate-box");
    if (!state.entries.length) {
      caixa.classList.add("hidden");
      return;
    }

    const comDuracao = state.entries.filter((item) => item.duration);
    const semDuracao = state.entries.length - comDuracao.length;
    const audioTotal = comDuracao.reduce((soma, item) => soma + item.duration, 0);

    const partes = [];

    if (audioTotal > 0) {
      partes.push(`<div class="est-line"><span>Audio na fila</span><strong>${fmtDuration(audioTotal)}</strong></div>`);
    }

    if (state.mode === "local" && state.hardware) {
      const nome = currentLocalModel();
      const dados = state.hardware.models[nome] || {};
      const fator = dados.speed_factor || 1;
      if (audioTotal > 0) {
        // Mesma formula de hardware.estimate_seconds - mexeu la, mexa aqui.
        const info = diarizationInfo();
        const separando = state.diarize && Boolean(info && info.available);
        let transcricao = audioTotal / fator;
        let preparo = audioTotal / 25;
        if (separando) {
          transcricao *= info.word_timestamps_overhead || 1.15;
          preparo += audioTotal / (info.speed_factor || 5.5);
        }
        const estimado = transcricao + preparo + 3 * state.entries.length;

        partes.push(
          `<div class="est-line destaque"><span>Tempo estimado</span><strong>~ ${fmtDuration(estimado)}</strong></div>`
        );
        partes.push(
          `<div class="est-note">Estimativa aproximada para ${dados.label || nome} em ${
            state.hardware.device === "cuda" ? "GPU" : "CPU"
          }${separando ? ", ja incluindo a separacao de falantes" : ""}. O tempo real varia com o que mais
           estiver rodando na maquina.</div>`
        );
        if (!dados.downloaded) {
          partes.push(
            `<div class="est-note warn">Some ainda o download unico do modelo de transcricao (~${dados.download_mb} MB) na primeira vez.</div>`
          );
        }
        if (separando && !info.downloaded) {
          partes.push(
            `<div class="est-note warn">E o download unico dos modelos de voz (~${info.download_mb} MB).</div>`
          );
        }
      } else {
        partes.push(
          '<div class="est-note">Nao foi possivel ler a duracao dos arquivos para estimar o tempo. A transcricao local costuma levar mais tempo que a nuvem.</div>'
        );
      }
    } else {
      partes.push(
        '<div class="est-note">No modo nuvem o tempo depende principalmente da sua conexao: o arquivo inteiro e enviado ao Google antes de ser transcrito. Como referencia, 1 hora de audio costuma levar alguns minutos apos o envio.</div>'
      );
    }

    if (semDuracao > 0) {
      partes.push(
        `<div class="est-note">${semDuracao} arquivo(s) sem duracao legivel pelo navegador nao entraram na conta.</div>`
      );
    }

    caixa.innerHTML = partes.join("");
    caixa.classList.remove("hidden");
  }

  // ------------------------------------------------------ estado do botao
  function updateReadiness() {
    const dica = el("start-hint");
    const faltando = [];
    if (state.mode === "cloud" && !state.hasApiKey) {
      faltando.push("salvar sua chave do Gemini");
    }
    if (!state.entries.length) faltando.push("adicionar pelo menos um arquivo (passo 4)");

    if (faltando.length) {
      dica.textContent = `Antes de comecar, falta: ${faltando.join(" e ")}.`;
      dica.className = "hint warn-hint";
    } else if (state.mode === "local") {
      dica.textContent = "Tudo pronto. A transcricao vai rodar inteiramente nesta maquina.";
      dica.className = "hint ok-hint";
    } else {
      dica.textContent = "Tudo pronto. Ao clicar, os arquivos serao enviados ao Google Gemini.";
      dica.className = "hint ok-hint";
    }
  }

  function setStatus(msg) {
    el("status-bar").textContent = msg;
  }

  function appendLogEntries(entries) {
    const box = el("log-box");
    if (!state.logCleared) {
      box.innerHTML = "";
      state.logCleared = true;
    }
    entries.forEach((entry) => {
      const line = document.createElement("div");
      line.className = entry.tag || "";
      line.textContent = entry.msg;
      box.appendChild(line);
    });
    box.scrollTop = box.scrollHeight;
  }

  // ---------------------------------------------------------------- job
  async function startJob() {
    if (!state.entries.length) {
      alert("Adicione pelo menos um arquivo de audio ou video (passo 4).");
      return;
    }
    if (state.mode === "cloud" && !state.hasApiKey) {
      alert(
        "Voce ainda nao salvou uma chave do Gemini.\n\n" +
          "Gere uma gratuitamente em https://aistudio.google.com/apikey, " +
          "cole no campo API key e clique em Salvar configuracao.\n\n" +
          "Ou troque para o modo local, que nao precisa de chave nem de internet."
      );
      return;
    }

    const formData = new FormData();
    state.entries.forEach((entrada) => formData.append("files", entrada.file));
    formData.append("lang", el("lang-select").value);
    formData.append("title", el("title-input").value.trim());
    formData.append("context_prompt", el("context-input").value);

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
    setStatus(
      state.mode === "local"
        ? "Transcricao local iniciada. Acompanhe pelo Log ao lado."
        : "Transcricao iniciada. Acompanhe pelo Log ao lado."
    );
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
      setStatus("Concluido! Baixe os relatorios no passo 5.");
      renderResults(job);
      loadHardware(); // atualiza o status de "ja baixado" dos modelos
    } else if (job.status === "error") {
      clearInterval(state.polling);
      resetStartButton();
      setStatus(`Erro: ${job.error}`);
      alert(`Erro na transcricao:\n\n${job.error}`);
    }
  }

  function renderResults(job) {
    const container = el("results");
    container.innerHTML = "";

    const titulo = document.createElement("strong");
    titulo.textContent = "Relatorios prontos - clique para baixar:";
    container.appendChild(titulo);

    job.files.forEach((filename) => {
      const link = document.createElement("a");
      link.href = `/api/jobs/${state.jobId}/download/${encodeURIComponent(filename)}`;
      link.textContent = filename;
      link.setAttribute("download", filename);
      container.appendChild(link);
    });

    if (job.files.length > 1) {
      const todos = document.createElement("a");
      todos.href = `/api/jobs/${state.jobId}/download-all`;
      todos.textContent = `Baixar tudo (.zip) - ${job.files.length} relatorios`;
      todos.setAttribute("download", "");
      container.appendChild(todos);
    }

    const nota = document.createElement("div");
    nota.className = "hint";
    nota.textContent = job.session_dir
      ? `Estes arquivos tambem foram salvos em ${job.session_dir}`
      : "Estes arquivos tambem foram salvos na pasta de destino configurada.";
    container.appendChild(nota);
  }

  // ---------------------------------------------------------------- eventos
  el("help-btn").addEventListener("click", () => openGuide());
  el("method-btn").addEventListener("click", () => openGuide(".guide-steps.method"));
  el("guide-close").addEventListener("click", closeGuide);
  el("guide-start").addEventListener("click", closeGuide);
  el("guide-overlay").addEventListener("click", (event) => {
    if (event.target === el("guide-overlay")) closeGuide();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeGuide();
  });

  el("output-dir").addEventListener("input", (event) => {
    // Valida enquanto digita, mas so depois que a pessoa para de teclar.
    clearTimeout(state.outputDirTimer);
    const valor = event.target.value;
    state.outputDirTimer = setTimeout(() => checkOutputDir(valor), 400);
  });
  el("save-output-dir-btn").addEventListener("click", () => saveOutputDir(el("output-dir").value));
  el("reset-output-dir-btn").addEventListener("click", () => saveOutputDir(""));

  el("mode-local").addEventListener("click", () => setMode("local"));
  el("mode-cloud").addEventListener("click", () => setMode("cloud"));
  el("local-model-select").addEventListener("change", onModelChange);
  el("local-diarize-toggle").addEventListener("change", onDiarizeChange);
  el("local-speakers-select").addEventListener("change", onSpeakersChange);

  el("add-files-btn").addEventListener("click", () => el("file-input").click());
  el("file-input").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  });

  const dropzone = el("dropzone");
  ["dragenter", "dragover"].forEach((evento) => {
    dropzone.addEventListener(evento, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((evento) => {
    dropzone.addEventListener(evento, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer && event.dataTransfer.files.length) {
      addFiles(event.dataTransfer.files);
    }
  });

  el("save-config-btn").addEventListener("click", saveConfig);
  el("start-btn").addEventListener("click", startJob);
  el("clear-log-btn").addEventListener("click", () => {
    el("log-box").innerHTML = "";
    state.logCleared = true;
  });

  renderFileList();
  loadMeta()
    .then(() => Promise.all([loadHardware(), loadAudit()]))
    .then(loadConfig)
    .then(maybeShowGuideOnFirstVisit);
})();
