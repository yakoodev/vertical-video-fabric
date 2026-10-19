# 💬 Чат Twitch-VOD

Чат стрима — сигнал «где было смешно/круто» для поиска моментов и материал для
оверлея чата в монтаже. Код: `app/twitch_chat.py` (импорт, хранение, хайп),
`app/chat_api.py` (ручки), тесты `tests/test_twitch_chat.py` (без сети).

## Откуда берём (проверено вживую, октябрь 2026)

- **Публичный GQL веб-плеера** `https://gql.twitch.tv/gql` с публичным Client-ID
  сайта `kimne78kx3ncx6brgo4mv6wki5h1ko` (его же использует TwitchDownloader).
  Ключ API и логин не нужны. Переопределить: `TWITCH_GQL_CLIENT_ID`.
- **Листаем только по времени** (`comments(contentOffsetSeconds: N)`): курсорные
  запросы (`cursor`, `after`) Twitch отклоняет с `failed integrity check`.
  Страница — ~50–60 сообщений около секунды N; следующую берём с последней
  секунды страницы (если ничего нового — N+1), дубли отсекает уникальный индекс
  `(source_id, msg_id)`. Сверка на реальном VOD (100 с, 813 сообщений) с
  перебором каждой секунды: совпадение 1:1. Теряются только сообщения сверх
  ~50 в одну и ту же секунду.
- Сначала идёт «сырой» GQL-запрос; если его закроют — автоматически persisted
  query `VideoCommentsByOffsetOrCursor` (как у веб-плеера).
- VOD делится на 4 отрезка, которые качаются параллельно (`TWITCH_CHAT_WORKERS`,
  до 8). Замер: 20 минут чата KaiCenat (7,6 тыс. сообщений) — 36 с, значит
  3-часовой VOD — около 5–10 минут.
- Пакет `chat-downloader` (xenova) для Twitch-VOD **не работает** (бесконечные
  ретраи с `KeyError 'data'` — он листает курсором). Зависимостей не добавляли:
  нужен только `httpx`, он уже есть.
- Удалённые, скрытые и подписочные VOD отдают `video: null` — импорт завершится
  статусом `failed` с понятной ошибкой. Twitch хранит VOD 7–60 дней: качайте
  чат вместе с видео (это и делает автоимпорт).

Смайлы: родные Twitch — по id во фрагментах
(`https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/2.0`); сторонние —
карта `имя → url` на источник из 7TV (`7tv.io/v3/users/twitch/{id}` + глобальный
набор), BTTV (`api.betterttv.net/3/cached/users/twitch/{id}` + global), FFZ
(`api.frankerfacez.com/v1/room/id/{id}` + `set/global`). Приоритет: канал над
глобальными, 7TV > BTTV > FFZ. Значки (badges) — глобальные и канальные через
тот же GQL. Упавший сервис смайлов импорт не валит.

## Хранение

- `source_chat` (одна строка на источник): `vod_id`, `vod_start_sec`, канал,
  `status` (`queued` / `running` / `succeeded` / `failed`), `progress`,
  `message_count`, `error`, `emotes_json`, `badges_json`.
- `chat_messages`: `source_id`, `msg_id`, `offset_sec` (секунда **источника**),
  `user_name`, `user_login`, `color`, `text`, `fragments_json` (только если есть
  родные смайлы), `badges_json`. Индексы `(source_id, offset_sec)` и уникальный
  `(source_id, msg_id)`. Удаление источника уносит чат каскадом.

Миграция — в `Database.init` (`app/db.py`, вызов `ensure_chat_schema`).

## Автоимпорт

`SourceIngestor.on_source_created` — колбэк после успешного создания источника.
`build_chat_router(..., ingestor=source_ingestor)` в `app/main.py` вешает туда
`ChatImportService.auto_import`: если у источника ссылка на Twitch-VOD (или клип —
тогда берётся его VOD и смещение), чат качается в фоновом потоке. Работает и для
`POST /api/sources`, и для фоновых загрузок (`/api/sources/download`), и для
автоматизации — все идут через один ingestor.

Выключить: env `TWITCH_CHAT_AUTO_IMPORT=0` или настройка `chat_auto_import=false`
(`app_settings`). В тестах автоимпорт выключен в `tests/conftest.py`.

Если приложение перезапустили посреди импорта, статус читается как `failed`
(«импорт прерван») — запустите заново.

## Ручки

| Метод | Путь | Что |
|---|---|---|
| POST | `/api/sources/{id}/chat/import` | Импорт в фоне. Тело (необязательно): `{"vod": "ссылка или id", "vod_start_sec": 3600}` — для источников-файлов или обрезанных записей (`vod_start_sec` — секунда VOD, которая у источника 0:00). Ответ — статус. 400, если VOD не указан и в источнике нет Twitch-ссылки. |
| GET | `/api/sources/{id}/chat/status` | `{source_id, status, progress, message_count, vod_id, vod_start_sec, channel_login, error, …}`; без чата `status: "none"`. |
| GET | `/api/sources/{id}/chat?from=&to=&limit=500` | `{"chat": статус, "messages": [сообщение…]}`, `t` — секунда источника. |
| GET | `/api/sources/{id}/chat/activity?bucket=10&top=20` | `{bucket, total, buckets: [{t, count, hype, reactions, caps, top, top_count}], windows: [окно…]}` |
| DELETE | `/api/sources/{id}/chat` | Забыть чат. |
| GET | `/api/clip-plans/{id}/chat?limit=5000` | Сообщения в кусках клипа, `t` — секунда **клипа**. |

Сообщение:

```json
{"t": 5.0, "user": "Bob", "color": "#FF4500", "text": "lol KEKW",
 "fragments": [{"text": "lol "}, {"emote": "KEKW", "url": "https://cdn.7tv.app/emote/…/2x.webp"}],
 "badges": [{"id": "subscriber/12", "url": "https://static-cdn.jtvnw.net/badges/v1/…/2"}]}
```

`color` всегда заполнен (нет своего — детерминированный цвет Twitch по нику);
`fragments` по порядку склеиваются в `text`; `badges` может быть пустым.

Время клипа — как у моушн-слоёв (`app.motion.clip_timeline`): куски встык,
переход съедает свой overlap на склейке. Сообщения между кусками в клип не
попадают.

## Хайп и поиск моментов

`score_activity` по бакетам: частота относительно локальной медианы ±5 минут ×
(1 + 1.5·доля реакций + 0.5·доля капса) × count/(count + медиана) (гасит
«всплеск» из двух сообщений в тихом чате), нормировано к 0..1. Реакции —
KEKW/LUL/OMEGALUL/ICANT/Pog*/W/+/GG/«ахах»/«ору»/… плюс любые смайлы из карты.

`chat_hype_windows(store, source_id, top=20)` — горячие окна (пик ≥ 0.2, не
ближе 45 с, расширение на соседей ≥ половины пика до ±60 с, без перекрытий):
`{start, end, peak_t, hype, count, top, top_count, label}`.

В анализе (`app/ai/service.py`) к промпту модели добавляется короткий блок
«Чат зрителей (Twitch): всплески активности …» с 15 окнами и напоминанием, что
чат отстаёт от события на 5–20 с. Сохранённый промпт анализа не меняется.
Выключить: настройка `analysis_use_chat=false`.
