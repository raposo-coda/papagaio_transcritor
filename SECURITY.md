# Seguranca e privacidade

Esta ferramenta processa gravacoes de reunioes, aulas e entrevistas — material que
quase sempre contem dados pessoais de terceiros. Leia esta pagina antes de usar em
qualquer coisa que nao seja um audio de teste.

## Modelo de ameaca

O Papagaio Transcritor foi desenhado como **aplicativo local, de uso individual**.
A API **nao tem login**. A protecao vem de o servidor so escutar em `127.0.0.1`.

| Cenario | Suportado |
|---|---|
| Rodar na sua maquina, acessar por `localhost` | Sim, e o uso previsto |
| Publicar na internet sem mais nada | **Nao.** Qualquer um usaria sua chave e leria suas transcricoes |
| Expor na rede local com `PAPAGAIO_TOKEN` | Aceitavel entre pessoas de confianca, sempre atras de HTTPS |

## Regras que valem sempre

1. **Nunca commite `data/` nem `output/`.** `data/config.json` guarda sua chave da
   API; `output/` guarda transcricoes. Ambos estao no `.gitignore` — nao os force.
2. **Use a sua propria chave do Gemini.** Gere em <https://aistudio.google.com/apikey>.
   Nao compartilhe chave entre pessoas: o consumo e cobrado de quem e dono dela.
3. **No modo nuvem o arquivo sai da sua maquina.** Ele vai inteiro para o Google.
   Para material sigiloso, use o **modo local**, que roda offline. O painel
   *O que sai do seu computador*, na propria interface, detalha isso por modo.
4. **Gravar terceiros exige base legal.** Se as vozes nao sao so a sua, a LGPD se
   aplica: tenha consentimento ou outra base valida, guarde o minimo necessario e
   apague o que nao precisa mais. As transcricoes ficam em disco ate voce apagar.

## Variaveis de ambiente

| Variavel | Padrao | Para que serve |
|---|---|---|
| `PAPAGAIO_HOST` | `127.0.0.1` | Interface de escuta. Sair do loopback exige `PAPAGAIO_TOKEN` |
| `PAPAGAIO_TOKEN` | vazio | Se definido, toda requisicao precisa do token. Acesse `http://host:8000/?token=<segredo>` uma vez e o navegador guarda um cookie |
| `PAPAGAIO_ALLOWED_ORIGINS` | vazio | Origens extras aceitas em POST (checagem anti-CSRF) |
| `PAPAGAIO_OUTPUT_DIR` | Documentos | Forca a pasta de saida (usado pelo Docker) |
| `PAPAGAIO_ALLOW_ANY_OUTPUT_DIR` | `0` | Permite salvar fora da pasta do usuario. Deixe desligado |
| `PAPAGAIO_LOG_LEVEL` | `INFO` | `DEBUG` inclui caminhos e metadados no terminal |
| `PAPAGAIO_LOG_RETENTION` | `20` | Quantos logs de sessao ficam guardados |
| `PAPAGAIO_JOB_RETENTION` | `21600` | Segundos que um job concluido fica na memoria |

## Protecoes implementadas

- Escuta em `127.0.0.1` por padrao; a porta do Docker e publicada so no loopback.
  Sair disso sem `PAPAGAIO_TOKEN` faz a aplicacao recusar a subir.
- Checagem de `Origin`/`Referer` nos metodos que alteram estado (anti-CSRF).
- Cabecalhos de seguranca em toda resposta: CSP restrita, `nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- `/api/config` com esquema fechado (Pydantic, `extra="forbid"`) e limites de tamanho.
- `config.json` criado com permissao `600` em sistemas POSIX.
- Upload limitado por arquivo e por quantidade, cortado durante a leitura.
- Download restrito a `.md`: o cache de transcricao verbatim (`_cache.json`)
  nao e servido pela API, e o zip inclui so os relatorios.
- Container roda como usuario nao-root, com `no-new-privileges` e teto de memoria.
- Log de sessao com retencao limitada; `stderr` do ffmpeg fica so no log local,
  nunca na resposta HTTP.

## Codigo legado

`legacy/transcrever_video.py` e a versao antiga em tkinter, mantida so como
referencia historica. **Nao a execute.** Ela aceita URL de servidor arbitraria
(SSRF), grava chaves de API em texto plano em `~/.transcritor_config.json` e
depende de pacotes que nao estao no `requirements.txt`.

## Reportar um problema

Abra uma issue **sem incluir dados reais** (sem transcricoes, sem chaves, sem
nomes de participantes). Se o problema envolver exposicao de dados, descreva o
comportamento e mande o detalhe sensivel em canal privado.
