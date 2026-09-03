#!/usr/bin/env bash
#
# deploy.sh - ciclo de vida do Papagaio Transcritor no modo servidor (Linux).
#
# Chamado pelas unidades systemd instaladas por scripts/instalar-servico-linux.sh:
#
#   papagaio.service         -> up / down
#   papagaio-deploy.service  -> sync   (disparado pelo papagaio-deploy.timer)
#
# Voce tambem pode chamar a mao: sudo /usr/local/lib/papagaio/deploy.sh status
#
# IMPORTANTE: as unidades apontam para a copia em /usr/local/lib/papagaio/, e
# nao para este arquivo dentro do clone. Motivo: o `sync` roda `git reset --hard`
# no clone; o bash le o script por deslocamento de bytes enquanto executa, entao
# um script que se sobrescreve no meio da propria execucao passa a executar
# lixo. A copia so e atualizada no fim de um sync bem-sucedido.

set -Eeuo pipefail

# ---------------------------------------------------------------- configuracao

: "${PAPAGAIO_BASE:?PAPAGAIO_BASE nao definido (veja /etc/default/papagaio)}"
: "${PAPAGAIO_REPO:?PAPAGAIO_REPO nao definido (veja /etc/default/papagaio)}"
: "${PAPAGAIO_BRANCH:=main}"
: "${PAPAGAIO_GPU:=1}"
# Espaco minimo livre no disco de dados. Abaixo disso o deploy para de proposito:
# e melhor falhar com mensagem clara do que deixar o ffmpeg escrever pela metade.
: "${PAPAGAIO_MIN_GB:=5}"
# Quanto esperar o aplicativo responder (build + carga do modelo ja excluidos).
: "${PAPAGAIO_HEALTH_TIMEOUT:=180}"

ENV_FILE="$PAPAGAIO_BASE/.env"
ESTADO="$PAPAGAIO_BASE/.estado"
MARCA_WARMUP="$PAPAGAIO_BASE/models/.warmup-ok"
COPIA_INSTALADA=/usr/local/lib/papagaio/deploy.sh
SERVICO=papagaio-transcritor

# PAPAGAIO_BASE precisa estar no ambiente: e ele que o docker-compose.deploy.yml
# interpola nos caminhos dos binds.
export PAPAGAIO_BASE

log()  { printf '[papagaio] %s\n' "$*"; }
erro() { printf '[papagaio] ERRO: %s\n' "$*" >&2; }

git_repo() { git -c safe.directory="$PAPAGAIO_REPO" -C "$PAPAGAIO_REPO" "$@"; }

# ---------------------------------------------------------------- trava
# O timer e o papagaio.service podem coincidir (um boot durante um tick
# pendente, por exemplo). Duas invocacoes do Compose no mesmo projeto se
# atrapalham, entao serializa.
exec 9>/run/papagaio-deploy.lock
if ! flock -w 900 9; then
    erro "outra execucao do deploy segue em andamento ha mais de 15 minutos"
    exit 1
fi

# ---------------------------------------------------------------- modo (gpu/cpu)

modo_salvo() { [ -f "$ESTADO" ] && cat "$ESTADO" || echo ""; }

modo_desejado() {
    if [ "$PAPAGAIO_GPU" != "1" ]; then
        echo cpu
    elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        echo gpu
    else
        echo cpu
    fi
}

# Monta a lista de -f do Compose. Mesma ideia do Arquivos-Compose do
# scripts/papagaio.ps1, que faz o equivalente no Windows.
#
# Caminhos absolutos de proposito: o Compose resolve -f relativo ao diretorio
# ATUAL, nao ao --project-directory, e sob systemd o diretorio atual e /.
arquivos_compose() {
    local modo="$1" r="$PAPAGAIO_REPO"
    printf '%s\n' -f "$r/docker-compose.yml" -f "$r/docker-compose.rede.yml" -f "$r/docker-compose.deploy.yml"
    [ "$modo" = "gpu" ] && printf '%s\n' -f "$r/docker-compose.gpu.yml"
    return 0
}

compose() {
    local modo="$1"; shift
    local -a arquivos
    mapfile -t arquivos < <(arquivos_compose "$modo")
    docker compose --project-directory "$PAPAGAIO_REPO" \
                   --env-file "$ENV_FILE" \
                   "${arquivos[@]}" "$@"
}

# ---------------------------------------------------------------- verificacoes

conferir_espaco() {
    local livre
    livre=$(df -BG --output=avail "$PAPAGAIO_BASE" | tail -1 | tr -dc '0-9')
    if [ "${livre:-0}" -lt "$PAPAGAIO_MIN_GB" ]; then
        erro "so ${livre}G livres em $PAPAGAIO_BASE (minimo $PAPAGAIO_MIN_GB G)."
        erro "libere espaco antes de continuar -- inclusive a lixeira do disco (.Trash-1000)."
        return 1
    fi
    log "espaco livre em $PAPAGAIO_BASE: ${livre}G"
}

preparar_diretorios() {
    # uid/gid 1000 = usuario 'papagaio' da imagem. Sem isso o container nao
    # escreve nos binds.
    install -d -o 1000 -g 1000 -m 0775 \
        "$PAPAGAIO_BASE/data" "$PAPAGAIO_BASE/output" "$PAPAGAIO_BASE/models"
}

esta_saudavel() {
    local modo="$1" cid estado
    cid=$(compose "$modo" ps -q "$SERVICO" 2>/dev/null) || return 1
    [ -n "$cid" ] || return 1
    estado=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null) || return 1
    [ "$estado" = "healthy" ] || [ "$estado" = "running" ]
}

esperar_saude() {
    local modo="$1" limite=$((SECONDS + PAPAGAIO_HEALTH_TIMEOUT)) cid estado
    cid=$(compose "$modo" ps -q "$SERVICO" 2>/dev/null || true)
    if [ -z "$cid" ]; then
        erro "o container nao existe depois do up"
        return 1
    fi
    while [ "$SECONDS" -lt "$limite" ]; do
        estado=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo desconhecido)
        case "$estado" in
            healthy)  log "aplicativo respondendo (healthy)"; return 0 ;;
            unhealthy|exited|dead)
                erro "container em estado '$estado'"
                docker logs --tail 40 "$cid" 2>&1 | sed 's/^/[container] /' >&2
                return 1 ;;
        esac
        sleep 5
    done
    erro "o aplicativo nao respondeu em ${PAPAGAIO_HEALTH_TIMEOUT}s (ultimo estado: $estado)"
    docker logs --tail 40 "$cid" 2>&1 | sed 's/^/[container] /' >&2
    return 1
}

# ---------------------------------------------------------------- acoes

# Sobe num modo especifico. Devolve 0 se subiu e ficou saudavel.
subir_em() {
    local modo="$1" saida
    log "subindo em modo $modo"
    if ! saida=$(compose "$modo" up -d --no-build 2>&1); then
        printf '%s\n' "$saida" | sed 's/^/[compose] /' >&2
        # Sintoma classico de NVIDIA Container Toolkit ausente ou mal
        # configurado. Nesse caso quem chamou tenta de novo em CPU.
        if [ "$modo" = "gpu" ] && printf '%s' "$saida" | grep -qiE 'device driver|nvidia|cdi|gpu'; then
            return 2
        fi
        return 1
    fi
    printf '%s\n' "$saida" | sed 's/^/[compose] /'
    esperar_saude "$modo"
}

# O warmup baixa os modelos, mas serve tambem de teste real da GPU: ele
# instancia o WhisperModel de verdade. E o unico jeito de pegar o caso em que a
# GPU aparece para o CTranslate2 mas o cuDNN nao tem kernel para a Pascal
# (sm_61) -- sem isso, a falha so apareceria na primeira transcricao do usuario,
# como erro 500.
rodar_warmup() {
    local modo="$1" saida
    if [ -f "$MARCA_WARMUP" ]; then
        log "modelos ja preparados (remova $MARCA_WARMUP para refazer)"
        return 0
    fi
    log "baixando e testando os modelos (pode demorar alguns minutos)"
    if saida=$(compose "$modo" exec -T "$SERVICO" python warmup.py 2>&1); then
        printf '%s\n' "$saida" | sed 's/^/[warmup] /'
        touch "$MARCA_WARMUP"
        return 0
    fi
    printf '%s\n' "$saida" | sed 's/^/[warmup] /' >&2
    if [ "$modo" = "gpu" ] && printf '%s' "$saida" | grep -qiE 'cuda|cudnn|cublas|libcu'; then
        erro "a GPU falhou no teste real (provavelmente cuDNN sem kernel para a GTX 1070)"
        return 2
    fi
    return 1
}

acao_up() {
    conferir_espaco
    preparar_diretorios

    local modo
    modo=$(modo_desejado)

    local rc=0
    subir_em "$modo" || rc=$?
    if [ "$rc" -eq 2 ] && [ "$modo" = "gpu" ]; then
        log "a GPU nao pode ser usada; recriando o container em CPU"
        compose gpu down --remove-orphans >/dev/null 2>&1 || true
        modo=cpu
        subir_em "$modo"
    elif [ "$rc" -ne 0 ]; then
        return "$rc"
    fi

    echo "$modo" > "$ESTADO"

    rc=0
    rodar_warmup "$modo" || rc=$?
    if [ "$rc" -eq 2 ] && [ "$modo" = "gpu" ]; then
        log "refazendo em CPU (a imagem e a mesma, so o container muda)"
        compose gpu down --remove-orphans >/dev/null 2>&1 || true
        modo=cpu
        echo "$modo" > "$ESTADO"
        subir_em "$modo"
        rodar_warmup "$modo"
    elif [ "$rc" -ne 0 ]; then
        return "$rc"
    fi

    log "no ar em modo $modo -- http://$(hostname).local:8000/?token=<seu token>"
}

acao_down() {
    # Le o modo salvo: uma lista de -f diferente da usada no up faz o Compose
    # enxergar outro conjunto de servicos.
    local modo
    modo=$(modo_salvo)
    [ -n "$modo" ] || modo=$(modo_desejado)
    log "parando (modo $modo)"
    compose "$modo" down --remove-orphans
}

acao_sync() {
    conferir_espaco
    preparar_diretorios

    local modo
    modo=$(modo_salvo)
    [ -n "$modo" ] || modo=$(modo_desejado)

    # Rede caida nao e falha de deploy: nao vale marcar a unidade como failed.
    if ! git_repo fetch --prune origin "$PAPAGAIO_BRANCH" 2>&1 | sed 's/^/[git] /'; then
        log "nao foi possivel falar com o GitHub agora; tentando no proximo ciclo"
        return 0
    fi

    local sha_local sha_remoto
    sha_local=$(git_repo rev-parse HEAD)
    sha_remoto=$(git_repo rev-parse "origin/$PAPAGAIO_BRANCH")

    if [ "$sha_local" = "$sha_remoto" ]; then
        if esta_saudavel "$modo"; then
            log "ja em ${sha_remoto:0:8} e no ar; nada a fazer"
            return 0
        fi
        log "ja em ${sha_remoto:0:8}, mas o aplicativo nao esta no ar; subindo"
        acao_up
        return 0
    fi

    # Recusa historia reescrita: um force-push (ou uma conta comprometida)
    # nao deve conseguir empurrar codigo arbitrario para dentro desta maquina
    # sem que fique evidente.
    if ! git_repo merge-base --is-ancestor "$sha_local" "$sha_remoto"; then
        erro "origin/$PAPAGAIO_BRANCH nao descende do que esta implantado (${sha_local:0:8})."
        erro "historia reescrita ou branch trocado -- resolva a mao antes de continuar."
        return 1
    fi

    log "atualizando ${sha_local:0:8} -> ${sha_remoto:0:8}"
    git_repo reset --hard "$sha_remoto" | sed 's/^/[git] /'
    git_repo clean -fd | sed 's/^/[git] /'

    # Build separado do up de proposito: enquanto ele roda (e se ele falhar), o
    # container em execucao nao foi tocado.
    if ! compose "$modo" build --progress plain 2>&1 | sed 's/^/[build] /'; then
        erro "o build falhou; voltando o clone para ${sha_local:0:8} e mantendo a versao no ar"
        git_repo reset --hard "$sha_local" | sed 's/^/[git] /'
        return 1
    fi

    if ! subir_em "$modo"; then
        erro "a versao nova nao subiu; voltando para ${sha_local:0:8}"
        git_repo reset --hard "$sha_local" | sed 's/^/[git] /'
        compose "$modo" build --progress plain 2>&1 | sed 's/^/[build] /' || true
        subir_em "$modo" || erro "a versao anterior tambem nao subiu -- intervencao manual necessaria"
        return 1
    fi

    rodar_warmup "$modo" || true

    # Cada rebuild deixa a imagem anterior orfa; sem poda o disco do sistema
    # enche sozinho.
    docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

    # Por ultimo, e so aqui: atualiza a copia que as unidades executam.
    if [ -f "$PAPAGAIO_REPO/scripts/deploy.sh" ]; then
        install -D -m 0755 "$PAPAGAIO_REPO/scripts/deploy.sh" "$COPIA_INSTALADA"
    fi

    log "implantado ${sha_remoto:0:8}"
}

acao_status() {
    local modo
    modo=$(modo_salvo)
    echo "modo      : ${modo:-nao subiu ainda}"
    echo "branch    : $PAPAGAIO_BRANCH"
    echo "commit    : $(git_repo rev-parse --short HEAD 2>/dev/null || echo '?') -- $(git_repo log -1 --format=%s 2>/dev/null || echo '?')"
    echo "dados     : $PAPAGAIO_BASE"
    echo
    compose "${modo:-cpu}" ps 2>/dev/null || true
}

case "${1:-}" in
    up)     acao_up ;;
    down)   acao_down ;;
    sync)   acao_sync ;;
    status) acao_status ;;
    *)      erro "uso: $0 {up|down|sync|status}"; exit 64 ;;
esac
