# Модули сервиса

Фич много, и часть из них тяжёлые (Whisper держит гигабайты памяти, автофокус и трекинг гоняют
OpenCV по кадрам, фоновые работники ходят в сеть) или платные (озвучка Polza). Модуль — именованная
часть сервиса, которую владелец включает и выключает в **«Настройки → Модули»**.

Реестр — `app/modules.py` (список `MODULES`), REST и прослойка — `app/modules_api.py`, страница —
`frontend/src/pages/settings/ModulesSettings.tsx`, тесты — `tests/test_modules.py`.

## Что делает выключение

| Где | Что происходит |
| --- | --- |
| Инструменты агентов | пропадают из MCP `tools/list`, чата (`/chat`, чат проекта) и ИИ-монтажёра в редакторе; вызов отвечает `модуль «…» выключен в настройках (Настройки → Модули)` |
| REST | адреса модуля отвечают **423** `{"detail": "...", "error": "module_disabled", "module": "<id>"}` (ASGI-прослойка `ModuleGate`) |
| Фоновые работники | останавливаются сразу; при включении стартуют снова — без перезапуска сервиса |
| Тяжёлое | точки входа сервисов зовут `modules.require(...)`: например, модель Whisper не загружается вовсе, а уже загруженная выгружается в момент выключения |
| Интерфейс | пропадают пункты меню, вкладки, панели и кнопки модуля; страница модуля по прямой ссылке показывает «Модуль выключен» со ссылкой на настройки |
| Данные | **не удаляются**: включили модуль — всё на месте |

По умолчанию **всё включено** — поведение такое же, как до появления модулей. Платное (озвучка) по-прежнему
спрашивает подтверждение внутри самого модуля и держится бюджета.

## Зависимости

Модуль работает, только если работают все его зависимости. Выключение модуля выключает и зависящие от
него (страница спрашивает подтверждение со списком), включение — включает то, без чего он не работает.

| Модуль | Зависит от |
| --- | --- |
| `voices` | `transcription` |
| `censor` | `transcription` |
| `dubbing` | `translation` |
| `autopilot` | `publishing`, `series` |
| `automation` | `ai_analysis` |
| `vod_watch` | `automation`, `downloads`, `series` |
| `ai_video` | `series` |
| `explainer` | `ai_video` |
| `stories` | `ai_video` |

## Список модулей

Значки нагрузки: CPU, RAM, сеть, платно.

### ИИ-агенты и нарезка

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `ai_analysis` | ИИ-нарезка моментов | сеть, CPU | анализ источника (Gemini / ffmpeg-детектор): `POST /api/sources/{id}/analyze`, `/api/ai-analyses/*`, чат-инструмент `analyze_source`, панель «Запустить анализ» |
| `ai_montage` | ИИ-монтаж и ✨подбор | сеть | 🤖 ИИ-монтаж клипа, пакетный монтаж, ✨ выбор лучших (`/ai-montage*`, `/ai-montage-batch`, `/ai-pick`), шаг ИИ-монтажа в «Авто» пропускается с предупреждением |
| `editor_agent` | ИИ-монтажёр в редакторе | сеть | чат 💬 в монтаже (`/api/clip-plans/{id}/agent*`), вкладка «ИИ» библиотеки |
| `agent_chat` | Чат с агентом | сеть | страница «Чат», чат проекта (`/api/chat/*`, `/api/projects/{id}/chat`) |
| `ai_video` | ИИ-видео (ролики с нуля) | сеть, платно | проект «ИИ-видео»: работник `aivideo-gen` (запуски по одобренным карточкам, опрос после перезапуска), `/api/ai-video/*`, `/api/projects/{id}/ai-video*`, 20 инструментов `aiv_*`, вкладка «ИИ-видео» проекта. Этапы refs → keyframes → voice → video (оживление планов, сцепка последним кадром); провайдеры подключаемые (`none`, `polza`). Платное — только по карточке этапа с суммой и при `AIVIDEO_ENABLED=1` ([`guides/ai-video.md`](guides/ai-video.md)) |
| `explainer` | Объяснялки (маскот объясняет тему) | сеть, платно | ролик «тема → клип»: работник `explainer` (сценарий → позы → карточка → сборка), `/api/explainer/*`, `/api/projects/{id}/explainer*`, 14 инструментов `explainer_*`, вкладка «Объяснялки» проекта «ИИ-видео». Новые позы и озвучка Polza — только по карточке (этап `explainer` машинерии ИИ-видео, потолок ролика `EXPLAINER_VIDEO_CAP_RUB`) ([`guides/explainer.md`](guides/explainer.md)) |
| `stories` | Истории (рассказчик и сцена на каждую фразу) | сеть, платно | эпизод «завязка → клип»: работник `stories` (сценарий → сцены из библиотеки → карточка → сборка), `/api/stories/*`, `/api/projects/{id}/stories*`, 14 инструментов `story_*`, вкладка «Истории» проекта «ИИ-видео». Новые сцены, листы актёров, плиты локаций и озвучка Polza — только по ОДНОЙ карточке эпизода (этап `stories` машинерии ИИ-видео, потолок `STORIES_EPISODE_CAP_RUB`, 200 ₽) ([`guides/stories.md`](guides/stories.md)) |
| `mcp` | MCP-сервер для внешних агентов | — | `/mcp` и `/api/agent/tools` (Claude Code, Codex) |

### Речь и субтитры

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `transcription` | Распознавание речи (Whisper) | CPU, RAM | загрузка модели Whisper (и выгрузка уже загруженной), расшифровка источника `/api/sources/{id}/transcript`, транскрипт в анализе, инструменты `get_source_transcript`, `listen`, `generate_subtitles`. Готовые субтитры и их правка остаются |
| `voices` | Голоса в субтитрах | сеть, CPU | разделение голосов (`/voices*`, `separate_voices`), кнопка «Голоса» и смена голоса строки |
| `translation` | Перевод субтитров | сеть | `/subtitles/translate`, `translate_subtitles`, переключатель перевода |
| `dubbing` | Озвучка RU (Gemini TTS — бесплатно, Polza TTS — платно) | сеть, платно (Polza) | `/dub`, `dub_clip`, блок «Озвучка RU» (проба голоса `tts_preview` — в ядре) |
| `censor` | Запикивание мата | CPU | `/censor`, `censor_profanity` |

### Монтаж

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `autofocus` | Автофокус и автокадрирование | CPU, RAM | `/api/segments/{id}/autofocus`, `/api/sources/{id}/autofocus`, `auto_focus`, `auto_crop_layer`, кнопки 🎯 |
| `tracking` | Трекинг объектов | CPU | `/track`, `/api/track-jobs/*`, `track_layer`, `untrack_layer`, панель трекинга слоя |
| `jump_cuts` | Джамп-каты | CPU | `/cuts/*`, `suggest_cuts`, `apply_cuts`, панель «Паузы и слова-паразиты» |
| `pngtuber` | PNGtuber-аватар | CPU | `POST /api/clip-plans/{id}/voice`, `add_pngtuber` |
| `chroma_key` | Хромакей | CPU | `/detect-key`, `/chroma-key`, `set_chroma_key`, блок «Хромакей» в раскладке |
| `end_screen` | Концовка и SVG→видео | CPU | `/api/end-screen/*`, `/end-screen`, `/api/svg-to-webm`, `set_end_screen`, `svg_to_webm` |
| `covers` | Обложки | CPU | варианты обложки (`/api/clips/{id}/covers*`), `generate_covers`, `list_covers`, `choose_cover`, `compose_cover`, блок обложек в окне публикации |
| `compilation` | Компиляции | CPU | `/compilation`, `make_compilation` |
| `promo` | Промо приложений | CPU | `build_app_promo` |

### Файлы для монтажа

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `web_images` | Картинки из интернета | сеть | поиск картинок и картинка по ссылке (`/search`, `/from-url`, `/media/asset-preview`), `search_web_images`, `add_image_from_url`; ИИ-монтаж сразу рисует, не ищет |
| `asset_ai` | ИИ для файлов монтажа | сеть | генерация картинки и ✨ автоописание файлов (`/generate`, `/describe`), `generate_image`; загрузки больше не описываются сами |

### Источники

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `downloads` | Загрузка по ссылкам (yt-dlp) | сеть | источник по ссылке, отрезок видео, музыка и звук по ссылке (`/api/sources/download`, `/api/downloads*`, `/video-from-url`, `/api/audio-tracks/from-url`), `add_source_from_url`, `add_video_from_url`, `add_music_from_url`, `add_sound_from_url`, поле ссылки в «Создать» |
| `twitch_chat` | Чат Twitch-VOD | сеть | импорт чата (и автоимпорт для Twitch-источников), `/api/sources/{id}/chat*`, `chat_activity`, `get_chat` |

### Публикация

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `publishing` | Публикация на площадки | сеть | работник выкладки `job-worker` (очередь копится, но не выкладывается), аккаунты, задания (`/api/posts`, `/api/jobs*`, `/api/accounts*`, `/api/clips/{id}/posts`), меню «Публикации» и «Аккаунты», кнопки «Опубликовать» |
| `publish_meta` | ИИ-заголовки и описания | сеть | ✨ в окне публикации, автозаполнение после рендера |
| `platform_stats` | Статистика площадок | сеть | работник `stats-collector`, `/api/stats/*`, `get_performance`, числа на «Публикациях» |
| `telegram` | Telegram-бот | сеть | карточки, сводки, ответы (long-polling в работнике автопилота), вкладка «Telegram» |

### Автоматизация

| id | Название | Нагрузка | Что выключается |
| --- | --- | --- | --- |
| `series` | Проекты-серии и каналы | — | серии: бриф, правила, гипотезы, ручные публикации (`/api/projects*`, `/api/rules*`, `/api/hypotheses/*`), 13 проектных инструментов, блок серий на «Проектах» |
| `autopilot` | Автопилот канала | сеть | работник `autopilot`, одобрение и расписание (`/api/approvals*`, `/autopilot*`, `/schedule`), `submit_for_approval`, `list_approvals`, `get_schedule`, `propose_rule`, меню «Одобрение» |
| `automation` | Авто: ссылка → клипы | CPU, сеть | «Авто» (`/api/auto/*`), меню «Авто» |
| `vod_watch` | Наблюдатель VOD | сеть, CPU | работник `vod-watch`, `/api/projects/{id}/vod-watch*`, панель наблюдателя |

Всегда включены (основа, не модули): источники и загрузка файлом, клипы и рендер, слои и камера, стиль и
правка субтитров, звук, раскладка, бренды, настройки, очередь задач, `list_modules`. Полный список
инструментов основы — `CORE_TOOLS` в `app/modules.py`.

## API

* `GET /api/modules` — группы, модули (`enabled` — работает с учётом зависимостей, `flag` — свой флаг,
  `blocked_by` — какие зависимости выключены, `locked: "env"` — задан переменной окружения) и живые
  ресурсы `resources`: память процесса (`rss_mb`), потоки, работники модулей (`running`), очередь тяжёлых
  задач (`heavy`), загруженные модели Whisper (`whisper_loaded`).
* `PUT /api/modules` `{"modules": {"<id>": true|false}, "confirm": false, "dry_run": false}`:
  * `dry_run: true` — только показать `also_off` / `also_on` (что изменится каскадом);
  * каскад без `confirm: true` → **409** `needs_confirm` со списками;
  * модуль, заданный в env → **409**;
  * **только владелец**: запрос с cookie-сессией браузера (интерфейс); Bearer-токен агентов → **403**.
    С выключенной авторизацией (`POSTING_AUTH_ENABLED=false`, dev) — можно.
* MCP-инструмент `list_modules` — то же для агентов, только чтение. Инструмента переключения нет.

## Env для установок без интерфейса

```
MODULES_DISABLED=dubbing,tracking,twitch_chat   # всегда выключены
MODULES_ENABLED=publishing                      # всегда включены (сильнее DISABLED)
MODULES_DISABLED=all                            # всё выключить, нужное — в MODULES_ENABLED
```

Заданные так модули на странице заблокированы (значок `env`). Прежние переключатели работников тоже
работают: `AUTOPILOT_WORKER=0`, `VOD_WATCH_WORKER=0`, `AIVIDEO_WORKER=0`, `STATS_INTERVAL_HOURS=0`, `AUTO_PUBLISH_META=0`,
`TWITCH_CHAT_AUTO_IMPORT=0`, `AUTOPILOT_TG_ENABLED=0`.

## Как добавить модуль или инструмент

1. Новый инструмент MCP/чата — впиши его имя в `tools` модуля или в `CORE_TOOLS`
   (`test_every_tool_belongs_to_exactly_one_module_or_core` иначе упадёт).
2. Новый модуль — `Module(...)` в `MODULES`: `id`, русское `name`, `group`, `description`, `cost`,
   `depends`, `tools`, `routes` (`"[METHOD ]/api/path/{param}[*]"`; тест сверяет каждый адрес с
   настоящими маршрутами), `ui`.
3. Фоновый работник — `modules.register_worker("<id>", "<имя>", lambda: obj)` в `main.py`; у объекта
   `start()` (повторный запуск после `stop()` должен работать) и `stop()`.
4. Тяжёлое или фоновое без REST — `modules.require("<id>", store)` в точке входа.
5. Интерфейс — `useModules().on("<id>")` / `useModuleOn` (`frontend/src/hooks/useModules.ts`),
   `<ModuleOnly id>` для панели, `<RequireModule id>` для страницы.
