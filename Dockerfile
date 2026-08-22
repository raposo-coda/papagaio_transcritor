FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-whisper.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Whisper local e opcional: adiciona ~2 GB a imagem (torch + pyannote).
# Ative com:  docker compose build --build-arg INSTALL_WHISPER=true
ARG INSTALL_WHISPER=false
RUN if [ "$INSTALL_WHISPER" = "true" ]; then \
        pip install --no-cache-dir -r requirements-whisper.txt; \
    fi

COPY . .

ENV PAPAGAIO_DATA_DIR=/app/data \
    PAPAGAIO_OUTPUT_DIR=/app/output \
    HF_HOME=/app/data/huggingface \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
