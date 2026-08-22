# Papagaio Transcritor

Aplicacao web (FastAPI + JS puro) para transcrever audio ou video em lote, gerar relatorios em
Markdown e resumir o conteudo. Roda localmente via Docker.

Dois motores de transcricao, selecionaveis na interface:

- **Gemini (nuvem)** - rapido, requer API key e `ffmpeg`.
- **Whisper local** - `faster-whisper` + diarizacao com `pyannote.audio`. Nada sai da maquina,
  nao precisa de API key nem do binario do `ffmpeg`, mas e bem mais lento sem GPU.

## O que o sistema faz

- Transcreve 1 ou varios arquivos de audio/video enviados pelo navegador.
- Identifica falantes (Gemini nativamente; Whisper via pyannote).
- Gera um resumo/analise consolidado do conteudo com o Gemini.
- Gera um `.md` por arquivo e um `_consolidado.md` da sessao, disponiveis para download pela interface.
- Mantem cache local por arquivo, isolado por provider e modelo (evita retranscrever o mesmo arquivo).

## Requisitos

- Docker e Docker Compose
- **Para o provider Gemini:** uma API key gerada em https://aistudio.google.com com sua conta Google
- **Para o provider Whisper local:** as dependencias de `requirements-whisper.txt` e, se quiser
  diarizacao, um token do HuggingFace (veja abaixo)

## Como rodar

```bash
docker compose up --build
```

Depois abra http://localhost:8000, escolha o provider e salve a configuracao.
Os arquivos gerados ficam em `./output` na maquina host (mapeado via volume).

Para incluir o Whisper local na imagem (adiciona ~2 GB por causa do torch):

```bash
INSTALL_WHISPER=true docker compose up --build
```

## Rodando sem Docker (desenvolvimento)

```bash
pip install -r requirements.txt
pip install -r requirements-whisper.txt   # opcional, so para o Whisper local
python main.py
```

O `ffmpeg` no PATH so e necessario para o provider Gemini (normalizacao para `.mp4` antes do upload).
O Whisper local decodifica o audio com PyAV, embutido no `faster-whisper`.

O servidor sobe em http://localhost:8000. As pastas de dados/saida usam `./data` e `./output` por padrao
(pode ser sobrescrito com as variaveis de ambiente `PAPAGAIO_DATA_DIR` e `PAPAGAIO_OUTPUT_DIR`).

## Whisper local

### Diarizacao (identificacao de falantes)

O Whisper nao identifica falantes sozinho. A diarizacao usa o `pyannote/speaker-diarization-3.1`,
que e um modelo com termos de uso. Antes do primeiro uso:

1. Crie um token em https://huggingface.co/settings/tokens
2. Aceite os termos em https://huggingface.co/pyannote/speaker-diarization-3.1
   (e tambem em https://huggingface.co/pyannote/segmentation-3.0)
3. Cole o token na secao "Whisper local" da interface e salve

Se a diarizacao falhar ou estiver desligada, a transcricao continua normalmente e todas as falas
saem como "Falante 1". Se voce sabe quantas pessoas falam no audio, informe no campo
"Numero de falantes" - a diarizacao fica bem mais precisa.

Os pesos do Whisper e do pyannote sao baixados no primeiro uso e ficam no cache do HuggingFace
(no Docker, dentro de `./data/huggingface`, entao o download acontece so uma vez).

### Escolha do modelo e velocidade

Sem GPU NVIDIA, o Whisper roda em CPU com quantizacao int8. Numeros medidos num
**Intel i7-6500U (2 nucleos / 4 threads, sem GPU)**, com `vad_filter` ligado:

| Modelo | Velocidade | 1h de audio | Observacao |
|---|---|---|---|
| `tiny` | 11.0x realtime | ~5 min | qualidade fraca em portugues |
| `base` | 6.0x realtime | ~10 min | ainda comete bastante erro |
| `small` | 2.9x realtime | ~21 min | padrao, melhor equilibrio |
| `medium` | ~1x realtime* | ~1h* | |
| `large-v3` | ~0.5x realtime* | ~2h* | so vale a pena com GPU |

\* extrapolado a partir dos tres primeiros; os outros foram cronometrados.

Duas ressalvas: audio com fala continua (sem pausas para o VAD cortar) fica mais lento que a
tabela, e o carregamento do modelo cobra a parte dele na primeira vez (~30s para o `small`, mais
o download dos pesos). Dentro de um mesmo processo o modelo fica em cache entre arquivos.

A diarizacao roda depois da transcricao e adiciona seu proprio tempo. Com GPU CUDA, escolha
`cuda` no campo "Dispositivo" (ou deixe em `auto`) - fica uma ordem de grandeza mais rapido.

## Resumo

O resumo consolidado sempre usa o Gemini, inclusive quando a transcricao e feita pelo Whisper local.
Sem API key salva, o sistema cai num resumo local simples (trechos iniciais de cada arquivo), sem
analise. Ou seja: com Whisper + API key voce tem transcricao offline e resumo na nuvem; sem API key,
o fluxo inteiro fica offline com um relatorio mais pobre.

## Modelos

Configuraveis na interface. Padroes: `gemini-flash-latest` para transcricao e resumo no Gemini,
`small` para o Whisper local.

## Saida

Cada sessao cria uma pasta dentro de `output/` com:

- `arquivo_1.md`, `arquivo_2.md`, ...
- `_consolidado.md`
- `_cache.json`
