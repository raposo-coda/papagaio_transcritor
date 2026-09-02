#!/usr/bin/env bash
# =============================================================
#  Papagaio Transcritor - instalador de 1 clique (Linux / macOS)
#
#  Instala o Docker (se faltar), monta a aplicacao e abre o
#  navegador. Uso:  bash instalar-linux-mac.sh
# =============================================================
set -u

cd "$(dirname "$0")" || exit 1

VERDE='\033[0;32m'; AMAR='\033[0;33m'; VERM='\033[0;31m'; NEG='\033[1m'; FIM='\033[0m'
info()  { printf "${NEG}%s${FIM}\n" "$*"; }
ok()    { printf "${VERDE}  %s${FIM}\n" "$*"; }
aviso() { printf "${AMAR}  %s${FIM}\n" "$*"; }
erro()  { printf "${VERM}  %s${FIM}\n" "$*"; }

echo
info "============================================"
info "  PAPAGAIO TRANSCRITOR - INSTALACAO"
info "============================================"
echo

SISTEMA="$(uname -s)"

abrir_navegador() {
  if [ "$SISTEMA" = "Darwin" ]; then
    open "$1" >/dev/null 2>&1
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$1" >/dev/null 2>&1
  else
    aviso "Abra manualmente no navegador: $1"
  fi
}

# ---------- 1. Docker instalado? ----------
info "[1/4] Procurando o Docker..."
if command -v docker >/dev/null 2>&1; then
  ok "Docker encontrado: OK"
else
  aviso "Docker nao encontrado. Instalando..."
  if [ "$SISTEMA" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then
      brew install --cask docker || {
        erro "A instalacao pelo Homebrew falhou."
        erro "Baixe o Docker Desktop em https://www.docker.com/products/docker-desktop/"
        abrir_navegador "https://www.docker.com/products/docker-desktop/"
        exit 1
      }
      ok "Docker Desktop instalado."
      aviso "Abra o Docker Desktop (pasta Aplicativos), aceite os termos"
      aviso "e rode este instalador de novo."
      open -a Docker >/dev/null 2>&1
      exit 0
    else
      erro "Homebrew nao encontrado."
      erro "Baixe o Docker Desktop em https://www.docker.com/products/docker-desktop/"
      abrir_navegador "https://www.docker.com/products/docker-desktop/"
      exit 1
    fi
  else
    # Linux: script oficial de conveniencia da Docker
    aviso "Sera pedida sua senha para instalar o Docker (sudo)."
    # Diretorio privado: /tmp/get-docker.sh e um caminho previsivel, que em maquina
    # multiusuario permite a outra pessoa plantar ou trocar o arquivo antes do sudo.
    tmpdir="$(mktemp -d)" || { erro "Nao foi possivel criar diretorio temporario."; exit 1; }
    chmod 700 "$tmpdir"
    script_docker="$tmpdir/get-docker.sh"
    trap 'rm -rf "$tmpdir"' EXIT

    if command -v curl >/dev/null 2>&1; then
      curl -fsSL https://get.docker.com -o "$script_docker"
    elif command -v wget >/dev/null 2>&1; then
      wget -qO "$script_docker" https://get.docker.com
    else
      erro "Preciso de 'curl' ou 'wget' instalado para baixar o Docker."
      exit 1
    fi

    [ -s "$script_docker" ] || { erro "O download do instalador do Docker falhou."; exit 1; }

    # Este script roda como root. A Docker nao publica hash fixo dele (muda a cada
    # release), entao o minimo honesto e mostrar o que sera executado e perguntar.
    if command -v sha256sum >/dev/null 2>&1; then
      hash_script="$(sha256sum "$script_docker" | cut -d' ' -f1)"
    elif command -v shasum >/dev/null 2>&1; then
      hash_script="$(shasum -a 256 "$script_docker" | cut -d' ' -f1)"
    else
      hash_script="(sem ferramenta de hash disponivel)"
    fi
    aviso "Baixado o instalador oficial de https://get.docker.com"
    aviso "SHA256: $hash_script"
    aviso "Ele sera executado como root. Para conferir antes: less $script_docker"
    printf '  Continuar com a instalacao do Docker? [s/N] '
    read -r resposta_docker </dev/tty
    case "$resposta_docker" in
      s|S|y|Y) ;;
      *) erro "Instalacao cancelada. Instale o Docker manualmente: https://docs.docker.com/engine/install/"; exit 1 ;;
    esac

    sudo sh "$script_docker" || { erro "A instalacao do Docker falhou."; exit 1; }
    sudo systemctl enable --now docker >/dev/null 2>&1
    sudo usermod -aG docker "$USER" >/dev/null 2>&1
    ok "Docker instalado."
    aviso "Seu usuario foi adicionado ao grupo 'docker'."
    aviso "SAIA e ENTRE de novo na sessao (ou reinicie) e rode este instalador"
    aviso "outra vez para terminar."
    exit 0
  fi
fi
echo

# ---------- 2. Motor do Docker ligado? ----------
info "[2/4] Verificando se o Docker esta ligado..."
DOCKER="docker"
if ! $DOCKER info >/dev/null 2>&1; then
  if [ "$SISTEMA" = "Darwin" ]; then
    aviso "Abrindo o Docker Desktop, aguarde..."
    open -a Docker >/dev/null 2>&1
  else
    sudo systemctl start docker >/dev/null 2>&1
    # se o usuario ainda nao esta no grupo docker nesta sessao, usa sudo
    if ! $DOCKER info >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
      aviso "Usando 'sudo' para falar com o Docker nesta sessao."
      DOCKER="sudo docker"
    fi
  fi

  tentativa=0
  while ! $DOCKER info >/dev/null 2>&1; do
    tentativa=$((tentativa + 1))
    if [ "$tentativa" -gt 60 ]; then
      erro "O Docker nao terminou de iniciar em 5 minutos."
      erro "Ligue o Docker na mao e rode este instalador de novo."
      exit 1
    fi
    printf "  Aguardando o Docker iniciar... (%s/60)\r" "$tentativa"
    sleep 5
  done
  echo
fi
ok "Docker ligado: OK"
echo

# ---------- 3. Montar e subir ----------
info "[3/4] Montando o Papagaio Transcritor..."
aviso "Na primeira vez isso demora bastante: sao baixados Python, ffmpeg e o motor de transcricao local."
echo
if ! $DOCKER compose up -d --build; then
  erro "Falha ao montar a aplicacao. Copie o erro acima ao pedir ajuda."
  exit 1
fi
echo
ok "Aplicacao no ar: OK"
echo

# ---------- 4. Esperar responder e abrir ----------
info "[4/4] Abrindo o navegador..."
tentativa=0
while [ "$tentativa" -lt 40 ]; do
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 3 http://localhost:8000/api/meta >/dev/null 2>&1 && break
  else
    break
  fi
  tentativa=$((tentativa + 1))
  sleep 3
done

abrir_navegador "http://localhost:8000"

echo
info "============================================"
info "  PRONTO! O Papagaio esta rodando."
info "============================================"
echo
echo "  Endereco: http://localhost:8000"
echo
echo "  PROXIMO PASSO: escolha o modo no passo 1 da tela."
echo
echo "  - MODO LOCAL: transcreve dentro do seu computador. Nada sai da"
echo "    maquina. Nao precisa de conta nem de chave. Ja separa os"
echo "    falantes pela voz, tambem offline. E mais lento que a nuvem e"
echo "    nao escreve um resumo interpretativo."
echo
echo "  - MODO NUVEM: envia os arquivos ao Google Gemini. Mais rapido,"
echo "    chama os falantes pelos nomes ditos na conversa e escreve um"
echo "    resumo. Precisa de uma chave gratuita: pegue em"
echo "    https://aistudio.google.com/apikey e cole no campo \"API key\""
echo "    dentro do aplicativo."
echo
echo "  Os relatorios ficam salvos na pasta \"output\" aqui do lado."
echo
echo "  Para ligar de novo depois:  docker compose up -d"
echo "  Para desligar:              docker compose down"
echo
