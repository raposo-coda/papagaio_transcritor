FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Suporte opcional a GPU NVIDIA no modo local.
# Ative com: docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
ARG WITH_CUDA=0
RUN if [ "$WITH_CUDA" = "1" ]; then \
        pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12; \
    fi
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

COPY . .

ENV PAPAGAIO_DATA_DIR=/app/data \
    PAPAGAIO_OUTPUT_DIR=/app/output \
    PAPAGAIO_MODELS_DIR=/app/models \
    PAPAGAIO_IN_DOCKER=1 \
    PYTHONUNBUFFERED=1

# O huggingface_hub (usado pelo faster-whisper para baixar o modelo) escreve
# caches auxiliares em $HOME/.cache mesmo quando recebe um download_root
# explicito. Sem um HOME que exista e seja gravavel, o download morre com
# "Permission denied (os error 13)". Apontar os caches para dentro do volume de
# modelos resolve e ainda faz eles sobreviverem a recriacao do container.
ENV HOME=/home/papagaio \
    HF_HOME=/app/models/hf \
    XDG_CACHE_HOME=/app/models/cache

# A aplicacao nao precisa de root. UID/GID 1000 e o primeiro usuario comum em
# Linux, o que mantem os arquivos dos volumes com o dono certo no host.
RUN groupadd --gid 1000 papagaio \
    && useradd --uid 1000 --gid 1000 --home-dir /home/papagaio --create-home --shell /usr/sbin/nologin papagaio \
    && mkdir -p /app/data /app/output /app/models \
    && chown -R papagaio:papagaio /app /home/papagaio
USER papagaio

EXPOSE 8000

# 0.0.0.0 aqui e a interface *do container*. Quem controla a exposicao real e o
# docker-compose.yml, que publica a porta so em 127.0.0.1 do host.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
