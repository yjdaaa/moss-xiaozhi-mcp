@echo off
chcp 65001 >nul
setlocal
title MOSS MCP + Laser Web
cd /d "%~dp0.."

where pwsh >nul 2>nul
if not errorlevel 1 (
    set "PS_EXE=pwsh"
) else (
    set "PS_EXE=powershell"
)

set "MOSS_START_NO_BROWSER=0"
set "MOSS_START_SKIP_INSTALL=0"
set "MOSS_START_HOST="
set "MOSS_START_PORT="
set "MOSS_START_VENV="

:parse_args
if "%~1"=="" goto run_powershell
if /I "%~1"=="-NoBrowser" (
    set "MOSS_START_NO_BROWSER=1"
    shift
    goto parse_args
)
if /I "%~1"=="-SkipInstall" (
    set "MOSS_START_SKIP_INSTALL=1"
    shift
    goto parse_args
)
if /I "%~1"=="-Host" (
    set "MOSS_START_HOST=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-BindHost" (
    set "MOSS_START_HOST=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-Port" (
    set "MOSS_START_PORT=%~2"
    shift
    shift
    goto parse_args
)
if /I "%~1"=="-VenvDir" (
    set "MOSS_START_VENV=%~2"
    shift
    shift
    goto parse_args
)
echo [ERROR] Unknown argument: %~1
exit /b 2

:run_powershell
set "MOSS_START_SCRIPT=%~f0"
"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$path = $env:MOSS_START_SCRIPT; $lines = Get-Content -LiteralPath $path -Encoding UTF8; $marker = [Array]::IndexOf($lines, '# POWERSHELL-BEGIN'); if ($marker -lt 0) { throw 'Embedded PowerShell section not found.' }; $script = ($lines[($marker + 1)..($lines.Length - 1)] -join [Environment]::NewLine); & ([scriptblock]::Create($script))"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [ERROR] MOSS MCP startup exited with code %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%

# POWERSHELL-BEGIN
$ErrorActionPreference = "Stop"
$WorkingDirectory = (Resolve-Path (Join-Path $env:MOSS_START_SCRIPT "..\..")).Path
Set-Location -LiteralPath $WorkingDirectory

function Get-DotEnvValue {
    param([string]$Key, [string]$Default = "")

    $envFile = Join-Path $WorkingDirectory ".env"
    if (-not (Test-Path -LiteralPath $envFile)) {
        return $Default
    }

    foreach ($raw in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }

        $separator = $line.IndexOf("=")
        if ($separator -lt 1 -or $line.Substring(0, $separator).Trim() -ne $Key) {
            continue
        }

        $value = $line.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }

    return $Default
}

function Get-VenvPythonPath {
    param([string]$Directory)

    if ([IO.Path]::IsPathRooted($Directory)) {
        return Join-Path $Directory "Scripts\python.exe"
    }
    return Join-Path $WorkingDirectory "$Directory\Scripts\python.exe"
}

function Test-VenvPython {
    param([string]$Directory)

    $pythonPath = Get-VenvPythonPath $Directory
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        return $false
    }

    try {
        & $pythonPath -c "import sys" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-ListeningProcessIds {
    param([int]$LocalPort)

    try {
        return @(
            Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        ) | Where-Object { $_ -and $_ -gt 0 }
    } catch {
        return @()
    }
}

$BindHost = $env:MOSS_START_HOST
if ([string]::IsNullOrWhiteSpace($BindHost)) {
    $BindHost = Get-DotEnvValue "LASER_WEB_HOST" "127.0.0.1"
}
if ([string]::IsNullOrWhiteSpace($BindHost)) {
    $BindHost = "127.0.0.1"
}

$Port = 0
if (-not [string]::IsNullOrWhiteSpace($env:MOSS_START_PORT)) {
    $Port = [int]$env:MOSS_START_PORT
}
if ($Port -le 0) {
    $Port = [int](Get-DotEnvValue "LASER_WEB_PORT" "8766")
}
if ($Port -lt 1 -or $Port -gt 65535) {
    Write-Host "[ERROR] Invalid Web port: $Port"
    exit 1
}

$VenvDir = $env:MOSS_START_VENV
if (-not [string]::IsNullOrWhiteSpace($VenvDir) -and -not (Test-VenvPython $VenvDir)) {
    Write-Host "[ERROR] Python environment is not usable: $VenvDir"
    exit 1
}
if ([string]::IsNullOrWhiteSpace($VenvDir)) {
    foreach ($candidate in @("moss_550W", "venv")) {
        if (Test-VenvPython $candidate) {
            $VenvDir = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($VenvDir) -or -not (Test-VenvPython $VenvDir)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] Python 3.10+ not found. Install Python and add it to PATH."
        exit 1
    }

    $VenvDir = "venv"
    Write-Host "[INFO] Creating virtual environment: $VenvDir"
    & python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment."
        exit 1
    }
}

$venvPython = Get-VenvPythonPath $VenvDir
Write-Host "[INFO] Using Python: $venvPython"

if ($env:MOSS_START_SKIP_INSTALL -ne "1") {
    & $venvPython -c "import mcp, pydantic, dotenv, websockets, PIL, fontTools, numpy, cv2, serial, requests" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] Installing Web and laser dependencies..."
        & $venvPython -m pip install -e ".[laser]"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Dependency installation failed."
            exit 1
        }
    }
}

foreach ($requiredFile in @(
    ".env",
    "moss_mcp\bridge.py",
    "moss_mcp\server.py",
    "moss_mcp\web_server.py",
    "apps\laser_web\ui\index.html"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $WorkingDirectory $requiredFile))) {
        Write-Host "[ERROR] Required file not found: $requiredFile"
        exit 1
    }
}

$occupiedPids = @(Get-ListeningProcessIds $Port)
if ($occupiedPids.Count -gt 0) {
    Write-Host "[ERROR] Port $Port is already in use by process ID(s): $($occupiedPids -join ', ')"
    Write-Host "[INFO] Use -Port to select another port, or stop the existing process."
    exit 1
}

$webProcess = Start-Process -FilePath $venvPython `
    -ArgumentList @("-u", "-m", "moss_mcp.web_server", "--host", $BindHost, "--port", "$Port") `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -PassThru

if (-not $webProcess -or -not $webProcess.Id) {
    Write-Host "[ERROR] Failed to start the Web service."
    exit 1
}

$listening = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 200
    if (Get-ListeningProcessIds $Port) {
        $listening = $true
        break
    }
    if ($webProcess.HasExited) {
        break
    }
}

if (-not $listening) {
    if (-not $webProcess.HasExited) {
        Stop-Process -Id $webProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[ERROR] Web service did not start listening on port $Port."
    exit 1
}

$openHost = if ($BindHost -in @("0.0.0.0", "::", "[::]")) { "127.0.0.1" } else { $BindHost }
$webUrl = "http://${openHost}:${Port}/"
Write-Host "[INFO] Web service started: $webUrl"
if ($BindHost -notin @("127.0.0.1", "localhost", "::1")) {
    Write-Host "[WARN] The Web service is reachable on the local network; do not expose it to the public internet."
}
if ($env:MOSS_START_NO_BROWSER -ne "1") {
    Start-Process -FilePath $webUrl
}

$exitCode = 0
try {
    Write-Host "[INFO] Starting MOSS MCP bridge..."
    & $venvPython -m moss_mcp.bridge moss_mcp.server
    $exitCode = $LASTEXITCODE
    if (-not $exitCode) {
        $exitCode = 0
    }
} finally {
    Write-Host "[INFO] Stopping laser Web service..."
    if (-not $webProcess.HasExited) {
        Stop-Process -Id $webProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
exit $exitCode
