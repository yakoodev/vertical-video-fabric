# Запуск проекта

## Docker Compose

1. Создайте `.env` в корне проекта:

   ```powershell
   copy .env.example .env
   ```

2. Заполните минимум эти значения:

   ```env
   DATA_DIR=/data
   POSTING_AUTH_ENABLED=true
   POSTING_PROVIDER_MODE=real
   AI_VIDEO_PROVIDER=gemini
   SUBTITLE_PROVIDER=gemini
   GEMINI_API_KEY=your-google-key
   ```

   `.env` не коммитится. Держите реальные ключи только локально или в secret-хранилище деплоя.

3. Соберите и запустите контейнер:

   ```powershell
   docker compose up --build -d
   ```

4. Проверьте статус:

   ```powershell
   docker compose ps
   docker compose logs -f app
   ```

5. Откройте UI:

   ```text
   http://localhost:8088
   ```

6. Если авторизация включена, получите токен:

   ```powershell
   docker compose exec app cat /data/api_token.txt
   ```

   Вставьте токен на странице `/login`. Для API используйте header:

   ```text
   Authorization: Bearer <token>
   ```

## Перезапуск Docker

После изменения `.env` перезапустите сервис:

```powershell
docker compose up -d --force-recreate app
```

Если менялись зависимости, Dockerfile или системные пакеты:

```powershell
docker compose up --build -d
```

Остановить сервис:

```powershell
docker compose down
```

Удалять volume с данными обычно не нужно. Команда ниже удалит базу, загруженные видео и токен:

```powershell
docker compose down -v
```

## Локальный запуск без Docker

Запуск в фоне на Windows:

```powershell
.\scripts\start-local.ps1 -Background -Open
```

По умолчанию URL:

```text
http://127.0.0.1:8097
```

Если нужно явно передать Google/Gemini ключ только для текущего окна PowerShell:

```powershell
$env:GEMINI_API_KEY="your-google-key"
.\scripts\start-local.ps1 -Background -Open
```

Запуск на другом порту:

```powershell
.\scripts\start-local.ps1 -Port 8098 -Background -Open
```

## Проверки

Локально:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

В Docker:

```powershell
docker compose run --rm app pytest
```
