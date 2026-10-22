# Контент моушн-слоёв: шрифты, эмодзи, стикеры, звуки, мемы

Дополнение к `spec.md`. Код — `app/motion_assets.py`, сборка — `tools/build_fonts.py`
(шрифты, вручную) и `tools/fetch_packs.py` (паки, при сборке образа).

## Где лежит

| Что | Путь (локально / в образе) | Как попадает |
|---|---|---|
| Шрифты | `vendor/fonts/` → `/app/vendor/fonts` | в репозитории; пересборка `tools/build_fonts.py` (нужен fonttools) |
| Паки | `vendor/packs/` → `/app/vendor/packs` | `python3 tools/fetch_packs.py [DIR]` — в Dockerfile отдельным слоем; локально в `.gitignore` |

Переопределяются переменными `MOTION_FONTS_DIR`, `MOTION_PACKS_DIR` (`settings.motion_fonts_dir/…packs_dir`).

`fetch_packs.py` — только stdlib, версии прибиты (тег/коммит + sha256 архива), повторный запуск
пропускает готовые паки (штамп `<pack>/.stamp`), `--only a,b` / `--skip imgflip`. Скачанное —
только данные, ничего не исполняется; имена файлов чистятся до `[A-Za-z0-9._-]`.

## Что внутри

### Шрифты — 24 семейства, 41 файл, ~9 МБ, все с кириллицей, OFL-1.1

Вариативные шрифты разрезаны на статические насыщенности (canvas/Skia из вариативного
файла видит только экземпляр по умолчанию). Список и подписи — `vendor/fonts/fonts.json`
(`family, file, weight, style, cyrillic, tags, label, hint`), копирайты — `vendor/fonts/LICENSES.md`.

* display: Montserrat 500/700/800/900, Rubik 500/700/900, Unbounded 700/900, Russo One, Exo 2 700/900,
  Dela Gothic One, Rubik Mono One, Rubik Glitch, Nunito 700/900
* condensed / «как Impact»: Oswald 500/700, Alumni Sans 800/900, Roboto Condensed 700/900, PT Sans Narrow 700
* text: Inter 400/600/800, Manrope 500/800, Golos Text 500/700/900
* hand: Caveat 700, Marck Script, Comfortaa 700, Lobster, Pacifico, Pangolin (комиксный)
* pixel: Press Start 2P; serif: PT Serif 400/700

Impact свободной версии не бывает, у Anton и Bebas Neue нет кириллицы — для мемов `Oswald 700`
или `Alumni Sans 900` (теги `meme`).

### Паки

| id | kind | Что | Шт. | Размер | Лицензия |
|---|---|---|---|---|---|
| `twemoji` | emoji | Twemoji v17.0.3, весь набор SVG + `emoji.json` | 4009 (1914 в индексе + тона кожи) | 10 МБ | **CC-BY 4.0** |
| `openmoji-extras` | sticker | OpenMoji 17 — свои (не-Unicode) символы, без логотипов брендов | 352 | 0.9 МБ | **CC-BY-SA 4.0** |
| `kenney-particles` | sticker | Kenney Particle Pack: искры, звёзды, дым, вспышки, следы (белые PNG 512px — подкрашиваются) | 80 | 4.7 МБ | CC0 |
| `freesound` | sfx | настоящие «киношные» записи с freesound.org (`tools/fetch_sfx.py`): мягкие вжухи и райзеры на переход, низкие удары, брам, саб-дроп и драма-бум на панчлайн, скретч, реакции зала (смех, ох, оу-у, аплодисменты), затвор камеры, касса, мягкий поп (выровнены по громкости ниже речи; интерфейсные динь/клик убраны) | 31 | 0.7 МБ | CC0 |
| `kenney-impact` | sfx | удары (металл, стекло, кулак, дерево…) и шаги | 130 | 1 МБ | CC0 |
| `kenney-interface` | sfx | клики, ошибки, подтверждения, глитчи, скрэтчи | 100 | 1 МБ | CC0 |
| `kenney-ui` | sfx | кнопки, переключатели | 51 | 0.3 МБ | CC0 |
| `imgflip` | meme | топ-100 мем-шаблонов imgflip (`box_count` — сколько подписей) | ≤100 | ~10 МБ | права у авторов исходников |

`emoji.json`: `{groups:[{key,label}], emoji:[{code, char, name, en, tags, en_tags, group, order, skins?}]}` —
русские названия/теги из emojibase-data 18 (MIT), английские оттуда же.

Громкость `generated` выровнена (RMS громкой части ≈ −15 dBFS, пик ≤ −1 dBFS; басам +5 дБ).

**imgflip из РФ-сетей**: `i.imgflip.com` (Cloudflare) режется на TLS, а `imgflip.com/s/meme/…`
(запасной адрес, его скрипт тоже пробует) зависает после первых ~20 КБ (проверено 19.10.2026). Тогда пак остаётся пустым,
сборка не падает. Лечится прокси: `docker build --build-arg HTTPS_PROXY=… ` (urllib его понимает)
или `--build-arg PACKS_SKIP=imgflip`, чтобы не ждать таймаутов (~1 мин).

## Ссылки на элементы (`src` у `image`, `asset_id` у `audio`)

* `emoji:<code>` — код как имя файла Twemoji (hex через `-`, FE0F выкидывается без ZWJ); `emoji:🔥` тоже понимается.
* `sticker:<pack>/<file>`, `meme:<pack>/<file>` — картинки (взаимозаменяемы: обе — картинки);
* `sfx:<pack>/<file>` — только паки `kind=sfx`.

```python
from app.motion_assets import resolve_pack_ref, pack_item_info, emoji_code_for_char
resolve_pack_ref("sfx:freesound/drama-boom.ogg")   # -> Path | None (None: битая/чужая/опасная ссылка)
pack_item_info("emoji:🔥")                     # -> {ref, kind, pack, file, path, name, tags, duration|width/height, license, attribution, url} | None
```

Для `--emoji-dir` компоновщика: `<packs>/twemoji`.

## API (`/api/motion`, под обычной авторизацией)

* `GET /fonts` → `[fonts.json-запись + url]`; `GET /fonts/{file}` — файл (только из fonts.json).
* `GET /emoji?q=&group=&limit=200` → `{groups, total, items:[… + ref, url]}` — поиск по-русски и по-английски, «ё»=«е», можно искать самим символом.
* `GET /emoji/{code}.svg`.
* `GET /packs?kind=` → сводка паков без элементов (`id, name, kind, license, license_url, attribution, source, version, count, size`).
* `GET /packs/{pack}?q=&limit=&offset=` → сводка + `total` + `items:[{file, name, tags, size, duration | width,height, box_count?, ref, url}]`.
* `GET /packs/{pack}/{file}` — файл (только из packs.json).
* `POST /packs/{pack}/{file}/import` — копия в «Файлы для монтажа» (для классических вставок и ИИ-монтажа);
  повтор возвращает ту же запись; SVG — 400 (ffmpeg-вставки SVG не читают).

Файлы отдаются с `Cache-Control: private, max-age=2592000`.

## Атрибуция (обязательная)

* **Twemoji** — CC-BY 4.0: «Twemoji graphics © Twitter, Inc and other contributors, CC-BY 4.0».
* **OpenMoji** — CC-BY-SA 4.0: «OpenMoji — the open-source emoji and icon project, openmoji.org, CC BY-SA 4.0».
  Для видео с такими стикерами CC-BY-SA требует указать автора и лицензию; само видео под SA не попадает,
  если стикер — неизменённая часть коллажа (ShareAlike — к изменённому стикеру).
* Kenney, `generated` — CC0, атрибуция по желанию. Шрифты — OFL, в готовом видео атрибуция не нужна.
* Мем-шаблоны imgflip — чужие изображения; использование в роликах — на свой риск (обычно пародия/fair use).

Строки атрибуции лежат в `packs.json` (`attribution`) — их стоит показать в справке/«О программе»
редактора и рядом с паком в пикере. Пока это нигде в UI не выведено.
