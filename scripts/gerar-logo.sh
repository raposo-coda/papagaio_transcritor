#!/bin/sh
# Gera as versoes usadas pela aplicacao a partir do logo original.
# Roda dentro do container, que ja tem ffmpeg:
#   docker run --rm -v "$PWD:/src" --entrypoint sh papagaio_transcritor-papagaio-transcritor /src/scripts/gerar-logo.sh
set -e
cd /src
SRC="ChatGPT Image 2 de set. de 2026, 07_18_54.png"
if [ ! -f "$SRC" ]; then echo "FONTE NAO ENCONTRADA"; ls; exit 1; fi
mkdir -p assets static

quadrado() {
  ffmpeg -y -loglevel error -i "$SRC" \
    -vf "scale=w=$1:h=$1:force_original_aspect_ratio=decrease,pad=$1:$1:(ow-iw)/2:(oh-ih)/2:color=#00000000" \
    -pix_fmt rgba -compression_level 100 "$2"
  echo "gerado: $2"
}

quadrado 512 assets/logo.png
quadrado 256 static/logo.png
quadrado 64  static/favicon.png
ls -la assets/logo.png static/logo.png static/favicon.png
