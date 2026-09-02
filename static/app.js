(() => {
  const state = {
    entries: [], // { file, duration } - duration em segundos, ou null se o navegador nao souber ler
    langs: {},
    supportedExts: [],
    hasApiKey: false,
    mode: "local",
    hardware: null,
    audit: null,
    localModelChoice: "auto",
    localAvailable: false,
    diarize: true,
    numSpeakers: 0,
    maxSpeakers: 10,
    jobId: null,
    running: false,
    outputDir: "",
    defaultOutputDir: "",
    outputDirEditavel: false,
    outputDirTimer: null,
    polling: null,
    renderedLogCount: 0,
    logLimpo: false,
  };

  const el = (id) => document.getElementById(id);
  const GUIDE_SEEN_KEY = "papagaio_guide_seen";

  // ---------------------------------------------------------------- formatacao
  function fmtDuration(segundos) {
    if (!segundos || segundos < 60) return "menos de 1 min";
    const totalMin = Math.round(segundos / 60);
    if (totalMin < 60) return `${totalMin} min`;
    const horas = Math.floor(totalMin / 60);
    const minutos = totalMin % 60;
    return minutos ? `${horas} h ${minutos} min` : `${horas} h`;
  }

  function fmtMb(bytes) {
    return `${(bytes / 1_048_576).toFixed(1)} MB`;
  }

  // ---------------------------------------------------------------- modais
  function abrirModal(id) {
    el(id).classList.remove("hidden");
  }

  function fecharModal(id) {
    el(id).classList.add("hidden");
  }

  function marcarGuiaVisto() {
    try {
      localStorage.setItem(GUIDE_SEEN_KEY, "1");
    } catch (err) {
      /* modo privativo: apenas nao lembra */
    }
  }

  function talvezMostrarAjuda() {
    let visto = null;
    try {
      visto = localStorage.getItem(GUIDE_SEEN_KEY);
    } catch (err) {
      visto = null;
    }
    // Na primeira visita abre o modal curto, nao o guia longo: tres passos
    // resolvem o uso, e quem quiser o resto clica em "ver guia completo".
    if (!visto) abrirModal("help-overlay");
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

    const formatos = state.supportedExts.map((ext) => ext.replace(".", "")).join(" ");
    el("formats-hint").textContent = formatos;
    el("guide-formats").textContent =
      `Audio e video nestes formatos: ${formatos.split(" ").join(", ")}. Nao precisa converter ` +
      `nada antes - o aplicativo prepara o arquivo sozinho.`;
    el("file-input").setAttribute("accept", state.supportedExts.join(","));
  }

  async function loadHardware() {
    const res = await fetch("/api/hardware");
    state.hardware = await res.json();
    renderHardwareResumo();
    renderModelSelect();
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
    if (state.langs[cfg.lang]) el("lang-select").value = cfg.lang;

    state.hasApiKey = Boolean(cfg.has_api_key);
    state.mode = cfg.mode || "local";
    state.localModelChoice = cfg.local_model_choice || "auto";
    state.localAvailable = Boolean(cfg.local_available);
    state.diarize = cfg.local_diarize !== false;
    state.numSpeakers = Number(cfg.local_num_speakers) || 0;
    state.maxSpeakers = Number(cfg.max_speakers) || 10;
    state.outputDir = cfg.output_dir || "";
    state.defaultOutputDir = cfg.default_output_dir || "";
    state.outputDirEditavel = Boolean(cfg.output_dir_editable);

    renderOutputDir();
    renderApiStatus();
    renderModelSelect();
    renderSpeakers();
    applyMode();
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
        const corpo = JSON.parse(detalhe);
        if (corpo && corpo.detail) detalhe = corpo.detail;
      } catch (err) {
        /* resposta nao era JSON */
      }
      if (opcoes.silent) throw new Error(detalhe);
      alert(`Nao foi possivel salvar: ${detalhe}`);
      return null;
    }
    return res.json();
  }

  // ---------------------------------------------------------------- modo
  function applyMode() {
    const local = state.mode === "local";

    el("mode-local").classList.toggle("ativa", local);
    el("mode-cloud").classList.toggle("ativa", !local);
    el("mode-local").setAttribute("aria-selected", String(local));
    el("mode-cloud").setAttribute("aria-selected", String(!local));
    el("mode-local").classList.toggle("indisponivel", !state.localAvailable);

    document.querySelectorAll(".mode-only-local").forEach((no) => no.classList.toggle("hidden", !local));
    document.querySelectorAll(".mode-only-cloud").forEach((no) => no.classList.toggle("hidden", local));

    renderChaveLinha();
    renderAudit();
    renderEstimativa();
    updateReadiness();
  }

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
      // Nao escreve status aqui: applyMode -> updateReadiness ja diz o que
      // falta para poder iniciar, que e mais util que repetir o modo - e a
      // troca ja esta obvia nas abas e no cartao de auditoria.
      applyMode();
    }
  }

  function renderChaveLinha() {
    if (state.mode !== "cloud") return;
    const linha = el("linha-chave");
    const texto = el("chave-estado");
    if (state.hasApiKey) {
      texto.innerHTML = "Chave do Gemini <b>salva</b>";
      linha.classList.remove("falta-chave");
    } else {
      texto.innerHTML = "Chave do Gemini <b>faltando</b>";
      linha.classList.add("falta-chave");
    }
  }

  // ---------------------------------------------------------------- hardware e modelo
  function renderHardwareResumo() {
    const info = state.hardware;
    if (!info) return;
    const partes = [info.device_label];
    if (info.ram_gb) partes.push(`${info.ram_gb} GB`);
    el("hardware-resumo").textContent = partes.join(" · ");
  }

  function renderModelSelect() {
    const info = state.hardware;
    const select = el("local-model-select");
    if (!info) return;

    select.innerHTML = "";
    const recomendado = info.models[info.recommended_model] || {};

    const auto = document.createElement("option");
    auto.value = "auto";
    auto.textContent = `Automatico · ${recomendado.label || info.recommended_model} (recomendado)`;
    select.appendChild(auto);

    Object.entries(info.models).forEach(([nome, dados]) => {
      const opt = document.createElement("option");
      opt.value = nome;
      const baixado = dados.downloaded ? "ja baixado" : `baixa ~${dados.download_mb} MB`;
      opt.textContent = `${dados.label} · ${dados.speed_factor}x tempo real · ${baixado}`;
      select.appendChild(opt);
    });

    select.value = state.localModelChoice || "auto";
  }

  function currentLocalModel() {
    if (!state.hardware) return null;
    const escolha = el("local-model-select").value || "auto";
    return escolha === "auto" ? state.hardware.recommended_model : escolha;
  }

  async function onModelChange() {
    state.localModelChoice = el("local-model-select").value;
    if (await postConfig({ local_model: state.localModelChoice })) renderEstimativa();
  }

  // ------------------------------------------------------ separacao de falantes
  function diarizationInfo() {
    return (state.hardware && state.hardware.diarization) || null;
  }

  function renderSpeakers() {
    const caixa = el("speakers-buttons");
    caixa.innerHTML = "";

    const opcoes = [{ v: 0, label: "auto" }];
    for (let n = 2; n <= 4; n += 1) opcoes.push({ v: n, label: String(n) });
    opcoes.push({ v: 5, label: "5+" });

    opcoes.forEach((opcao) => {
      const botao = document.createElement("button");
      botao.type = "button";
      botao.textContent = opcao.label;
      botao.classList.toggle("selecionado", state.numSpeakers === opcao.v);
      botao.addEventListener("click", () => onSpeakersChange(opcao.v));
      caixa.appendChild(botao);
    });

    renderDiarizeState();
  }

  function renderDiarizeState() {
    const info = diarizationInfo();
    const disponivel = Boolean(info && info.available);
    const toggle = el("local-diarize-toggle");
    const aviso = el("diarize-aviso");

    toggle.disabled = !disponivel;
    toggle.setAttribute("aria-checked", String(state.diarize && disponivel));
    el("speakers-field").classList.toggle("hidden", !state.diarize || !disponivel);

    if (!disponivel) {
      aviso.textContent = "Nao instalado. Refaca a imagem: docker compose up -d --build";
      aviso.classList.remove("hidden");
    } else if (state.diarize && info && !info.downloaded) {
      aviso.textContent = `Primeira vez: baixa ~${info.download_mb} MB de modelos de voz.`;
      aviso.classList.remove("hidden");
    } else {
      aviso.classList.add("hidden");
    }
  }

  async function onDiarizeChange() {
    const info = diarizationInfo();
    if (!info || !info.available) return;
    const ligado = !state.diarize;
    state.diarize = ligado;
    if (await postConfig({ local_diarize: ligado })) {
      renderDiarizeState();
      renderEstimativa();
    }
  }

  async function onSpeakersChange(quantos) {
    state.numSpeakers = quantos;
    renderSpeakers();
    if (await postConfig({ local_num_speakers: quantos })) renderEstimativa();
  }

  // ---------------------------------------------------------------- auditoria
  function renderAudit() {
    if (!state.audit) return;
    const dados = state.audit[state.mode];
    if (!dados) return;

    const sai = Boolean(dados.leaves_machine);
    el("audit-card").classList.toggle("sai", sai);
    el("audit-glifo").textContent = sai ? "△" : "▣";
    el("audit-titulo").textContent = sai
      ? "Seu conteudo SAI deste computador"
      : "Seu conteudo NAO sai deste computador";

    // O destino vem do servidor para nao divergir do que o backend realmente faz.
    const destino = (dados.destinations && dados.destinations[0]) || "servidores do provedor";
    el("audit-linha").textContent = sai
      ? `Cada arquivo e a transcricao vao para ${destino}.`
      : "Audio, video, transcricao e contexto ficam no disco. So o download inicial dos modelos usa a internet.";
  }

  // ---------------------------------------------------------------- arquivos
  function isSupported(file) {
    const nome = file.name || "";
    const ponto = nome.lastIndexOf(".");
    if (ponto < 0) return false;
    return state.supportedExts.includes(nome.slice(ponto).toLowerCase());
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
      media.onloadedmetadata = () =>
        encerrar(Number.isFinite(media.duration) && media.duration > 0 ? media.duration : null);
      media.onerror = () => encerrar(null);
      // Alguns formatos nunca disparam evento algum: nao espera para sempre.
      setTimeout(() => encerrar(null), 8000);
      media.src = url;
    });
  }

  async function addFiles(lista) {
    const chegando = Array.from(lista);
    const aceitos = chegando.filter(isSupported);
    const recusados = chegando.filter((file) => !isSupported(file));

    aceitos.forEach((file) => state.entries.push({ file, duration: null, probed: false }));
    renderFileList();

    if (recusados.length) {
      alert(
        `Estes arquivos nao sao audio nem video e foram ignorados:\n\n` +
          `${recusados.map((f) => f.name).join(", ")}\n\n` +
          `Formatos aceitos: ${state.supportedExts.join(", ")}`
      );
    }

    for (const entrada of state.entries) {
      if (!entrada.probed) {
        entrada.probed = true;
        entrada.duration = await probeDuration(entrada.file);
        renderFileList();
      }
    }
  }

  function renderFileList() {
    const lista = el("file-list");
    lista.innerHTML = "";

    if (!state.entries.length) {
      const vazio = document.createElement("li");
      vazio.className = "vazio";
      vazio.textContent = "Nenhum arquivo na fila.";
      lista.appendChild(vazio);
    } else {
      state.entries.forEach((entrada, indice) => {
        const li = document.createElement("li");

        const nome = document.createElement("span");
        nome.className = "arquivo-nome";
        nome.textContent = entrada.file.name;
        nome.title = entrada.file.name;

        const duracao = document.createElement("span");
        duracao.className = "arquivo-duracao";
        duracao.textContent = entrada.duration ? fmtDuration(entrada.duration) : "--";

        const tamanho = document.createElement("span");
        tamanho.className = "arquivo-tamanho";
        tamanho.textContent = fmtMb(entrada.file.size);

        const remover = document.createElement("button");
        remover.type = "button";
        remover.className = "arquivo-remover";
        remover.textContent = "×";
        remover.title = "Tirar da fila";
        remover.addEventListener("click", () => {
          state.entries.splice(indice, 1);
          renderFileList();
        });

        li.append(nome, duracao, tamanho, remover);
        lista.appendChild(li);
      });
    }

    const temArquivos = state.entries.length > 0;
    el("dropzone").classList.toggle("pede-acao", !temArquivos);
    el("clear-files-btn").disabled = !temArquivos;

    renderFila();
    renderEstimativa();
    updateReadiness();
  }

  function renderFila() {
    const quantos = state.entries.length;
    if (!quantos) {
      el("fila-resumo").textContent = "vazia";
      return;
    }
    const total = state.entries.reduce((soma, item) => soma + (item.duration || 0), 0);
    const plural = quantos === 1 ? "arquivo" : "arquivos";
    el("fila-resumo").textContent = total
      ? `${quantos} ${plural} · ${fmtDuration(total)}`
      : `${quantos} ${plural}`;
  }

  // ---------------------------------------------------------------- estimativa
  function renderEstimativa() {
    const alvo = el("estimativa");
    if (!state.entries.length) {
      alvo.textContent = "--";
      return;
    }

    const audioTotal = state.entries.reduce((soma, item) => soma + (item.duration || 0), 0);
    if (!audioTotal) {
      alvo.textContent = "sem duracao legivel";
      return;
    }

    if (state.mode === "cloud") {
      alvo.textContent = "depende da conexao";
      return;
    }

    const info = state.hardware;
    if (!info) {
      alvo.textContent = "--";
      return;
    }

    // Mesma formula de hardware.estimate_seconds - mexeu la, mexa aqui.
    const nome = currentLocalModel();
    const dados = info.models[nome] || {};
    const diar = diarizationInfo();
    const separando = state.diarize && Boolean(diar && diar.available);

    let transcricao = audioTotal / (dados.speed_factor || 1);
    let preparo = audioTotal / 25;
    if (separando) {
      transcricao *= (diar && diar.word_timestamps_overhead) || 1.15;
      preparo += audioTotal / ((diar && diar.speed_factor) || 5.5);
    }
    const estimado = transcricao + preparo + 3 * state.entries.length;

    let texto = `~ ${fmtDuration(estimado)}`;
    // Downloads de primeira vez entram como sufixo curto, nao como paragrafo.
    const faltando = [];
    if (dados.downloaded === false) faltando.push(`${dados.download_mb} MB`);
    if (separando && diar && !diar.downloaded) faltando.push(`${diar.download_mb} MB`);
    if (faltando.length) texto += ` + ${faltando.join(" e ")} 1a vez`;

    alvo.textContent = texto;
  }

  // ---------------------------------------------------------------- prontidao
  function updateReadiness() {
    const faltando = [];
    if (!state.entries.length) faltando.push("adicione arquivos");
    if (state.mode === "cloud" && !state.hasApiKey) faltando.push("salve a chave do Gemini na engrenagem");

    const pronto = faltando.length === 0 && !state.running;
    el("start-btn").disabled = !pronto;

    if (state.running) return;
    if (faltando.length) {
      setStatus(`Para comecar: ${faltando.join(" e ")}.`, true);
    } else {
      setStatus(
        state.mode === "local"
          ? "Tudo pronto. A transcricao roda inteiramente nesta maquina."
          : "Tudo pronto. Ao iniciar, os arquivos serao enviados ao Google Gemini."
      );
    }
  }

  function setStatus(msg, falta = false) {
    const barra = el("status-bar");
    barra.textContent = msg;
    barra.classList.toggle("falta", falta);
  }

  function appendLogEntries(entradas) {
    const caixa = el("log-box");
    entradas.forEach((entrada) => {
      const linha = document.createElement("div");
      linha.className = entrada.tag || "";
      linha.textContent = entrada.msg;
      caixa.appendChild(linha);
    });
    caixa.scrollTop = caixa.scrollHeight;
  }

  // ---------------------------------------------------------------- execucao
  async function startJob() {
    if (!state.entries.length || state.running) return;
    if (state.mode === "cloud" && !state.hasApiKey) {
      abrirModal("config-overlay");
      return;
    }

    const formData = new FormData();
    state.entries.forEach((entrada) => formData.append("files", entrada.file));
    formData.append("lang", el("lang-select").value);
    formData.append("title", el("title-input").value.trim());
    formData.append("context_prompt", el("context-input").value);

    state.running = true;
    el("start-btn").disabled = true;
    el("start-btn").textContent = "Processando...";
    el("results").innerHTML = "";
    el("log-box").innerHTML = "";
    state.renderedLogCount = 0;

    let res;
    try {
      res = await fetch("/api/jobs", { method: "POST", body: formData });
    } catch (err) {
      encerrarExecucao();
      setStatus(`Erro de rede: ${err}`, true);
      return;
    }

    if (!res.ok) {
      let detalhe = await res.text();
      try {
        const corpo = JSON.parse(detalhe);
        if (corpo && corpo.detail) detalhe = corpo.detail;
      } catch (err) {
        /* resposta nao era JSON */
      }
      encerrarExecucao();
      setStatus(`Erro: ${detalhe}`, true);
      appendLogEntries([{ msg: detalhe, tag: "err" }]);
      return;
    }

    const dados = await res.json();
    state.jobId = dados.job_id;
    setStatus(state.mode === "local" ? "Transcrevendo nesta maquina..." : "Enviando ao Gemini...");
    state.polling = setInterval(pollJob, 1500);
  }

  function encerrarExecucao() {
    state.running = false;
    el("start-btn").textContent = "Iniciar";
    updateReadiness();
  }

  async function pollJob() {
    if (!state.jobId) return;
    const res = await fetch(`/api/jobs/${state.jobId}`);
    if (!res.ok) return;
    const job = await res.json();

    if (job.log.length > state.renderedLogCount) {
      appendLogEntries(job.log.slice(state.renderedLogCount));
      state.renderedLogCount = job.log.length;
      const ultima = job.log[job.log.length - 1];
      if (ultima) setStatus(ultima.msg.slice(0, 160));
    }

    if (job.status === "done") {
      clearInterval(state.polling);
      encerrarExecucao();
      setStatus("Concluido. Baixe os relatorios no passo 4.");
      renderResults(job);
      loadHardware(); // atualiza o "ja baixado" dos modelos
    } else if (job.status === "error") {
      clearInterval(state.polling);
      encerrarExecucao();
      setStatus(`Erro: ${job.error}`, true);
    }
  }

  function renderResults(job) {
    const caixa = el("results");
    caixa.innerHTML = "";

    if (!job.files || !job.files.length) {
      renderResultsVazio();
      return;
    }

    job.files.forEach((nome) => {
      const chip = document.createElement("a");
      chip.href = `/api/jobs/${state.jobId}/download/${encodeURIComponent(nome)}`;
      chip.textContent = nome;
      chip.setAttribute("download", nome);
      caixa.appendChild(chip);
    });

    if (job.files.length > 1) {
      const zip = document.createElement("a");
      zip.href = `/api/jobs/${state.jobId}/download-all`;
      zip.textContent = `todos.zip (${job.files.length})`;
      zip.setAttribute("download", "");
      caixa.appendChild(zip);
    }

    if (job.session_dir) el("destino-resumo").textContent = job.session_dir;
  }

  function renderResultsVazio() {
    const caixa = el("results");
    caixa.innerHTML = "";
    const nota = document.createElement("span");
    nota.className = "results-vazio";
    nota.textContent = "Os arquivos .md aparecem aqui quando a transcricao terminar.";
    caixa.appendChild(nota);
  }

  // ---------------------------------------------------------------- pasta de saida
  function renderOutputDir() {
    el("output-dir").value = state.outputDir;
    el("destino-resumo").textContent = state.outputDir || state.defaultOutputDir;
    const dica = el("output-dir-hint");

    if (!state.outputDirEditavel) {
      // No Docker a pasta e o volume ./output; deixar editavel criaria uma
      // expectativa que o container nao consegue cumprir.
      el("output-dir").disabled = true;
      dica.textContent = "Rodando no Docker: os relatorios ficam na pasta output ao lado do aplicativo.";
      dica.className = "hint";
      return;
    }
    dica.textContent = `Pasta padrao: ${state.defaultOutputDir}`;
    dica.className = "hint";
  }

  async function checkOutputDir(caminho) {
    const dica = el("output-dir-hint");
    if (!caminho.trim()) {
      dica.textContent = `Vazio = usar a pasta padrao: ${state.defaultOutputDir}`;
      dica.className = "hint";
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
      return; // sem rede para validar: o Salvar ainda dira o que houve
    }
    if (dados.ok) {
      dica.textContent = `Pasta valida: ${dados.resolved}`;
      dica.className = "hint ok-hint";
    } else {
      dica.textContent = dados.error || dados.detail || "Caminho invalido.";
      dica.className = "hint warn-hint";
    }
  }

  function renderApiStatus() {
    const status = el("api-status");
    if (state.hasApiKey) {
      status.textContent = "Chave salva.";
      status.className = "api-status ok";
    } else {
      status.textContent = "Nenhuma chave salva ainda.";
      status.className = "api-status warn";
    }
  }

  async function salvarAjustes() {
    const payload = {
      transcription_model: el("transcription-model").value.trim(),
      summary_model: el("summary-model").value.trim(),
      lang: el("lang-select").value,
    };
    const chave = el("api-key").value.trim();
    if (chave) payload.gemini_api_key = chave;
    if (state.outputDirEditavel) payload.output_dir = el("output-dir").value;

    try {
      await postConfig(payload, { silent: true });
    } catch (err) {
      el("output-dir-hint").textContent = String(err.message || err);
      el("output-dir-hint").className = "hint warn-hint";
      return;
    }

    el("api-key").value = "";
    fecharModal("config-overlay");
    setStatus("Ajustes salvos.");
    await loadConfig();
    loadAudit(); // o cartao de auditoria cita a pasta de destino
  }

  // ---------------------------------------------------------------- eventos
  el("mode-local").addEventListener("click", () => setMode("local"));
  el("mode-cloud").addEventListener("click", () => setMode("cloud"));

  el("local-model-select").addEventListener("change", onModelChange);
  el("local-diarize-toggle").addEventListener("click", onDiarizeChange);

  el("add-files-btn").addEventListener("click", (evento) => {
    evento.stopPropagation(); // a dropzone inteira ja e clicavel
    el("file-input").click();
  });
  el("dropzone").addEventListener("click", () => el("file-input").click());
  el("file-input").addEventListener("change", (evento) => {
    addFiles(evento.target.files);
    evento.target.value = "";
  });

  const dropzone = el("dropzone");
  ["dragenter", "dragover"].forEach((nome) => {
    dropzone.addEventListener(nome, (evento) => {
      evento.preventDefault();
      dropzone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((nome) => {
    dropzone.addEventListener(nome, (evento) => {
      evento.preventDefault();
      dropzone.classList.remove("dragging");
    });
  });
  dropzone.addEventListener("drop", (evento) => {
    if (evento.dataTransfer && evento.dataTransfer.files.length) addFiles(evento.dataTransfer.files);
  });

  el("clear-files-btn").addEventListener("click", () => {
    state.entries = [];
    renderFileList();
    renderResultsVazio();
  });

  el("start-btn").addEventListener("click", startJob);
  el("clear-log-btn").addEventListener("click", () => {
    el("log-box").innerHTML = "";
  });

  // --- modal de ajustes ---
  el("config-btn").addEventListener("click", () => abrirModal("config-overlay"));
  el("trocar-chave-btn").addEventListener("click", () => abrirModal("config-overlay"));
  el("config-close").addEventListener("click", () => fecharModal("config-overlay"));
  el("config-cancel").addEventListener("click", () => fecharModal("config-overlay"));
  el("config-save").addEventListener("click", salvarAjustes);
  el("config-overlay").addEventListener("click", (evento) => {
    if (evento.target === el("config-overlay")) fecharModal("config-overlay");
  });
  el("output-dir").addEventListener("input", (evento) => {
    clearTimeout(state.outputDirTimer);
    const valor = evento.target.value;
    state.outputDirTimer = setTimeout(() => checkOutputDir(valor), 400);
  });

  // --- modal de ajuda e guia completo ---
  el("help-btn").addEventListener("click", () => abrirModal("help-overlay"));
  el("help-close").addEventListener("click", () => {
    fecharModal("help-overlay");
    marcarGuiaVisto();
  });
  el("help-ok").addEventListener("click", () => {
    fecharModal("help-overlay");
    marcarGuiaVisto();
  });
  el("help-overlay").addEventListener("click", (evento) => {
    if (evento.target === el("help-overlay")) {
      fecharModal("help-overlay");
      marcarGuiaVisto();
    }
  });
  el("ver-guia-btn").addEventListener("click", () => {
    fecharModal("help-overlay");
    marcarGuiaVisto();
    abrirModal("guide-overlay");
  });
  el("guide-close").addEventListener("click", () => fecharModal("guide-overlay"));
  el("guide-start").addEventListener("click", () => fecharModal("guide-overlay"));
  el("guide-overlay").addEventListener("click", (evento) => {
    if (evento.target === el("guide-overlay")) fecharModal("guide-overlay");
  });

  document.addEventListener("keydown", (evento) => {
    if (evento.key !== "Escape") return;
    ["guide-overlay", "help-overlay", "config-overlay"].forEach((id) => {
      if (!el(id).classList.contains("hidden")) fecharModal(id);
    });
  });

  // ---------------------------------------------------------------- inicio
  renderFileList();
  renderResultsVazio();
  appendLogEntries([
    { msg: "Aguardando. Adicione arquivos e clique em Iniciar.", tag: "dim" },
  ]);
  loadMeta()
    .then(() => Promise.all([loadHardware(), loadAudit()]))
    .then(loadConfig)
    .then(talvezMostrarAjuda);
})();
