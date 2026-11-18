# gl-transitions — шейдерные переходы склеек

Источник: <https://github.com/gl-transitions/gl-transitions>, каталог `transitions/`, коммит
`1886f07392b173b66ddc75d8d948ca0553626096` (2026-11-01). Лицензия репозитория — MIT (копия —
`node/gltrans/shaders/LICENSE`), но у каждого перехода своя лицензия в заголовке `.glsl` (GitHub показывает
NOASSERTION). Берём **только** переходы с лицензией MIT / BSD / Apache / CC0 в заголовке; файлы скопированы без
правок, заголовки (автор, лицензия, у BSD — полный текст) сохранены. Тест `tests/test_transitions_gl.py` сверяет
заголовок каждого файла с этой таблицей и реестром `app/transitions_gl.py`; лишние файлы в каталог не кладутся.

Рисует `node/gltrans/render.mjs` (headless Chromium, WebGL 1, SwiftShader) по спецификации GL Transition v1;
нет браузера/WebGL или шейдер упал — стык идёт запасным xfade.

| Ключ | В редакторе | Файл | Автор | Лицензия | Запасной xfade |
|---|---|---|---|---|---|
| `gl_curtains` | Занавес | `Curtains.glsl` | gre | MIT | horzopen |
| `gl_wipe` | Шторка мягкая | `DirectionalWipe.glsl` | gre | MIT | wipeleft (по direction) |
| `gl_glass` | Стекло | `Refraction.glsl` | gre | MIT | dissolve |
| `gl_shatter` | Осколки | `Shatter.glsl` | lbl | MIT | dissolve |
| `gl_ripple` | Волна | `Ripple.glsl` | gre | MIT | fade |
| `gl_drop` | Капля | `WaterDrop.glsl` | Paweł Płóciennik | MIT | fade |
| `gl_tear` | Разрыв | `PaperTear.glsl` | gre | MIT | horzopen |
| `gl_pixels` | Пиксели | `Pixelize.glsl` | gre | MIT | pixelize |
| `gl_circle` | Круг | `CircleOpen.glsl` | gre | MIT | circleopen |
| `gl_squeeze` | Сжатие | `Squeeze.glsl` | gre | MIT | squeezev |
| `gl_cube` | Куб | `Cube.glsl` | gre | MIT | slideleft |
| `gl_doors` | Двери | `Doorway.glsl` | gre | MIT | horzopen |
| `gl_swirl` | Вихрь | `Swirl.glsl` | Sergey Kosarevsky | MIT | radial |
| `gl_glitch` | Помехи | `GlitchDisplace.glsl` | Matt DesLauriers | MIT | pixelize |
| `gl_page` | Страница | `InvertedPageCurl.glsl` | Hewlett-Packard (адаптация Sergey Kosarevsky) | BSD 3 Clause | wipeleft |
| `gl_honeycomb` | Соты | `Honeycomb.glsl` | gre | MIT | dissolve |
| `gl_grid` | Плитки | `GridFlip.glsl` | TimDonselaar | MIT | dissolve |
| `gl_tvoff` | Выключение ТВ | `TvTurnOff.glsl` | gre | MIT | fadeblack |
| `gl_burn` | Прожиг | `BurnThrough.glsl` | liubailin2020@gmail.com | MIT | dissolve |
| `gl_melt` | Таяние | `DoomMelt.glsl` | Zeh Fernando | MIT | wipedown |

На коммите источника все 148 переходов — MIT, кроме двух BSD (`InvertedPageCurl` — BSD 3 Clause,
`StereoViewer` — BSD 2 Clause); переходов с другими лицензиями или без лицензии нет. Добавляя новый — проверь
заголовок, скопируй файл без правок, добавь строку сюда и в `GL_KINDS`.
