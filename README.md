# Vertical Video Fabric

AI-комбайн для автопостинга вертикальных видео в YouTube, TikTok и Instagram.

Сервис хранит аккаунты, cookies, загруженные файлы и очередь задач в Docker volume `/data`.
Публикация выполняется с сохранёнными cookies (YouTube — с авто-обновлением ротируемых
куки, Instagram — через instagrapi).

> **Полный гайд по развёртыванию на новом ПК/сервере: [DEPLOY.md](DEPLOY.md).**

## Быстрый старт

```powershell
copy .env.example .env
docker compose up --build
```

Web UI будет доступен на `http://localhost:8088`. Swagger UI: `http://localhost:8088/docs`.
Подробная инструкция по Docker и локальному запуску: [docs/run-project.md](docs/run-project.md).

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
