# Vertical Video Fabric

Cookie-based автопостинг вертикальных видео в YouTube и TikTok.

Сервис хранит аккаунты, cookies, загруженные файлы и очередь задач в Docker volume `/data`.
Официальные API не используются. Публикация выполняется HTTP-запросами с сохраненными cookies.

## Быстрый старт

```powershell
copy .env.example .env
docker compose up --build
```

Web UI будет доступен на `http://localhost:8088`. Swagger UI: `http://localhost:8088/docs`.

Глобальный fallback proxy задается через `.env`:

```env
POSTING_PROXY_URL=http://user:pass@host:port
```

Для конкретного аккаунта proxy можно задать в `Accounts -> Publishing Proxy` или через поле
`proxy_url` в `POST /api/accounts`. Account-level proxy имеет приоритет над `POSTING_PROXY_URL`.

## Авторизация

По умолчанию сервис требует токен. Если `POSTING_API_TOKEN` не задан, первый запуск создаст
долгоживущий токен в Docker volume: `/data/api_token.txt`.

```powershell
docker compose exec app cat /data/api_token.txt
```

Для UI вставьте токен на странице `/login`. Для API используйте Bearer header:

```bash
Authorization: Bearer <token>
```

Чтобы задать токен явно, добавьте в `.env`:

```env
POSTING_API_TOKEN=change-this-token
```

## Аккаунты

Откройте `Accounts` и вставьте `Cookie` header из браузера для нужной платформы.
Cookies сохраняются зашифрованно в `/data/app.sqlite` с ключом `/data/secret.key`.

Если платформа инвалидирует cookies или попросит challenge, задача перейдет в `needs_reauth`.

Подробный гайд по импорту cookies: [docs/cookie-import.md](docs/cookie-import.md).

## AI video pipeline

План развития сервиса в AI-конвейер с взаимозаменяемыми Polza.ai/Gemini-провайдерами,
разбором исходных видео, таймлайном фрагментов, ffmpeg-реализацией,
karaoke-субтитрами и публикацией готовых клипов описан в
[docs/ai-video-pipeline-roadmap.md](docs/ai-video-pipeline-roadmap.md).

## API

```bash
curl -X POST http://localhost:8088/api/accounts \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"platform":"tiktok","label":"tt-main","proxy_url":"http://user:pass@host:port","cookie":"sessionid=...; tt-target-idc=..."}'

curl -X POST http://localhost:8088/api/posts \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/video.mp4" \
  -F "title=Title" \
  -F "description=Description" \
  -F "targets=1" \
  -F "privacy=public"
```

## Проверки

```bash
docker compose build
docker compose run --rm app pytest
```
