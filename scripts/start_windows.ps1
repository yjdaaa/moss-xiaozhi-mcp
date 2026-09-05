# MOSS MCP 一键启动脚本（Windows）
# Windows 一键启动：自动检测/创建 venv、安装依赖、启动激光 Web 服务与 MCP bridge
$ErrorActionPreference = "Stop"
$WorkingDirectory = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $WorkingDirectory

function Get-DotEnvValue {
    param([string]$Key, [string]$Default = "")
    $envFile = Join-Path $WorkingDirectory ".env"
    if (-not (Test-Path -LiteralPath $envFile)) { return $Default }
    foreach ($raw in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { continue }
        $name = $line.Substring(0, $eq).Trim()
        if ($name -ne $Key) { continue }
        $value = $line.Substring($eq + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value
    }
    return $Default
}

# --- 虚拟环境：检测，不存在则自动创建 ---
Write-Host "[INFO] Checking Python environment..."
$venvDir = $null
foreach ($c in @("moss_550W", "venv")) {
    if (Test-Path -LiteralPath (Join-Path $WorkingDirectory "$c\Scripts\python.exe")) {
        $venvDir = $c
        break
    }
}
if (-not $venvDir) {
    Write-Host "[INFO] No virtual environment found, creating one..."
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] Python not found. Please install Python 3.10+ and add it to PATH."
        exit 1
    }
    & python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment. Check your Python installation."
        exit 1
    }
    $venvDir = "venv"
}
$venvPython = Join-Path $WorkingDirectory "$venvDir\Scripts\python.exe"
Write-Host "[INFO] Using virtual environment: $venvDir"

# --- 依赖：检测，缺失则安装（核心 + 激光雕刻） ---
Write-Host "[INFO] Checking dependencies..."
& $venvPython -c "import mcp, pydantic, dotenv, websockets" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Installing core dependencies..."
    & $venvPython -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple/
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Core dependencies installation failed. Check your network connection."
        exit 1
    }
}
& $venvPython -c "import PIL, fontTools, numpy, cv2, serial, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Installing laser dependencies..."
    & $venvPython -m pip install -e ".[laser]" -i https://pypi.tuna.tsinghua.edu.cn/simple/
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Laser dependencies installation failed. Check your network connection."
        exit 1
    }
}

# --- 必要文件校验 ---
foreach ($f in @(".env", "moss_mcp/bridge.py", "moss_mcp/server.py", "moss_mcp/web_server.py")) {
    if (-not (Test-Path -LiteralPath (Join-Path $WorkingDirectory $f))) {
        Write-Host "[ERROR] $f not found"
        exit 1
    }
}

# --- 启动激光 Web 服务（原 start_laser_web.ps1 逻辑） ---
Write-Host "[INFO] Preparing laser web UI..."
$bindHost = Get-DotEnvValue "LASER_WEB_HOST" "127.0.0.1"
if ([string]::IsNullOrWhiteSpace($bindHost)) { $bindHost = "127.0.0.1" }
$portText = Get-DotEnvValue "LASER_WEB_PORT" "8766"
if ([string]::IsNullOrWhiteSpace($portText)) { $portText = "8766" }
$port = [int]$portText

$pids = @()
try {
    $pids += Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
} catch {}
try {
    $pids += Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*moss_mcp.web_server*" -and $_.Name -match "^python" } |
        Select-Object -ExpandProperty ProcessId
} catch {}
$pids = @($pids | Where-Object { $_ -and $_ -gt 0 } | Select-Object -Unique)
foreach ($procId in $pids) {
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "[INFO] Stopped previous laser web process: $procId"
    } catch {
        Write-Host "[WARN] Could not stop process $procId"
    }
}
if ($pids.Count -gt 0) { Start-Sleep -Milliseconds 400 }

$proc = Start-Process -FilePath $venvPython `
    -ArgumentList @("-u", "-m", "moss_mcp.web_server", "--host", $bindHost, "--port", "$port") `
    -WorkingDirectory $WorkingDirectory `
    -WindowStyle Hidden `
    -PassThru

if (-not $proc -or -not $proc.Id) {
    Write-Host "[ERROR] Failed to start laser web process."
    exit 1
}

$listenPid = $null
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Milliseconds 200
    try {
        $listenPid = @(
            Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        ) | Where-Object { $_ -and $_ -gt 0 } | Select-Object -First 1
    } catch {}
    if ($listenPid) { break }
    if ($proc.HasExited) { break }
}
$webPid = if ($listenPid) { $listenPid } else { $proc.Id }
if (-not $listenPid -and $proc.HasExited) {
    Write-Host "[ERROR] moss_mcp.web_server exited before port $port started listening."
    exit 1
}

$openHost = if ($bindHost -in @("0.0.0.0", "::", "[::]")) { "127.0.0.1" } else { $bindHost }
$webUrl = "http://${openHost}:${port}/"
Write-Host "[INFO] Starting laser web UI: $webUrl  (bind $bindHost`:$port)"
if ($bindHost -notin @("127.0.0.1", "localhost", "::1")) {
    Write-Host "[WARN] Laser web UI binds LAN address - reachable on local network; do not expose to the public internet."
}
Start-Process $webUrl

# --- 启动 MOSS MCP bridge（前台运行，退出后停止激光 Web） ---
Write-Host "[INFO] Starting MOSS MCP bridge..."
$exitCode = 0
try {
    & $venvPython -m moss_mcp.bridge moss_mcp.server
    $exitCode = $LASTEXITCODE
    if (-not $exitCode) { $exitCode = 0 }
} finally {
    Write-Host "[INFO] Stopping laser web UI..."
    Stop-Process -Id $webPid -Force -ErrorAction SilentlyContinue
}
exit $exitCode
