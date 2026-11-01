# Видеомодели для ИИ-серий (аниме/2D, 9:16, 5 планов × 4–5 с): обзор на 01.11.2026

Цель: найти модели, которые держат персонажей (девочка + маленький дисковый робот-пылесос), не ломают физику
(предметы не двоятся, пылесос на полу), делают ровно одно непрерывное действие без склеек, умеют первый+последний
кадр, хорошо рисуют 2D/аниме и дают звук. Источники в конце, номера в квадратных скобках.

## Главное

1. **Корень наших поломок не только в модели, а в режиме подачи.** У Seedance 2.x, MiniMax H3, Gemini Omni Flash
   и Veo 3.1 режим «первый/последний кадр» и режим «референсы персонажей» **взаимоисключающие** [7][8][9][14].
   Мы шлём в seedance-2-fast одновременно `images` (первый кадр) и `reference_images` (листы персонажей,
   коммит 6b0da91). По правилам ByteDance/KIE такой запрос либо отклоняется с 400
   («first/last frame content cannot be mixed with reference media content»), либо одна из групп молча
   отбрасывается [7][8]. Надо проверить, что реально уходит в KIE через Polza, и выбрать один режим.
2. **Kling 4.0 (вышел 15.10.2026) — единственная найденная модель, где в одном запросе можно передать сразу
   стартовый кадр, конечный кадр, промежуточные ключевые кадры и «элементы» персонажей** (до 7 элементов
   и до 10 картинок) [3][4][5]. Это закрывает обе наши проблемы: сцепку планов и сохранение персонажей.
   Риск в том, что модель новая: в независимых рейтингах её ещё нет, есть только данные вендора.
3. **Polza не продаёт ни Kling 4.0, ни Kling 3.0 Omni, ни Vidu** [1]. За рубли Kling 4.0 есть у GenAPI (gen-api.ru,
   российские карты) [5]. Также он есть на kie.ai и fal.ai, но там нужна зарубежная карта.
4. Если остаёмся на Polza, лучший путь — **ключевые кадры из картинковой модели + режим первый+последний кадр**
   на Seedance 2.0 (не fast), MiniMax H3 Max или Gemini Omni Flash 1.1. Тогда внешность персонажа зафиксирована
   на обоих концах плана, и модели остаётся только движение между ними.

## Рейтинги (независимые)

Artificial Analysis, image-to-video, общий зачёт со звуком [2]. Эло: Kling 3.0 Pro = 1000 (опорная точка).

| # | Модель | Эло I2V | $/мин (API вендора) |
|---|---|---|---|
| 1 | Seedance 2.5 | 1153 | 34.1 |
| 3 | Seedance 2.0 | 1136 | 22.5 |
| 4 | MiniMax H3 (768p) | 1128 | 4.8 |
| 8 | MiniMax H3 Max | 1117 | 9.6 |
| 9 | Wan 3.0 | 1111 | 12.0 |
| 13 | Grok Imagine 1.5 | 1052 | 15.0 |
| 14 | Gemini Omni Flash 1.1 | 1046 | 9.1 |
| 17–18 | Kling 3.0 Pro / Kling 3.0 Omni | 1000 / 1000 | 10.1 / 8.4 |
| 20 | PixVerse V6 | 959 | 6.9 |
| 21–22 | Veo 3.1 / Veo 3.1 Fast | 957 / 937 | 24 / 7.2 |
| 23 | Vidu Q3 Pro | 930 | 7.2 |

В text-to-video первое место у Wan 3.0 (1175), Seedance 2.5 четвёртый (1157) [2]. Kling 4.0 и Vidu Q4
в таблицах AA пока нет. По физике независимых сравнений мало. В AV-Phys Bench (май 2026) Seedance 2.0 выше
Kling 3.0 и Veo 3.1, в PhyGround впереди Wan и Veo 3.1. Оба результата заявлены самими авторами [17].
Общий вывод бенчмарков: законы Ньютона и сохранение предметов плохо держат все модели [17]. Поэтому
дубли и «летающий» пылесос лечатся прежде всего ограничениями на входе (ключевые кадры), а не сменой модели.

## Сводная таблица по нашим 6 критериям

Обозначения: ✅ — есть и это сильная сторона, ◐ — есть с оговорками, ✗ — нет. Цены даны за 5 с.

| Модель | 1 Персонажи по реф. | 2 Физика/предметы | 3 Один план без склеек | 4 Первый+последний | 5 Аниме | 6 Звук | Polza, 5 с 720p | Другие площадки, 5 с 720p / 1080p |
|---|---|---|---|---|---|---|---|---|
| **Kling 4.0** (Omni Reference) | ✅ 7 элементов + 10 картинок, **вместе со старт/конец кадром** | ◐ нет данных | ✅ 3–30 с, без `multi_prompt` один план | ✅ + до 8 промежуточных | ◐ у Kling сильное движение | ✅ заявлен | нет | GenAPI 196 ₽ / 294 ₽; kie $0.71 / $1.06; fal $0.79 / $1.18 |
| **Seedance 2.5** | ✅ до 30 реф. (отдельный режим) | ◐ | ✅ до 30 с одним дублем | ✅ (отдельный режим) | ✅ стиль держит лучше других [15] | ✅ | 213 ₽ (480p-черновик 95 ₽ → finalize 1080p) | kie от $0.085/с |
| **Seedance 2.0** (pro) | ✅ 9 реф. (отдельный режим) | ◐ лучший в AV-Phys | ◐ | ✅ (отдельный режим) | ✅ | ✅ | 138 ₽ (1080p 344 ₽) | kie от $0.057/с |
| Seedance 2 fast (сейчас) | ◐ | ✗ (наш опыт) | ◐ | ✅ | ◐ | ✅ | 111 ₽ | kie от $0.034/с |
| **MiniMax H3 Max** (hailuo-3-max) | ◐ до 9 реф. у вендора, **в Polza только кадры** | ◐ | ✅ 5–15 с | ✅ | ? | ✅ стерео | **46 ₽** (768p) | kie, Runway, Runware |
| **Gemini Omni Flash 1.1** | ◐ до 7 реф., не вместе с кадрами | ◐ | ✅ 4/6/8/10 с | ✅ (новое в 1.1) | ◐ | ✅ | **48 ₽ за 4 с**, 63 ₽ за 6 с | kie от $0.315/видео |
| Wan 3.0 | ✅ r2v у вендора, **в Polza только кадры** | ◐ (слабее на сложной физике [15]) | ✅ 2–30 с | ✅ | ✅ держит линию и плоскую заливку [15] | ✅ | 114 ₽ | Krea $0.15/с 720p; kie от $0.04/с |
| Kling 3.0 Omni (O3) | ✅ элементы + старт-кадр | ◐ | ◐ | ✅ | ◐ | ✅ | нет (есть только kling/v3 без элементов) | kie от $0.07/с; fal $0.112–0.14/с; GenAPI |
| Kling v3 (Polza) | ✗ | ◐ | ✗ режет на планы при `multi_shots`/длинном промпте | ✅ | ◐ | ✅ | 60 ₽ (pro без звука), 90 ₽ со звуком | — |
| Vidu Q3 / Q4 preview | ✅ Q3 до 7, Q4 до 15 реф. | ◐ | ✅ до 16 с | ◐ (отдельный режим) | ✅ аниме — заявленная сильная сторона [12] | ✅ Q3 | нет | Vidu API Q3-pro r2v $0.10/с 720p; kie Q4 от $0.036/с |
| Veo 3.1 / Fast | ◐ ≤3 реф., по одному источнику только 16:9 | ✅ реализм | ✅ 4–8 с | ✅ | ◐ | ✅ | veo3_fast 45 ₽ за запрос, veo3 188 ₽ | Gemini API, $0.10–0.40/с |
| Grok Imagine 1.5 | ◐ | ✗ артефакты (наш опыт) | ◐ | ◐ | ◐ | ✗ в Polza | 15 ₽ | — |
| Sora 2 | — | — | — | — | — | — | нет | API по одному источнику отключён 24.09.2026 [16], не рассматриваем |
| PixVerse V6 | ◐ | ◐ | ◐ | ✅ | ✅ есть аниме-режим (по блогам) | ✅ | нет | kie $0.044–0.06/с 720p |

Runway Gen-4.5, Luma Ray 3 и Midjourney Video в свежих рейтингах AA не видны. У Midjourney нет публичного API,
а Higgsfield — агрегатор с интерфейсом поверх тех же Kling и Seedance. Для нас это не приоритет.

## Топ-3 рекомендации

Стоимость эпизода считаем для 5 планов по 5 с. «×2» означает реалистичный бюджет с одной перегенерацией на каждый план.

### 1. Kling 4.0 reference-to-video через GenAPI (рубли) или kie.ai

- **Какие проблемы решает.** Один запрос содержит `start_image_url` (последний кадр прошлого плана),
  `elements` (лист девочки и лист пылесоса, @Element1/@Element2) и, по желанию, `end_image_url` [4][5].
  Персонаж не переизобретается, сцепка сохраняется. Длительность от 3 до 30 с. Если не передавать `multi_prompt`,
  получается один план.
- **Цена эпизода.** GenAPI: 720p ≈ 981 ₽ (×2 ≈ 1 960 ₽), 1080p ≈ 1 470 ₽. kie: $3.56 при 720p, $5.31 при 1080p.
  fal: $3.93 / $5.89.
- **Доступ.** GenAPI принимает российские карты и поддерживает `callback_url` [5]. У kie асинхронный createTask → taskId.
  У fal очередь: submit → request_id [4]. Официальный API Kuaishou пока в статусе «coming soon» [6].
- **Риск.** Модель вышла 15.10, независимых тестов нет. Начать с пилота: прогнать эпизод из 2 планов.

### 2. Seedance 2.0 (pro) или 2.5 на Polza в режиме первый+последний кадр

- **Какие проблемы решает.** Лидер AA в image-to-video. Оба конца плана задаются кадрами, нарисованными
  картинковой моделью по листам персонажей. Референсы в этом режиме не передаются, потому что персонаж уже
  нарисован в обоих кадрах. Fast заменить на 2.0: у fast заметно хуже качество, а модели семейства 2.0 и 2.5
  стоят на 3-м и 1-м местах AA.
- **Цена эпизода.** Seedance 2.0 720p ≈ 692 ₽ (×2 ≈ 1 380 ₽), 1080p ≈ 1 720 ₽. Seedance 2.5 720p ≈ 1 063 ₽.
  Есть экономный вариант для 2.5: черновик 480p за 473 ₽ на эпизод, потом `finalize` до 1080p только для принятых
  планов [1].
- **Доступ.** Уже подключено. Те же параметры `images[0..1]` и `generate_audio`, плюс `return_last_frame`.

### 3. MiniMax H3 Max (hailuo-3-max) на Polza: первый+последний кадр, самый дешёвый сильный вариант

- **Какие проблемы решает.** H3 и H3 Max занимают 4-е и 8-е места AA I2V. Режим первый+последний кадр
  (`images` ≤ 2), длительность 5–15 с, нативный звук [1][2][9].
- **Цена эпизода.** 768p ≈ 228 ₽ (×2 ≈ 456 ₽), это втрое дешевле seedance-2-fast.
- **Альтернатива того же класса.** Gemini Omni Flash 1.1: 48 ₽ за 4 с, около 240 ₽ за эпизод из 5 × 4 с,
  есть `first_frame_url` + `last_frame_url` [1][10].
- Подходит для дешёвых черновиков и для серий, где действие простое.

## Изменения в конвейере

1. **Сначала ключевые кадры, потом видео.** Для каждого плана картинковая модель (Nano Banana 2.1 /
   GPT Image 2.5 в Polza) рисует **стартовый и конечный кадр** по листам персонажей и описанию «было → стало».
   Конечный кадр плана N сразу становится стартовым кадром плана N+1. Не нужно вырезать последний кадр из видео,
   поэтому дрейф не накапливается от плана к плану. Видео генерируется в режиме первый+последний кадр.
   Это главное лекарство для Seedance, H3, Omni и Veo, которые не умеют совмещать кадры с референсами.
2. **Исправить смешение режимов в Seedance** (`app/aivideo_providers.py`, где `images` и `reference_images`
   отправляются вместе). Есть два варианта. (а) Режим кадров: только `images=[first,last]`, без `reference_images`.
   (б) Режим референсов: только `reference_images` (листы персонажей + прошлый кадр), а в промпте
   «@image1 is the first frame». Вариант (б) ненадёжен по тесту Pixo [8], поэтому основным выбрать (а).
   В Kling 4.0 смешивать можно, это штатный режим.
3. **Описывать пылесос как предмет, а не персонажа.** Пример: «a small flat round disc robot vacuum, 30 cm wide,
   8 cm tall, no arms, no legs, no face, wheels hidden, always touching the floor». В каждом промпте указывать
   «exactly one girl and exactly one robot vacuum in the scene». Так модель не превращает его в гуманоида,
   не плодит копии и не поднимает в воздух.
4. **Одно простое действие на план.** Писать в виде «начальное состояние → одно действие → конечное состояние,
   совпадающее с последним кадром». Длительность 3–4 с там, где модель это позволяет (Kling от 3 с,
   Seedance и Omni от 4 с). Чем короче план, тем меньше модель «досочиняет».
5. **Анти-склейки.** Писать «single continuous shot, one take, no cuts, no scene change, no transitions,
   locked-off or slow push-in camera». У Kling не передавать `multi_prompt` и явно ставить `multi_shots=false`.
   Склейки в kling/v3 появлялись именно от многошагового промпта и мультишота.
6. **Негативы.** Там, где есть `negative_prompt` (Wan 2.5 в Polza), писать «duplicate objects, extra robot,
   floating objects, humanoid robot, morphing, cut, scene change, text». Где его нет, добавлять в конец промпта
   короткий блок «Avoid: …».
7. **Фиксированная строка стиля** для всех планов, например «2D cel-shaded anime, clean lineart, flat colors,
   no 3D shading». Ключевые кадры должны уже быть в этом стиле: модели держат стиль, заданный стартовым кадром [15].
8. **Камера статичная или медленная.** Меньше движения камеры означает меньше новых областей, которые модель
   должна придумать. Где поддерживается, ставить `seed`, а у Seedance 1.5 — `fixed_lens`.
9. **Автопроверка плана перед склейкой.** Взять 3 кадра (начало, середина, конец) и спросить VLM:
   сколько пылесосов, на полу ли пылесос, похож ли персонаж на лист. Если нет, перегенерировать.
   Ответы проверяет Gemini, как и сейчас.
10. **Звук.** Нативный звук хорош для фона. Речь лучше делать бесплатным Gemini TTS и накладывать отдельно.
    В Kling v3 на Polza звук дороже на 50 %, в Seedance 2 fast — без доплаты.
11. **Черновик, потом финал.** Черновики делать на H3 Max или Omni (46–48 ₽ за план), финал на Seedance 2.0/2.5
    или Kling 4.0. У Seedance 2.5 есть `draft=true` и `finalize` до 1080p.
12. **Провайдер `genapi` (или `kie`)** в `aivideo_providers.py` нужен для Kling 4.0, потому что в Polza его нет.
    Можно также написать в поддержку Polza с просьбой добавить Kling 4.0 reference-to-video и Kling 3.0 Omni.

## Оплата из России и асинхронность

| Площадка | Оплата из РФ | Асинхронный API |
|---|---|---|
| Polza.ai | рубли | `POST /api/v1/media` → id, опрос `/media/:id` |
| GenAPI (gen-api.ru) | российские карты [5] | `callback_url` или опрос |
| kie.ai | нужна зарубежная карта; приём крипты не подтвердил | createTask → taskId, callBackUrl |
| fal.ai, Replicate | нужна зарубежная карта | очередь, request_id |
| Официальные API (Google, BytePlus, Vidu, Kuaishou) | нужна зарубежная карта; Kling 4.0 API ещё не открыт [6] | все асинхронные |

## Источники

1. Каталог Polza (JSON, 22 видеомодели, параметры и цены): https://polza.ai/api/v1/models ; https://polza.ai/collections/video.md
2. Artificial Analysis, I2V и T2V: https://artificialanalysis.ai/video/leaderboard/image-to-video ; https://artificialanalysis.ai/video/leaderboard/text-to-video
3. Анонс Kling 4.0 (Bernama): https://bernama.com/en/press/news.php?id=2622471
4. fal, Kling 4.0 reference-to-video и image-to-video: https://fal.ai/models/kling/v4/reference-to-video/llms.txt ; https://fal.ai/models/fal-ai/kling-video/v4/image-to-video/llms.txt
5. GenAPI, Kling v4 (рубли, параметры, start/end кадр в reference): https://gen-api.ru/model/kling-v4
6. Статус API Kling 4.0 и цены у посредников: https://www.nemovideo.com/blog/ai-video-models/kling-4-0-cost ; цены kie: https://kie.ai/pricing
7. Seedance: режимы кадров и референсов несовместимы: https://docs.apiyi.com/en/faq/seedance-first-frame-mixed-with-reference ; https://comfy.icu/node/KIE_Next_bytedance_seedance_2_fast_c22f5a5e ; https://docs.byteplus.com/docs/ModelArk/2291680
8. Тест приёма «@image1 как первый кадр»: https://pixo.video/blog/seedance-2-first-frame-reference-image
9. MiniMax H3, режимы: https://help.scenario.com/articles/1593651655-minimax-h3-the-essentials ; https://runware.ai/minimax-h3
10. Gemini Omni Flash 1.1: https://blog.segmind.com/gemini-omni-1-1-flash-features-examples-and-1-0-compared/ ; https://runware.ai/docs/models/google-gemini-omni-flash-1-1/guides/first-and-last-frame
11. Seedance 2.5: https://openrouter.ai/blog/insights/seedance-2-5-review ; https://www.yottalabs.ai/post/seedance-2-5-vs-seedance-2-0-differences-which-to-use-2026
12. Vidu Q3/Q4: https://platform.vidu.com/docs/pricing ; https://www.krea.ai/docs/api-reference/video/vidu-q3.md ; https://www.cometapi.com/vidu-q4-preview-launches/
13. Wan 3.0: https://www.mindstudio.ai/models/wan-3-0 ; https://www.krea.ai/docs/api-reference/video/wan-30.md
14. Veo 3.1 (референсы ≤ 3, первый и последний кадр): https://ai.google.dev/gemini-api/docs/video ; https://www.layer3labs.io/guides/veo-3-pricing
15. Аниме-сравнения (вендорские блоги, мнения расходятся): https://openart.ai/blog/best-ai-video-generators-for-anime/ ; https://www.piclumen.com/blog/4-best-ai-animation-models/ ; https://cinemagiq.com/resources/ai-video-generator-anime
16. Sora 2 API: https://www.cometapi.com/sora-api-access-in-2026-pricing-rate-limits-and-what-s-actually-available-through-aggregators/
17. Физика: https://benchmarklist.com/benchmarks/do_joint_audio_video_generation_models_understand_physics/ ; https://benchmarklist.com/benchmarks/phyground/ ; https://aiweekly.co/editors-blog/found-first-six-video-generators-score-below-0-42-on-physics-bench-0-80-on-vbench

Оговорка: большинство сравнений аниме-качества и «консистентности» — блоги вендоров и перепродавцов. Твёрдые данные
есть только в рейтинге AA и в схемах API. Перед переходом стоит прогнать один и тот же план из 2 шагов на Kling 4.0,
Seedance 2.0 (кадры) и H3 Max: это стоит примерно 500 ₽.
