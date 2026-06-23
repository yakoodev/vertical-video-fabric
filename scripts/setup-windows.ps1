# Vertical Video Fabric — интерактивный установщик для Windows.
# Заполняет .env (с автогенерацией токена), собирает и запускает сервис в Docker,
# открывает веб-интерфейс. Запускается двойным кликом по setup.cmd в корне проекта.

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Title($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Ok($t)    { Write-Host "  $t" -ForegroundColor Green }
function Warn($t)  { Write-Host "  $t" -ForegroundColor Yellow }

Write-Host "Vertical Video Fabric — установка" -ForegroundColor Magenta

# --- 1. Проверка Docker ---
Title "Проверяю Docker"
try {
    docker version *> $null
    docker compose version *> $null
    Ok "Docker найден и запущен."
} catch {
    Warn "Docker не запущен или не установлен."
    Write-Host "  Установите Docker Desktop: https://www.docker.com/products/docker-desktop/"
    Write-Host "  Затем ЗАПУСТИТЕ Docker Desktop (значок кита в трее = Running) и снова запустите этот установщик."
    return
}

# --- 2. Сбор параметров ---
Title "Настройки (.env)"

if (-not (Test-Path ".env.example")) { Warn ".env.example не найден — вы точно в папке проекта?"; return }

$keepEnv = $false
if (Test-Path ".env") {
    $ans = Read-Host "Файл .env уже есть. Перенастроить? (y = да / Enter = оставить как есть)"
    if ($ans -notmatch '^(y|yes|д|да)$') { $keepEnv = $true; Ok "Оставляю текущий .env." }
}

if (-not $keepEnv) {
    $gemini = Read-Host "Вставьте Gemini API ключ (Google AI Studio). Enter — пропустить пока"
    $token  = Read-Host "Токен для входа в сервис. Enter — сгенерировать автоматически"
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = -join (1..40 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
        Ok "Сгенерирован токен входа."
    }
    $modeAns = Read-Host "Режим публикации: Enter = real (публикует), m = mock (тест без отправки)"
    $mode = if ($modeAns -match '^(m|mock)$') { "mock" } else { "real" }

    function Set-EnvVar([string]$content, [string]$key, [string]$value) {
        $safe = ($value -replace '\$', '$$$$')
        if ($content -match "(?m)^\s*$([regex]::Escape($key))=") {
            return ($content -replace "(?m)^\s*$([regex]::Escape($key))=.*$", "$key=$safe")
        }
        return ($content.TrimEnd("`n") + "`n$key=$safe`n")
    }

    $env = Get-Content ".env.example" -Raw
    $env = Set-EnvVar $env "POSTING_AUTH_ENABLED" "true"
    $env = Set-EnvVar $env "POSTING_API_TOKEN"    $token
    $env = Set-EnvVar $env "POSTING_PROVIDER_MODE" $mode
    $env = Set-EnvVar $env "AI_VIDEO_PROVIDER"    "gemini"
    $env = Set-EnvVar $env "SUBTITLE_PROVIDER"    "whisper"
    $env = Set-EnvVar $env "GEMINI_VIDEO_MODEL"   "gemini-3.5-flash"
    if (-not [string]::IsNullOrWhiteSpace($gemini)) { $env = Set-EnvVar $env "GEMINI_API_KEY" $gemini.Trim() }

    [System.IO.File]::WriteAllText((Join-Path $root ".env"), $env)
    Ok ".env сохранён."
    Write-Host "`n  Токен для входа: " -NoNewline; Write-Host $token -ForegroundColor White
    Write-Host "  (сохраните его — он понадобится при входе на http://localhost:8088)"
}

# --- 3. Сборка и запуск ---
Title "Сборка и запуск"
$run = Read-Host "Собрать и запустить сейчас? Первая сборка ~10-20 мин. (Enter = да / n = нет)"
if ($run -match '^(n|no|нет)$') { Ok "Пропускаю. Позже: docker compose up -d --build"; return }

Write-Host "  Собираю образ и запускаю (можно идти за кофе)…" -ForegroundColor Yellow
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Warn "Сборка/запуск завершились с ошибкой. Проверьте, что Docker Desktop запущен, и повторите."; return }

Ok "Сервис запущен."
Start-Sleep -Seconds 2
Start-Process "http://localhost:8088"
Write-Host "`nОткрыл http://localhost:8088 в браузере. Войдите по токену." -ForegroundColor Green
