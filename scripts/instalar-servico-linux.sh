#!/usr/bin/env bash
#
# instalar-servico-linux.sh - deixa esta maquina hospedando o Papagaio
# Transcritor para a rede local, sob systemd, com atualizacao automatica.
#
#   sudo ./scripts/instalar-servico-linux.sh
#
# E idempotente: rodar de novo conserta o que estiver faltando e nao duplica
# nada. Cada passo diz o que vai fazer antes de fazer.
#
# O que ele mexe no sistema:
#   1. /etc/fstab            monta o disco de dados em /mnt/container-data
#   2. pacman                instala nvidia-container-toolkit (opcional)
#   3. /etc/docker/daemon.json  registra o runtime da NVIDIA (com backup)
#   4. systemctl enable docker.service
#   5. /mnt/container-data/homelab/papagaio/  clone + dados + .env
#   6. /etc/default/papagaio, /usr/local/lib/papagaio/, unidades systemd

set -Eeuo pipefail

# ---------------------------------------------------------------- parametros

DISCO_UUID="${DISCO_UUID:-6DF14CDF478D9DBD}"
PONTO="${PONTO:-/mnt/container-data}"
BASE="${BASE:-$PONTO/homelab/papagaio}"
REPO="$BASE/repo"
REMOTO="${REMOTO:-https://github.com/raposo-coda/papagaio_transcritor.git}"
BRANCH="${BRANCH:-main}"
USAR_GPU="${USAR_GPU:-1}"
DONO_UID=1000
DONO_GID=1000

ORIGEM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

verde()    { printf '\033[32m%s\033[0m\n' "$*"; }
amarelo()  { printf '\033[33m%s\033[0m\n' "$*"; }
vermelho() { printf '\033[31m%s\033[0m\n' "$*" >&2; }
passo()    { printf '\n\033[1m== %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    vermelho "rode com sudo: sudo $0"
    exit 1
fi

# ---------------------------------------------------------------- 1. o disco

passo "1/6  Disco de dados em $PONTO"

if ! blkid -U "$DISCO_UUID" >/dev/null 2>&1; then
    vermelho "nao achei nenhuma particao com UUID=$DISCO_UUID."
    vermelho "confira com: lsblk -f"
    exit 1
fi
DISPOSITIVO="$(blkid -U "$DISCO_UUID")"
verde "particao encontrada: $DISPOSITIVO"

if grep -q "$PONTO" /etc/fstab; then
    verde "/etc/fstab ja tem uma entrada para $PONTO"
else
    cp -a /etc/fstab "/etc/fstab.bak-papagaio-$(date +%Y%m%d%H%M%S)"
    cat >> /etc/fstab <<EOF

# Disco de dados dos containers (label: container-data).
# nofail: sem ele, o disco ausente derruba o boot num shell de emergencia.
# As opcoes de dono/acl replicam o que o udisks ja usava, para as permissoes
# dos arquivos existentes continuarem valendo.
UUID=$DISCO_UUID $PONTO ntfs3 rw,nosuid,nodev,uid=$DONO_UID,gid=$DONO_GID,acl,iocharset=utf8,prealloc,nofail,x-systemd.device-timeout=10s 0 0
EOF
    verde "entrada acrescentada ao /etc/fstab (backup guardado ao lado)"
fi

# A montagem do udisks (/run/media/...) e a nova nao podem coexistir sobre a
# mesma particao: o Compose seguiria escrevendo pelo caminho antigo.
while read -r antigo; do
    [ -n "$antigo" ] || continue
    [ "$antigo" = "$PONTO" ] && continue
    amarelo "desmontando a montagem antiga em $antigo"
    if ! umount "$antigo" 2>/dev/null; then
        vermelho "nao consegui desmontar $antigo -- algo esta usando o disco:"
        fuser -vm "$antigo" 2>&1 | head -20 >&2 || true
        vermelho "feche o que estiver usando (gerenciador de arquivos, containers) e rode de novo."
        exit 1
    fi
done < <(findmnt -rn -S "$DISPOSITIVO" -o TARGET || true)

mkdir -p "$PONTO"
systemctl daemon-reload
if ! mountpoint -q "$PONTO"; then
    mount "$PONTO"
fi
mountpoint -q "$PONTO" || { vermelho "falhou ao montar $PONTO"; exit 1; }
touch "$PONTO/.container-data-ok"
verde "$PONTO montado ($DISPOSITIVO)"

# ---------------------------------------------------------------- 2. GPU

passo "2/6  GPU NVIDIA"

if [ "$USAR_GPU" != "1" ]; then
    amarelo "pulado (USAR_GPU=0)"
elif ! command -v nvidia-smi >/dev/null 2>&1; then
    amarelo "nvidia-smi nao encontrado; o aplicativo vai usar CPU"
else
    if ! pacman -Q nvidia-container-toolkit >/dev/null 2>&1; then
        verde "instalando nvidia-container-toolkit"
        pacman -S --needed --noconfirm nvidia-container-toolkit
    else
        verde "nvidia-container-toolkit ja instalado"
    fi

    # O daemon.json desta maquina tem "bip" e "dns" nao-padrao, e o
    # systemd-resolved depende deles (DNSStubListener em 172.17.0.1). O
    # nvidia-ctk faz merge, mas conferir e barato e a falha seria confusa:
    # containers sem DNS, com o build morrendo no pip install.
    if ! grep -q '"nvidia"' /etc/docker/daemon.json 2>/dev/null; then
        BACKUP="/etc/docker/daemon.json.bak-papagaio-$(date +%Y%m%d%H%M%S)"
        cp -a /etc/docker/daemon.json "$BACKUP"
        nvidia-ctk runtime configure --runtime=docker
        if ! python3 -c "
import json, sys
antes = json.load(open('$BACKUP'))
depois = json.load(open('/etc/docker/daemon.json'))
faltando = [k for k in antes if k not in depois or depois[k] != antes[k]]
sys.exit(1 if faltando else 0)
"; then
            vermelho "o nvidia-ctk alterou chaves que ja existiam no daemon.json."
            vermelho "backup em $BACKUP -- compare os dois antes de seguir."
            exit 1
        fi
        verde "runtime da NVIDIA registrado (backup em $BACKUP)"
        systemctl restart docker
    else
        verde "runtime da NVIDIA ja registrado no daemon.json"
    fi
fi

# ---------------------------------------------------------------- 3. docker no boot

passo "3/6  Docker no boot"

if [ "$(systemctl is-enabled docker.service 2>/dev/null)" = "enabled" ]; then
    verde "docker.service ja sobe no boot"
else
    # Ate agora o docker era ativado sob demanda pelo socket. Ao habilita-lo,
    # TODO container parado com restart: always/unless-stopped volta a subir no
    # proximo boot -- inclusive os do umbrel e do homelab, que podem disputar
    # porta e memoria com o Papagaio.
    orfaos=$(docker ps -a --filter status=exited --format '{{.Names}}\t{{.Image}}' || true)
    if [ -n "$orfaos" ]; then
        amarelo "containers parados que podem voltar a subir no proximo boot:"
        printf '%s\n' "$orfaos" | sed 's/^/    /'
        amarelo "se algum deles nao deveria voltar, remova com: docker rm <nome>"
        read -r -p "seguir e habilitar o docker no boot? [s/N] " resposta
        case "$resposta" in [sS]*) ;; *) vermelho "abortado"; exit 1 ;; esac
    fi
    systemctl enable docker.service
    verde "docker.service habilitado"
fi

# ---------------------------------------------------------------- 4. dados e segredos

passo "4/6  Dados e segredos em $BASE"

install -d -o "$DONO_UID" -g "$DONO_GID" -m 0775 "$BASE" "$BASE/data" "$BASE/output" "$BASE/models"
verde "diretorios prontos (dono $DONO_UID:$DONO_GID, que e o usuario da imagem)"

if [ -f "$BASE/.env" ]; then
    verde ".env ja existe (nao vou mexer no seu token)"
else
    IP="$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)"
    NOME="$(hostname)"
    TOKEN="$(openssl rand -hex 24)"
    cat > "$BASE/.env" <<EOF
# Segredos do Papagaio Transcritor. Fica FORA do clone de proposito: o
# 'git clean' do deploy apagaria, e o CI recusa um .env versionado.
#
# Token de acesso: sem ele, qualquer aparelho da rede abre a tela, le as
# transcricoes e gasta a sua chave do Gemini.
PAPAGAIO_TOKEN=$TOKEN

# Hostnames pelos quais ESTE servidor e acessado (nao os clientes). Sem o nome
# usado no navegador nesta lista, a tela carrega mas todo envio volta 403 -- e
# a checagem de CSRF do security.py.
# O IP vem de DHCP: prefira o nome .local (avahi), que nao muda.
PAPAGAIO_ALLOWED_ORIGINS=$NOME.local,$NOME,$IP
EOF
    chmod 600 "$BASE/.env"
    chown root:root "$BASE/.env"
    verde ".env criado com um token novo"
fi

# ---------------------------------------------------------------- 5. clone

passo "5/6  Clone de deploy (branch $BRANCH)"

if [ -d "$REPO/.git" ]; then
    git -c safe.directory="$REPO" -C "$REPO" fetch --prune origin "$BRANCH"
    git -c safe.directory="$REPO" -C "$REPO" checkout -q "$BRANCH"
    git -c safe.directory="$REPO" -C "$REPO" reset --hard "origin/$BRANCH"
    verde "clone atualizado"
else
    git clone --branch "$BRANCH" "$REMOTO" "$REPO"
    verde "clone criado"
fi
git -c safe.directory="$REPO" -C "$REPO" log -1 --format='    %h %s' | cat

# ---------------------------------------------------------------- 6. servico

passo "6/6  Unidades systemd"

cat > /etc/default/papagaio <<EOF
# Variaveis lidas pelas unidades do Papagaio Transcritor.
PAPAGAIO_BASE=$BASE
PAPAGAIO_REPO=$REPO
PAPAGAIO_BRANCH=$BRANCH
PAPAGAIO_REMOTE=$REMOTO
PAPAGAIO_GPU=$USAR_GPU
EOF
chmod 600 /etc/default/papagaio

# As unidades executam esta copia, e nao o script dentro do clone: o 'sync' roda
# 'git reset --hard' la, e um script que se sobrescreve enquanto executa passa a
# executar lixo.
install -D -m 0755 "$REPO/scripts/deploy.sh" /usr/local/lib/papagaio/deploy.sh

install -m 0644 "$REPO/deploy/papagaio.service"        /etc/systemd/system/
install -m 0644 "$REPO/deploy/papagaio-deploy.service" /etc/systemd/system/
install -m 0644 "$REPO/deploy/papagaio-deploy.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable papagaio.service papagaio-deploy.timer
verde "unidades instaladas e habilitadas"

passo "Subindo pela primeira vez (build + modelos: pode levar 10-20 minutos)"
systemctl restart papagaio.service

TOKEN_ATUAL="$(grep -E '^PAPAGAIO_TOKEN=' "$BASE/.env" | cut -d= -f2-)"
echo
verde "pronto."
echo
echo "  acesse:   http://$(hostname).local:8000/?token=$TOKEN_ATUAL"
echo "  estado:   systemctl status papagaio"
echo "  logs:     journalctl -u papagaio -f"
echo "  deploys:  journalctl -u papagaio-deploy -f"
echo "  forcar:   systemctl start papagaio-deploy"
echo
echo "  O token vira cookie na primeira visita; depois basta o endereco."
