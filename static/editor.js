// Editor de transcricao: audio ao lado do texto, palavra por palavra.
//
// O texto de cada trecho e a verdade; as palavras com tempo sao derivadas dele.
// Toda vez que o texto muda, as palavras sao realinhadas contra as antigas: as
// que continuam iguais mantem o tempo, as novas ganham um tempo interpolado
// entre as vizinhas. Assim corrigir uma palavra nao estraga o clique nas outras.
(() => {
  const state = {
    sessoes: [],
    sessao: "",
    stem: "",
    dados: null, // resposta de GET /api/sessoes/{sessao}/{stem}
    trechos: [], // { speaker, start, end, text, pal: [{t, s, e}], aprox }
    sujo: false,
    salvando: false,
    autosave: null,
    editando: -1, // indice do trecho em edicao, ou -1
    ativo: -1, // trecho tocando agora
    palavraAtiva: null, // span da palavra tocando agora
    seguirPausadoAte: 0, // rolagem manual suspende o "seguir o audio" por um tempo
    amostraFim: null, // ms em que a amostra de um falante deve parar
    amostraInicio: null,
    amostraIdx: {}, // falante -> qual trecho tocar no proximo clique de amostra
    raf: null,
    vozes: [],
  };

  const AUTOSAVE_MS = 3000;
  const SALTO_S = 5;
  const AMOSTRA_MAX_MS = 8000;
  const PAUSA_SEGUIR_MS = 4000;
  // Acima disso o LCS fica caro demais; cai para alinhamento proporcional.
  const LCS_MAX_CELULAS = 4_000_000;

  const el = (id) => document.getElementById(id);
  const player = el("player");

  // ---------------------------------------------------------------- utilidades
  function fmtTempo(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const mm = String(m).padStart(2, "0");
    const ss = String(s).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function fmtDuracao(ms) {
    const min = Math.round(ms / 60000);
    return min < 1 ? `${Math.round(ms / 1000)}s` : `${min} min`;
  }

  function setStatus(msg, falta = false) {
    const barra = el("status");
    barra.textContent = msg;
    barra.classList.toggle("falta", falta);
  }

  async function api(url, opcoes = {}) {
    const resp = await fetch(url, opcoes);
    let corpo = null;
    try {
      corpo = await resp.json();
    } catch (_) {
      corpo = null;
    }
    if (!resp.ok) {
      const erro = new Error((corpo && corpo.detail) || `Erro ${resp.status}`);
      erro.status = resp.status;
      throw erro;
    }
    return corpo;
  }

  function enviarJson(url, metodo, dados) {
    return api(url, {
      method: metodo,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });
  }

  function urlArquivo(sufixo = "") {
    return `/api/sessoes/${encodeURIComponent(state.sessao)}/${encodeURIComponent(state.stem)}${sufixo}`;
  }

  function ehRotuloGenerico(nome) {
    return /^Falante( \d+)?$/.test(nome || "");
  }

  // ---------------------------------------------------------------- alinhamento
  const tokenizar = (texto) => (texto || "").split(/\s+/).filter(Boolean);
  const normalizar = (t) =>
    t
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^\p{L}\p{N}]/gu, "");

  // Distribui tempos por igual para as posicoes [de, ate) que ficaram sem par,
  // entre o fim da vizinha anterior e o inicio da proxima.
  function preencherBuracos(pal, inicio, fim) {
    let i = 0;
    while (i < pal.length) {
      if (pal[i].s != null) {
        i += 1;
        continue;
      }
      let j = i;
      while (j < pal.length && pal[j].s == null) j += 1;
      const de = i > 0 ? pal[i - 1].e : inicio;
      const ate = j < pal.length ? pal[j].s : fim;
      const passo = Math.max(0, ate - de) / (j - i);
      for (let k = i; k < j; k += 1) {
        pal[k].s = Math.round(de + passo * (k - i));
        pal[k].e = Math.round(de + passo * (k - i + 1));
      }
      i = j;
    }
    return pal;
  }

  // Sem tempo por palavra (Gemini, cache antigo): proporcional a posicao do caractere.
  function interpolarPorCaractere(tokens, inicio, fim) {
    const total = tokens.reduce((soma, t) => soma + t.length + 1, 0) || 1;
    let acumulado = 0;
    return tokens.map((t) => {
      const s = inicio + ((fim - inicio) * acumulado) / total;
      acumulado += t.length + 1;
      const e = inicio + ((fim - inicio) * acumulado) / total;
      return { t, s: Math.round(s), e: Math.round(e) };
    });
  }

  // Casa os tokens novos com as palavras antigas (que tem tempo) por LCS.
  function alinhar(tokens, antigas, inicio, fim) {
    const pal = tokens.map((t) => ({ t, s: null, e: null }));
    const n = tokens.length;
    const m = antigas.length;
    if (!n) return pal;
    if (!m) return preencherBuracos(pal, inicio, fim);

    if (n * m > LCS_MAX_CELULAS) {
      // Trecho enorme: mapeia pela posicao relativa, sem casar palavra a palavra.
      pal.forEach((p, i) => {
        const a = antigas[Math.min(m - 1, Math.floor((i * m) / n))];
        p.s = a.s;
        p.e = a.e;
      });
      return pal;
    }

    const A = tokens.map(normalizar);
    const B = antigas.map((p) => normalizar(p.t));
    // Tabela de sufixos em linhas tipadas: cabe na memoria mesmo com milhares de palavras.
    const tab = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        tab[i][j] = A[i] && A[i] === B[j] ? tab[i + 1][j + 1] + 1 : Math.max(tab[i + 1][j], tab[i][j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (A[i] && A[i] === B[j]) {
        pal[i].s = antigas[j].s;
        pal[i].e = antigas[j].e;
        i += 1;
        j += 1;
      } else if (tab[i + 1][j] >= tab[i][j + 1]) {
        i += 1;
      } else {
        j += 1;
      }
    }
    return preencherBuracos(pal, inicio, fim);
  }

  function montarTrecho(u) {
    const tokens = tokenizar(u.text);
    const palavras = (u.words || []).map(([t, s, e]) => ({ t, s, e }));
    const aprox = palavras.length === 0;
    const pal = aprox
      ? interpolarPorCaractere(tokens, u.start_ms, u.end_ms)
      : alinhar(tokens, palavras, u.start_ms, u.end_ms);
    return { speaker: u.speaker || "Falante", start: u.start_ms, end: u.end_ms, text: tokens.join(" "), pal, aprox };
  }

  // ---------------------------------------------------------------- busca no tempo
  function trechoNoTempo(ms) {
    const lista = state.trechos;
    let lo = 0;
    let hi = lista.length - 1;
    let achado = -1;
    while (lo <= hi) {
      const meio = (lo + hi) >> 1;
      if (lista[meio].start <= ms) {
        achado = meio;
        lo = meio + 1;
      } else {
        hi = meio - 1;
      }
    }
    return achado;
  }

  function palavraNoTempo(pal, ms) {
    let lo = 0;
    let hi = pal.length - 1;
    let achado = -1;
    while (lo <= hi) {
      const meio = (lo + hi) >> 1;
      if (pal[meio].s <= ms) {
        achado = meio;
        lo = meio + 1;
      } else {
        hi = meio - 1;
      }
    }
    return achado;
  }

  // ---------------------------------------------------------------- player
  function irPara(ms, tocar = true) {
    state.amostraFim = null;
    if (!state.dados || !state.dados.audio) return;
    player.currentTime = Math.max(0, ms / 1000);
    if (tocar) player.play().catch(() => {});
    sincronizar();
  }

  function sincronizar() {
    if (!state.trechos.length) return;
    const ms = player.currentTime * 1000;

    if (state.amostraFim != null && ms >= state.amostraFim) {
      state.amostraFim = null;
      player.pause();
    }

    const idx = trechoNoTempo(ms);
    if (idx !== state.ativo) {
      const anterior = linhaDoTrecho(state.ativo);
      if (anterior) anterior.classList.remove("ativo");
      state.ativo = idx;
      const linha = linhaDoTrecho(idx);
      if (linha) {
        linha.classList.add("ativo");
        rolarAte(linha);
      }
    }

    let span = null;
    if (idx >= 0 && idx !== state.editando) {
      const trecho = state.trechos[idx];
      if (ms <= trecho.end + 250) {
        const p = palavraNoTempo(trecho.pal, ms);
        const linha = linhaDoTrecho(idx);
        if (p >= 0 && linha) span = linha.querySelector(`.palavra[data-p="${p}"]`);
      }
    }
    if (span !== state.palavraAtiva) {
      if (state.palavraAtiva) state.palavraAtiva.classList.remove("falando");
      if (span) span.classList.add("falando");
      state.palavraAtiva = span;
    }
  }

  function rolarAte(linha) {
    if (!el("opt-seguir").checked) return;
    if (state.editando >= 0) return;
    if (Date.now() < state.seguirPausadoAte) return;
    linha.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // timeupdate so dispara a cada ~250ms; com o audio tocando, um laco de
  // animacao deixa o destaque da palavra acompanhar a fala.
  function laco() {
    sincronizar();
    state.raf = player.paused ? null : requestAnimationFrame(laco);
  }

  player.addEventListener("play", () => {
    if (!state.raf) state.raf = requestAnimationFrame(laco);
  });
  player.addEventListener("seeked", sincronizar);
  player.addEventListener("timeupdate", sincronizar);
  player.addEventListener("seeking", () => {
    // Arrastar a barra para fora do trecho cancela uma amostra em andamento. O
    // proprio salto da amostra tambem dispara seeking, por isso o teste de faixa.
    if (state.amostraFim == null) return;
    const ms = player.currentTime * 1000;
    if (ms < state.amostraInicio - 50 || ms > state.amostraFim) state.amostraFim = null;
  });

  el("velocidades").addEventListener("click", (ev) => {
    const botao = ev.target.closest("button[data-v]");
    if (!botao) return;
    player.playbackRate = Number(botao.dataset.v);
    el("velocidades")
      .querySelectorAll("button")
      .forEach((b) => b.classList.toggle("ativo", b === botao));
  });

  // ---------------------------------------------------------------- render do texto
  function linhaDoTrecho(i) {
    return i >= 0 ? el("trechos").querySelector(`.trecho[data-i="${i}"]`) : null;
  }

  function falantesExistentes() {
    const vistos = [];
    state.trechos.forEach((t) => {
      if (!vistos.includes(t.speaker)) vistos.push(t.speaker);
    });
    return vistos;
  }

  function montarSelectFalante(trecho) {
    const sel = document.createElement("select");
    sel.className = "trecho-falante";
    sel.title = "Quem fala neste trecho";
    falantesExistentes().forEach((nome) => {
      const opt = document.createElement("option");
      opt.value = nome;
      opt.textContent = nome;
      opt.selected = nome === trecho.speaker;
      sel.appendChild(opt);
    });
    const novo = document.createElement("option");
    novo.value = "__novo__";
    novo.textContent = "novo...";
    sel.appendChild(novo);
    return sel;
  }

  function montarTexto(trecho) {
    const caixa = document.createElement("div");
    caixa.className = "trecho-texto";
    if (!trecho.pal.length) {
      caixa.classList.add("vazio");
      caixa.textContent = "(trecho vazio - duplo clique para escrever)";
      return caixa;
    }
    trecho.pal.forEach((p, j) => {
      const span = document.createElement("span");
      span.className = "palavra";
      span.dataset.p = String(j);
      span.textContent = p.t;
      caixa.appendChild(span);
      caixa.appendChild(document.createTextNode(" "));
    });
    return caixa;
  }

  function montarLinha(i) {
    const trecho = state.trechos[i];
    const linha = document.createElement("div");
    linha.className = "trecho";
    linha.dataset.i = String(i);
    linha.tabIndex = 0;
    if (i === state.ativo) linha.classList.add("ativo");

    const cabeca = document.createElement("div");
    cabeca.className = "trecho-cabeca";

    const tempo = document.createElement("button");
    tempo.type = "button";
    tempo.className = "trecho-tempo";
    tempo.textContent = fmtTempo(trecho.start);
    tempo.title = "Ir para este trecho";
    cabeca.appendChild(tempo);

    cabeca.appendChild(montarSelectFalante(trecho));

    if (trecho.aprox) {
      const marca = document.createElement("span");
      marca.className = "trecho-aprox";
      marca.textContent = "~";
      marca.title = "Sem tempo por palavra neste trecho: o clique numa palavra e aproximado.";
      cabeca.appendChild(marca);
    }

    const lapis = document.createElement("button");
    lapis.type = "button";
    lapis.className = "trecho-editar";
    lapis.textContent = "✎";
    lapis.title = "Editar o texto (Enter)";
    cabeca.appendChild(lapis);

    linha.appendChild(cabeca);
    linha.appendChild(montarTexto(trecho));
    return linha;
  }

  function renderTrechos() {
    const lista = el("trechos");
    lista.innerHTML = "";
    state.palavraAtiva = null;
    if (!state.trechos.length) {
      const vazio = document.createElement("div");
      vazio.className = "ed-vazio";
      vazio.textContent = "Esta transcricao nao tem trechos.";
      lista.appendChild(vazio);
      return;
    }
    const frag = document.createDocumentFragment();
    state.trechos.forEach((_, i) => frag.appendChild(montarLinha(i)));
    lista.appendChild(frag);
  }

  function reRenderLinha(i) {
    const atual = linhaDoTrecho(i);
    if (!atual) return;
    if (state.palavraAtiva && atual.contains(state.palavraAtiva)) state.palavraAtiva = null;
    atual.replaceWith(montarLinha(i));
  }

  // ---------------------------------------------------------------- edicao
  function marcarSujo() {
    state.sujo = true;
    atualizarSalvo();
    clearTimeout(state.autosave);
    state.autosave = setTimeout(() => salvar(), AUTOSAVE_MS);
  }

  function atualizarSalvo(texto) {
    const alvo = el("salvo-status");
    if (texto) {
      alvo.textContent = texto;
    } else if (state.salvando) {
      alvo.textContent = "salvando...";
    } else {
      alvo.textContent = state.sujo ? "alteracoes nao salvas" : "tudo salvo";
    }
    alvo.classList.toggle("sujo", state.sujo);
    el("btn-salvar").disabled = !state.sujo || state.salvando;
  }

  function abrirEdicao(i) {
    if (i < 0 || i >= state.trechos.length) return;
    if (state.editando >= 0 && state.editando !== i) confirmarEdicao();
    if (state.editando === i) return;

    const linha = linhaDoTrecho(i);
    if (!linha) return;
    state.editando = i;
    if (el("opt-pausar").checked && !player.paused) player.pause();

    const trecho = state.trechos[i];
    const caixa = linha.querySelector(".trecho-texto");
    const area = document.createElement("textarea");
    area.className = "trecho-area entrada";
    area.value = trecho.text;
    area.spellcheck = true;
    caixa.replaceWith(area);
    linha.classList.add("editando");
    ajustarAltura(area);
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);

    area.addEventListener("input", () => ajustarAltura(area));
    area.addEventListener("blur", () => {
      // O blur tambem dispara quando a edicao foi trocada por Tab/Esc; so
      // confirma se esta mesma area ainda e a edicao corrente.
      if (state.editando === i && linha.contains(area)) confirmarEdicao();
    });
    area.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        cancelarEdicao();
        linhaDoTrecho(i)?.focus();
      } else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        confirmarEdicao();
        linhaDoTrecho(i)?.focus();
      } else if (ev.key === "Tab") {
        ev.preventDefault();
        const proximo = ev.shiftKey ? i - 1 : i + 1;
        confirmarEdicao();
        if (proximo >= 0 && proximo < state.trechos.length) {
          abrirEdicao(proximo);
          linhaDoTrecho(proximo)?.scrollIntoView({ block: "center" });
        }
      }
    });
  }

  function ajustarAltura(area) {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  function confirmarEdicao() {
    const i = state.editando;
    if (i < 0) return;
    const linha = linhaDoTrecho(i);
    const area = linha && linha.querySelector("textarea");
    state.editando = -1;
    if (!area) return;

    const trecho = state.trechos[i];
    const tokens = tokenizar(area.value);
    const novoTexto = tokens.join(" ");
    if (novoTexto !== trecho.text) {
      trecho.pal = trecho.aprox
        ? interpolarPorCaractere(tokens, trecho.start, trecho.end)
        : alinhar(tokens, trecho.pal, trecho.start, trecho.end);
      trecho.text = novoTexto;
      marcarSujo();
    }
    reRenderLinha(i);
  }

  function cancelarEdicao() {
    const i = state.editando;
    state.editando = -1;
    if (i >= 0) reRenderLinha(i);
  }

  const lista = el("trechos");

  lista.addEventListener("click", (ev) => {
    const linha = ev.target.closest(".trecho");
    if (!linha) return;
    const i = Number(linha.dataset.i);
    if (i === state.editando) return;

    const palavra = ev.target.closest(".palavra");
    if (palavra) {
      irPara(state.trechos[i].pal[Number(palavra.dataset.p)].s);
      return;
    }
    if (ev.target.closest(".trecho-tempo")) {
      irPara(state.trechos[i].start);
      return;
    }
    if (ev.target.closest(".trecho-editar")) {
      abrirEdicao(i);
      return;
    }
    // Clique na area livre da linha (fora de palavra, select e botoes) tambem leva ao trecho.
    if (!ev.target.closest("select, button, textarea")) irPara(state.trechos[i].start);
  });

  lista.addEventListener("dblclick", (ev) => {
    const linha = ev.target.closest(".trecho");
    if (!linha || !ev.target.closest(".trecho-texto")) return;
    ev.preventDefault();
    abrirEdicao(Number(linha.dataset.i));
  });

  lista.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "TEXTAREA" || ev.target.tagName === "SELECT") return;
    const linha = ev.target.closest(".trecho");
    if (!linha) return;
    const i = Number(linha.dataset.i);
    if (ev.key === "Enter") {
      ev.preventDefault();
      abrirEdicao(i);
    } else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      linhaDoTrecho(ev.key === "ArrowDown" ? i + 1 : i - 1)?.focus();
    }
  });

  lista.addEventListener("change", (ev) => {
    const sel = ev.target.closest(".trecho-falante");
    if (!sel) return;
    const i = Number(sel.closest(".trecho").dataset.i);
    let nome = sel.value;
    if (nome === "__novo__") {
      nome = (window.prompt("Nome do falante deste trecho:") || "").trim();
      if (!nome) {
        sel.value = state.trechos[i].speaker;
        return;
      }
    }
    if (nome === state.trechos[i].speaker) return;
    state.trechos[i].speaker = nome;
    marcarSujo();
    renderTudoMenosAudio();
  });

  // Rolar a lista a mao suspende o "seguir o audio" por alguns segundos, para
  // a pagina nao arrancar o usuario de onde ele estava lendo.
  ["wheel", "touchmove"].forEach((evento) =>
    lista.addEventListener(
      evento,
      () => {
        state.seguirPausadoAte = Date.now() + PAUSA_SEGUIR_MS;
      },
      { passive: true },
    ),
  );

  // ---------------------------------------------------------------- falantes
  function resumoFalantes() {
    const mapa = new Map();
    state.trechos.forEach((t, i) => {
      if (!mapa.has(t.speaker)) mapa.set(t.speaker, { nome: t.speaker, trechos: [], total: 0 });
      const item = mapa.get(t.speaker);
      item.trechos.push(i);
      item.total += Math.max(0, t.end - t.start);
    });
    mapa.forEach((item) => {
      item.trechos.sort(
        (a, b) =>
          state.trechos[b].end - state.trechos[b].start - (state.trechos[a].end - state.trechos[a].start),
      );
    });
    return [...mapa.values()];
  }

  function renderFalantes() {
    const caixa = el("falantes");
    caixa.innerHTML = "";
    const falantes = resumoFalantes();
    const reconhecidas = (state.dados && state.dados.vozes_reconhecidas) || {};
    const temAudio = Boolean(state.dados && state.dados.audio);

    el("falantes-nota").textContent = falantes.length ? `${falantes.length} voz(es)` : "";

    falantes.forEach((f) => {
      const linha = document.createElement("div");
      linha.className = "falante";

      const topo = document.createElement("div");
      topo.className = "falante-topo";

      const tocar = document.createElement("button");
      tocar.type = "button";
      tocar.className = "btn-mini falante-tocar";
      tocar.textContent = "▶ amostra";
      tocar.title = "Ouvir um trecho desta voz (clique de novo para outro trecho)";
      tocar.disabled = !temAudio;
      tocar.addEventListener("click", () => tocarAmostra(f));
      topo.appendChild(tocar);

      const nome = document.createElement("input");
      nome.className = "entrada falante-nome";
      nome.value = f.nome;
      nome.maxLength = 60;
      nome.title = "Renomeia este falante em todos os trechos";
      nome.addEventListener("change", () => renomearFalante(f.nome, nome.value));
      nome.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") nome.blur();
      });
      topo.appendChild(nome);
      linha.appendChild(topo);

      const info = document.createElement("div");
      info.className = "falante-info";
      const partes = [`${f.trechos.length} trecho(s)`, fmtDuracao(f.total)];
      info.textContent = partes.join(" · ");
      if (reconhecidas[f.nome] != null) {
        const selo = document.createElement("span");
        selo.className = "falante-reconhecido";
        selo.textContent = `voz reconhecida (${Math.round(reconhecidas[f.nome] * 100)}%)`;
        selo.title = "Nome dado automaticamente a partir de uma voz salva. Confira ouvindo a amostra.";
        info.appendChild(selo);
      }
      linha.appendChild(info);

      const acoes = document.createElement("div");
      acoes.className = "falante-acoes";

      const outros = falantes.filter((o) => o.nome !== f.nome);
      if (outros.length) {
        const juntar = document.createElement("select");
        juntar.className = "falante-juntar";
        juntar.title = "Quando a separacao partiu a mesma pessoa em dois rotulos";
        const primeiro = document.createElement("option");
        primeiro.value = "";
        primeiro.textContent = "juntar com...";
        juntar.appendChild(primeiro);
        outros.forEach((o) => {
          const opt = document.createElement("option");
          opt.value = o.nome;
          opt.textContent = o.nome;
          juntar.appendChild(opt);
        });
        juntar.addEventListener("change", () => {
          if (!juntar.value) return;
          if (window.confirm(`Todos os trechos de "${f.nome}" passam a ser de "${juntar.value}". Continuar?`)) {
            renomearFalante(f.nome, juntar.value, true);
          } else {
            juntar.value = "";
          }
        });
        acoes.appendChild(juntar);
      }

      const salvarVoz = document.createElement("button");
      salvarVoz.type = "button";
      salvarVoz.className = "btn-mini";
      salvarVoz.textContent = "salvar esta voz";
      salvarVoz.disabled = !temAudio;
      salvarVoz.title = temAudio
        ? "Guarda a voz para reconhecer esta pessoa nas proximas transcricoes"
        : "Sem audio guardado nesta transcricao";
      salvarVoz.addEventListener("click", () => salvarVozDoFalante(f.nome, salvarVoz));
      acoes.appendChild(salvarVoz);

      linha.appendChild(acoes);
      caixa.appendChild(linha);
    });
  }

  function tocarAmostra(f) {
    if (!f.trechos.length) return;
    const vez = state.amostraIdx[f.nome] || 0;
    const i = f.trechos[vez % f.trechos.length];
    state.amostraIdx[f.nome] = vez + 1;
    const trecho = state.trechos[i];
    irPara(trecho.start);
    state.amostraInicio = trecho.start;
    state.amostraFim = Math.min(trecho.end, trecho.start + AMOSTRA_MAX_MS);
    setStatus(`Amostra de ${f.nome}: trecho ${fmtTempo(trecho.start)} (${vez % f.trechos.length + 1}/${f.trechos.length}).`);
  }

  function renomearFalante(antigo, novo, confirmado = false) {
    novo = (novo || "").replace(/\s+/g, " ").trim();
    if (!novo || novo === antigo) {
      renderFalantes();
      return;
    }
    const existe = state.trechos.some((t) => t.speaker === novo);
    if (existe && !confirmado) {
      if (!window.confirm(`Ja existe "${novo}". Juntar os trechos de "${antigo}" com os dele?`)) {
        renderFalantes();
        return;
      }
    }
    state.trechos.forEach((t) => {
      if (t.speaker === antigo) t.speaker = novo;
    });
    // O selo de voz reconhecida acompanha o nome novo.
    const rec = state.dados && state.dados.vozes_reconhecidas;
    if (rec && rec[antigo] != null && !existe) {
      rec[novo] = rec[antigo];
      delete rec[antigo];
    }
    marcarSujo();
    renderTudoMenosAudio();
    setStatus(`"${antigo}" agora e "${novo}" em todos os trechos.`);
  }

  async function salvarVozDoFalante(nome, botao) {
    if (ehRotuloGenerico(nome)) {
      setStatus(`De um nome a "${nome}" antes de salvar a voz (ex.: o nome da pessoa).`, true);
      return;
    }
    botao.disabled = true;
    try {
      if (state.sujo) await salvar();
      if (state.sujo) throw new Error("Nao foi possivel salvar as edicoes antes.");
      setStatus(`Calculando a voz de ${nome}...`);
      const resp = await enviarJson("/api/vozes", "POST", {
        sessao: state.sessao,
        stem: state.stem,
        falante: nome,
        nome,
      });
      setStatus(`Voz de ${resp.nome} salva (${resp.amostras} amostra(s)). Sera reconhecida nas proximas transcricoes locais.`);
      carregarVozes();
    } catch (erro) {
      setStatus(`Voz nao salva: ${erro.message}`, true);
    } finally {
      botao.disabled = false;
    }
  }

  // ---------------------------------------------------------------- vozes salvas
  async function carregarVozes() {
    try {
      const resp = await api("/api/vozes");
      state.vozes = resp.vozes || [];
    } catch (_) {
      state.vozes = [];
    }
    renderVozes();
  }

  function renderVozes() {
    const caixa = el("vozes");
    caixa.innerHTML = "";
    el("vozes-qtd").textContent = state.vozes.length ? `(${state.vozes.length})` : "(nenhuma)";
    if (!state.vozes.length) {
      const nota = document.createElement("div");
      nota.className = "campo-nota";
      nota.textContent = "Nenhuma voz salva ainda.";
      caixa.appendChild(nota);
      return;
    }
    state.vozes.forEach((v) => {
      const linha = document.createElement("div");
      linha.className = "voz";
      const nome = document.createElement("span");
      nome.textContent = `${v.nome} `;
      const qtd = document.createElement("span");
      qtd.className = "titulo-nota";
      qtd.textContent = `${v.amostras} amostra(s)`;
      nome.appendChild(qtd);
      const apagar = document.createElement("button");
      apagar.type = "button";
      apagar.className = "btn-mini btn-apaga";
      apagar.textContent = "apagar";
      apagar.addEventListener("click", async () => {
        if (!window.confirm(`Apagar a voz de "${v.nome}"? Ela deixa de ser reconhecida.`)) return;
        try {
          await api(`/api/vozes/${encodeURIComponent(v.nome)}`, { method: "DELETE" });
          setStatus(`Voz de ${v.nome} apagada.`);
        } catch (erro) {
          setStatus(`Nao foi possivel apagar: ${erro.message}`, true);
        }
        carregarVozes();
      });
      linha.appendChild(nome);
      linha.appendChild(apagar);
      caixa.appendChild(linha);
    });
  }

  // ---------------------------------------------------------------- salvar
  function payload() {
    return {
      utterances: state.trechos.map((t) => ({
        speaker: t.speaker,
        text: t.text,
        // Trecho sem tempo real por palavra continua sem: a interpolacao e
        // refeita ao abrir, em vez de virar um "tempo exato" falso no disco.
        words: t.aprox ? [] : t.pal.map((p) => [p.t, p.s, p.e]),
      })),
    };
  }

  async function salvar() {
    if (!state.sujo || state.salvando || !state.stem) return;
    if (state.editando >= 0) confirmarEdicao();
    clearTimeout(state.autosave);
    state.salvando = true;
    state.sujo = false;
    atualizarSalvo();
    try {
      await enviarJson(urlArquivo(), "PUT", payload());
      const hora = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
      state.salvando = false;
      atualizarSalvo(state.sujo ? undefined : `salvo as ${hora}`);
    } catch (erro) {
      state.salvando = false;
      state.sujo = true;
      atualizarSalvo();
      setStatus(`Nao foi possivel salvar: ${erro.message}`, true);
    }
  }

  el("btn-salvar").addEventListener("click", () => salvar());

  window.addEventListener("beforeunload", (ev) => {
    if (state.sujo || state.editando >= 0) {
      ev.preventDefault();
      ev.returnValue = "";
    }
  });

  // ---------------------------------------------------------------- atalhos
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      ev.preventDefault();
      if (state.editando >= 0) confirmarEdicao();
      salvar();
    } else if (ev.ctrlKey && ev.code === "Space") {
      ev.preventDefault();
      if (!state.dados || !state.dados.audio) return;
      state.amostraFim = null;
      if (player.paused) player.play().catch(() => {});
      else player.pause();
    } else if (ev.altKey && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) {
      ev.preventDefault();
      const delta = ev.key === "ArrowLeft" ? -SALTO_S : SALTO_S;
      player.currentTime = Math.max(0, player.currentTime + delta);
    }
  });

  // ---------------------------------------------------------------- sessoes
  function renderTudoMenosAudio() {
    renderTrechos();
    renderFalantes();
    sincronizar();
  }

  function preencherSelect(sel, itens, valor) {
    sel.innerHTML = "";
    itens.forEach(([v, rotulo]) => {
      const opt = document.createElement("option");
      opt.value = v;
      opt.textContent = rotulo;
      opt.selected = v === valor;
      sel.appendChild(opt);
    });
  }

  function arquivosDaSessao(nome) {
    const sessao = state.sessoes.find((s) => s.nome === nome);
    return sessao ? sessao.arquivos : [];
  }

  async function carregarSessoes() {
    try {
      const resp = await api("/api/sessoes");
      state.sessoes = resp.sessoes || [];
    } catch (erro) {
      setStatus(`Nao foi possivel listar as sessoes: ${erro.message}`, true);
      return;
    }

    if (!state.sessoes.length) {
      preencherSelect(el("sel-sessao"), [["", "nenhuma sessao"]], "");
      preencherSelect(el("sel-arquivo"), [], "");
      el("trechos").innerHTML =
        '<div class="ed-vazio">Nenhuma transcricao com dados para o editor ainda. ' +
        "So as transcricoes feitas a partir desta versao aparecem aqui.</div>";
      return;
    }

    const params = new URLSearchParams(window.location.search);
    let sessao = params.get("sessao") || "";
    if (!state.sessoes.some((s) => s.nome === sessao)) sessao = state.sessoes[0].nome;
    preencherSelect(
      el("sel-sessao"),
      state.sessoes.map((s) => [s.nome, s.nome]),
      sessao,
    );
    const arquivos = arquivosDaSessao(sessao);
    let stem = params.get("arquivo") || "";
    if (!arquivos.some((a) => a.stem === stem)) stem = arquivos.length ? arquivos[0].stem : "";
    await abrirArquivo(sessao, stem);
  }

  async function trocar(sessao, stem) {
    if (state.editando >= 0) confirmarEdicao();
    if (state.sujo) await salvar();
    if (state.sujo) {
      // Salvar falhou: nao troca, senao as edicoes se perdem.
      el("sel-sessao").value = state.sessao;
      el("sel-arquivo").value = state.stem;
      return;
    }
    await abrirArquivo(sessao, stem);
  }

  async function abrirArquivo(sessao, stem) {
    state.sessao = sessao;
    state.stem = stem;
    preencherSelect(
      el("sel-arquivo"),
      arquivosDaSessao(sessao).map((a) => [a.stem, a.audio ? a.stem : `${a.stem} (sem audio)`]),
      stem,
    );
    const url = new URL(window.location.href);
    url.searchParams.set("sessao", sessao);
    url.searchParams.set("arquivo", stem);
    window.history.replaceState(null, "", url);

    player.pause();
    state.ativo = -1;
    state.editando = -1;
    state.amostraIdx = {};
    state.amostraFim = null;
    if (!stem) return;

    setStatus(`Abrindo ${stem}...`);
    try {
      state.dados = await api(urlArquivo());
    } catch (erro) {
      state.dados = null;
      state.trechos = [];
      renderTudoMenosAudio();
      setStatus(`Nao foi possivel abrir: ${erro.message}`, true);
      return;
    }

    state.trechos = (state.dados.utterances || []).map(montarTrecho);
    state.sujo = false;
    atualizarSalvo("tudo salvo");

    el("sem-audio").hidden = state.dados.audio;
    player.hidden = !state.dados.audio;
    if (state.dados.audio) {
      player.src = urlArquivo("/audio");
    } else {
      player.removeAttribute("src");
      player.load();
    }

    const btnMd = el("btn-md");
    btnMd.href = urlArquivo("/md");
    btnMd.hidden = !state.dados.md;

    el("texto-titulo").textContent = state.dados.source_name || stem;
    const aprox = state.trechos.some((t) => t.aprox);
    el("texto-nota").textContent = aprox
      ? "~ tempo por palavra aproximado"
      : `${state.trechos.length} trecho(s)`;

    renderTudoMenosAudio();
    setStatus(
      "O _consolidado.md da sessao nao e refeito ao salvar; o .md deste arquivo, sim.",
    );
  }

  el("sel-sessao").addEventListener("change", (ev) => {
    const arquivos = arquivosDaSessao(ev.target.value);
    trocar(ev.target.value, arquivos.length ? arquivos[0].stem : "");
  });
  el("sel-arquivo").addEventListener("change", (ev) => trocar(state.sessao, ev.target.value));

  carregarSessoes();
  carregarVozes();
})();
