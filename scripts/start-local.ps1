[CmdletBinding()]
param(
    [int]$Port = $(if ($env:VVF_PORT) { [int]$env:VVF_PORT } else { 8097 }),
    [int]$FrontendPort = $(if ($env:VVF_FRONTEND_PORT) { [int]$env:VVF_FRONTEND_PORT } else { 5173 }),
    [string]$HostAddress = "127.0.0.1",
    [string]$DataDir = "",
    [ValidateSet("gemini", "polza", "artemox", "mock")]
    [string]$AiProvider = "gemini",
    [ValidateSet("gemini", "polza", "artemox", "mock")]
    [string]$SubtitleProvider = "gemini",
    [switch]$RequireAuth,
    [switch]$MockPosting,
    [switch]$NoInstall,
    [switch]$KeepExisting,
    [switch]$Background,
    [switch]$Open,
    [switch]$BuildFrontend,
    [switch]$NoFrontend,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $key, $value = $trimmed.Split("=", 2)
        $key = $key.Trim()
        if (-not $key) {
            continue
        }
        if ([Environment]::GetEnvironmentVariable($key, "Process")) {
            continue
        }
        $value = $value.Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Resolve-LocalPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Path))
}

function Find-Python {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    if ($NoInstall) {
        throw "Python venv not found at .venv\Scripts\python.exe and -NoInstall was supplied."
    }
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & py -3 -m venv (Join-Path $RepoRoot ".venv")
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Install Python or create .venv manually."
        }
        & python -m venv (Join-Path $RepoRoot ".venv")
    }
    if (-not (Test-Path $venvPython)) {
        throw "Failed to create Python venv."
    }
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r (Join-Path $RepoRoot "requirements.txt")
    return $venvPython
}

function Ensure-NodeDeps {
    if ($NoInstall) {
        return
    }
    if ((Test-Path (Join-Path $RepoRoot "package.json")) -and -not (Test-Path (Join-Path $RepoRoot "node_modules"))) {
        & npm install
    }
    if ($env:TIKTOK_VENDOR_ROOT) {
        $signatureDir = Join-Path $env:TIKTOK_VENDOR_ROOT "tiktok_uploader\tiktok-signature"
        if ((Test-Path (Join-Path $signatureDir "package.json")) -and -not (Test-Path (Join-Path $signatureDir "node_modules"))) {
            Push-Location $signatureDir
            try {
                & npm install
            } finally {
                Pop-Location
            }
        }
    }
}

function Ensure-FrontendDeps {
    if ($NoInstall) {
        return
    }
    $frontendDir = Join-Path $RepoRoot "frontend"
    if ((Test-Path (Join-Path $frontendDir "package.json")) -and -not (Test-Path (Join-Path $frontendDir "node_modules"))) {
        Push-Location $frontendDir
        try {
            & npm install
        } finally {
            Pop-Location
        }
    }
}

function Build-Frontend {
    $frontendDir = Join-Path $RepoRoot "frontend"
    Push-Location $frontendDir
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed (npm run build exit $LASTEXITCODE)."
        }
    } finally {
        Pop-Location
    }
}

function Set-TiktokVendorRoot {
    if ($env:TIKTOK_VENDOR_ROOT -and (Test-Path (Join-Path $env:TIKTOK_VENDOR_ROOT "tiktok_uploader\tiktok-signature\browser.js"))) {
        return
    }
    $candidates = @(
        (Join-Path $RepoRoot "..\video-agent-starter\tmp\ext-repos\makiisthenes-TiktokAutoUploader"),
        (Join-Path $RepoRoot "..\TiktokAutoUploader"),
        (Join-Path $RepoRoot "vendor\TiktokAutoUploader")
    )
    foreach ($candidate in $candidates) {
        $full = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path (Join-Path $full "tiktok_uploader\tiktok-signature\browser.js")) {
            $env:TIKTOK_VENDOR_ROOT = $full
            return
        }
    }
}

function Stop-PortListener {
    param([int]$LocalPort)
    $listeners = Get-NetTCPConnection -LocalAddress $HostAddress -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        return
    }
    if ($KeepExisting) {
        throw "Port $LocalPort is already in use. Re-run without -KeepExisting to stop the listener first."
    }
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Force
    }
    Start-Sleep -Seconds 2
}

Import-DotEnv (Join-Path $RepoRoot ".env")

if (-not $DataDir) {
    $realPipelineDir = Join-Path $RepoRoot "data\real-pipeline-manual"
    if (Test-Path $realPipelineDir) {
        $DataDir = $realPipelineDir
    } else {
        $DataDir = Join-Path $RepoRoot "data\local-dev"
    }
}
$DataDir = Resolve-LocalPath $DataDir
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "logs") | Out-Null

$env:DATA_DIR = $DataDir
$env:POSTING_AUTH_ENABLED = if ($RequireAuth) { "true" } else { "false" }
$env:POSTING_PROVIDER_MODE = if ($MockPosting) { "mock" } else { "real" }
$env:AI_VIDEO_PROVIDER = $AiProvider
$env:SUBTITLE_PROVIDER = $SubtitleProvider
$env:GEMINI_VIDEO_MODEL = if ($env:GEMINI_VIDEO_MODEL) { $env:GEMINI_VIDEO_MODEL } else { "gemini-3.1-flash-lite" }
$env:GEMINI_TRANSCRIBE_MODEL = if ($env:GEMINI_TRANSCRIBE_MODEL) { $env:GEMINI_TRANSCRIBE_MODEL } else { "gemini-3.1-flash-lite" }
$env:WORKER_POLL_SECONDS = if ($env:WORKER_POLL_SECONDS) { $env:WORKER_POLL_SECONDS } else { "1" }

$localGeminiKeyPath = Join-Path $RepoRoot "tests data\gemini api key.txt"
if (-not $env:GEMINI_API_KEY -and (Test-Path $localGeminiKeyPath)) {
    $env:GEMINI_API_KEY = (Get-Content -Raw $localGeminiKeyPath).Trim()
}

Set-TiktokVendorRoot

$python = Find-Python
Ensure-NodeDeps

# Frontend (React/Vite). Default dev flow runs the Vite dev server with HMR and
# proxies the API to uvicorn. -BuildFrontend produces a static build served by
# uvicorn directly. -NoFrontend skips the SPA entirely (API only).
$useViteDev = (-not $BuildFrontend) -and (-not $NoFrontend)
if (-not $NoFrontend) {
    Ensure-FrontendDeps
}
if ($BuildFrontend) {
    Build-Frontend
}

if (-not $env:GEMINI_API_KEY -and $AiProvider -eq "gemini") {
    Write-Warning "GEMINI_API_KEY is not set. Gemini analysis/upload endpoints will fail until it is configured."
}
if (-not $env:TIKTOK_VENDOR_ROOT -and -not $MockPosting) {
    Write-Warning "TIKTOK_VENDOR_ROOT was not found. TikTok real publishing will fail until the vendor helper is configured."
}

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", "$Port")
$url = "http://${HostAddress}:$Port"
$frontendUrl = "http://${HostAddress}:$FrontendPort"
$frontendMode = if ($useViteDev) { "vite-dev" } elseif ($BuildFrontend) { "static-build" } else { "none" }
$openUrl = if ($useViteDev) { $frontendUrl } else { $url }

$config = [PSCustomObject]@{
    Url = $url
    OpenUrl = $openUrl
    FrontendMode = $frontendMode
    FrontendUrl = if ($useViteDev) { $frontendUrl } else { "(served by uvicorn)" }
    DataDir = $env:DATA_DIR
    AuthEnabled = $env:POSTING_AUTH_ENABLED
    PostingMode = $env:POSTING_PROVIDER_MODE
    AiProvider = $env:AI_VIDEO_PROVIDER
    SubtitleProvider = $env:SUBTITLE_PROVIDER
    GeminiKeyConfigured = [bool]$env:GEMINI_API_KEY
    TiktokVendorRootConfigured = [bool]$env:TIKTOK_VENDOR_ROOT
    Python = $python
    Background = [bool]$Background
}

if ($DryRun) {
    $config | Format-List
    Write-Host "Command: $python $($uvicornArgs -join ' ')"
    if ($useViteDev) {
        Write-Host "Frontend: npm run dev (cwd frontend) -> $frontendUrl (VVF_BACKEND=$url)"
    }
    exit 0
}

Stop-PortListener -LocalPort $Port

# In dev mode launch the Vite dev server (its own window) pointed at this backend.
if ($useViteDev) {
    Stop-PortListener -LocalPort $FrontendPort
    $env:VVF_BACKEND = $url
    $frontendDir = Join-Path $RepoRoot "frontend"
    Start-Process -FilePath "npm" -ArgumentList @("run", "dev", "--", "--port", "$FrontendPort") -WorkingDirectory $frontendDir | Out-Null
    Write-Host "Vite dev server: $frontendUrl (proxying API to $url)"
}

if ($Background) {
    $stdout = Join-Path $DataDir "logs\uvicorn-$Port.out.log"
    $stderr = Join-Path $DataDir "logs\uvicorn-$Port.err.log"
    $process = Start-Process -FilePath $python -ArgumentList $uvicornArgs -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Start-Sleep -Seconds 4
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/accounts" -TimeoutSec 10
        Write-Host "Started PID $($process.Id) at $url (HTTP $($response.StatusCode))."
    } catch {
        Write-Warning "Process PID $($process.Id) started, but health check failed: $($_.Exception.Message)"
        Write-Warning "Logs: $stdout ; $stderr"
    }
    if ($Open) {
        Start-Process $openUrl
    }
    exit 0
}

Write-Host "Starting Vertical Video Fabric API at $url"
if ($useViteDev) {
    Write-Host "Open the app at $frontendUrl"
}
Write-Host "DataDir: $DataDir"
if ($Open) {
    Start-Process $openUrl
}
& $python @uvicornArgs
