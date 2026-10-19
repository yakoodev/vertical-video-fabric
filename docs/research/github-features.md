# Что украсть у open-source: клипперы, субтитры, редакторы, агенты

Исследование от 2026-10-19. Звёзды и активность взяты из GitHub API в тот же день (★ округлены).
Фичи взяты из README и документации проектов. Код не запускали. Цель — найти **конкретные**
вещи, которых у нас нет или которые сделаны слабее.

**Что у нас уже есть (не предлагаем повторно):** Gemini/Polza/action-анализ + Whisper-транскрипт;
✨ ИИ-выбор лучших со score; cut_refine и scene_cuts; autofocus/VLM-фокус с точками; blur_background;
karaoke ASS; луки; переходы xfade + sfx; мем-вставки; моушн-слои с ключами и камерой; ИИ-монтажёр
с инструментами; MCP (`view_frame`, слои, субтитры, вставки); OTIO/Kdenlive; render QC (тишина, LUFS);
публикация в YT/TikTok/IG; Авто с разнесением публикаций; паки Twemoji, OpenMoji, Kenney.

**Чего нет (по grep):** сигналы Twitch-чата и YouTube heatmap, нормализация громкости при рендере
(QC её только измеряет), LUT, диаризация и активный спикер, раскладки facecam/split, удаление
тишины и слов-паразитов внутри клипа, перевод и дубляж, слежение за каналом, обратный сбор
статистики с платформ.

---

## 1. Обзор проектов

### 1.1 Open-source аналоги OpusClip, Klap и Vizard

| Проект | ★ / активность | Лицензия | Что взять (у нас нет) | Сложность |
|---|---|---|---|---|
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | 6.8k, активен | MIT (кроме `cloud/`) | Раскладки на каждую сцену: **TRACK / GENERAL (blur) / SPLIT (2 спикера друг над другом, субтитры на стыке) / SCREENCAST**, раскладку выбирает Gemini; MCP `recut_clip` / `add_subtitles` / `publish_clip`; webhooks с подписью HMAC-SHA256; API-ключи; Agent Skill и n8n workflow; 10 вариантов заголовка с чатом доработки; описание с главами из транскрипта | M |
| [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) | 1.5k, активен | AGPL-3.0 | 4 оценки клипа (hook, engagement, value, shareability); **просмотры и лайки с платформ влияют на будущий выбор**; хук-заголовок сверху в первые секунды; b-roll из Pexels | M |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | 5.5k, активен | MIT | Сначала классифицирует тип контента (подкаст, влог, туториал) и плотность, потом подстраивает промпт; таксономия «hook / emotional peak / opinion bomb / revelation / conflict / quotable / story peak / practical value»; чанки по 20 мин с перекрытием 60 с; склейка кандидатов при перекрытии >50% | S |
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) | 350, активен | **AGPL-3.0** | Лучший источник идей для стримов, см. ниже | — |
| [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) (Clips Kitty) | 120, активен | AGPL (Plugin SDK под MIT) | Слияние сигналов (транскрипт, возбуждение в аудио, движение, реакции), **нормализация внутри каждого видео**; бонус с ограничением от **Twitch-чата и YouTube «самые пересматриваемые»**; финалистов LLM сравнивает попарно; Creator Profile (повторяющиеся темы и шутки канала); TalkNet active-speaker «режет, а не панорамирует»; режим Gaming/Reaction (вебка + игра); NL-строка «Ask» превращает фразу в задачу | M |
| [fralapo/clippyme](https://github.com/fralapo/clippyme) | 60, активен | MIT | **Монитор каналов Kick/Twitch/YouTube**: сам режет и публикует лучшее; удаление тишины и слов-паразитов; «вырежи интро» обычным текстом; публикация в прайм-тайм с учётом лимитов; разбиение строк субтитров перенесено из VideoLingo | M |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | 185 | MIT | **LLM возвращает индексы слов, а не таймкоды**; кадрирование по шотам, без интерполяции через склейку; 4 пресета libass: `bold_pop`, `karaoke_fill`, `clean_lower`, `boxed`; выход −14 LUFS; падение пайплайна → продолжение с упавшего шага | S |
| [mutonby/shortcast](https://github.com/mutonby/shortcast) | 390 | Apache-2.0 | **Подписи под каждую площадку**: TikTok коротко и хлёстко, IG сторителлинг + 20–30 хэштегов, YT заголовок под поиск; копирует голос автора по примерам подписей; сетка «телефонов» с одобрением; TikTok публикуется как черновик | S |
| [RafaelGodoyEbert/ViralCutter](https://github.com/RafaelGodoyEbert/ViralCutter) | 430 | GPL-3.0 | Split-screen для 2 лиц; experimental active speaker; перевод субтитров на 10+ языков; экспорт Premiere XML | M |
| [divyaprakash0426/autoshorts](https://github.com/divyaprakash0426/autoshorts) | 200 | Apache-2.0 | 15 пресетов контента, у каждого свои категории моментов, стиль субтитров и голос; `CAPTION_STYLE=auto` выбирает стиль под момент; TTS-нарратив поверх геймплея (Qwen3-TTS) | M |
| [JayWebtech/autoshorts](https://github.com/JayWebtech/autoshorts) | 1.3k | не указана | Мало нового: десктоп и онбординг-визард | — |
| [andriidrok1/autobroll](https://github.com/andriidrok1/autobroll) | 20, свежий | MIT | **Gemini выделяет 1–2 ключевых слова золотом, а не каждое слово**; **по кадру сдвигает субтитры подальше от лица**; b-roll по транскрипту (сначала свои файлы, потом Pexels); группировка дублей; MCP на 24 инструмента; повторная генерация сохраняет ручные правки | S–M |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 550, заброшен с 2024 | MIT | Ресайз с диаризацией (pyannote) и переключением на говорящего | — |
| [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT), [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 8k / 133k | MIT | Генераторы «из текста», не клипперы. Для нас интересны только стоковый b-roll по ключевым словам и TTS | — |

#### hotclip: идеи для нарезки стримов (AGPL, поэтому берём только идеи, код не копируем)

- **Плотность чата (danmaku)**: скользящее окно; вес у «энергичных» ключевых слов; донаты, подписки, фолловы и лайки весят больше; **лимит на одного пользователя** против спама; бонус за резкий всплеск.
- Аудио и видео: пики громкости, частота смены шотов, пики движения, смех и аплодисменты, пики выражения лица (YuNet + FER+). **Сигналы засчитываются, только если совпадают**: одна громкость может оказаться просто музыкой.
- Второй проход LLM ставит оценки hook / structure / value / topicality и помечает слабые клипы «не рекомендуется». Импорт CSV-аналитики площадок учит, какие темы, хуки и длины заходят.
- Кадрирование: держит кадр неподвижным, пока лица помещаются; плавно ведёт, только когда нужно; возвращает в центр через ~1.25 с после потери лица. `clips.json` записывает, почему кадр такой. Автоматически убирает интерфейс мобильного стрима и чёрные поля. TransNetV2 + отсутствие разрезов посреди слова.
- Таймлайн показывает **кривые громкости, тепла чата и движения**, кандидаты стоят на пиках. Хоткеи Space/K, J/L ±5 с, I/O, `[`/`]` между кандидатами. Undo на 60 шагов сохраняется в проекте.
- Хук крупно на первые 2 с; 8 «углов» хука и 5 типов CTA; двуязычные субтитры; цвета спикеров; оверлей зон интерфейса для 9 платформ; словарь «горячих слов» для исправления транскрипта.
- Экспорт: −14 LUFS; обложка, выбранная по качеству кадра; **платформенные папки** (обложка, перекадрированная под соотношение площадки, текст, обрезанный под её лимиты, manifest); **2–3 различающиеся версии клипа** для мультиаккаунтов; компиляция с главами; черновик CapCut и EDL CMX3600; линт запрещённых слов; NVENC/QSV/VideoToolbox.
- Наблюдение за папкой OBS/рекордера: ждёт, пока размер файла стабилизируется, затем ставит задачу. Webhook blrec.

### 1.2 Twitch, чат и поиск хайлайтов

| Проект | ★ | Лицензия | Что взять | Сложность |
|---|---|---|---|---|
| [lay295/TwitchDownloader](https://github.com/lay295/TwitchDownloader) | 4k, активен | MIT | CLI `chatdownload` (JSON, `-E` встраивает эмоуты BTTV/FFZ/7TV) и **`chatrender -b -e --generate-mask`**: рендер чата отрезка с альфа-маской → слой «чат» в клипе; `--ban-words`, `--ignore-users` (боты), `--highlight-users`, `--readable-colors`, `--outline` | M |
| [xurei/twitch-highlights-logger](https://github.com/xurei/twitch-highlights-logger) | 35 | GPL-3.0 | Считает в окне сообщения, подходящие под фильтр (например «KEKW|LUL»), и отмечает окно при превышении порога. Простейшая база | S |
| [jekku0966/HighlightMiner](https://github.com/jekku0966/HighlightMiner) | новый | MIT | Слияние аудио-онсетов, фраз-реакций в транскрипте и всплесков чата с весами из `settings.json`; `merge_gap_sec`; пре- и пост-ролл; парсеры чата JSON/JSONL/CSV | S |
| [teseo/twitch-toolkit](https://github.com/teseo/twitch-toolkit) | 2 | MIT | **Распознаёт музыку (ACRCloud) и пропускает её**, чтобы не получить DMCA в клипе; LLM оценивает 2-минутные чанки по шкале 1–10 | S |
| [artkulak/twitch-stream-highlights-detection](https://github.com/artkulak/twitch-stream-highlights-detection) | 60, старый | Unlicense | ML по чату в реальном времени | — |

### 1.3 Субтитры

| Проект | ★ | Лицензия | Что взять | Сложность |
|---|---|---|---|---|
| [francozanardi/pycaps](https://github.com/francozanardi/pycaps) | 230, активен | MIT | Стили на CSS с классами состояния слова и строки (past/current/future), шаблон = CSS + правила в одном `pycaps.template.json`; **теги слов через regex / список / AI → эмодзи, звуковой эффект, свой цвет**; анимации fade, pop, slide, typewriter | M |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | 185 | MIT | Пресеты libass `bold_pop` (текущее слово растёт и становится жёлтым), `boxed` (плашка). Подходит нашему ASS-движку | S |
| [sebetancurch/auto-caption](https://github.com/sebetancurch/auto-caption) | новый | MIT, Anton OFL | ASS: пословный `pop` (вспышка цвета + скейл) и плавная заливка `karaoke`; **дополнительный экспорт `.davinci.srt`** с `<font color>` на активном слове | S |
| [unconv/captacity](https://github.com/unconv/captacity) | 140 | MIT | Параметры: stroke, shadow strength/blur, highlight color, line_count, padding. Минимальная, но удобная схема | S |
| [remotion-dev/template-tiktok](https://github.com/remotion-dev/template-tiktok) | 290 | Remotion License | `createTikTokStyleCaptions({combineTokensWithinMilliseconds})` группирует слова в «страницы» по времени. Идею берём, код нет | S |
| [Huanshere/VideoLingo](https://github.com/Huanshere/VideoLingo) | 18.8k | Apache-2.0 | Резка строк по стандарту Netflix (NLP + LLM), перевод с выравниванием, дубляж | M |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 24.7k | BSD-2 | Диаризация (pyannote) + выравнивание слов → цвета спикеров, активный спикер | M |
| [jianfch/stable-ts](https://github.com/jianfch/stable-ts) | 2.3k | MIT | Forced alignment правленого текста на аудио: после ручной правки субтитров тайминг слов не ломается | S |
| [linto-ai/whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) | 2.9k | **AGPL** | Уверенность распознавания по словам. Только идея | — |

### 1.4 Браузерные редакторы и движки моушна

| Проект | ★ | Лицензия | Что взять | Сложность |
|---|---|---|---|---|
| [ronak-create/FableCut](https://github.com/ronak-create/FableCut) | 730, активен | **MIT** | Ближе всех к нам: редактор, которым управляет агент. **Счётчик `revision` → 409 при конфликте записи агента**; live-reload UI через SSE; MCP `fablecut_frame` (кадр / **контактный лист** / собранный таймлайн), `fablecut_analyze_reference` (шоты, биты, BPM, энергия, дроп **референсного ролика**), `auto_duck`, `normalize_audio`, `track` (motion tracking), `docs` (раздел мануала по запросу); 17 переходов, 33 пресета анимаций, маски, speed ramps с ремапом звука; проверка готового файла чтением | S–M |
| [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) | 95k | MIT | Сейчас переписывается на Rust-ядро; MCP и headless только в планах. Брать нечего, следить | — |
| [omni-media/omniclip](https://github.com/omni-media/omniclip) | 1.5k | MIT | Экспорт на WebCodecs до 4K, совместная работа через WebRTC | L |
| [openvideodev/react-video-editor](https://github.com/openvideodev/react-video-editor) (бывший designcombo) | 1.8k | **двойная, платная для компаний >3 чел.** | Шейдерные переходы, экспорт в браузере через WebCodecs | — |
| [ncounterspecialist/twick](https://github.com/ncounterspecialist/twick) | 540 | Sustainable Use | WebGL-эффекты на элемент (vignette, pixelate, warp), `@twick/mcp-agent` | — |
| [motion-canvas/motion-canvas](https://github.com/motion-canvas/motion-canvas) | 19k | MIT | Генераторы анимаций, эффект «пишущегося» текста и кода, signals | M |
| [midrender/revideo](https://github.com/midrender/revideo) (бывший redotvideo) | 4.1k | MIT | Форк Motion Canvas с headless-рендером и шаблонами через API | M |
| [mifi/editly](https://github.com/mifi/editly) | 5.5k | MIT | Декларативный JSON-монтаж в Node + **gl-transitions** через headless GL | S |
| [diffusionstudio/core](https://github.com/diffusionstudio/core) | 1.2k | MPL-2.0 | Композитинг на WebCodecs | L |
| [theatre-js/theatre](https://github.com/theatre-js/theatre) | 12.8k | core Apache-2.0, studio AGPL | Редактор кривых и ключей (graph editor) как UX-образец | — |

### 1.5 Агенты, тишина, звук, кадрирование

| Проект | ★ | Лицензия | Что взять | Сложность |
|---|---|---|---|---|
| [WhiteTowerAI/cut-as-code](https://github.com/WhiteTowerAI/cut-as-code) | новый | MIT | Набор Agent Skills (`/video-cut`, `/video-color-grade` → **генерирует и запекает LUT**, `/video-add-content-cards`: lower thirds, статистика, цитаты, CTA; `/video-edit-compare`); решения агента лежат файлами, человек их одобряет; ревизии против устаревшего рендера | S–M |
| [video-db/Director](https://github.com/video-db/Director) | 1.6k | MIT | Цепочки агентов: поиск момента по смыслу («самая смешная сцена») → клип → публикация, с прогрессом в чате | M |
| [gyoridavid/short-video-maker](https://github.com/gyoridavid/short-video-maker) | 1.4k | MIT | **Теги настроения музыки** (sad, chill, euphoric, dark, funny…); уровень громкости музыки low/med/high | S |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 5.6k | Unlicense | `--edit audio:threshold` / `motion:` / логика через and/or; `--margin 0.2s`; `--smooth mincut,minclip`; `--when-inactive speed:N` (ускорять паузы, а не вырезать); `--transition dissolve`; экспорт Premiere / Resolve / FCP / Shotcut / Kdenlive | S |
| [bugbakery/audapolis](https://github.com/bugbakery/audapolis) | 1.9k | AGPL | Монтаж через текст: удалил слово → вырезал звук. Только идея | — |
| [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) AutoFlip (legacy) | 37k | Apache-2.0 | Для каждого шота решает «стоять / вести / панорамировать»; учитывает значимость сигналов (лица > люди > текст) | — |
| [TaoRuijie/TalkNet-ASD](https://github.com/TaoRuijie/TalkNet-ASD) | 510 | MIT | Активный спикер по аудио и видео | M |
| [CPJKU/beat_this](https://github.com/CPJKU/beat_this) | 430 | MIT | Детекция битов и даунбитов, сильнее librosa | S |
| [jrgillick/laughter-detection](https://github.com/jrgillick/laughter-detection) | 300 | MIT | Сегменты смеха → сигнал «смешно» | S |
| [soCzech/TransNetV2](https://github.com/soCzech/TransNetV2) | 1.1k | MIT | Границы шотов точнее порога ffmpeg | S |
| [hkchengrex/MMAudio](https://github.com/hkchengrex/MMAudio) | 2.3k | код MIT, **проверить лицензию весов** | Генерация foley/SFX по видеоряду | L |

### 1.6 Коммерческие эталоны: что публично заявлено

- **Submagic:** 6 стилей авто-зума по словам (Smooth Zoom In, Crash Zoom, Zoom Fast…), «Magic B-roll» в один клик (зумы + b-roll, привязанные к транскрипту), авто-SFX и переходы, Auto Cut тишины.
- **OpusClip:** ClipAnything (выбор по визуалу, тону, мимике и речи), virality score 0–99, бренд-шаблоны, reframe 9:16/1:1/16:9, Agent Opus (2026).

---

## 2. Топ-25 фич (по приоритету внутри группы)

Сложность: **S** — до 2 дней, **M** — до недели, **L** — больше недели.

### (A) Поиск моментов

| # | Фича | Откуда | Как у нас | Сл. |
|---|---|---|---|---|
| 1 | **Тепло Twitch-чата.** Для `twitch_url` скачивать чат (`TwitchDownloaderCLI chatdownload`, JSON). Считать окна по 5 с: z-score частоты сообщений, число уникальных авторов, вес эмоутов и слов (KEKW, LUL, OMEGALUL, Pog, «ахах», «+», «F»), донаты и подписки ×N, лимит на одного автора. **Сдвигать кривую назад на 3–8 с**, потому что чат реагирует с задержкой. Кривую передавать в промпт Gemini как «пики чата: t=…» и показывать на таймлайне | hotclip, Clips Kitty, HighlightMiner | новый `app/chat_signal.py` + поле в analysis | M |
| 2 | **YouTube «самые пересматриваемые».** yt-dlp уже отдаёт поле `heatmap` (`start_time/end_time/value`). Сохранять его в `metadata_json`, передавать в промпт и рисовать на таймлайне. Бесплатный сигнал аудитории | Clips Kitty | ingest.py | S |
| 3 | **Слияние сигналов с правилом совпадения.** Расширить `action`: громкость + смех (laughter-detection, MIT) + плотность склеек + движение + чат. Нормализовать внутри каждого видео. Пик засчитывается, только если совпали ≥2 сигнала; одна громкость = вероятно музыка. Всё это дешёвый префильтр перед Gemini | hotclip, Clips Kitty | action_detect.py | M |
| 4 | **Второй проход «критик» и обучение на статистике.** Финалистов оценивать по 4 осям (hook / payoff-structure / value / topicality) и сравнивать попарно. Показатели опубликованных клипов (просмотры, досмотры) возвращать в промпт как примеры «что зашло / не зашло» для этого канала (Creator Profile) | supoclip, hotclip, Clips Kitty | pick.py + статистика (E-24) | M |
| 5 | **Слежение за каналом.** Подписка на Twitch, Kick или YouTube канал: новый VOD → анализ → ★ лучшие → рендер → публикация по расписанию. Плюс вариант «наблюдать папку OBS» с ожиданием стабильного размера файла | clippyme, hotclip | automation.py (сейчас разовый URL) | M |
| 6 | **Распознавание музыки и защита от DMCA.** Отмечать отрезки, где звучит трек (ACRCloud / AcoustID): штрафовать их при выборе и предлагать заглушить музыку при рендере | twitch-toolkit | анализ + render | M |

### (B) Монтаж, субтитры, моушн

| # | Фича | Откуда | Как у нас | Сл. |
|---|---|---|---|---|
| 7 | **Раскладка «вебка + игра» для стримов.** Один раз на источник пользователь обводит прямоугольник вебки (или его находит детектор лиц в статичном углу). Рендер ставит crop вебки сверху (~35–40%), а геймплей с отдельным фокусом снизу. Плюс режим SPLIT (2 лица друг над другом, субтитры на стыке). Раскладку можно менять на каждом куске | openshorts, Clips Kitty, ViralCutter | render.py filter_complex + CropFrame на 2 окна | M |
| 8 | **Новые пресеты субтитров.** (a) `boxed-pop`: под активным словом скруглённая плашка акцентного цвета, слово ×1.15 на 80 мс и обратно; (b) `keyword-accent`: LLM выделяет 1–2 ключевых слова в строке (золото и чуть крупнее), остальные белые; (c) **авто-эмодзи после ключевых слов** (теги regex / словарь / LLM → Twemoji над строкой, pop-in); (d) цвета спикеров. В ASS плашку делать отдельным слоем `\p`-рисунка или `BorderStyle=3` на слово | autoclip, pycaps, autobroll, ViralCutter | subtitles/ass.py | M |
| 9 | **Авто-зум по смысловым словам.** LLM или пик энергии выбирает 3–6 слов, к каждому генерируются ключи камеры: «smooth» 1.0→1.12 за 0.4 с, «crash» 1.0→1.25 за 0.08 с с отскоком, «out». Опционально whoosh. Ключи камеры уже есть, нужен только генератор и кнопка | Submagic | motion camera keys + agent tool | S |
| 10 | **Тишина и слова-паразиты внутри клипа.** По пословным таймкодам вырезать паузы > X с с отступом 0.2 с, минимальным разрезом 0.2 с и микро-кроссфейдом звука 20 мс. Словарь «эээ, ну, короче, типа, как бы». Режим «ускорить ×2 вместо выреза» | auto-editor, clippyme, autobroll | сегменты клипа = куски; отдать в cut_refine | M |
| 11 | **Звук под платформы.** `loudnorm` до −14 LUFS / −1 dBTP при рендере (сейчас только измеряем в QC); ducking музыки под речь через `sidechaincompress`; авто-SFX на события (склейка, появление хука, зум, эмодзи) | hotclip, autoclip, FableCut | render.py | S |
| 12 | **Оверлей Twitch-чата.** `chatrender -b start -e end --generate-mask --ban-words … --ignore-users nightbot,streamelements` → прозрачный слой «чат» (webm/альфа) в моушн-слоях. Пресеты «справа сбоку» и «внизу полупрозрачно». Эмоуты 7TV/BTTV есть | TwitchDownloader | новый тип вставки | M |
| 13 | **Синхронизация с битом и LUT-луки.** beat_this по треку музыки → привязка склеек, вставок и зум-ключей к битам и дропу. Луки как `.cube` через `lut3d` в ffmpeg и WebGL-превью в браузере | FableCut, cut-as-code | music + looks | M |

### (C) UX редактора

| # | Фича | Откуда | Как у нас | Сл. |
|---|---|---|---|---|
| 14 | **Кривые сигналов на таймлайне источника.** Дорожки громкости, тепла чата, YT-heatmap и движения; кандидаты стоят на пиках; клик по пику → «создать момент здесь ±20 с» | hotclip | Timeline + данные из A-1..3 | S–M |
| 15 | **Монтаж через транскрипт.** Вкладка «Текст»: поиск по фразам; выделил текст → «сделать клип» или «вырезать». Удаление слов режет куски. Двойной клик по слову = seek | audapolis, hotclip, clippyme | SubtitleEditor + куски | M |
| 16 | **Варианты A/B.** «Сделать 3 версии»: другой первый кусок (хук), стиль субтитров и заголовок. Нужны для мультиаккаунтов и теста. Версии связаны с клипом-родителем | hotclip | clip_history / копия плана | S |
| 17 | **Профессиональные хоткеи и телефонное превью.** J/K/L shuttle, I/O, `[`/`]` между моментами, ←/→ по кадру. Превью в рамке телефона с **оверлеем интерфейса площадки** (TikTok / Reels / Shorts: кнопки справа, подпись снизу), переключатель площадки | hotclip, shortcast, FableCut | safe zones уже есть → заменить на реальные UI-маски | S |

### (D) Возможности ИИ-агента

| # | Фича | Откуда | Как у нас | Сл. |
|---|---|---|---|---|
| 18 | **`revision` у клипа и live-reload.** Каждая запись MCP/REST передаёт `revision`; при несовпадении 409 с текущим состоянием. Открытый редактор получает SSE `clip_changed` и сам обновляется (сейчас правка агента видна после перезагрузки или Ctrl+Z?) | FableCut, cut-as-code | agent_api + store | S |
| 19 | **Агент «смотрит» результат.** (a) `contact_sheet(clip_id, n=12)`: сетка кадров одной картинкой, дешевле 12 вызовов `view_frame`; (b) `review_render(clip_id)`: готовый mp4 → Gemini с вопросами «хук в первые 2 с? мёртвые места? субтитры перекрывают лицо?» → список проблем с таймкодами; агент чинит их в цикле | FableCut `frame`, cut-as-code `edit-compare` | agent_tools + gemini | M |
| 20 | **Стиль по референсу.** `analyze_reference(url)`: скачивает чужой шортс и извлекает длины шотов, BPM, частоту зумов, стиль и позицию субтитров, хук. Результат — «рецепт», который агент применяет к клипу | FableCut, hotclip | yt-dlp + scene_cuts + Gemini | M |
| 21 | **Агент как продукт.** Командная строка Ctrl+K: «сделай 5 клипов из последнего VOD с вебкой сверху» → задача в очереди. Плюс webhooks о готовности с подписью HMAC, API-ключи и папка `skills/` в формате Agent Skills для Claude Code и Codex | Clips Kitty, openshorts | палитра Ctrl+K уже есть | S–M |

### (E) Экспорт и публикация

| # | Фича | Откуда | Как у нас | Сл. |
|---|---|---|---|---|
| 22 | **Пакет под площадку.** Отдельный текст для каждой площадки: TikTok коротко + 3–5 тегов, IG сторителлинг + 20–30 тегов, YT заголовок под поиск ≤100 символов. Обрезка под лимиты, обложка перекадрирована под площадку. «Голос автора» по 3–5 примерам его прошлых подписей | shortcast, hotclip | publish_meta.py | S |
| 23 | **Умное расписание.** Слоты прайм-тайма по каждой площадке и часовому поясу, минимальный интервал между постами одного аккаунта, TikTok по умолчанию как черновик, разнос вариантов A/B по разным аккаунтам | clippyme, shortcast | automation interval → слоты | S |
| 24 | **Обратная статистика.** Периодически забирать просмотры, лайки и комментарии опубликованных роликов (YT Data API / cookies, TikTok, IG через instagrapi) → дашборд «что зашло» → сигнал для A-4 | supoclip, hotclip | providers + jobs | M |
| 25 | **Выход для NLE и компиляции.** Из имеющегося OTIO через адаптеры писать FCPXML (Resolve/FCP), Premiere XML и EDL CMX3600. Плюс «Компиляция»: склейка лучших моментов в 16:9 для YouTube с автоглавами в описании | hotclip, ViralCutter, Clips Kitty | timeline_export.py | S |

**Не вошли в топ, но стоят внимания:** активный спикер по TalkNet и диаризации WhisperX (подкасты и коопы);
двуязычные субтитры и перевод по VideoLingo; дубляж (ElevenLabs); кадрирование «держать, пока лицо
помещается, вести только при выходе» с журналом решений (hotclip); NVENC/QSV; линт запрещённых слов;
b-roll по транскрипту из своих файлов или Pexels; TTS-нарратив поверх геймплея; карточки контента
(lower third, статистика, цитата, CTA) как пресеты моушн-слоёв; forced alignment после ручной правки
(stable-ts); теги настроения у музыки.

---

## 3. Что можно переиспользовать напрямую

| Что | Лицензия | Как использовать |
|---|---|---|
| [gl-transitions](https://github.com/gl-transitions/gl-transitions): ~100 GLSL-переходов, npm `gl-transitions`, есть llms.txt | лицензия у каждого файла (в основном MIT), репо NOASSERTION, **проверять каждый** | Превью в браузере через WebGL; рендер в Node (`gl` headless, как в editly) |
| [FableCut](https://github.com/ronak-create/FableCut): 17 переходов, 33 пресета анимаций, логика revision | MIT | Перенести пресеты анимаций в `frontend/src/motion/templates.ts` |
| [autoclip](https://github.com/artbyjazi/autoclip): ASS-пресеты `bold_pop/karaoke_fill/clean_lower/boxed` | MIT | Перенести параметры в default_pack |
| [auto-caption](https://github.com/sebetancurch/auto-caption): `styles.py` и пословные ASS-анимации pop/karaoke | MIT (+ Anton OFL) | То же, плюс экспорт `.davinci.srt` |
| [pycaps](https://github.com/francozanardi/pycaps): CSS-шаблоны и правила тегов | MIT | Формат правил «тег → эмодзи / цвет / SFX» |
| [captacity](https://github.com/unconv/captacity) | MIT | Схема параметров стиля |
| [TwitchDownloader](https://github.com/lay295/TwitchDownloader) CLI | MIT | Положить бинарник в Docker: чат JSON и рендер чата с маской |
| [auto-editor](https://github.com/WyattBlue/auto-editor) | Unlicense | Звать как CLI или переписать алгоритм порога, margin и smooth |
| [microsoft/fluentui-emoji](https://github.com/microsoft/fluentui-emoji): 3D, Color, Flat | MIT | Новый пак «3D-эмодзи», популярный стиль в шортсах |
| Animated Noto Emoji (Lottie / WebP / GIF) | CC BY 4.0 (атрибуция) | Анимированные стикеры; рендер Lottie через [lottie-web](https://github.com/airbnb/lottie-web) (MIT) |
| [noto-emoji](https://github.com/googlefonts/noto-emoji) статичные | OFL-1.1 / Apache-2.0 | Альтернативный набор эмодзи |
| Q-DDL LUT pack (800+ `.cube`: кино, плёнка, настроения) | CC BY 4.0 (атрибуция) | Луки через `lut3d`; LUT-паков с CC0/MIT не нашлось |
| Модели: TransNetV2, laughter-detection, beat_this, TalkNet-ASD, faster-whisper, stable-ts (все MIT), WhisperX (BSD-2), MediaPipe (Apache-2.0), PySceneDetect (BSD-3) | указаны | Сигналы для A-3, B-13 и активного спикера |
| [motion-canvas](https://github.com/motion-canvas/motion-canvas), [revideo](https://github.com/midrender/revideo), [editly](https://github.com/mifi/editly) | MIT | Подсмотреть easing и генераторы анимаций текста |

**Код не копировать, только идеи:** hotclip, supoclip, Clips Kitty (ядро), audapolis, whisper-timestamped,
Theatre studio — AGPL; ViralCutter (GPL-3), lossless-cut (GPL-2); Remotion и template-tiktok (Remotion
License, платная для компаний >3 человек); twick (Sustainable Use); openvideodev/react-video-editor (двойная
лицензия). Diffusion Studio под MPL-2.0: можно как библиотеку, но правки в её файлах придётся открывать.
Мем-SFX из интернета почти всегда без лицензии; Kenney (CC0) у нас уже есть.

---

## Источники

Ссылки на репозитории указаны в таблицах. Описание коммерческих продуктов:
[submagic.co/features/auto-zooms](https://submagic.co/features/auto-zooms),
[submagic.co/features/b-roll](https://submagic.co/features/b-roll),
[techsy.io: OpusClip vs Vizard](https://techsy.io/en/blog/opusclip-vs-vizard),
[castmagic: OpusClip](https://www.castmagic.io/software-reviews/opus-clip).
LUT: [cgchannel: 800+ free LUTs (Q-DDL)](https://www.cgchannel.com/?p=111770).
pycaps: [dev.to/francozanardi](https://dev.to/francozanardi/adding-animated-subtitles-to-videos-with-python-4hml).
