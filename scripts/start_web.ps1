param(
    [Alias("Host")]
    [string]$BindHost = "",
    [int]$Port = 0,
    [string]$VenvDir = "",
    [switch]$NoBrowser,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$WorkingDirectory = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

function Test-VenvPython {
    param([string]$Directory)
    $pythonPath = Join-Path $WorkingDirectory "$Directory\Scripts\python.exe"
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

if ([string]::IsNullOrWhiteSpace($BindHost)) {
    $BindHost = Get-DotEnvValue "LASER_WEB_HOST" "127.0.0.1"
}
if ([string]::IsNullOrWhiteSpace($BindHost)) {
    $BindHost = "127.0.0.1"
}

if ($Port -le 0) {
    $portText = Get-DotEnvValue "LASER_WEB_PORT" "8766"
    if ([string]::IsNullOrWhiteSpace($portText)) {
        $portText = "8766"
    }
    $Port = [int]$portText
}

if ($Port -lt 1 -or $Port -gt 65535) {
    Write-Host "[ERROR] Invalid Web port: $Port"
    exit 1
}

if (-not [string]::IsNullOrWhiteSpace($VenvDir) -and -not (Test-VenvPython $VenvDir)) {
    Write-Host "[ERROR] Python environment is not usable: $VenvDir"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($VenvDir)) {
    foreach ($candidate in @("venv", "moss_550W")) {
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

    $VenvDir = if ([string]::IsNullOrWhiteSpace($VenvDir)) { "venv" } else { $VenvDir }
    Write-Host "[INFO] Creating virtual environment: $VenvDir"
    & python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment."
        exit 1
    }
}

$venvPython = Join-Path $WorkingDirectory "$VenvDir\Scripts\python.exe"
Write-Host "[INFO] Using Python: $venvPython"

if (-not $SkipInstall) {
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

$serverProcess = Start-Process -FilePath $venvPython `
    -ArgumentList @("-u", "-m", "moss_mcp.web_server", "--host", $BindHost, "--port", "$Port") `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -PassThru

if (-not $serverProcess -or -not $serverProcess.Id) {
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
    if ($serverProcess.HasExited) {
        break
    }
}

if (-not $listening) {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
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

try {
    if (-not $NoBrowser) {
        Start-Process -FilePath $webUrl
    }
    Write-Host "[INFO] Press Ctrl+C to stop the Web service."
    Wait-Process -Id $serverProcess.Id
    if ($serverProcess.ExitCode -ne 0) {
        Write-Host "[ERROR] Web service exited with code $($serverProcess.ExitCode)."
        exit $serverProcess.ExitCode
    }
} finally {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
