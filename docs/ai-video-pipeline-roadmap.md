# AI Video Pipeline Roadmap

Документ фиксирует целевую архитектуру AI-конвейера для Vertical Video Fabric. Его задача -
дать следующему агенту решение без продуктовых развилок: какие сущности добавить, какие API
сделать, как связать взаимозаменяемые AI-провайдеры, ffmpeg, субтитры и уже существующий
posting в YouTube/TikTok.

## Цель

Пользователь загружает исходное видео файлом или ссылкой, сервис сохраняет его в `/data`,
отправляет на AI-разбор, показывает найденные фрагменты на таймлайне, позволяет отправить
любой фрагмент на ffmpeg-реализацию, а затем публикует готовый вертикальный ролик через
существующий cookie-based posting.

Целевой поток:

```text
source video
  -> local ingest and metadata
  -> AI video analysis through provider registry
  -> colored timeline segments
  -> ffmpeg vertical clip render
  -> optional karaoke subtitles
  -> existing YouTube/TikTok posting
```

## Текущая база

В репозитории уже есть:

- FastAPI + Jinja UI.
- SQLite store в `/data/app.sqlite`.
- Зашифрованное хранение cookies и account-level proxy.
- Очередь публикаций `jobs` / `job_targets`.
- Провайдеры публикации `youtube`, `tiktok`, `mock`.
- Docker image с `ffmpeg`, `node`, `chromium`.

Новый конвейер должен расширять эти подсистемы, а не переписывать posting. Для публикации
готового клипа нужно переиспользовать текущий provider layer.

## Область v1

В v1 поддерживаются:

- Источники: upload файла, прямой URL на `mp4`/`mov`/`webm`, YouTube URL через `yt-dlp`.
- Всегда сохраняется локальная копия исходника в `/data/sources`.
- AI-анализ: через `VideoAnalyzer` registry; начальные взаимозаменяемые providers:
  `polza`, `gemini`, `artemox`, `mock`.
- Polza.ai input:
  - локальные файлы сначала загружаются в Polza storage;
  - YouTube URL можно передавать как `video_url`, когда выбранная модель это поддерживает;
  - ответ запрашивается через OpenAI-compatible `response_format=json_schema`.
- Google Gemini API input:
  - локальные файлы загружаются через Gemini Files API;
  - YouTube URL можно передавать напрямую как video input, когда выбранная модель это поддерживает;
  - структурированный ответ запрашивается через `response_mime_type=application/json`
    и `response_json_schema`.
- Реализация клипа: ffmpeg-обрезка, вертикальный формат, аудио, webm-alpha banner,
  прожиг karaoke subtitles при выбранном subtitle profile.
- Субтитры: provider-интерфейс с начальными providers `polza`, `gemini`, `mock`.
- Ручная правка субтитров и таймкодов не входит в v1.

Не входят в v1:

- Полноценный subtitle/timeline editor с ручным drag-and-drop таймингов.
- AI-автокадрирование по лицам/объектам.
- Мультиплатформенный downloader для всех сайтов, кроме YouTube и прямых media URL.
- Генерация музыки, озвучки, B-roll и сложного AI-монтажа.

## Доменные сущности

### `sources`

Исходные видео, из которых выбираются моменты.

Обязательные поля:

- `id`
- `status`: `created`, `downloading`, `ready`, `analyzing`, `analyzed`, `failed`
- `source_type`: `upload`, `direct_url`, `youtube_url`
- `original_url`
- `original_filename`
- `local_path`
- `sha256`
- `size_bytes`
- `duration_sec`
- `width`
- `height`
- `fps`
- `metadata_json`
- `error`
- `created_at`, `updated_at`

Правила:

- `local_path` всегда указывает на файл внутри `/data/sources`.
- `duration_sec`, `width`, `height`, `fps` заполняются через `ffprobe`.
- Если скачивание или metadata extraction падает, `status=failed`, `error` содержит
  сообщение без секретов.

### `ai_analyses`

Одна попытка AI-разбора исходника.

Поля:

- `id`
- `source_id`
- `provider`: `polza`, `gemini`, `mock`
- `model`
- `prompt_version`
- `status`: `queued`, `running`, `succeeded`, `failed`
- `request_json`
- `response_json`
- `usage_json`
- `error`
- `created_at`, `updated_at`, `started_at`, `finished_at`

Правила:

- Повторный анализ создает новую запись, старые результаты не удаляются.
- `usage_json` сохраняет usage/cost из ответа AI-провайдера, если он вернулся.
- Успешный анализ создает набор `ai_segments`.

### `ai_segments`

Фрагменты, предложенные AI.

Поля:

- `id`
- `source_id`
- `analysis_id`
- `start_sec`
- `end_sec`
- `title`
- `description`
- `score`
- `category`
- `color`
- `reason`
- `status`: `candidate`, `rendering`, `rendered`, `rejected`
- `sort_order`
- `created_at`, `updated_at`

Правила валидации:

- `start_sec >= 0`.
- `end_sec > start_sec`.
- `end_sec <= sources.duration_sec`.
- Минимальная длительность по умолчанию: 5 секунд.
- Максимальная длительность по умолчанию: 180 секунд.
- `score` нормализуется в диапазон `0..1`.
- `color` должен быть безопасным CSS hex `#RRGGBB`; если модель не вернула цвет,
  сервис выбирает цвет по `category`.

Целевая JSON-схема ответа LLM:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["segments"],
  "properties": {
    "segments": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "start_sec",
          "end_sec",
          "title",
          "description",
          "score",
          "category",
          "color",
          "reason"
        ],
        "properties": {
          "start_sec": { "type": "number", "minimum": 0 },
          "end_sec": { "type": "number", "exclusiveMinimum": 0 },
          "title": { "type": "string", "minLength": 1, "maxLength": 100 },
          "description": { "type": "string", "maxLength": 500 },
          "score": { "type": "number", "minimum": 0, "maximum": 1 },
          "category": { "type": "string", "maxLength": 40 },
          "color": {
            "type": "string",
            "pattern": "^#[0-9A-Fa-f]{6}$"
          },
          "reason": { "type": "string", "maxLength": 500 }
        }
      }
    }
  }
}
```

### `ffmpeg_presets`

Настройки реализации вертикального ролика.

Поля:

- `id`
- `label`
- `description`
- `output_width`: default `1080`
- `output_height`: default `1920`
- `fps`: default `30`
- `video_codec`: default `libx264`
- `audio_codec`: default `aac`
- `video_bitrate`
- `audio_bitrate`
- `audio_mix_mode`: `primary`, `secondary`, `mix`
- `audio_primary_stream`: zero-based input audio stream, default `0`
- `audio_primary_volume`: default `1`
- `audio_secondary_stream`: zero-based input audio stream, optional
- `audio_secondary_volume`: default `1`
- `scale_mode`: `cover`, `contain`, `blur_background`
- `crop_anchor`: `center`, `top`, `bottom`
- `banner_id`
- `subtitle_profile_id`
- `extra_json`
- `created_at`, `updated_at`

Правила:

- Не хранить произвольную shell-строку как главный контракт пресета.
- `extra_json` можно использовать только для заранее поддержанных параметров.
- Команду ffmpeg собирать через список аргументов, без shell interpolation.

### `banners`

Хранилище webm-alpha overlay.

Поля:

- `id`
- `label`
- `file_path`
- `original_filename`
- `mime_type`
- `width`
- `height`
- `duration_sec`
- `position`: `top`, `center`, `bottom`, `custom`
- `x`
- `y`
- `opacity`
- `created_at`, `updated_at`

Правила:

- Файл сохраняется в `/data/banners`.
- В v1 ожидается `webm` с alpha channel.
- Если alpha channel не найден, UI должен показать предупреждение, но не блокировать
  сохранение.

### `subtitle_profiles`

Настройки субтитров и provider.

Поля:

- `id`
- `label`
- `provider`: `polza`, `gemini`, `mock`
- `model`: default `openai/gpt-4o-transcribe`
- `language`
- `font_family`
- `font_size`
- `primary_color`
- `active_word_color`
- `outline_color`
- `back_color`
- `alignment`
- `margin_v`
- `max_words_per_line`
- `uppercase`
- `created_at`, `updated_at`

Правила:

- Provider выбирается из registry, чтобы Polza.ai, Google Gemini API, локальный Whisper
  или другой STT можно было менять без изменения render workflow.
- В v1 нет ручной правки слов и таймкодов.

### `subtitle_tracks`

Сохраненный результат распознавания для конкретного клипа.

Поля:

- `id`
- `clip_id`
- `subtitle_profile_id`
- `provider`
- `model`
- `status`: `queued`, `running`, `succeeded`, `failed`
- `transcript_json`
- `ass_path`
- `usage_json`
- `error`
- `created_at`, `updated_at`

`transcript_json` должен хранить:

- `text`
- `language`
- `duration`
- `segments[]` с `start`, `end`, `text`
- `words[]` с `word`, `start`, `end`

### `clips`

Готовые вертикальные ролики, пригодные для публикации.

Поля:

- `id`
- `source_id`
- `segment_id`
- `ffmpeg_preset_id`
- `subtitle_profile_id`
- `subtitle_track_id`
- `status`: `queued`, `rendering`, `succeeded`, `failed`
- `output_path`
- `preview_path`
- `duration_sec`
- `width`
- `height`
- `size_bytes`
- `title`
- `description`
- `error`
- `created_at`, `updated_at`, `started_at`, `finished_at`

Правила:

- `output_path` хранится в `/data/clips`.
- `title` и `description` по умолчанию берутся из `ai_segments`, но могут быть
  переопределены при публикации.
- Posting job из клипа должен ссылаться на `clip_id`.

## Очереди и статусы

Рекомендуемый v1-подход: оставить текущий простой worker, но расширить его типами задач.
Если реализация начнет конкурировать за долгие задачи, следующий шаг - отдельные очереди
`analysis`, `render`, `post`.

Минимальная модель:

- `analysis_jobs`: анализ источника.
- `render_jobs`: создание клипа.
- существующие `jobs` / `job_targets`: публикация.

Каждый worker action обязан:

- ставить `running` до внешнего вызова;
- сохранять `started_at` / `finished_at`;
- писать человекочитаемый `error`;
- не логировать cookies, proxy credentials, Polza API key и Gemini API key;
- быть повторяемым через создание новой попытки, а не через перезапись старого результата.

## AI provider integrations

Env:

```env
AI_VIDEO_PROVIDER=polza
SUBTITLE_PROVIDER=polza
EXTERNAL_HTTP_RETRIES=3
EXTERNAL_HTTP_RETRY_SECONDS=2

POLZA_API_KEY=
POLZA_BASE_URL=https://polza.ai/api/v1
POLZA_VIDEO_MODEL=google/gemini-2.5-pro-preview
POLZA_TRANSCRIBE_MODEL=openai/gpt-4o-transcribe

GEMINI_API_KEY=
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_VIDEO_MODEL=gemini-3.1-flash-lite
GEMINI_TRANSCRIBE_MODEL=gemini-3.1-flash-lite

ARTEMOX_API_KEY=
ARTEMOX_BASE_URL=https://api.artemox.com/v1
ARTEMOX_VIDEO_MODEL=gemini-2.0-flash-lite
ARTEMOX_TRANSCRIBE_MODEL=gemini-2.0-flash-lite
```

Правила взаимозаменяемости:

- Вся остальная система работает с нормализованными contracts `VideoAnalyzer`
  и `SubtitleProvider`, а не с SDK конкретного поставщика.
- `AI_VIDEO_PROVIDER` выбирает provider анализа видео по умолчанию.
- `SUBTITLE_PROVIDER` выбирает provider распознавания субтитров по умолчанию.
- В UI можно переопределить provider на уровне analysis run или subtitle profile.
- Polza.ai, Artemox и Google Gemini API могут использовать похожие Gemini-модели, но это
  разные adapters: разные ключи, base URL, формат загрузки файлов и формат structured output.
- Provider обязан вернуть одну и ту же доменную форму: `AnalysisResult.segments[]`
  или `SubtitleResult.words[]/segments[]`.
- `gemini-3.1-flash-lite` выбран default, потому что `models.list` подтверждает
  `generateContent` для видео/аудио workflow. Если нужен более тяжелый профиль, можно
  переопределить `GEMINI_VIDEO_MODEL` на доступный Flash model, например
  `gemini-3.5-flash`.

AI analyzer contract:

```python
class VideoAnalyzer:
    provider: str

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        ...
```

Client contracts:

- `PolzaClient.upload_file(path, storage_policy="TEMP_UPLOAD")`
- `PolzaClient.chat_completion(payload)`
- `PolzaClient.transcribe_audio(path, model, language)`
- `GeminiClient.upload_file(path, mime_type)`
- `GeminiClient.generate_content(payload)`
- `GeminiClient.transcribe_audio(path, model, language)`
- `ArtemoxClient.chat_completion(payload)`

`PolzaVideoAnalyzer`:

- Для локального файла вызывает `/storage/upload`, получает URL.
- Для YouTube URL использует original URL как `video_url`, если модель поддерживает video input.
- Вызывает `/chat/completions`.
- Передает `response_format` с JSON Schema из этого документа.
- Парсит `choices[0].message.content`.
- Валидирует и нормализует сегменты перед записью в БД.

`GeminiVideoAnalyzer`:

- Для локального файла вызывает Gemini Files API и ждет, пока файл станет доступен модели.
- Для YouTube URL в текущей реализации также может использовать локальную копию source через
  Files API, чтобы поведение для больших файлов было единым и не зависело от публичности URL.
- Вызывает `models.generateContent`.
- Передает JSON-схему через `generationConfig.responseMimeType="application/json"`
  и `generationConfig.responseJsonSchema`.
- Парсит текст ответа как JSON.
- Валидирует и нормализует сегменты перед записью в БД.

`ArtemoxVideoAnalyzer`:

- Использует Artemox как OpenAI-compatible gateway к Gemini.
- Вызывает `POST /chat/completions` относительно `ARTEMOX_BASE_URL`.
- Передает `response_format` с JSON Schema из этого документа.
- Для v1 поддерживает URL-источники; локальный upload требует отдельного контракта загрузки
  файла в Artemox и должен завершаться понятной ошибкой.
- Парсит `choices[0].message.content` как JSON.
- Валидирует и нормализует сегменты перед записью в БД.

Базовый prompt должен просить модель найти клиповые моменты для vertical short-form:

- цепляющее начало;
- понятный контекст без длинной подготовки;
- эмоциональная реакция, конфликт, шутка, инсайт, напряжение или зрелищный момент;
- возможность смотреть фрагмент отдельно от полного видео;
- рекомендации по title/description.

Ошибки:

- 401/403 от Polza -> `failed`, `error="Polza auth failed"`.
- 401/403 от Gemini -> `failed`, `error="Gemini auth failed"`.
- Provider/model не поддерживает video input -> `failed` с понятным `error`;
  автоматический fallback на другого provider в v1 не выполняется.
- Некорректный JSON -> `failed`, сохранить сырой response в `response_json`.
- Пустой список сегментов -> `succeeded`, но UI показывает empty state.

## Source ingestion

API должен принять два режима:

- multipart upload;
- JSON body `{ "url": "..." }`.

URL validation:

- `http`/`https` only.
- Direct media URL принимается, если path заканчивается на `.mp4`, `.mov`, `.webm`
  или response `Content-Type` начинается с `video/`.
- YouTube URL определяется по host `youtube.com`, `www.youtube.com`, `youtu.be`.

Downloader:

- Direct URL скачивать через `httpx` streaming с лимитом `MAX_UPLOAD_BYTES`.
- YouTube скачивать через `yt-dlp` в `/data/sources`.
- После скачивания считать sha256 и ffprobe metadata.
- Если уже есть файл с тем же sha256, можно создать новый `source` на тот же local file
  или переиспользовать запись; для v1 допустимо хранить дубликат.

## FFmpeg render

Render input:

```json
{
  "ffmpeg_preset_id": 1,
  "subtitle_profile_id": 1
}
```

Render steps:

1. Проверить `segment`, `source`, `ffmpeg_preset`.
2. Создать `clip` со статусом `queued`.
3. Worker ставит `rendering`.
4. Обрезать исходник по `start_sec` / `end_sec`.
5. Привести к вертикальному размеру пресета.
6. Наложить banner, если указан.
6.1. Обработать аудио по preset:
   - `primary` оставляет выбранную primary-дорожку;
   - `secondary` оставляет выбранную secondary-дорожку;
   - `mix` применяет отдельные `volume` к primary/secondary и сводит их через `amix`
     в одну финальную stereo-дорожку.
7. Если указан subtitle profile:
   - извлечь аудио из клипа во временный `.m4a` или `.wav`;
   - вызвать subtitle provider;
   - сохранить `subtitle_track`;
   - сгенерировать `.ass`;
   - прожечь `.ass` в финальный файл.
8. Записать metadata финального клипа через ffprobe.
9. Поставить `clip.status=succeeded`.

Scale modes:

- `cover`: масштабировать с заполнением 9:16 и crop по `crop_anchor`.
- `contain`: вписать видео в 9:16 с черными полями.
- `blur_background`: фон из размытой копии исходника, поверх contain-видео.

В v1 нужно реализовать хотя бы `cover` и `blur_background`; `contain` простой fallback.

## Karaoke subtitles

Subtitle provider contract:

```python
class SubtitleProvider:
    provider: str

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        ...
```

`PolzaSubtitleProvider`:

- Извлекает аудио ffmpeg-ом.
- Отправляет audio transcription в Polza.
- Запрашивает `response_format=verbose_json`.
- Передает `timestamp_granularities=["word", "segment"]`.
- Сохраняет `words` и `segments`.

`GeminiSubtitleProvider`:

- Извлекает аудио ffmpeg-ом.
- Загружает аудио через Gemini Files API.
- Вызывает `models.generateContent` с prompt на транскрипцию и word-level timestamps.
- Запрашивает structured output с полями `text`, `language`, `segments[]`, `words[]`.
- Нормализует ответ в тот же `SubtitleResult`, что и Polza provider.
- Если модель вернула только segment-level timestamps без word-level timestamps, provider
  должен завершить track как `failed`, потому что karaoke highlight требует точные `words[]`.

ASS renderer:

- Работает по word-level timestamps.
- Группирует слова в строки по `max_words_per_line` и длительности.
- Активное слово выделяется `active_word_color`.
- Остальные слова используют `primary_color`.
- Обводка и тень задаются из `subtitle_profile`.
- Файл сохраняется в `/data/subtitles`.

Минимальный стиль v1:

- 2 строки максимум.
- Центр по горизонтали.
- Нижняя треть кадра.
- Жирный шрифт с outline.
- Без анимаций, кроме смены цвета активного слова.

## API

Новые публичные endpoint'ы требуют тот же Bearer/UI auth, что и текущие API.

### Sources

- `GET /sources` - UI список источников.
- `GET /sources/{source_id}` - UI детальная страница с плеером и таймлайном.
- `POST /api/sources` - создать source из multipart file или JSON URL.
- `GET /api/sources` - список.
- `GET /api/sources/{source_id}` - source + analyses + segments + clips.
- `POST /api/sources/{source_id}/analyze` - запустить анализ.

### Segments and clips

- `POST /api/segments/{segment_id}/realizations` - поставить render job.
- `GET /api/clips` - список клипов.
- `GET /api/clips/{clip_id}` - детали клипа.
- `POST /api/clips/{clip_id}/posts` - создать publication job из клипа.

### Presets, banners, subtitles

- CRUD `/api/ffmpeg-presets`.
- CRUD `/api/banners`.
- CRUD `/api/subtitle-profiles`.
- UI страницы `/presets`, `/banners`, `/subtitle-profiles`.

`POST /api/clips/{clip_id}/posts` body:

```json
{
  "title": "Final title",
  "description": "Final description",
  "targets": [1, 2],
  "privacy": "public",
  "allow_comments": true
}
```

Этот endpoint должен создать обычный posting job, где `source_path` указывает на
`clips.output_path`, а `clip_id` сохраняется для трассировки.

## UI

Navigation:

- Sources
- Clips
- Presets
- Banners
- Subtitle Profiles
- New Post
- Jobs
- Accounts
- API Docs

### Sources list

Показывает:

- ID
- filename или URL label
- status
- duration
- resolution
- analyses count
- clips count
- created date

Actions:

- upload source;
- add URL;
- open detail.

### Source detail

Состав:

- video player;
- metadata;
- кнопка `Analyze`;
- цветной timeline;
- список AI segments.

Timeline:

- Каждый `ai_segment` рисуется как абсолютный диапазон от общей длительности.
- Цвет берется из `ai_segments.color`.
- Hover/click показывает `title`, `start`, `end`, `score`, `reason`.
- Segment card содержит action `Render`.

### Clip detail/list

Показывает:

- preview video;
- source/segment;
- preset;
- subtitle status;
- render status;
- title/description;
- action `Publish`.

### Preset pages

Для v1 достаточно форм:

- label;
- output size;
- fps;
- scale mode;
- crop anchor;
- banner;
- subtitle profile;
- bitrate fields.

### Subtitle Profiles

Форма:

- label;
- provider;
- model;
- language;
- font settings;
- primary color;
- active word color;
- outline color;
- max words per line;
- uppercase.

## Implementation roadmap

### 1. Documentation and config

- Добавить этот документ.
- Добавить ссылку из README.
- Расширить `.env.example` Polza.ai и Google Gemini API параметрами.
- Добавить `yt-dlp` в Python dependencies.

Acceptance:

- Документация описывает data model, API, UI, AI provider registry, ffmpeg, subtitles.
- README содержит ссылку на roadmap.

### 2. Data foundation

- Расширить `Database.init()` новыми таблицами.
- Добавить idempotent `_ensure_column` для `jobs.clip_id`.
- Добавить store-методы для sources, analyses, segments, presets, banners, subtitle profiles, clips.
- Добавить директории `/data/sources`, `/data/clips`, `/data/banners`, `/data/subtitles`, `/data/tmp`.

Acceptance:

- Unit tests проверяют создание таблиц и CRUD.
- Старые тесты accounts/jobs проходят без изменений поведения.

### 3. Source ingestion

- Реализовать upload и URL ingestion.
- Добавить direct downloader и YouTube downloader через `yt-dlp`.
- Добавить ffprobe metadata extraction.

Acceptance:

- Маленький upload сохраняется в `/data/sources`.
- Direct URL mock скачивается streaming-ом.
- YouTube URL route вызывает downloader adapter, в тестах используется mock.

### 4. AI analysis

- Реализовать `VideoAnalyzer` registry.
- Добавить `PolzaClient`.
- Добавить `GeminiClient`.
- Добавить `PolzaVideoAnalyzer`.
- Добавить `GeminiVideoAnalyzer`.
- Добавить mock analyzer для тестов.
- Валидировать и сохранять segments.

Acceptance:

- Mock analyzer создает цветные segments.
- Некорректные таймкоды не пишутся в БД.
- Ошибка Polza или Gemini отображается в `ai_analyses.error`.

### 5. Timeline UI

- Добавить pages/templates для sources.
- Реализовать source detail с video player и timeline.
- Добавить segment cards и render action.

Acceptance:

- TestClient рендерит `/sources` и `/sources/{id}`.
- Timeline не ломается на пустом списке segments.

### 6. Presets and banners

- CRUD для ffmpeg presets.
- Загрузка banners.
- Безопасный ffmpeg command builder.

Acceptance:

- Preset создается из UI/API.
- Banner сохраняется в `/data/banners`.
- Command builder возвращает список argv, а не shell string.

### 7. Render clips

- Реализовать render job.
- Добавить ffmpeg pipeline для scale/crop/banner.
- Сохранять output и metadata.

Acceptance:

- Из тестового видео создается вертикальный mp4.
- Ошибка ffmpeg сохраняется в `clips.error`.

### 8. Subtitles service

- Реализовать subtitle provider registry.
- Реализовать Gemini subtitle provider с тем же `SubtitleResult` contract.
- Реализовать ASS karaoke renderer.
- Подключить subtitle profile к render job.
- Реализовать Polza STT provider отдельным шагом, если снова понадобится Polza как
  provider для транскрибации.

Acceptance:

- Mock transcript рендерится в ASS.
- Word-level timestamps подсвечивают активные слова.
- Финальный ffmpeg прожигает ASS в mp4.

### 9. Publishing integration

- Добавить `clip_id` в posting job.
- Реализовать `POST /api/clips/{id}/posts`.
- Переиспользовать существующий `JobWorker` и platform providers.

Acceptance:

- Готовый clip публикуется через mock provider.
- Existing `/api/posts` продолжает принимать обычный upload.

### 10. Hardening

- Добавить retries для внешних HTTP calls.
- Добавить readable errors и status polling.
- Чистить временные файлы.
- Сохранять Polza/Gemini usage/cost, если provider вернул эти данные.
- Не логировать секреты.

Текущий hardening:

- Gemini Files API использует chunked resumable upload и retry/query offset.
- Artemox gateway и direct URL downloader используют retry для transient network/5xx/429.
- `JobWorker` сохраняет exception platform provider как `job_targets.status=failed`,
  после чего job корректно завершает lifecycle вместо зависания в `running`.
- Временные audio/base render файлы чистятся после render.

Acceptance:

- Все старые и новые tests проходят.
- `docker compose run --rm app pytest` зеленый.

## Test plan

Unit:

- DB migrations.
- Store CRUD.
- URL validation.
- Segment schema validation.
- Polza/Gemini adapter payload validation.
- AI provider mock client.
- ASS renderer.
- ffmpeg command builder.

Worker:

- ingest -> analyze -> render -> post с mock providers.
- failed analysis не блокирует повторный analysis.
- failed render не создает publication job.

API/UI:

- auth на всех новых endpoint'ах.
- pages render через TestClient.
- empty states для sources, segments, clips.

Media:

- generated 5-10 second test mp4.
- optional generated webm-alpha banner fixture.
- subtitle render smoke test.

Regression:

- `tests/test_cookies.py`
- `tests/test_providers.py`
- `tests/test_store_worker.py`
- `tests/test_web_ui.py`

## Внешние ориентиры

- Polza quickstart: https://polza.ai/docs/glavnoe/quickstart
- Polza media input: https://polza.ai/docs/gaidy/media-input
- Polza storage upload: https://polza.ai/docs/api-reference/storage/upload
- Polza chat completions: https://polza.ai/docs/api-reference/chat/completions
- Polza audio transcriptions: https://polza.ai/docs/api-reference/audio/transcriptions
- Gemini video understanding: https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini structured output: https://ai.google.dev/gemini-api/docs/structured-output
- Gemini audio understanding: https://ai.google.dev/gemini-api/docs/audio
- Gemini Files API: https://ai.google.dev/gemini-api/docs/files
