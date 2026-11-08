# Звуки бренда «Витубики» — источники и лицензии

Все исходники — Freesound.org, пак **kkerasik** автора **ssud_motion**
(https://freesound.org/people/ssud_motion/packs/40833/; «Recorded with handheld Tascam DR-40X internal mics»).
Лицензия у всех — **Creative Commons 0 (CC0 1.0)**, https://creativecommons.org/publicdomain/zero/1.0/ —
общественное достояние: коммерческое использование в рекламе разрешено, указывать автора не требуется
(указываем для порядка). Лицензия проверена на странице каждого звука 08.11.2026. Взяты публичные HQ-превью
(mp3 128 кбит/с) без входа и без API-ключа.

| Файл | Исходник | Страница | Обработка |
|------|----------|----------|-----------|
| `kalimba_chord.ogg` | kk_kalimba_chord3 — аккорд калимбы (C4 E4 G4 B4 D5, «перебором») | https://freesound.org/people/ssud_motion/sounds/732291/ | №10 раунда 2 (выбор владельца) как есть: с удара, ФВЧ 90 Гц, ФНЧ 9 кГц, 3 с, затухание 2.4–3.0 с, −22 LUFS |
| `dot_a_note.ogg` | тот же kk_kalimba_chord3 | https://freesound.org/people/ssud_motion/sounds/732291/ | нота B4 вырезана из аккорда (3 × bandpass 494 Гц, Q 6) + щелчок пальца по язычку (40 мс выше 1.2 кГц), тон +5 полутонов (E5), 0.5 с, мягкое затухание |
| `dot_b_pluck.ogg` | kk_Kalimba3 — одиночная нота G4 той же калимбы | https://freesound.org/people/ssud_motion/sounds/732284/ | с удара, ФВЧ 150 Гц, ФНЧ 9 кГц, тон +5 полутонов (C5), 0.5 с, мягкое затухание |
| `dot_c_tap.ogg` | тот же kk_kalimba_chord3 (через A) | https://freesound.org/people/ssud_motion/sounds/732291/ | A через ФНЧ 3 кГц (2 × 12 дБ/окт), быстрый спад (τ 70 мс), 0.32 с, ещё на 3 дБ тише |

Щипки точки (A, B) на 6 дБ тише аккорда по громким 50 мс, C — на 9 дБ. Пересборка из исходников:
`python tools/vtubika_brand_sfx.py D:/vvf-offload/vtubika/endcard_stock` (исходники и замеры —
`D:/vvf-offload/vtubika/endcard_stock/SOURCES.md`, `raw2/`, `raw3/`).
