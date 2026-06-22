# Vertical Video Fabric

AI-комбайн для автопостинга вертикальных видео в YouTube, TikTok и Instagram.

Сервис хранит аккаунты, cookies, загруженные файлы и очередь задач в Docker volume `/data`.
Публикация выполняется с сохранёнными cookies (YouTube — с авто-обновлением ротируемых
куки, Instagram — через instagrapi).

## Как установить (Docker)

Всё состояние (БД, секреты, видео, клипы, модели Whisper) живёт в одном Docker-томе —
перенос и бэкап тривиальны. GPU не нужен.

### Требования
- **Docker** + **Docker Compose v2** (Windows/macOS — Docker Desktop; Linux — docker + `docker compose`).
- **Git**.
- Интернет на время первой сборки (образ клонирует TiktokAutoUploader, ставит npm/pip-зависимости, собирает фронт).
- Диск ~3–4 ГБ под образ + место под видео; ОЗУ 4 ГБ минимум, 8+ комфортно.

### Установка

```bash
git clone https://github.com/yakoodev/vertical-video-fabric.git
cd vertical-video-fabric
cp .env.example .env        # Windows: copy .env.example .env
```

Заполни `.env` (минимум для боевого запуска):

```ini
POSTING_AUTH_ENABLED=true
POSTING_API_TOKEN=                 # свой длинный токен; пусто = сгенерится в /data/api_token.txt
POSTING_PROVIDER_MODE=real         # real = публикует; mock = ничего не шлёт (тест пайплайна)

AI_VIDEO_PROVIDER=gemini           # gemini | polza | action | mock
SUBTITLE_PROVIDER=whisper          # whisper (локально) | gemini | mock
GEMINI_API_KEY=...                 # ключ Google AI Studio
GEMINI_VIDEO_MODEL=gemini-3.5-flash
```

Полный список переменных — в [.env.example](.env.example). Запуск:

```bash
docker compose up -d --build       # --build обязателен при первом старте и после git pull
docker compose logs -f app         # логи
```

Открой **http://localhost:8088** и войди по токену. Если токен не задавал — возьми авто-сгенерированный:

```bash
docker compose exec app cat /data/api_token.txt
```

Swagger UI: `http://localhost:8088/docs`. Порт меняется в `docker-compose.yml` (`ports: - "8088:8088"`).

### Данные, бэкап, обновление

- Том `vv-fabric-data` → `/data`: `app.sqlite` (база), `secret.key` (ключ шифрования куки — **не теряй**),
  `api_token.txt`, загрузки/клипы, `whisper-models/`. Дефолтный пак (луки, стили субтитров, промпты) сеется сам.
- **Бэкап/перенос** — выгрузить/загрузить том:
  ```bash
  docker run --rm -v vv-fabric-data:/data -v "$PWD":/backup alpine tar czf /backup/vvf-data.tar.gz -C /data .
  docker run --rm -v vv-fabric-data:/data -v "$PWD":/backup alpine tar xzf /backup/vvf-data.tar.gz -C /data
  ```
- **Обновление**: `git pull && docker compose up -d --build` (миграции БД применяются автоматически).
- **Управление**: `docker compose stop|start|restart app`, `docker compose down` (том остаётся),
  `docker compose down -v` ⚠️ удалит и данные.

### Удалённый доступ
Не публикуй порт 8088 в интернет голым. Поставь reverse-proxy (Nginx Proxy Manager / Caddy / Traefik)
с HTTPS на `app:8088` и держи `POSTING_AUTH_ENABLED=true` с длинным токеном.

### Если что-то не так
- Не открывается / 502 → `docker compose logs app` (часто незаполненный `.env` или занятый порт).
- «Invalid token» → сверь токен: `docker compose exec app cat /data/api_token.txt`.
- Анализ падает с «high demand» → перегрузка Gemini на стороне Google, перезапусти (ретраи с Retry-After обычно проходят).
- Статус `needs_reauth` → куки протухли, обнови в Настройки → Аккаунты.
- Сборка падает на clone/npm/pip → нет интернета во время `--build`.

> Локальный Windows-запуск без Docker — через `scripts\start-local.ps1` (см. ниже).

В `Sources -> URL` можно добавить прямую ссылку на `mp4/mov/webm`, YouTube, Twitch или
страницу Smotvibe с таким же встроенным плеером. Для Smotvibe сервис находит HLS/MP4
внутри страницы или iframe и скачивает ролик через `yt-dlp`.

Для локального Windows-запуска без Docker используйте launcher:

```powershell
.\scripts\start-local.ps1
```

По умолчанию он запускает `http://127.0.0.1:8097`, использует `data\real-pipeline-manual`,
если такая папка уже есть, иначе `data\local-dev`, подхватывает локальный Gemini key из
`tests data\gemini api key.txt`, если переменная `GEMINI_API_KEY` не задана, и не выводит
секреты в консоль. Для фонового запуска:

```powershell
.\scripts\start-local.ps1 -Background -Open
```

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
Безопасная схема тестов на реальных файлах, аккаунтах и ключах: [docs/real-testing.md](docs/real-testing.md).

## AI video pipeline

План развития сервиса в AI-конвейер с взаимозаменяемыми Polza.ai/Gemini-провайдерами,
разбором исходных видео, таймлайном фрагментов, ffmpeg-реализацией,
karaoke-субтитрами и публикацией готовых клипов описан в
[docs/ai-video-pipeline-roadmap.md](docs/ai-video-pipeline-roadmap.md).

## Субтитры

Karaoke-субтитры рендерятся как ASS: на экране всегда видна вся фраза, а
подсветка «бежит» по активному слову. Раскладка строки фиксируется один раз на
страницу, поэтому текст не прыгает и не дёргается при появлении новых слов.
Тайминг берётся напрямую из распознавания и не растягивается нелинейно — слова
не «уезжают» от речи. Тонкая подстройка доступна в профиле субтитров:
`max_words_per_line`, `active_word_color`, `outline_width`, `timing_offset_sec`.

## Анализ под аниме и сериалы

- Пресет **Anime analysis** работает в режиме «сильные самостоятельные моменты»:
  3–5 отдельных клипов-хайлайтов (эмоции, экшен, шутки, твисты) без
  принудительного пересказа серии. Границы сегментов phrase-safe — нарезка не
  рвёт фразы посередине.
- Пресет **Series analysis** (и любой промпт со словами «Episode Story Recap»)
  работает в режиме narrative: первый клип — сжатый пересказ сюжета серии.

## Музыка и видео-фильтры

В разделе `Presets` можно:

- загрузить фоновые треки (`Music Track`) и привязать трек к ffmpeg-пресету:
  громкость, луп под длину клипа, fade in/out и авто-`ducking` под речь
  (музыка приглушается, когда говорят);
- наложить кино-`Look`: цветовые стили (`cinematic` teal/orange, `warm`, `cold`,
  `vibrant`, `noir`, `vintage`) с регулируемой силой, плюс виньетка и плёночное
  зерно.

В студии при рендере можно переопределить музыку на конкретный прогон
(чекбокс «Музыка» + выбор трека). Для API эти поля принимают
`POST /api/ffmpeg-presets`, `POST /api/audio-tracks` и параметр `music_track_id`
в запросах рендера.

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
