# Библиотека 3D-моделей вещей

Клавиатуры, мониторы, ноутбуки, столы, кресла, кружки, лампы, растения, наушники, мыши, книги, телефоны, полки,
кровати, диваны, окна и ковры для 3D-сцен движка `node/scene3d` — вместо одинаковых процедурных коробок.
Окружения-пресеты (`cozy_desk`, `gamer_rgb`, офис, кухня) и стол мем-канала (`tools/meme3d_scene.py`) берут их сами;
нет библиотеки или файла — вещь строится по-старому, из процедурных деталей.

## Где лежит

| | Путь |
|---|---|
| Модели | `DATA_DIR/runtime/models3d/<категория>/<slug>.glb` — у владельца `D:\vvf-data\runtime\models3d`, в контейнере `/data/runtime/models3d` (bind-mount `D:/vvf-data/runtime:/data/runtime`, docker-compose.override.yml) |
| Манифест | `DATA_DIR/runtime/models3d/manifest.json` |
| Источники | `tools/models3d/sources.json` — откуда, автор, лицензия, нормализация, якоря |
| Импорт | `tools/models3d/import.mjs` (+ `gen_keyboard.mjs` — свои механические клавиатуры) |
| Проверка глазами | `tools/models3d/catalog.mjs` (лист всех моделей), `showcase.mjs` («до/после» сцен), `inspect.mjs`, `faces.mjs` |

Движок получает файлы по `http://scene3d.local/models3d/…` — их отдаёт `render.mjs` из `--models DIR`
(по умолчанию `SCENE3D_MODELS_DIR`, иначе `DATA_DIR/runtime/models3d`); отдаются только `manifest.json` и `.glb`.

## Лицензии

Только то, что можно бесплатно использовать в коммерческих роликах: **CC0** (без условий) и **CC BY 3.0** (условие —
указать автора; строка атрибуции у каждой модели в манифесте, поле `attribution`). Платные, «editorial»,
«no-redistribution» и модели с неясной лицензией не берём. Лицензия каждой модели проверена на её странице
poly.pizza (поле `Licence` страницы) 2026-11-12. Свои клавиатуры (`keyboard/mech_*`) — собственная работа, CC0.

## Ссылка из сцены

```json
{"id": "kb", "model": "keyboard", "position": [0, 0.75, 0.1], "rotation": [0, 12, 0],
 "fitMax": [0.42, 0.05, 0.16], "modelTags": ["dark"],
 "primitive": "keyboard", "params": {"width": 0.4}}
```

- `model: "keyboard"` — вариант категории, засеянный: `models.seed` сцены (по умолчанию — от содержания ролика:
  подписи, герои, длительность) + `variantSeed` объекта (окружения ставят свой `seed`) + `id` объекта. Одна сцена —
  одна и та же вещь в любом куске рендера; другая сцена — другая. `model: "keyboard/mech_tkl_dark"` — точно эта.
- Ссылки паков — с расширением (`kenney-furniture/desk.glb`), библиотечные — без (`[a-z0-9_]+(/[a-z0-9_]+)?`).
- `modelTags` — нужны модели с этими тегами (растение на стол — `["desk"]`, в угол — `["floor"]`).
- `fitMax: [w, h, d]` — равномерно вписать в габарит (0 — не ограничивать); `fit: [w, h, d]` — растянуть точно
  (стол под высоту столешницы); без них — реальный размер из манифеста.
- Замена: нет манифеста, модели в нём, якоря для `mounts` или файла — объект строится из своего `primitive`/`params`
  либо из списка `fallback` (детали в мировых координатах); предупреждение в отчёте рендера.
- `mounts: [{"anchor": "screen", "size": [w, h], "parts": [...]}]` — детали на якоре модели (экран монитора с картинкой
  или живым интерфейсом, лампочка со светом); координаты деталей — от якоря, `size` — под его размер.
- `materials` сцены перекрашивают и модели (по имени материала или `"*"`).
- `"models": {"off": true}` в сцене — всё из замены (сравнение «до/после»).
- Toon/ink (`environment.toon`) ложится на модели как на свои меши: материалы → cel, обводка — у каждого меша;
  у мелочи тоньше (поле `outline` манифеста → `userData.outlineScale`).

Агенту: `list_3d_presets category=model` (по `q` — поиск) — категории и варианты с габаритом, тегами, якорями.

## Манифест

```jsonc
{
  "version": 1, "generated": "2026-11-12", "doc": "docs/3d/models.md",
  "categories": {"keyboard": ["keyboard/mech_tkl_dark", "..."]},
  "models": {
    "keyboard/mech_tkl_dark": {
      "id": "keyboard/mech_tkl_dark", "category": "keyboard", "slug": "mech_tkl_dark", "file": "keyboard/mech_tkl_dark.glb",
      "title": "Механическая TKL, тёмная с оранжевым",
      "size": [0.36, 0.0252, 0.142],          // габарит после нормализации, м: ширина x, высота y, глубина z
      "tris": 1686, "bytes": 29696, "sha1": "…", "textures": 0, "materials": ["case", "plate", "alpha", "mod", "accent"],
      "normalize": {"up": "y", "yaw": 0, "scale": 1, "fit": {"dim": "width", "value": 0.36}, "pivot": "bottom-center", "front": "+z"},
      "outline": 0.45, "tags": ["mechanical", "dark", "gaming"], "weight": 1,
      "anchors": {"screen": {"pos": [0, 0.23, 0.001], "size": [0.56, 0.25], "rot": [-9.3, 0, 0]}},   // у мониторов/ноутбуков; у ламп — bulb
      "source": {"kind": "poly.pizza", "url": "https://poly.pizza/m/…", "download": "https://static.poly.pizza/….glb"},
      "author": "…", "license": "CC0-1.0" | "CC-BY-3.0", "license_name": "…", "license_url": "…",
      "attribution": "«…» — автор, poly.pizza/m/…, CC BY 3.0"
    }
  }
}
```

Нормализация запечена в GLB (узел `vvf:normalize`): «вверх» — +y, перёд — +z (поворот `yaw`, `up: "z"` для
выгрузок без конверсии), масштаб — до габарита `fit` в метрах, опора — центр низа в (0, 0, 0). Проверка — 
`validateManifest` (`node/scene3d/src/models3d.ts`, тест `node/scene3d/models3d.test.mjs`).

## Добавить модель

1. Найти модель с CC0/CC BY (poly.pizza, Kenney, Quaternius, Poly Haven, …), записать в `tools/models3d/sources.json`:
   `id` (`категория/slug`), `title`, `pp`/`res` (id страницы и файла poly.pizza), `author`, `license`, `fit`, `tags`.
2. `cd tools/models3d && npm install` (один раз), затем
   `node tools/models3d/import.mjs --out D:/vvf-data/runtime/models3d --dl D:/vvf-offload/tmp/assets3d/dl --only категория/slug`.
   Скачанное — только данные (каждая модель в своём каталоге `--dl`), из него ничего не запускается.
   У провайдера владельца `static.poly.pizza` для curl/node режется после ~20 КБ, браузер качает нормально — файл
   кладётся в `--dl/<категория>__<slug>/source.glb` вручную (импорт берёт уже скачанное).
3. `node tools/models3d/catalog.mjs --only категория/ --front` — посмотреть: перёд на +z, стоит на полу, размер;
   поправить `yaw`/`fit`. Экран/лампа — якоря по `node tools/models3d/faces.mjs файл.glb --dir 0,0.3,1`.
4. Бюджет: ≤ 2 МБ на модель (текстуры ≤ 1024 px, геометрия — meshopt), до ~40 тыс. треугольников (больше — упрощается).

## Модели (65)

| id | Что | Габарит, м | Треуг. | Источник | Автор | Лицензия |
|---|---|---|---|---|---|---|
| `bed/kenney_single` | Кровать односпальная (Kenney) | 1.04×0.68×2.05 | 314 | [poly.pizza](https://poly.pizza/m/sn8az3odMR) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `bed/kid` | Кровать с оранжевым пледом | 1.17×0.92×2.05 | 414 | [poly.pizza](https://poly.pizza/m/aF8zkL7mfb0) | 64 Bit | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `bed/wood` | Кровать деревянная | 1.59×0.88×2.10 | 726 | [poly.pizza](https://poly.pizza/m/rXo5Rkl5LC) | CreativeTrio | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `bookcase/books` | Книжный шкаф с книгами | 1.78×1.80×0.61 | 3440 | [poly.pizza](https://poly.pizza/m/15B0ihXkg3C) | Wanja Pflüger | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `bookcase/quaternius` | Шкаф с книгами (Quaternius) | 0.98×1.80×0.35 | 10948 | [poly.pizza](https://poly.pizza/m/tACDGJ4CGW) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `books/leaning` | Книги с наклоном | 0.51×0.24×0.23 | 312 | [poly.pizza](https://poly.pizza/m/fkPRnm8iSXI) | Tiff Eidmann | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `books/row` | Ряд книг | 0.30×0.24×0.19 | 360 | [poly.pizza](https://poly.pizza/m/dxt7dETAy9) | CreativeTrio | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `books/row_long` | Длинный ряд книг | 0.73×0.24×0.17 | 794 | [poly.pizza](https://poly.pizza/m/aksI3ML9rpi) | Alex Safayan | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `books/stack` | Стопка книг | 0.26×0.18×0.26 | 640 | [poly.pizza](https://poly.pizza/m/1WggoIFq8tx) | Danni Bittman | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `chair/executive_black` | Кресло руководителя, чёрное | 0.68×1.20×0.80 | 1624 | [poly.pizza](https://poly.pizza/m/hW11P9DDXa) | Zsky | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `chair/kenney_red` | Стул на колёсиках (Kenney) | 0.52×0.95×0.49 | 588 | [poly.pizza](https://poly.pizza/m/CKSz6PB1vO) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `chair/mesh_grey` | Кресло серое с сеткой | 0.74×1.05×0.78 | 1540 | [poly.pizza](https://poly.pizza/m/aB4-AS93Wd7) | CMHT Oculus | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `chair/office_blue` | Офисное кресло, синее | 0.67×1.05×0.75 | 2116 | [poly.pizza](https://poly.pizza/m/UfKvrZBK6C) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `desk/kenney` | Стол светлый с ящиком | 1.43×0.75×0.77 | 198 | [poly.pizza](https://poly.pizza/m/6PbVkqPzEU) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `desk/standing` | Стол на металлических ногах | 1.19×0.75×0.65 | 220 | [poly.pizza](https://poly.pizza/m/dE6BqyF4Ws) | Zsky | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `desk/wood_drawers` | Стол деревянный с ящиками | 1.50×0.76×0.70 | 2640 | [poly.pizza](https://poly.pizza/m/V86Go2rlnq) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `headphones/headset` | Гарнитура с микрофоном | 0.19×0.21×0.13 | 2244 | [poly.pizza](https://poly.pizza/m/a7h9oaV3xzV) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `headphones/orange` | Наушники оранжево-чёрные | 0.20×0.18×0.07 | 472 | [poly.pizza](https://poly.pizza/m/PSsWSIAYIL) | CreativeTrio | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `headphones/pink` | Наушники розовые | 0.17×0.20×0.05 | 775 | [poly.pizza](https://poly.pizza/m/VLB0kuGDoP) | Ginsta | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `headphones/teal` | Наушники бирюзовые | 0.18×0.20×0.08 | 534 | [poly.pizza](https://poly.pizza/m/frvTEfwm9Yg) | jeremy | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `keyboard/compact_white` | Компактная белая | 0.29×0.01×0.10 | 808 | [poly.pizza](https://poly.pizza/m/fOy2zvPJAj-) | Alex Safayan | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `keyboard/mech_60_pastel` | Механическая 60%, пастельная | 0.29×0.02×0.11 | 1218 | своя (gen_keyboard.mjs) | vertical-video-fabric | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `keyboard/mech_65_cream` | Механическая 65%, кремовая ретро | 0.32×0.02×0.12 | 1344 | своя (gen_keyboard.mjs) | vertical-video-fabric | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `keyboard/mech_tkl_dark` | Механическая TKL, тёмная с оранжевым | 0.36×0.03×0.14 | 1686 | своя (gen_keyboard.mjs) | vertical-video-fabric | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `keyboard/office_black` | Офисная, чёрная со светлыми клавишами | 0.44×0.02×0.16 | 2872 | [poly.pizza](https://poly.pizza/m/4pzLbWshZ9m) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `keyboard/rgb_black` | Игровая, чёрная с подсветкой | 0.45×0.02×0.17 | 1972 | [poly.pizza](https://poly.pizza/m/3oFfQCSsUmQ) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `lamp/architect` | Лампа на шарнирах | 0.14×0.50×0.34 | 4109 | [poly.pizza](https://poly.pizza/m/0_FDq_UjWHp) | involuntary tsetse | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `lamp/cartoon_white` | Лампа мультяшная, белая | 0.23×0.42×0.35 | 412 | [poly.pizza](https://poly.pizza/m/4ScdjC1wytg) | jeremy | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `lamp/classic_black` | Настольная лампа, чёрная | 0.18×0.42×0.33 | 356 | [poly.pizza](https://poly.pizza/m/uJDWrSJGVH) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `lamp/silver_wood` | Лампа серебристая на деревянной подставке | 0.33×0.45×0.16 | 856 | [poly.pizza](https://poly.pizza/m/0iZSZezOQxC) | Alex Safayan | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `laptop/dark` | Ноутбук тёмный | 0.34×0.25×0.30 | 1328 | [poly.pizza](https://poly.pizza/m/4UuDHXFJ7I0) | jeremy | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `laptop/macbook` | Ноутбук, серебристый | 0.33×0.23×0.24 | 1424 | [poly.pizza](https://poly.pizza/m/27hcX_w47Jb) | Alex Safayan | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `laptop/silver` | Ноутбук белый | 0.34×0.23×0.31 | 18631 | [poly.pizza](https://poly.pizza/m/6eBS-C3E33W) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `monitor/allinone_white` | Моноблок, белый | 0.60×0.54×0.21 | 292 | [poly.pizza](https://poly.pizza/m/5qZ5IaClHHw) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `monitor/flat_black` | Монитор, тонкая рамка | 0.60×0.37×0.12 | 84 | [poly.pizza](https://poly.pizza/m/PvSjEbz11k) | CreativeTrio | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `monitor/slim` | Монитор на ножке | 0.58×0.46×0.22 | 196 | [poly.pizza](https://poly.pizza/m/e8cELDeDuTr) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `mouse/black` | Мышь чёрная | 0.09×0.04×0.12 | 100 | [poly.pizza](https://poly.pizza/m/V2Ebx3pvo4) | CreativeTrio | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `mouse/black_round` | Мышь чёрная округлая | 0.07×0.03×0.12 | 128 | [poly.pizza](https://poly.pizza/m/fbmU0OF0G2) | Zsky | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `mouse/white` | Мышь белая | 0.07×0.04×0.12 | 209 | [poly.pizza](https://poly.pizza/m/6ikB7PIRVyd) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `mousepad/dark` | Коврик для мыши | 0.30×0.00×0.37 | 92 | [poly.pizza](https://poly.pizza/m/S5sNqsyyOs) | dook | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `mug/cup` | Чашка | 0.07×0.10×0.07 | 6372 | [poly.pizza](https://poly.pizza/m/drpe4yrzgU) | SoyMaria | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `mug/simple` | Кружка простая | 0.11×0.10×0.07 | 232 | [poly.pizza](https://poly.pizza/m/54cOZNS04L) | MilkAndBanana | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `mug/white` | Кружка белая | 0.13×0.10×0.08 | 2112 | [poly.pizza](https://poly.pizza/m/eYH-QqBybgU) | Michael Fuchs | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `phone/black` | Смартфон чёрный | 0.07×0.01×0.15 | 182 | [poly.pizza](https://poly.pizza/m/4DRZmTs3jq) | smallbigsquare | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `phone/notch` | Смартфон с иконками | 0.07×0.15×0.01 | 1392 | [poly.pizza](https://poly.pizza/m/1fhwylgLb6I) | Sal Blrm | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `phone/white` | Телефон белый | 0.07×0.15×0.01 | 224 | [poly.pizza](https://poly.pizza/m/k2kgBepoMU) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `plant/aloe` | Алоэ в горшке | 0.24×0.30×0.21 | 276 | [poly.pizza](https://poly.pizza/m/8KMKYzAqWjp) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `plant/cactus` | Кактус в большом горшке | 0.52×0.80×0.52 | 324 | [poly.pizza](https://poly.pizza/m/Kr4kr7OCCQ) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `plant/kenney` | Агава (Kenney) | 0.10×0.30×0.11 | 104 | [poly.pizza](https://poly.pizza/m/23Dx9CC95C) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `plant/monstera` | Монстера в напольном горшке | 1.32×1.00×1.38 | 449 | [poly.pizza](https://poly.pizza/m/bfLOqIV5uP) | Quaternius | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `plant/small_pot` | Росток в горшочке | 0.23×0.26×0.25 | 240 | [poly.pizza](https://poly.pizza/m/auhzQHajHd) | scaranto | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `plant/snake` | Сансевиерия в терракоте | 0.26×0.32×0.23 | 532 | [poly.pizza](https://poly.pizza/m/3qh9saogdJd) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `rug/kilim` | Ковёр-килим | 1.80×0.01×0.78 | 1442 | [poly.pizza](https://poly.pizza/m/cORwtjCf95) | Akimkhan | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `rug/oriental` | Ковёр восточный | 1.44×0.01×2.00 | 2108 | [poly.pizza](https://poly.pizza/m/8-vPv5_yqKb) | jeremy | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `rug/round_fluffy` | Ковёр круглый пушистый | 1.48×0.01×1.50 | 94 | [poly.pizza](https://poly.pizza/m/tr0LwyNnCt) | J-Toastie | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `rug/round_kenney` | Ковёр круглый (Kenney) | 1.60×0.02×1.60 | 376 | [poly.pizza](https://poly.pizza/m/jeDDiN69Ze) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `shelf/cubby` | Стеллаж-ячейки | 1.00×0.69×0.27 | 1574 | [poly.pizza](https://poly.pizza/m/3aAw-c_7gkO) | Jonathan Granskog | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `shelf/wall_double` | Две настенные полки | 0.80×1.10×0.32 | 324 | [poly.pizza](https://poly.pizza/m/7544dJq1UVu) | Jarlan Perez | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `shelf/wall_plank` | Полка-доска на кронштейнах | 0.80×0.15×0.22 | 44 | [poly.pizza](https://poly.pizza/m/144BQsV4GdZ) | jeremy | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `sofa/grey` | Диван серый | 2.00×0.83×0.74 | 1229 | [poly.pizza](https://poly.pizza/m/obMPe0AheB) | J-Toastie | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `sofa/kenney` | Диван (Kenney) | 1.90×0.89×0.79 | 256 | [poly.pizza](https://poly.pizza/m/jMu2iCmGxU) | Kenney | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `sofa/olive` | Диван оливковый | 2.00×1.00×1.00 | 1678 | [poly.pizza](https://poly.pizza/m/aFwNuRNmcO5) | CMHT Oculus | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `sofa/wide` | Диван широкий тёмный | 2.20×0.72×0.90 | 716 | [poly.pizza](https://poly.pizza/m/7Q_Ab2HLll1) | Danni Bittman | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `window/white_double` | Окно белое двойное | 1.54×1.30×0.33 | 228 | [poly.pizza](https://poly.pizza/m/98ZJrd80Q1V) | Poly by Google | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
| `window/wood_cross` | Окно деревянное с крестовиной | 1.12×1.20×0.10 | 124 | [poly.pizza](https://poly.pizza/m/dwBpM-aSA_t) | Justin Randall | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) |
