# Papagaio Transcritor

Aplicacao web (FastAPI + JS puro) para transcrever audio ou video em lote usando a API do Google Gemini,
gerar relatorios em Markdown e resumir o conteudo. Roda localmente via Docker.

## O que o sistema faz

- Transcreve 1 ou varios arquivos de audio/video enviados pelo navegador.
- Usa o Gemini (upload direto do arquivo, sem precisar de ffmpeg) para transcrever com identificacao de falantes.
- Usa o Gemini para gerar um resumo/analise consolidado do conteudo.
- Gera um `.md` por arquivo e um `_consolidado.md` da sessao, disponiveis para download pela interface.
- Mantem cache local por arquivo (evita retranscrever o mesmo arquivo).

## Requisitos

- Docker e Docker Compose
- Uma API key do Gemini, gerada em https://aistudio.google.com com sua conta Google

## Como rodar

```bash
docker compose up --build
```

Depois abra http://localhost:8000 no navegador, cole sua API key do Gemini na secao "Gemini" e salve.
Os arquivos gerados ficam disponiveis em `./output` na maquina host (mapeado via volume).

## Rodando sem Docker (desenvolvimento)

```bash
pip install -r requirements.txt
python main.py
```

O servidor sobe em http://localhost:8000. As pastas de dados/saida usam `./data` e `./output` por padrao
(pode ser sobrescrito com as variaveis de ambiente `PAPAGAIO_DATA_DIR` e `PAPAGAIO_OUTPUT_DIR`).

## Modelos

Os modelos de transcricao e resumo sao configuraveis na interface (padrao: `gemini-flash-latest` para ambos).

## Saida

Cada sessao cria uma pasta dentro de `output/` com:

- `arquivo_1.md`, `arquivo_2.md`, ...
- `_consolidado.md`
- `_cache.json`
