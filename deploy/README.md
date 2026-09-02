# Modo servidor (Linux)

Deixa uma maquina hospedando o Papagaio Transcritor para a rede local, sob
systemd, atualizando sozinha a partir do GitHub.

Para uso pessoal no Windows, nao e isto que voce quer -- use o
`instalar-windows.bat`, que e mais simples e nao mexe no sistema.

## Instalar

```bash
sudo ./scripts/instalar-servico-linux.sh
```

O instalador e idempotente e explica cada passo antes de executa-lo. Ele monta o
disco de dados, registra a GPU no Docker, habilita o Docker no boot, cria o
clone de deploy com um token novo e sobe o servico.

## Como funciona

| Peca | Papel |
|---|---|
| `papagaio.service` | Sobe e derruba a stack. `oneshot` + `RemainAfterExit`. |
| `papagaio-deploy.timer` | A cada 30 min dispara o servico abaixo. |
| `papagaio-deploy.service` | Roda `deploy.sh sync`: busca o branch, reconstroi se mudou. |
| `deploy.sh` | Toda a logica. Instalado em `/usr/local/lib/papagaio/`. |
| `/etc/default/papagaio` | Qual disco, qual clone, qual branch, GPU sim ou nao. |
| `docker-compose.deploy.yml` | Sobreposicao com os binds do disco de dados e o healthcheck. |

Os arquivos ficam assim:

```
/mnt/container-data/homelab/papagaio/
├── repo/      clone de deploy (nao edite aqui: o sync faz reset --hard)
├── data/      config.json com a chave do Gemini, logs, uploads temporarios
├── output/    os relatorios .md -- o unico dado insubstituivel
├── models/    Whisper e diarizacao
├── .env       PAPAGAIO_TOKEN e PAPAGAIO_ALLOWED_ORIGINS (0600, root)
└── .estado    "gpu" ou "cpu": o modo com que a stack subiu
```

## Operacao

```bash
systemctl status papagaio            # esta no ar?
journalctl -u papagaio -f            # o que aconteceu na subida
journalctl -u papagaio-deploy -f     # historico de deploys
systemctl start papagaio-deploy      # nao esperar os 30 minutos
systemctl restart papagaio           # recriar os containers

sudo /usr/local/lib/papagaio/deploy.sh status   # modo, commit, containers
```

## O ciclo de deploy

O `sync` foi escrito para nunca derrubar uma versao que funciona:

1. `git fetch`. Sem rede, avisa e sai com sucesso -- internet caida nao e falha
   de deploy, e marcar a unidade como `failed` so geraria ruido.
2. Se o commit nao mudou **e** o container esta saudavel, sai sem fazer nada.
3. Recusa continuar se o commit remoto nao descende do implantado. Um
   force-push (ou uma conta comprometida) nao entra em producao caladamente.
4. `build` **antes** de tocar no que esta rodando. Build quebrado = clone volta
   ao commit anterior e a versao no ar continua servindo.
5. `up` e espera o healthcheck. Se a versao nova nao responder em 3 minutos,
   volta para a anterior.
6. Poda imagens com mais de uma semana. Sem isso, cada rebuild deixa uma imagem
   orfa e o disco do sistema enche sozinho.

## GPU

A imagem sempre inclui as bibliotecas CUDA (`WITH_CUDA=1`). Usar ou nao a GPU e
so uma questao de passar ou nao o `docker-compose.gpu.yml` -- trocar de modo
recria o container em segundos, sem rebuild.

O `deploy.sh` cai para CPU sozinho em dois pontos:

- o `up` falha com erro de driver de dispositivo (toolkit ausente ou mal
  configurado);
- o `warmup.py` falha com erro de CUDA/cuDNN. Este segundo caso importa: numa
  GTX 1070 (Pascal, `sm_61`) a GPU aparece normalmente para o CTranslate2, mas o
  cuDNN 9 vem removendo kernels dessa geracao. Sem o warmup como teste real, a
  falha so apareceria na primeira transcricao do usuario, como erro 500.

Se cair para CPU e voce quiser investigar, o modo fica registrado em
`$BASE/.estado`; apague o arquivo e reinicie o servico para tentar a GPU de novo.

## Cuidados

- **Porta 8000 e uma so.** O clone de deploy publica em `0.0.0.0:8000`. Se voce
  subir o Compose no seu checkout de desenvolvimento (que usa `127.0.0.1:8000`),
  a segunda subida falha. Pare um antes de usar o outro.
- **Nao edite dentro de `repo/`.** O `sync` roda `git reset --hard` e `clean -fd`.
- **O disco raiz e criptografado.** Depois de um reboot, nada sobe ate alguem
  digitar a senha do LUKS no teclado. Feito isso, o servico sobe sozinho -- nao
  precisa fazer login.
- **`ufw` nao protege esta porta.** O trafego publicado pelo Docker passa por
  `FORWARD`, nao por `INPUT`, e o `after.rules` desta maquina ja libera toda a
  rede privada. Quem protege e o `PAPAGAIO_TOKEN`.
- **Espaco em disco.** O `deploy.sh` recusa rodar com menos de 5 GB livres no
  disco de dados. Se apagar arquivos pelo gerenciador, esvazie tambem a lixeira
  do disco (`.Trash-1000`), senao o espaco nao volta.

## Desinstalar

```bash
sudo systemctl disable --now papagaio.service papagaio-deploy.timer
sudo rm /etc/systemd/system/papagaio*.{service,timer}
sudo rm -rf /usr/local/lib/papagaio /etc/default/papagaio
sudo systemctl daemon-reload
```

Os dados em `/mnt/container-data/homelab/papagaio/` continuam intactos. A entrada
do `/etc/fstab` tambem -- remova a mao se nao quiser mais o disco montado ali.
