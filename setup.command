#!/bin/bash
# Vertical Video Fabric — установка в один клик на macOS.
# Двойной клик в Finder: заполняет .env (с автогенерацией токена), собирает и
# запускает сервис в Docker, открывает веб-интерфейс.

set -e
cd "$(dirname "$0")"

cyan() { printf '\033[36m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

printf '\033[35m%s\033[0m\n' "Vertical Video Fabric — установка"

# --- 1. Docker ---
cyan "=== Проверяю Docker ==="
if ! docker version >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  yellow "Docker не запущен или не установлен."
  echo "  Установите Docker Desktop: https://www.docker.com/products/docker-desktop/"
  echo "  Затем ЗАПУСТИТЕ Docker Desktop (значок кита в меню-баре = Running) и снова откройте этот установщик."
  exit 1
fi
green "  Docker найден и запущен."

# --- 2. Настройки (.env) ---
cyan "=== Настройки (.env) ==="
if [ ! -f .env.example ]; then yellow "  .env.example не найден — вы точно в папке проекта?"; exit 1; fi

keep_env=0
if [ -f .env ]; then
  read -r -p "Файл .env уже есть. Перенастроить? (y = да / Enter = оставить): " ans
  case "$ans" in y|yes|д|да) ;; *) keep_env=1; green "  Оставляю текущий .env." ;; esac
fi

if [ "$keep_env" -eq 0 ]; then
  read -r -p "Вставьте Gemini API ключ (Google AI Studio). Enter — пропустить: " gemini
  read -r -p "Токен для входа. Enter — сгенерировать автоматически: " token
  if [ -z "$token" ]; then token=$(openssl rand -hex 20); green "  Сгенерирован токен входа."; fi
  read -r -p "Режим публикации: Enter = real (публикует), m = mock (тест): " modeans
  case "$modeans" in m|mock) mode="mock" ;; *) mode="real" ;; esac

  cp .env.example .env
  set_env() {
    key="$1"; val="$2"
    if grep -qE "^${key}=" .env; then
      # запятые/слэши/амперсанды в значении безопасны при разделителе |
      sed -i '' -E "s|^${key}=.*|${key}=${val}|" .env
    else
      printf '\n%s=%s\n' "$key" "$val" >> .env
    fi
  }
  set_env POSTING_AUTH_ENABLED true
  set_env POSTING_API_TOKEN "$token"
  set_env POSTING_PROVIDER_MODE "$mode"
  set_env AI_VIDEO_PROVIDER gemini
  set_env SUBTITLE_PROVIDER whisper
  set_env GEMINI_VIDEO_MODEL gemini-3.5-flash
  [ -n "$gemini" ] && set_env GEMINI_API_KEY "$gemini"

  green "  .env сохранён."
  printf "\n  Токен для входа: "; printf '\033[97m%s\033[0m\n' "$token"
  echo "  (запишите его — понадобится при входе на http://localhost:8088)"
fi

# --- 3. Сборка и запуск ---
cyan "=== Сборка и запуск ==="
read -r -p "Собрать и запустить сейчас? Первая сборка ~10-20 мин. (Enter = да / n = нет): " run
case "$run" in n|no|нет) green "  Позже: docker compose up -d --build"; exit 0 ;; esac

yellow "  Собираю образ и запускаю (можно за кофе)…"
docker compose up -d --build

green "  Сервис запущен."
sleep 2
open "http://localhost:8088" || true
green "Открыл http://localhost:8088 — войдите по токену."
