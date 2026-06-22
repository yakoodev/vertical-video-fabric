# Real data testing

Не отправляйте cookies, API keys и proxy credentials в чат, issue или commit. Для реальных
проверок используйте локальный `.env`, Web UI или env-переменные тестового запуска.

## Source video smoke test

Локальный тест `tests/test_real_data_optional.py` пропускается по умолчанию. Чтобы проверить
настоящий видеофайл через `ffprobe`:

```powershell
$env:VVF_REAL_SOURCE_VIDEO="C:\path\to\sample.mp4"
.\.venv\Scripts\python.exe -m pytest tests\test_real_data_optional.py
```

## Real render smoke test

`tests/test_real_media_render_optional.py` рендерит короткий вертикальный клип из реального
видео, включает mock karaoke subtitles и проверяет сведение двух audio streams в одну stereo
дорожку через ffmpeg preset `audio_mix_mode=mix`.

```powershell
$env:VVF_RUN_REAL_MEDIA_TESTS="1"
$env:VVF_REAL_RENDER_SOURCE="C:\path\to\two-track-gameplay.mp4"
.\.venv\Scripts\python.exe -m pytest tests\test_real_media_render_optional.py
```

Если `VVF_REAL_RENDER_SOURCE` не задан, тест использует
`tests data\Apex tests video 1.mp4`.

## Artemox live smoke test

Для проверки соединения с Artemox без запуска полного видеоанализа:

```powershell
$env:ARTEMOX_API_KEY="sk-..."
$env:VVF_RUN_REAL_AI_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests\test_artemox_optional.py
```

## Gemini Files API smoke test

Прямой Gemini adapter использует Google Files API для загрузки видео в облако Google. Для
быстрой проверки можно взять `tests data\overlay.webm`; для больших файлов задайте
`VVF_GEMINI_LIVE_SOURCE`.

```powershell
$env:GEMINI_API_KEY=(Get-Content ".\tests data\gemini api key.txt" -Raw).Trim()
$env:VVF_RUN_REAL_GEMINI_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests\test_gemini_optional.py
```

Для live smoke транскрибации и karaoke subtitles:

```powershell
$env:GEMINI_API_KEY=(Get-Content ".\tests data\gemini api key.txt" -Raw).Trim()
$env:VVF_RUN_REAL_GEMINI_SUBTITLE_TESTS="1"
$env:GEMINI_TRANSCRIBE_MODEL="gemini-3.1-flash-lite"
$env:VVF_GEMINI_SUBTITLE_LIVE_SOURCE="C:\path\to\video-with-speech.mp4"
.\.venv\Scripts\python.exe -m pytest tests\test_gemini_subtitle_optional.py
```

Если `VVF_GEMINI_SUBTITLE_LIVE_SOURCE` не задан, тест попробует взять
`tests data\Apex tests video 1.mp4` и извлечь первые 12 секунд аудио.
Для другого фрагмента используйте `VVF_GEMINI_SUBTITLE_LIVE_OFFSET` и
`VVF_GEMINI_SUBTITLE_LIVE_DURATION`.

Для проверки именно большого upload в Gemini Files API без расхода токенов на анализ:

```powershell
$env:GEMINI_API_KEY=(Get-Content ".\tests data\gemini api key.txt" -Raw).Trim()
$env:VVF_RUN_REAL_GEMINI_LARGE_UPLOAD_TESTS="1"
$env:VVF_GEMINI_LARGE_SOURCE="C:\path\to\large-video.mp4"
.\.venv\Scripts\python.exe -m pytest tests\test_gemini_large_upload_optional.py
```

Если `VVF_GEMINI_LARGE_SOURCE` не задан, тест использует
`tests data\Apex tests video 1.mp4`.

## Posting checks

Для настоящей публикации нужны:

- тестовый YouTube и/или TikTok аккаунт, где безопасно публиковать пробные ролики;
- актуальные cookies из того же браузерного сеанса;
- proxy URL, если аккаунт должен работать через конкретный IP или cookies сняты через proxy;
- короткое тестовое видео `.mp4`/`.mov`/`.webm`;
- `POSTING_PROVIDER_MODE=real`.

Cookies лучше импортировать через `/accounts` или `POST /api/accounts`; сервис хранит их
зашифрованно в `/data/app.sqlite`.

## AI provider checks

Для реального AI-анализа понадобятся ключи выбранного провайдера:

- Polza.ai: `POLZA_API_KEY`, при необходимости `POLZA_BASE_URL`, `POLZA_VIDEO_MODEL`;
- Gemini: `GEMINI_API_KEY`, при необходимости `GEMINI_BASE_URL`, `GEMINI_VIDEO_MODEL`.
- Artemox: `ARTEMOX_API_KEY`, при необходимости `ARTEMOX_BASE_URL`, `ARTEMOX_VIDEO_MODEL`.

До подключения реальных adapters используйте `AI_VIDEO_PROVIDER=mock` для end-to-end проверки
контура ingestion -> analysis -> segments без внешних вызовов. Artemox adapter использует
OpenAI-compatible `POST /chat/completions`; для видеоанализа сейчас ожидается URL-источник,
потому что отдельный контракт загрузки локальных видеофайлов в Artemox не описан.
