<#
    papagaio.ps1 - Logica dos atalhos de 1 clique do Papagaio Transcritor.

    Chamado pelos .bat da raiz do projeto. Batch puro nao da conta de detectar
    WSL, achar o Docker fora do PATH, testar GPU e fazer polling HTTP sem virar
    um emaranhado ilegivel - por isso a logica mora aqui.

    Uso:
        powershell -ExecutionPolicy Bypass -File papagaio.ps1 -Acao setup
        powershell -ExecutionPolicy Bypass -File papagaio.ps1 -Acao iniciar
        powershell -ExecutionPolicy Bypass -File papagaio.ps1 -Acao parar
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("setup", "iniciar", "parar")]
    [string]$Acao,

    # Pula o download antecipado dos modelos do modo local.
    [switch]$SemModelos,

    # Forca CPU mesmo havendo placa NVIDIA.
    [switch]$SemGpu,

    # Nao faz perguntas (usado em teste automatizado).
    [switch]$NaoInterativo
)

# NAO usar "Stop" aqui. No PowerShell 5.1, qualquer escrita em stderr por um
# programa externo vira erro terminante - e o 'docker compose' manda todo o
# progresso de build por stderr, o que derrubava o instalador no meio. O controle
# de erro deste script e feito conferindo $LASTEXITCODE a cada chamada.
$ErrorActionPreference = "Continue"
$env:WSL_UTF8 = 1

$Raiz = Split-Path -Parent $PSScriptRoot
$Endereco = "http://localhost:8000"

# ---------------------------------------------------------------- rede local

function Ler-Env {
    <# Le o .env da raiz (nao versionado). Vazio quando nao existe. #>
    $caminho = Join-Path $Raiz ".env"
    $valores = @{}
    if (-not (Test-Path $caminho)) { return $valores }
    foreach ($linha in Get-Content $caminho) {
        $t = $linha.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -gt 0) { $valores[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim() }
    }
    return $valores
}

$EnvLocal = Ler-Env
$Token = $EnvLocal["PAPAGAIO_TOKEN"]

# Ter token configurado E o arquivo de rede presente e o sinal de que esta
# maquina foi deliberadamente aberta para a rede local. Sem esse sinal, tudo
# continua so em 127.0.0.1.
$ModoRede = [bool]$Token -and (Test-Path (Join-Path $Raiz "docker-compose.rede.yml"))

function Endereco-Com-Token($base = $Endereco) {
    # Com token ativo, abrir a URL nua devolveria 401. O token entra uma vez na
    # query e o servidor o converte em cookie.
    if ($Token) { return "$base/?token=$Token" }
    return $base
}

function Ip-Local {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -eq "Dhcp" } |
            Select-Object -First 1 -ExpandProperty IPAddress
        return $ip
    } catch { return $null }
}

# ---------------------------------------------------------------- saida

function Titulo($texto) {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "    $texto" -ForegroundColor Cyan
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""
}
function Passo($texto)  { Write-Host "  $texto" -ForegroundColor White }
function Ok($texto)     { Write-Host "      OK: $texto" -ForegroundColor Green }
function Aviso($texto)  { Write-Host "      !  $texto" -ForegroundColor Yellow }
function Erro($texto)   { Write-Host "      X  $texto" -ForegroundColor Red }

function Encerrar($codigo) {
    if (-not $NaoInterativo) {
        Write-Host ""
        Write-Host "  Pressione ENTER para fechar." -ForegroundColor DarkGray
        try { Read-Host | Out-Null } catch { }
    }
    exit $codigo
}

# ---------------------------------------------------------------- docker

function Encontrar-Docker {
    <#
        O Docker Desktop pode ser instalado por usuario (%LOCALAPPDATA%) ou para
        a maquina toda (%ProgramFiles%), e a instalacao por usuario nem sempre
        entra no PATH - foi exatamente esse o caso que quebrou a versao anterior
        deste instalador.
    #>
    $doPath = Get-Command docker -ErrorAction SilentlyContinue
    if ($doPath) { return $doPath.Source }

    $candidatos = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($c in $candidatos) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Encontrar-DockerDesktop {
    $candidatos = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    foreach ($c in $candidatos) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Engine-NoAr($docker) {
    & $docker info --format "{{.ServerVersion}}" > $null 2>&1
    return $?
}

function Aguardar-Engine($docker, $segundos = 300) {
    $fim = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $fim) {
        if (Engine-NoAr $docker) { return $true }
        Start-Sleep -Seconds 5
        Write-Host "      aguardando o Docker subir..." -ForegroundColor DarkGray
    }
    return $false
}

function Aguardar-Servidor($segundos = 180) {
    # Com token configurado, /api/meta sem credencial responde 401 - o que ja
    # prova que o servidor esta de pe. Por isso o token vai junto na consulta.
    $alvo = "$Endereco/api/meta"
    if ($Token) { $alvo += "?token=$Token" }

    $fim = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $fim) {
        try {
            $r = Invoke-WebRequest -Uri $alvo -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

# ---------------------------------------------------------------- wsl

function Estado-WSL {
    <# Devolve o que falta para o backend do Docker funcionar. #>
    $featureOk = Test-Path "HKLM:\SYSTEM\CurrentControlSet\Services\LxssManager"
    $hypervisor = $false
    try { $hypervisor = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent } catch { }

    return [pscustomobject]@{
        FeatureInstalada = $featureOk
        HypervisorAtivo  = $hypervisor
        # Recurso habilitado no registro mas hypervisor ainda parado = falta reiniciar.
        RebootPendente   = ($featureOk -and -not $hypervisor)
    }
}

function Habilitar-WSL {
    Passo "Habilitando o Subsistema do Windows para Linux..."
    $mudou = $false
    foreach ($nome in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
        try {
            $f = Get-WindowsOptionalFeature -Online -FeatureName $nome -ErrorAction Stop
            if ($f.State -ne "Enabled") {
                Enable-WindowsOptionalFeature -Online -FeatureName $nome -All -NoRestart -ErrorAction Stop | Out-Null
                Ok "$nome habilitado"
                $mudou = $true
            } else {
                Ok "$nome ja estava habilitado"
            }
        } catch {
            Erro "Falha ao habilitar ${nome}: $($_.Exception.Message)"
            return $null
        }
    }
    return $mudou
}

# ---------------------------------------------------------------- gpu

function Detectar-Gpu {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        $padrao = "$env:SystemRoot\System32\nvidia-smi.exe"
        if (Test-Path $padrao) { $smi = $padrao } else { return $null }
    } else {
        $smi = $smi.Source
    }
    try {
        $saida = & $smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $saida) { return $null }
        return ($saida -split "`n")[0].Trim()
    } catch { return $null }
}

function Arquivos-Compose($usarGpu) {
    # Nao usar $args aqui: e variavel automatica do PowerShell.
    $lista = @("-f", "docker-compose.yml")
    if ($usarGpu) { $lista += @("-f", "docker-compose.gpu.yml") }
    # Sem isto, ligar pelo atalho devolveria o app para 127.0.0.1 e derrubaria o
    # acesso pela rede que o usuario configurou de proposito.
    if ($ModoRede) { $lista += @("-f", "docker-compose.rede.yml") }
    return $lista
}

# ================================================================ acoes

function Acao-Parar {
    Titulo "PAPAGAIO TRANSCRITOR - DESLIGAR"
    $docker = Encontrar-Docker
    if (-not $docker) { Erro "Docker nao encontrado. Nada a desligar."; Encerrar 0 }

    Push-Location $Raiz
    try {
        & $docker compose down
        Ok "Desligado. Seus relatorios continuam salvos."
    } finally { Pop-Location }
    Encerrar 0
}

function Acao-Iniciar {
    Titulo "PAPAGAIO TRANSCRITOR"

    $docker = Encontrar-Docker
    if (-not $docker) {
        Erro "Docker nao encontrado."
        Aviso "Rode 'instalar-windows.bat' (botao direito > Executar como administrador)."
        Encerrar 1
    }

    if (-not (Engine-NoAr $docker)) {
        Passo "Ligando o Docker Desktop..."
        $app = Encontrar-DockerDesktop
        if ($app) { Start-Process $app }
        if (-not (Aguardar-Engine $docker 300)) {
            Erro "O Docker nao terminou de subir."
            Aviso "Abra o Docker Desktop na mao, espere aparecer 'Engine running' e tente de novo."
            Encerrar 1
        }
    }
    Ok "Docker no ar"

    $gpu = if ($SemGpu) { $null } else { Detectar-Gpu }
    Push-Location $Raiz
    try {
        & $docker compose @(Arquivos-Compose ($null -ne $gpu)) up -d
        if ($LASTEXITCODE -ne 0 -and $gpu) {
            Aviso "Nao subiu com GPU. Tentando sem."
            & $docker compose @(Arquivos-Compose $false) up -d
        }
        if ($LASTEXITCODE -ne 0) {
            Erro "Nao foi possivel iniciar. Rode 'instalar-windows.bat' primeiro."
            Encerrar 1
        }
    } finally { Pop-Location }

    if (Aguardar-Servidor 120) { Ok "No ar em $Endereco" } else { Aviso "Subiu, mas demorou a responder." }
    Mostrar-Acesso
    Start-Process (Endereco-Com-Token)
    Encerrar 0
}

function Mostrar-Acesso {
    if (-not $ModoRede) { return }
    $ip = Ip-Local
    Write-Host ""
    Write-Host "  Disponivel na rede local, com token." -ForegroundColor Cyan
    if ($ip) {
        Write-Host "  De outro aparelho, abra:" -ForegroundColor Gray
        Write-Host "    http://${ip}:8000/?token=$Token" -ForegroundColor White
        Write-Host "  O token vira cookie na primeira visita; depois basta http://${ip}:8000" -ForegroundColor DarkGray
    }
    Write-Host "  Para fechar o acesso pela rede, apague o .env e rode 'iniciar.bat'." -ForegroundColor DarkGray
}

function Acao-Setup {
    Titulo "PAPAGAIO TRANSCRITOR - INSTALACAO"
    Write-Host "  Este instalador deixa tudo pronto: Docker, o motor de transcricao" -ForegroundColor Gray
    Write-Host "  offline e os modelos ja baixados. Pode levar bastante tempo na" -ForegroundColor Gray
    Write-Host "  primeira vez - nao feche esta janela." -ForegroundColor Gray
    Write-Host ""

    # ---- 1. Docker instalado? ----
    Passo "[1/6] Procurando o Docker..."
    $docker = Encontrar-Docker
    if (-not $docker) {
        Aviso "Docker nao encontrado. Instalando pelo winget..."
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Erro "O winget nao existe nesta versao do Windows."
            Aviso "Baixe o Docker Desktop em https://www.docker.com/products/docker-desktop/"
            Start-Process "https://www.docker.com/products/docker-desktop/"
            Encerrar 1
        }
        winget install --exact --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements --silent
        $docker = Encontrar-Docker
        if (-not $docker) {
            Aviso "Docker instalado, mas ainda nao visivel nesta sessao."
            Aviso "REINICIE o computador e rode este instalador de novo."
            Encerrar 0
        }
    }
    Ok "Docker encontrado em $docker"

    # ---- 2. WSL, que e o backend do Docker no Windows ----
    Passo "[2/6] Verificando o WSL (backend do Docker)..."
    $wsl = Estado-WSL
    if (-not $wsl.FeatureInstalada) {
        $mudou = Habilitar-WSL
        if ($null -eq $mudou) { Encerrar 1 }
        Write-Host ""
        Aviso "RECURSOS HABILITADOS. Agora REINICIE o computador."
        Aviso "Depois de reiniciar, rode este instalador de novo para continuar."
        Encerrar 0
    }
    if ($wsl.RebootPendente) {
        Write-Host ""
        Aviso "O WSL esta habilitado mas ainda nao ativo: falta REINICIAR o computador."
        Aviso "Reinicie e rode este instalador de novo."
        Encerrar 0
    }
    Ok "WSL habilitado e ativo"

    Passo "      Atualizando o kernel do WSL2 (rapido se ja estiver em dia)..."
    & wsl.exe --update 2>&1 | ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray }
    Ok "Kernel do WSL2 em dia"

    # ---- 3. Engine ----
    Passo "[3/6] Subindo o Docker..."
    if (-not (Engine-NoAr $docker)) {
        $app = Encontrar-DockerDesktop
        if ($app) { Start-Process $app }
        if (-not (Aguardar-Engine $docker 420)) {
            Erro "O Docker nao subiu em 7 minutos."
            Aviso "Abra o Docker Desktop na mao, aceite os termos, espere 'Engine running' e rode de novo."
            Encerrar 1
        }
    }
    Ok "Docker no ar"

    # ---- 4. GPU ----
    Passo "[4/6] Procurando placa de video NVIDIA..."
    $gpu = if ($SemGpu) { $null } else { Detectar-Gpu }
    if ($gpu) { Ok "GPU detectada: $gpu" } else { Aviso "Sem GPU NVIDIA utilizavel. Vai usar a CPU (mais lento)." }

    # ---- 5. Montar e subir ----
    Passo "[5/6] Montando a aplicacao (demorado na primeira vez)..."
    Push-Location $Raiz
    try {
        $usarGpu = ($null -ne $gpu)
        & $docker compose @(Arquivos-Compose $usarGpu) up -d --build
        if ($LASTEXITCODE -ne 0 -and $usarGpu) {
            Aviso "A montagem com GPU falhou. Refazendo sem GPU."
            $usarGpu = $false
            & $docker compose @(Arquivos-Compose $false) up -d --build
        }
        if ($LASTEXITCODE -ne 0) {
            Erro "Falha ao montar a aplicacao. Copie a mensagem acima ao pedir ajuda."
            Encerrar 1
        }
    } finally { Pop-Location }

    if (-not (Aguardar-Servidor 180)) {
        Erro "A aplicacao subiu mas nao respondeu."
        Aviso "Veja o erro com: docker compose logs"
        Encerrar 1
    }
    Ok "Aplicacao no ar"

    # ---- 6. Modelos ----
    if ($SemModelos) {
        Passo "[6/6] Download dos modelos pulado (-SemModelos)."
        Aviso "A primeira transcricao vai baixar os modelos e demorar mais."
    } else {
        Passo "[6/6] Baixando os modelos do modo local..."
        Write-Host "        Sao alguns GB, uma unica vez. Depois disso funciona sem internet." -ForegroundColor DarkGray
        Push-Location $Raiz
        try {
            & $docker compose exec -T papagaio-transcritor python warmup.py 2>&1 |
                ForEach-Object { Write-Host "        $_" -ForegroundColor DarkGray }
        } finally { Pop-Location }
    }

    # ---- pronto ----
    Titulo "PRONTO! O Papagaio esta rodando."
    Write-Host "    Endereco: $Endereco" -ForegroundColor White
    Write-Host ""
    if ($gpu) {
        Write-Host "    Usando a sua placa de video para transcrever." -ForegroundColor Gray
    } else {
        Write-Host "    Usando a CPU. Funciona, mas e mais lento que com uma GPU NVIDIA." -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "    O modo LOCAL ja esta pronto: transcreve e separa os falantes" -ForegroundColor Gray
    Write-Host "    dentro do seu computador, sem enviar nada para a internet." -ForegroundColor Gray
    Write-Host ""
    Write-Host "    Para usar o modo NUVEM (mais rapido, com resumo escrito), pegue" -ForegroundColor Gray
    Write-Host "    uma chave gratuita em https://aistudio.google.com/apikey e cole" -ForegroundColor Gray
    Write-Host "    no aplicativo." -ForegroundColor Gray
    Write-Host ""
    Write-Host "    Ligar de novo depois: duplo clique em 'iniciar.bat'" -ForegroundColor DarkGray
    Write-Host "    Desligar:             duplo clique em 'parar.bat'" -ForegroundColor DarkGray

    Mostrar-Acesso
    Start-Process (Endereco-Com-Token)
    Encerrar 0
}

switch ($Acao) {
    "setup"   { Acao-Setup }
    "iniciar" { Acao-Iniciar }
    "parar"   { Acao-Parar }
}
