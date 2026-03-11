# transcrever_video.py

Transcreve vídeos e áudios com IA na nuvem, diarização de falantes e gera relatório em Markdown.

---

## Stack utilizada

| Componente | Ferramenta | Por quê |
|---|---|---|
| Extração de áudio | `ffmpeg` (sistema) | Leve, zero dependência Python |
| Transcrição + Diarização | AssemblyAI API | Gratuito (5h/mês), nuvem, preciso |
| Resumo por IA | AssemblyAI LeMUR (Claude Haiku) | Incluso no plano gratuito |
| Saída | Markdown `.md` | Portátil, legível, exportável |

---

## Instalação

### 1. ffmpeg (sistema)
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg
```

### 2. Dependência Python (só uma)
```bash
pip install assemblyai
```

### 3. API Key gratuita
1. Acesse https://www.assemblyai.com/ e crie uma conta (gratuita)
2. Copie sua API key no dashboard
3. Defina no ambiente **ou** passe via argumento:

```bash
# Opção A: variável de ambiente (recomendado)
export ASSEMBLYAI_API_KEY="sua_key_aqui"

# Opção B: argumento direto
python transcrever_video.py video.mp4 --api-key sua_key_aqui
```

---

## Uso

```bash
# Básico (português)
python transcrever_video.py reuniao.mp4

# Especificar idioma
python transcrever_video.py lecture.mkv --lang en

# Definir nome do arquivo de saída
python transcrever_video.py entrevista.mp4 --saida relatorio_entrevista.md

# Arquivo de áudio direto (sem extração)
python transcrever_video.py podcast.mp3 --lang pt
```

### Idiomas suportados
`pt` `en` `es` `fr` `de` `it` `ja` `ko` `zh`

---

## O que o relatório contém

```
📄 arquivo.md
├── Metadados (duração, idioma, nº de falantes, palavras)
├── Análise e Resumo (IA / LeMUR)
│   ├── Resumo Executivo
│   ├── Pontos-chave
│   ├── Decisões e Ações
│   ├── Participantes
│   ├── Entidades Mencionadas
│   └── Conclusão
├── Capítulos / Tópicos (com timestamps)
├── Transcrição Completa (com diarização por falante)
└── Entidades Detectadas (nomes, datas, locais, orgs)
```

---

## Plano gratuito AssemblyAI

| Recurso | Limite gratuito |
|---|---|
| Transcrição | 5 horas / mês |
| Diarização | Incluída |
| Capítulos automáticos | Incluído |
| Detecção de entidades | Incluído |
| LeMUR (resumo IA) | 5 horas de áudio / mês |

Para uso intenso, o plano pago começa em ~$0.37/hora de áudio.

---

## Formatos suportados

**Vídeo:** `.mp4` `.mkv` `.avi` `.mov` `.webm` `.flv` `.wmv` `.m4v`  
**Áudio:** `.mp3` `.wav` `.m4a` `.ogg` `.flac` `.aac`
# papagaio_transcritor
# papagaio_transcritor
# papagaio_transcritor
