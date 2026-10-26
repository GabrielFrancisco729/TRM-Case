# Quillon LeadLoop bootstrap for Windows PowerShell 5.1 or later.
# Only uses Python from a verified executable path, never assumes py -3.13 exists.
# Installs into the current user's profile when no compatible Python is found.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
$LogDir = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir 'startup_last.log'
$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$InstallTarget = Join-Path $env:LOCALAPPDATA 'Programs\QuillonLeadLoop\Python312'
$Requirements = Join-Path $Root 'requirements.txt'
$RequirementsStamp = Join-Path $Venv '.quillon_requirements.sha256'
# Run a real .py file: PowerShell 5.1 alters nested quotes passed to python -c.
$PythonCheckScript = Join-Path $Root 'verify_python_runtime.py'
$DependenciesCheckScript = Join-Path $Root 'verify_dependencies.py'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Start-Transcript -Path $LogFile -Force | Out-Null

function Test-Python([string]$Executable, [string[]]$Prefix = @()) {
    if (-not $Executable) { return $null }
    # Validate an actual runtime, not just the presence of the py.exe launcher.
    # A Python FILE avoids PowerShell 5.1 corrupting quoted code sent with -c.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        $output = & $Executable @Prefix $PythonCheckScript 2>$null
        $code = $LASTEXITCODE
        if ($code -eq 0 -and $output) {
            $resolved = (@($output) | Select-Object -Last 1).ToString().Trim()
            if ((Test-Path -LiteralPath $resolved -PathType Leaf) -and
                $resolved.EndsWith('python.exe', [StringComparison]::OrdinalIgnoreCase)) {
                return $resolved
            }
        }
    } catch {
        # E.g. the Windows app execution alias or py.exe without a runtime.
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $null
}

function Find-Python {
    $candidates = New-Object System.Collections.ArrayList
    # First inspect explicit paths. This works before PATH refresh after install.
    [void]$candidates.Add(@{ File=(Join-Path $InstallTarget 'python.exe'); Args=@() })
    $baseDirs = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'),
                  (Join-Path $env:LOCALAPPDATA 'Programs\QuillonLeadLoop'),
                  (Join-Path $env:ProgramFiles 'Python'))
    foreach ($base in $baseDirs) {
        foreach ($folder in @('Python312','Python313','Python311','Python310')) {
            [void]$candidates.Add(@{ File=(Join-Path $base "$folder\python.exe"); Args=@() })
        }
    }
    # Installed runtimes may register their path without putting python on PATH.
    foreach ($regBase in @('HKCU:\Software\Python\PythonCore',
                           'HKLM:\Software\Python\PythonCore')) {
        foreach ($version in @('3.10','3.11','3.12','3.13')) {
            $installKey = Join-Path $regBase "$version\InstallPath"
            $props = Get-ItemProperty -LiteralPath $installKey -ErrorAction SilentlyContinue
            if ($props) {
                if ($props.ExecutablePath) {
                    [void]$candidates.Add(@{ File=[string]$props.ExecutablePath; Args=@() })
                } elseif ($props.'(default)') {
                    [void]$candidates.Add(@{ File=(Join-Path $props.'(default)' 'python.exe'); Args=@() })
                } else {
                    $item = Get-Item -LiteralPath $installKey -ErrorAction SilentlyContinue
                    if ($item -and $item.GetValue('')) {
                        [void]$candidates.Add(@{ File=(Join-Path $item.GetValue('') 'python.exe'); Args=@() })
                    }
                }
            }
        }
    }
    # Search PATH next, skipping Microsoft Store placeholder executables.
    foreach ($commandName in @('python.exe','python3.exe')) {
        foreach ($command in @(Get-Command $commandName -All -ErrorAction SilentlyContinue)) {
            if ($command.Source -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\') {
                [void]$candidates.Add(@{ File=$command.Source; Args=@() })
            }
        }
    }
    # py.exe is a launcher, not necessarily a working Python runtime.
    if (Get-Command 'py.exe' -ErrorAction SilentlyContinue) {
        foreach ($version in @('3.12','3.13','3.11','3.10')) {
            [void]$candidates.Add(@{ File='py.exe'; Args=@("-$version") })
        }
    }
    foreach ($candidate in $candidates) {
        if ($candidate.File -ne 'py.exe' -and
            -not (Test-Path -LiteralPath $candidate.File -PathType Leaf)) { continue }
        $actual = Test-Python -Executable $candidate.File -Prefix $candidate.Args
        if ($actual) { return $actual }
    }
    return $null
}

function Install-Python {
    Write-Host ''
    Write-Host 'Nenhuma instalacao compativel do Python 64-bit (3.10 a 3.13) foi localizada.' -ForegroundColor Yellow
    Write-Host 'Posso instalar Python 3.12 para o seu usuario, sem privilegios de administrador.'
    Write-Host 'Depois instalarei as bibliotecas listadas em requirements.txt.'
    $answer = Read-Host 'Deseja instalar automaticamente? [S/n]'
    if ($answer -and $answer -notmatch '^(s|sim|y|yes)$') {
        throw 'Instalacao cancelada pelo usuario. Instale Python 3.12 e execute novamente.'
    }

    $wingetCommand = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($wingetCommand) {
        Write-Host '[Python] Tentando instalar com o gerenciador oficial winget...'
        # User scope avoids requiring an elevated terminal on supported Windows.
        try {
            $oldPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & $wingetCommand.Source install --id Python.Python.3.12 --exact --source winget --scope user --accept-source-agreements --accept-package-agreements --disable-interactivity
            $installCode = $LASTEXITCODE
            $ErrorActionPreference = $oldPreference
            if ($installCode -eq 0) {
                $found = Find-Python
                if ($found) { return $found }
                Write-Host 'winget concluiu, mas ainda nao foi possivel localizar Python. Tentando instalador oficial.'
            } else {
                Write-Host ("winget falhou com codigo $installCode. Tentando instalador oficial da Python Software Foundation.") -ForegroundColor Yellow
            }
        } catch {
            $ErrorActionPreference = 'Stop'
            Write-Host 'Nao foi possivel concluir a instalacao via winget. Tentando instalador oficial.' -ForegroundColor Yellow
        }
    }

    # Trusted, fixed python.org URL. Signature is checked before executing.
    $version = '3.12.10'
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'O pacote exige Windows de 64 bits. Instale em um sistema compativel.'
    }
    $installerName = "python-$version-$architecture.exe"
    $url = "https://www.python.org/ftp/python/$version/$installerName"
    $installerFile = Join-Path $env:TEMP ('quillon-' + $installerName)
    Write-Host "[Python] Baixando instalador oficial: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $installerFile -ErrorAction Stop
    $signature = Get-AuthenticodeSignature -FilePath $installerFile
    if ($signature.Status -ne 'Valid' -or
        -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'A assinatura do instalador Python nao passou na verificacao. Instalacao bloqueada.'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $InstallTarget) -Force | Out-Null
    Write-Host '[Python] Instalando para este usuario...'
    $arguments = @('/quiet', 'InstallAllUsers=0', 'Include_pip=1', 'Include_launcher=0',
                   'PrependPath=0', 'AssociateFiles=0', 'Shortcuts=0', 'Include_test=0',
                   ('TargetDir="' + $InstallTarget + '"'))
    $process = Start-Process -FilePath $installerFile -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0,3010)) { throw "Instalador Python retornou codigo $($process.ExitCode)." }
    $found = Find-Python
    if (-not $found) { throw 'Instalacao finalizada, mas Python nao foi localizado. Confira o log.' }
    return $found
}

function Invoke-NativeCommand([string]$Executable, [string[]]$Arguments, [string]$Description, [bool]$AllowFailure = $false) {
    # Windows PowerShell 5.1 raises NativeCommandError on Python stderr when
    # ErrorActionPreference is Stop. Do not let pip warnings or a traceback hide
    # the actual process exit code. Capture *both* streams in the transcript.
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $message = $_.ToString()
            if (-not [string]::IsNullOrWhiteSpace($message)) {
                Write-Host $message
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "$Description falhou (codigo $exitCode). Veja os detalhes acima e no log."
    }
    return $exitCode
}

function Run-Step([string]$Title, [string]$Python, [string[]]$Arguments) {
    Write-Host $Title -ForegroundColor Cyan
    [void](Invoke-NativeCommand -Executable $Python -Arguments $Arguments -Description $Title)
}

try {
    Write-Host '=============================================='
    Write-Host '  QUILLON LEADLOOP - INSTALADOR AUTOMATICO'
    Write-Host '=============================================='
    Write-Host "Projeto: $Root"
    Write-Host "Log: $LogFile"
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw 'requirements.txt nao encontrado. Extraia o ZIP completo antes de executar.'
    }
    if (-not (Test-Path -LiteralPath $PythonCheckScript -PathType Leaf)) {
        throw 'verify_python_runtime.py nao encontrado. Extraia o arquivo ZIP completo antes de executar.'
    }
    if (-not (Test-Path -LiteralPath $DependenciesCheckScript -PathType Leaf)) {
        throw 'verify_dependencies.py nao encontrado. Extraia o arquivo ZIP completo antes de executar.'
    }

    Write-Host ''
    Write-Host '[1/4] Conferindo Python e ambiente virtual...'
    $venvValid = Test-Python -Executable $VenvPython
    if (-not $venvValid) {
        if (Test-Path -LiteralPath $Venv) {
            Write-Host 'Ambiente virtual anterior incompleto ou incompativel. Recriando somente a pasta .venv...'
            Remove-Item -LiteralPath $Venv -Recurse -Force
        }
        Write-Host 'Procurando Python existente (incluindo versoes instaladas anteriormente)...'
        $systemPython = Find-Python
        if (-not $systemPython) { $systemPython = Install-Python }
        Write-Host "Python localizado: $systemPython"
        Run-Step -Title 'Criando ambiente virtual...' -Python $systemPython -Arguments @('-m','venv',$Venv)
    } else {
        Write-Host 'Ambiente virtual existente: OK.'
    }
    if (-not (Test-Python -Executable $VenvPython)) {
        throw 'Ambiente virtual criado, mas o executavel Python nao esta funcionando.'
    }

    Write-Host ''
    Write-Host '[2/4] Verificando pip...'
    $pipExit = Invoke-NativeCommand -Executable $VenvPython -Arguments @('-m','pip','--version') -Description 'Verificando pip' -AllowFailure $true
    if ($pipExit -ne 0) {
        Run-Step -Title 'Instalando pip no ambiente virtual...' -Python $VenvPython -Arguments @('-m','ensurepip','--upgrade')
    }

    Write-Host ''
    Write-Host '[3/4] Verificando bibliotecas de requirements.txt...'
    $requirementsHash = (Get-FileHash -LiteralPath $Requirements -Algorithm SHA256).Hash
    $previousHash = if (Test-Path -LiteralPath $RequirementsStamp) { (Get-Content -LiteralPath $RequirementsStamp -Raw).Trim() } else { '' }
    # A .py file is used instead of -c to avoid quote mangling in PS 5.1.
    $dependencyExit = Invoke-NativeCommand -Executable $VenvPython -Arguments @($DependenciesCheckScript) -Description 'Verificando dependencias' -AllowFailure $true
    $packagesReady = ($dependencyExit -eq 0)
    if (-not $packagesReady -or $requirementsHash -ne $previousHash) {
        Write-Host 'Instalando/atualizando dependencias. Esta etapa precisa de internet na primeira execucao.'
        Run-Step -Title 'Instalando packages do requirements.txt...' -Python $VenvPython -Arguments @('-m','pip','install','--disable-pip-version-check','--prefer-binary','-r',$Requirements)
        Run-Step -Title 'Conferindo conflitos nas dependencias...' -Python $VenvPython -Arguments @('-m','pip','check')
        Set-Content -LiteralPath $RequirementsStamp -Value $requirementsHash -Encoding Ascii
    } else {
        Write-Host 'Dependencias ja instaladas; nao e necessario baixar novamente.'
    }

    Write-Host ''
    Write-Host '[4/4] Iniciando o servidor...'
    Write-Host ''
    Write-Host 'Abra o navegador em: http://127.0.0.1:7860' -ForegroundColor Green
    Write-Host 'PIN de demonstracao do gestor: quillon-demo'
    Write-Host 'Deixe esta janela aberta durante o uso. Ctrl+C encerra o servidor.'
    Write-Host ''
    Run-Step -Title 'Executando LeadLoop...' -Python $VenvPython -Arguments @('-u', (Join-Path $Root 'windows_boot.py'))
    Write-Host 'Servidor encerrado normalmente.'
    exit 0
} catch {
    Write-Host ''
    Write-Host 'ERRO DURANTE A INICIALIZACAO' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ('Detalhes em: ' + $LogFile)
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
