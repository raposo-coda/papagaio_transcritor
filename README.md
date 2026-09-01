# Papagaio Transcritor

Transforma audios e videos (reunioes, aulas, entrevistas, podcasts) em relatorios de texto
com marcacao de tempo, mais um panorama do conteudo.

Funciona de dois jeitos, e voce escolhe com um clique:

- **Modo local** — a transcricao e a separacao dos falantes rodam dentro do seu computador.
  **Nenhum arquivo e nenhum texto sai da maquina.** Nao precisa de conta, chave nem internet.
- **Modo nuvem** — os arquivos sao enviados ao Google Gemini. Mais rapido, chama os falantes
  pelos nomes ditos na conversa e escreve um resumo analitico. Exige uma chave de API gratuita.

Tudo roda pelo navegador, na sua propria maquina. Nao precisa saber programar.

---

## Sumario

- [Para quem nunca instalou nada](#para-quem-nunca-instalou-nada-instalacao-de-1-clique)
- [Escolhendo o modo](#escolhendo-o-modo)
- [Modo local: como funciona](#modo-local-como-funciona)
- [Modo nuvem: pegando sua chave do Gemini](#modo-nuvem-pegando-sua-chave-do-gemini)
- [Usando o aplicativo](#usando-o-aplicativo)
- [O que voce recebe](#o-que-voce-recebe)
- [O metodo, passo a passo](#o-metodo-passo-a-passo)
- [O que sai do seu computador](#o-que-sai-do-seu-computador)
- [Ligar e desligar no dia a dia](#ligar-e-desligar-no-dia-a-dia)
- [Problemas comuns](#problemas-comuns)
- [Para desenvolvedores](#para-desenvolvedores)
- [Licenca](#licenca)

---

## Para quem nunca instalou nada: instalacao de 1 clique

Os instaladores fazem tudo sozinhos: verificam se o Docker existe, instalam se faltar,
montam a aplicacao e abrem o navegador no endereco certo.

> **O que e Docker?** E um programa que empacota o Papagaio com tudo de que ele precisa
> (Python, ffmpeg, o motor de transcricao) numa caixinha isolada. Voce nao precisa entender
> nada disso — o instalador cuida do assunto. So precisa existir na maquina.

### Windows

1. Baixe este projeto e descompacte a pasta em algum lugar (ex.: `Documentos`).
2. Clique com o **botao direito** em `instalar-windows.bat` e escolha
   **Executar como administrador**.
3. Aceite o aviso de permissao do Windows e espere. Na primeira vez isso pode levar
   de 10 a 30 minutos (o motor de transcricao local e grande).
4. Se o instalador disser que o Docker foi instalado e pedir para **reiniciar o
   computador**, reinicie, abra o **Docker Desktop** uma vez (aceite os termos) e rode
   `instalar-windows.bat` de novo. Isso so acontece uma vez na vida.
5. Quando terminar, o navegador abre sozinho em `http://localhost:8000`.

### Linux ou macOS

Abra o Terminal na pasta do projeto e rode:

```bash
bash instalar-linux-mac.sh
```

- **Linux:** o script instala o Docker via script oficial (vai pedir sua senha) e adiciona
  seu usuario ao grupo `docker`. Depois disso, **saia e entre na sessao de novo** (ou
  reinicie) e rode o script mais uma vez para terminar.
- **macOS:** o script instala o Docker Desktop pelo Homebrew, se voce o tiver. Abra o
  Docker Desktop uma vez, aceite os termos e rode o script de novo.

### Se preferir fazer na mao

Com Docker ja instalado e funcionando:

```bash
docker compose up -d --build
```

### Com GPU NVIDIA (opcional, deixa o modo local muito mais rapido)

Requer driver NVIDIA atualizado e o NVIDIA Container Toolkit (no Windows, o Docker Desktop
com WSL2 ja resolve). Suba assim:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

O aplicativo detecta a GPU sozinho e passa a usa-la. Sem GPU acessivel, ele cai para a CPU
automaticamente e avisa na tela.

---

## Escolhendo o modo

O passo 1 da tela tem dois botoes. A escolha fica salva e pode ser trocada quando quiser.

| | Modo local | Modo nuvem |
|---|---|---|
| Onde transcreve | No seu computador (Whisper) | Servidores do Google (Gemini) |
| Seus arquivos saem da maquina? | **Nao** | **Sim** |
| Precisa de internet? | Nao (so no download unico do modelo) | Sim |
| Precisa de conta/chave? | Nao | Sim, chave gratuita do Gemini |
| Separa quem falou | Sim (`Falante 1`, `Falante 2`...) | Sim, e usa os nomes ditos na conversa |
| Resumo interpretativo | Nao (panorama estatistico) | Sim |
| Velocidade | Depende do seu hardware | Rapido |
| Custo | Zero | Plano gratuito do Google, com limites |

**Regra pratica:** conteudo sensivel (saude, juridico, dados de clientes, informacao
confidencial) → modo local. Precisa de velocidade, de um resumo bem escrito ou de falantes
chamados pelo nome → modo nuvem.

---

## Modo local: como funciona

O modo local usa o **Whisper**, um modelo de reconhecimento de fala que roda inteiramente
na sua maquina. Nao ha servidor, nao ha conta, nao ha envio.

### O modelo e escolhido pelo seu hardware

Ao abrir o aplicativo, ele mede o que a maquina tem — placa de video NVIDIA acessivel,
numero de nucleos da CPU, memoria — e seleciona o maior modelo que roda em tempo razoavel.
O bloco *Motor local* mostra o que foi detectado e o que foi escolhido. Voce pode
sobrescrever a escolha no seletor logo abaixo.

| Modelo | Download | Qualidade | Velocidade tipica em CPU | Em GPU |
|---|---|---|---|---|
| Tiny | ~75 MB | Basica | ~12x tempo real | ~60x |
| Base | ~145 MB | Razoavel | ~8x | ~50x |
| Small | ~480 MB | Boa | ~3,5x | ~35x |
| Medium | ~1,5 GB | Muito boa | ~1,2x | ~20x |
| Large v3 | ~3,1 GB | Melhor | ~0,5x | ~12x |

"~3,5x tempo real" quer dizer que 1 hora de audio leva cerca de 17 minutos. Os numeros sao
aproximados e servem de ordem de grandeza; o aplicativo mostra uma estimativa concreta
assim que voce adiciona os arquivos.

### Separacao de falantes, tambem offline

Ligada por padrao. Funciona em duas etapas, ambas na sua maquina:

1. Um modelo de **segmentacao** corta a gravacao nos pontos em que a voz muda.
2. Um modelo de **embedding de voz** transforma cada trecho num vetor que descreve o timbre.
   Trechos de timbre parecido sao agrupados, e cada grupo vira um falante.

Depois, cada palavra transcrita e atribuida ao falante que ocupava aquele instante. Como o
Whisper roda com marcacao por palavra, uma frase em que duas pessoas se atropelam e cortada
no ponto certo, em vez de ir inteira para a pessoa errada.

O motor e o [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), escolhido por rodar em ONNX
Runtime — **sem PyTorch, sem conta e sem token**. Os dois modelos somam ~35 MB e vem das
releases publicas do projeto no GitHub.

No campo *Quantas pessoas falam* voce pode informar o numero de participantes. Se souber,
informe: o resultado fica bem melhor do que deixar o aplicativo adivinhar.

**Onde erra:** vozes muito parecidas, pessoas falando por cima uma da outra por muito tempo,
e gravacoes ruins (telefone, microfone distante).

**Quanto custa:** a separacao roda sempre na CPU, independente da GPU, a cerca de 6x o tempo
real — ou seja, ~10 min para 1 hora de audio. Ela tambem liga a marcacao por palavra no
Whisper, que encarece a transcricao em torno de 15%. Desligar o interruptor devolve esse
tempo. Numa GTX 1070, 1 hora de audio com `large-v3` e falantes separados leva cerca de
26 minutos no total.

### Os downloads unicos

Na primeira vez que voce usa cada modelo, ele e baixado e guardado na maquina:

| O que | De onde | Tamanho |
|---|---|---|
| Modelo de transcricao (Whisper) | Hugging Face | conforme o tamanho escolhido |
| Modelos de separacao de falantes | GitHub (releases do sherpa-onnx) | ~35 MB |

**Nenhum dos dois exige conta ou token, e sao downloads de mao unica — nada seu vai junto.**
Depois disso, o modo local funciona com a internet desligada.

### Limitacoes honestas do modo local

- **Os falantes nao tem nome.** O aplicativo reconhece *vozes*, nao pessoas: sabe que sao
  tres participantes diferentes, mas nao sabe quem e quem. Saem como `Falante 1`,
  `Falante 2`... Renomeie no `.md`, ou use o modo nuvem, onde o Gemini aproveita os nomes
  ditos durante a conversa.
- **Nao ha resumo interpretativo.** Um resumo de verdade precisa de um modelo de linguagem.
  No lugar dele, o aplicativo calcula um panorama a partir do proprio texto: duracao,
  contagem de palavras, termos recorrentes, tempo de fala por participante e trechos reais.
  E estatistica, nao analise.
- **E mais lento que a nuvem**, especialmente sem GPU.

---

## Modo nuvem: pegando sua chave do Gemini

O modo nuvem usa a IA do Google. Para isso ele precisa de uma **API key** — uma senha que
autoriza o aplicativo a usar a IA **na sua conta**. E gratuita para uso normal.

1. Abra <https://aistudio.google.com/apikey>.
2. Entre com sua conta Google.
3. Clique em **Create API key**.
4. Copie o codigo gerado (comeca com `AIza...`).
5. No Papagaio, cole no campo **API key** e clique em **Salvar configuracao**.

A chave fica salva no seu computador e nunca e exibida de volta. Voce so faz isso uma vez.
No modo local nao existe chave nem conta: nao ha nada a configurar.

---

## Usando o aplicativo

A tela e dividida em passos numerados. Da primeira vez que voce abre, um guia aparece
explicando cada um — depois disso ele fica no botao **? Como usar**, no topo.

| Passo | O que fazer |
|---|---|
| **1. Modo de processamento** | Local ou nuvem. No modo nuvem, cole a chave do Gemini logo abaixo. |
| **2. Idioma** | Em que lingua as pessoas falam nos arquivos. |
| **3. Sobre esta sessao** | De um nome ao relatorio (vira o nome da subpasta dentro da pasta de destino) e descreva o contexto. No modo nuvem, isso melhora bastante o resumo. |
| **4. Arquivos** | Arraste os audios/videos ou clique em *Adicionar arquivos*. O aplicativo le a duracao e mostra uma estimativa de tempo. |
| **5. Rodar** | Clique em **Iniciar transcricao** e acompanhe pelo painel *Log*. |

O cabecalho mostra sempre, em destaque, qual modo esta ativo. A coluna da direita traz o
painel **O que sai do seu computador**, que muda conforme o modo.

**Dicas:**

- Nao feche a aba enquanto estiver processando.
- Uma transcricao de cada vez: se voce tentar iniciar outra enquanto a primeira roda, o
  aplicativo avisa e pede para aguardar.
- Se voce enviar o mesmo arquivo de novo, o resultado vem do cache — sem espera. Trocar de
  modo ou de modelo gera uma entrada nova no cache.

### Formatos aceitos

- **Video:** mp4, mkv, avi, mov, webm, flv, wmv, m4v
- **Audio:** mp3, wav, m4a, ogg, flac, aac

Voce nao precisa converter nada antes: o aplicativo normaliza tudo internamente com ffmpeg.
No modo nuvem ha um limite de 2 GB por arquivo, imposto pelo proprio Gemini.

---

## O que voce recebe

Cada execucao cria uma pasta dentro da **pasta de destino**, nomeada a partir do titulo que
voce deu no passo 3. Sem Docker, o destino padrao e `Documentos/Papagaio Transcritor`, e voce
pode troca-lo na secao *Onde salvar* da interface; no Docker, e a pasta `output/` do projeto.

Repetir o mesmo titulo de proposito reaproveita a pasta e o cache (nao reprocessa o que ja foi
feito). Sem titulo, cada execucao ganha uma pasta propria, para nao se misturar com as outras.
Dentro dela:

- **`nome_do_arquivo.md`** — um por arquivo enviado: transcricao completa com marcacao de
  tempo, mais metadados (duracao, idioma, palavras, modo e modelo usados).
- **`_consolidado.md`** — resumo analitico (modo nuvem) ou panorama estatistico (modo local),
  juntando todos os arquivos da sessao.
- **`_cache.json`** — arquivo interno de cache. Pode ignorar.

Arquivos `.md` (Markdown) sao texto simples: abrem no Bloco de Notas, no Word, no VS Code,
no Obsidian ou em qualquer editor.

Na propria pagina, ao terminar, cada relatorio tem um link de download, e quando ha mais de um
aparece tambem um botao **Baixar tudo (.zip)**. O `_cache.json` nao e servido pelo navegador:
ele guarda a transcricao verbatim e fica so em disco.

No Docker, como a pasta `output/` e compartilhada, os relatorios aparecem direto na pasta do
projeto no seu computador — mesmo sem baixar pelo navegador.

---

## O metodo, passo a passo

1. **Preparacao do arquivo — sempre local, nos dois modos.**
   *Local:* o ffmpeg extrai apenas o audio, em WAV 16 kHz mono — exatamente o que o Whisper e
   a separacao de falantes consomem, entao a imagem de um video nem chega a ser processada.
   *Nuvem:* gera um mp4 padrao (video H.264 + audio AAC, ou so audio quando a origem nao tem
   imagem), que e o formato que o Gemini aceita. Isso evita erros de formato e faz qualquer
   extensao aceita funcionar igual.

2. **Separacao de falantes — so no modo local, quando ligada.**
   Um modelo de segmentacao corta a gravacao onde a voz muda; um modelo de embedding
   transforma cada trecho num vetor de timbre; trechos parecidos sao agrupados por
   clustering. Voce pode fixar o numero de participantes ou deixar automatico. Roda antes da
   transcricao, para ja informar quantas vozes existem e liberar a memoria do audio antes da
   parte pesada. Se falhar, vira aviso no log e a transcricao segue sem rotulos.

3. **Transcricao.**
   *Local:* o Whisper roda na sua CPU (ou GPU NVIDIA, se acessivel) e converte fala em
   texto, marcando o instante de cada trecho; um detector de voz descarta silencios. Com a
   separacao ligada, ativa tambem a marcacao por palavra, para casar fala e falante.
   *Nuvem:* o mp4 e enviado aos servidores do Google e o Gemini devolve a transcricao ja
   dividida por falante, em formato estruturado (JSON com schema fixo).

4. **Atribuicao — so no modo local, quando ligada.**
   Cada palavra recebe o falante que ocupava aquele instante (maior sobreposicao temporal), e
   palavras seguidas do mesmo falante viram um bloco. E por isso que uma frase em que duas
   pessoas se atropelam e cortada no ponto certo, em vez de ir inteira para a pessoa errada.

5. **Montagem do relatorio.**
   Os trechos viram um arquivo Markdown com uma tabela de metadados e a transcricao
   completa, agrupada por falante.

6. **Panorama consolidado.**
   *Nuvem:* todas as transcricoes, mais o contexto que voce escreveu, vao ao Gemini, que
   produz resumo executivo, pontos-chave, decisoes, acoes e participantes.
   *Local:* o aplicativo calcula duracao total, contagem de palavras, tempo de fala e
   participacao de cada falante, termos mais recorrentes (descontando palavras vazias) e
   trechos iniciais de cada arquivo. Sem IA, sem rede.

7. **Cache.**
   Cada transcricao e guardada com uma assinatura derivada de caminho, tamanho, data de
   modificacao, modo, modelo e configuracao de falantes. Reenviar o mesmo arquivo nas mesmas
   condicoes reaproveita o resultado na hora; mexer na separacao de falantes gera uma
   entrada nova.

8. **Persistencia.**
   Relatorios na pasta de destino (`Documentos/Papagaio Transcritor` por padrao, ou `output/`
   no Docker), configuracao em `data/config.json` — criada com permissao `600`, porque guarda
   a chave da API —, logs em `data/logs/` com retencao limitada, modelos do modo local em um
   volume Docker separado (para nao serem baixados de novo a cada atualizacao).
---

## O que sai do seu computador

Esta secao existe para voce nao precisar confiar na nossa palavra: o mesmo conteudo esta no
painel lateral do aplicativo, e o codigo que o gera esta em `server.py`, no endpoint
`/api/audit`.

### Modo local

**Nada do seu conteudo sai da maquina.**

- O audio e o video sao lidos do disco e processados aqui mesmo.
- A transcricao e feita pelo Whisper rodando localmente.
- A separacao de falantes tambem roda localmente. O que ela compara sao caracteristicas da
  voz, calculadas nesta maquina e descartadas ao fim do processamento — nada e guardado nem
  reconhecido entre arquivos diferentes.
- O panorama consolidado e calculado a partir do proprio texto, sem IA e sem rede.
- Relatorios, cache, configuracao e logs ficam so no seu computador.

*Unica excecao:* os modelos sao baixados na primeira vez que voce os usa — o de transcricao
de `huggingface.co`, os de separacao de falantes das releases do sherpa-onnx em `github.com`.
Sao downloads de mao unica, sem conta e sem token; nenhum dado seu vai junto. Depois disso, o
modo local funciona offline.

### Modo nuvem

**Seu conteudo sai da maquina.** Especificamente:

- O arquivo de audio/video inteiro, convertido para mp4, e enviado para
  `generativelanguage.googleapis.com` (Google Gemini API).
- O texto transcrito volta e e reenviado ao Google para gerar o resumo.
- O contexto que voce escreveu no passo 3 acompanha o pedido de resumo.
- Sua chave de API identifica sua conta Google em cada chamada.

Continuam so no seu computador: os relatorios `.md`, o cache, a chave de API (guardada em
disco) e os logs. Vale a politica de dados da API do Google para o que e enviado.

---

## Ligar e desligar no dia a dia

Depois de instalado, o Papagaio continua rodando em segundo plano — inclusive apos reiniciar
o computador, assim que o Docker sobe.

**Windows** (duplo clique):

- `iniciar.bat` — liga o aplicativo e abre o navegador.
- `parar.bat` — desliga.

**Linux / macOS** (na pasta do projeto):

```bash
docker compose up -d     # ligar
docker compose down      # desligar
docker compose logs -f   # ver o que esta acontecendo
```

Para atualizar depois de baixar uma versao nova do codigo:

```bash
docker compose up -d --build
```

Os modelos do modo local ficam num volume proprio e **nao** sao baixados de novo a cada
rebuild.

---

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| A pagina `localhost:8000` nao abre | Confira se o Docker Desktop esta aberto e diz *Engine running*. Depois rode `iniciar.bat` (ou `docker compose up -d`). |
| O botao *Modo local* aparece como "nao instalado" | Sua imagem foi montada antes do modo local existir. Rode `docker compose up -d --build`. Sem Docker: `pip install faster-whisper`. |
| A primeira transcricao local demora muito para comecar | E o download unico do modelo. O tamanho aparece no seletor de modelo; acompanhe pelo *Log*. |
| Modo local sem memoria / processo morto | Escolha um modelo menor no seletor (Base ou Tiny). Em gravacoes muito longas, desligar a separacao de falantes tambem alivia — ela carrega o audio inteiro em memoria. |
| O interruptor *Separar os falantes* aparece desativado | O `sherpa-onnx` nao esta na imagem. Rode `docker compose up -d --build`. Sem Docker: `pip install sherpa-onnx`. |
| Os falantes sairam trocados ou de menos | Informe o numero de participantes no campo *Quantas pessoas falam* em vez de deixar automatico. Vozes parecidas, muita fala sobreposta e audio ruim sao os casos dificeis. |
| A transcricao terminou mas sem falantes, com aviso no Log | A separacao falhou e o aplicativo seguiu sem ela, de proposito — a transcricao vale mais que os rotulos. A causa exata esta no log da sessao. |
| A GPU nao e usada mesmo tendo uma NVIDIA | Suba com `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`. Sem o NVIDIA Container Toolkit, o container nao enxerga a placa. |
| *"Nenhuma chave salva ainda"* (modo nuvem) | Veja [Modo nuvem](#modo-nuvem-pegando-sua-chave-do-gemini) — ou troque para o modo local, que dispensa chave. |
| Erro `API key not valid` | A chave foi copiada errada ou revogada. Gere outra e salve de novo. |
| Erro de limite / `quota` / `429` | Voce bateu o limite gratuito da conta Google. Espere, verifique seu plano, ou use o modo local. |
| *"Ja existe uma transcricao em andamento"* | Espere a atual terminar — o aplicativo processa uma sessao por vez. |
| *"Extensao nao suportada"* | O arquivo nao e audio nem video reconhecido. Veja a lista de formatos aceitos. |
| Travou ou algo estranho | `docker compose down` e depois `docker compose up -d`. Para ver o erro completo: `docker compose logs`. |

O painel **Log** dentro do aplicativo mostra cada etapa em tempo real; erros aparecem em
vermelho, geralmente com a causa escrita por extenso.

---

## Para desenvolvedores

### Rodando sem Docker

Requer Python 3.11+ e `ffmpeg` no PATH.

```bash
pip install -r requirements.txt
python main.py
```

O servidor sobe em `http://localhost:8000`.

### Variaveis de ambiente

| Variavel | Padrao | Para que serve |
|---|---|---|
| `PAPAGAIO_DATA_DIR` | `./data` (Docker) | Onde ficam `config.json` e os logs. |
| `PAPAGAIO_OUTPUT_DIR` | `Documentos/Papagaio Transcritor` (`/app/output` no Docker) | Forca a pasta de saida, ignorando a escolhida na interface. |
| `PAPAGAIO_MODELS_DIR` | `<data>/models` | Onde os modelos Whisper do modo local sao guardados. |
| `PORT` | `8000` | Porta do servidor (apenas ao rodar `python main.py`). |
| `PAPAGAIO_HOST` | `127.0.0.1` | Interface de escuta. Sair do loopback exige `PAPAGAIO_TOKEN`. |
| `PAPAGAIO_TOKEN` | vazio | Exige token em toda requisicao. Acesse `http://host:8000/?token=<segredo>` uma vez. |
| `PAPAGAIO_LOG_LEVEL` | `INFO` | `DEBUG` inclui caminhos e metadados no terminal. |

As demais variaveis e o modelo de ameaca completo estao em [SECURITY.md](SECURITY.md).

> **Aviso.** A aplicacao nao tem login: a protecao e escutar so em `127.0.0.1`. Nao publique
> esta porta na internet. Se precisar acessar de outra maquina da rede, defina
> `PAPAGAIO_TOKEN` e use HTTPS — sem isso, qualquer um na mesma rede consegue enviar arquivos
> com a sua chave da API e ler as suas transcricoes.

### Estrutura

```
server.py         API FastAPI: config, hardware, auditoria, jobs, download, estaticos
pipeline.py       orquestra preparacao -> transcricao -> relatorio -> consolidado
gemini_client.py  modo nuvem: upload, transcricao e resumo via Google Gemini
local_client.py   modo local: transcricao offline via faster-whisper, atribuicao de falantes
                  e panorama estatistico
diarizer.py       modo local: separacao de falantes offline via sherpa-onnx
hardware.py       deteccao de GPU/CPU/RAM, escolha do modelo e estimativa de tempo
converter.py      normalizacao com ffmpeg: mp4 (nuvem) ou WAV 16 kHz mono (local)
config.py         constantes, modos, formatos suportados, persistencia de config
models.py         dataclasses do pipeline (inclui LocalConfig e o campo mode)
utils.py          cache por arquivo, pastas de sessao, formatacao de tempo
logger.py         log com callback para a interface
static/           interface web (HTML + CSS + JS puro, sem build)
legacy/           versao antiga em tkinter, mantida para referencia
```

### API HTTP

| Metodo | Rota | Descricao |
|---|---|---|
| `GET` | `/api/config` | Config atual: modo, modelo local resolvido, separacao de falantes, se ha chave salva. |
| `POST` | `/api/config` | Salva modo, modelo local, separacao de falantes, chave, modelos Gemini e idioma. |
| `GET` | `/api/hardware` | GPU/CPU/RAM detectados, modelo recomendado, velocidade estimada por modelo, quais ja estao baixados e o estado da separacao de falantes. |
| `GET` | `/api/audit` | Declaracao, por modo, do que sai e do que fica na maquina. |
| `POST` | `/api/jobs` | Cria um job (multipart com `files`, `lang`, `title`, `context_prompt`). |
| `GET` | `/api/jobs/{id}` | Status, modo, log acumulado e arquivos gerados. |
| `GET` | `/api/jobs/{id}/download/{arquivo}` | Baixa um relatorio da sessao. |

### Modelos

- **Nuvem:** configuraveis em *Opcoes avancadas*. Padrao `gemini-flash-latest` para
  transcricao e para resumo.
- **Local:** `tiny`, `base`, `small`, `medium`, `large-v3`, ou `auto` (padrao), que resolve
  para o recomendado pelo `hardware.detect()`.
- **Separacao de falantes (local):** segmentacao `sherpa-onnx-pyannote-segmentation-3-0` e
  embedding `3dspeaker_speech_campplus_sv_en_voxceleb_16k`, baixados sob demanda para a pasta
  de modelos. Numero de falantes fixo (2 a 10) ou automatico por clustering.

---

## Licenca

[MIT](LICENSE) — use, modifique e distribua livremente, inclusive comercialmente,
mantendo o aviso de copyright.
