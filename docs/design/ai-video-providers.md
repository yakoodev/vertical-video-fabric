# Провайдеры ИИ-видео: сравнение цен против Polza (01.11.2026)

Курс: **1 $ = 80,56 ₽**. Это курс ЦБ РФ на 31.10.2026; 01.11 воскресенье, поэтому действует он ([ppt.ru](https://ppt.ru/currencies/usd/31-10-2026)).
Цены в долларах пересчитаны по этому курсу без комиссий посредника. Карта зарубежного банка, крипто-карта или обменник добавят ещё 3–10 %.
Ниже везде прайс-листы, ни одной платной генерации не делалось.

**Эпизод** = 5 шотов по 4 с (20 с видео 720p) + 5 ключевых кадров 1K + ~60 слов TTS (~400 символов).

## 1. Главное

* **Polza продаёт Seedance 2.0 Fast с наценкой ~2,2×.** Polza берёт 22,3 ₽/с. У kie.ai, через который Polza и маршрутизирует модель, та же модель стоит 0,124 $/с ≈ 10 ₽/с. Официальный прайс BytePlus: 5,60 $ за 1M токенов × 21 600 токенов/с ≈ 0,121 $/с ≈ 9,7 ₽/с.
* **Картинки Polza продаёт почти по себестоимости.** Nano Banana 2.1 за 3 ₽ против 2,71 ₽ у Google и 1,61 ₽ у kie. Экономия на картинках мала, основная переплата в видео.
* **Veo 3.1 в Gemini API закрыт.** Превью-модели `veo-3.1-*-preview` отключены **22.10.2026**. Замена — `gemini-omni-1.1-flash`: ≈0,10 $/с за 720p со звуком, только платный тариф ([ai.google.dev/…/veo](https://ai.google.dev/gemini-api/docs/veo), [pricing](https://ai.google.dev/gemini-api/docs/pricing)).
* **Бесплатный тариф Gemini.** Для картинок и видео его нет («Not available»). Для **TTS `gemini-3.8-flash-tts`** он есть («Free of charge»). Платно это 0,00225 $ за 10 с аудио до конца 2026 года, затем вдвое дороже.
* **Российские агрегаторы.** ProxyAPI продаёт Seedance 2.0 Fast 720p за **12,63 ₽/с**, это на 43 % дешевле Polza, а API у него асинхронный. Timeweb берёт 39,15 ₽/с, это дороже Polza.
* **kie.ai, похоже, принимает российские способы оплаты.** В интерфейсе пополнения есть блок «Russian Payment Methods: SBP, SberPay, YooMoney, MIR, Russian Bank Cards». Это строка из фронтенда kie.ai/pricing, сам платёж я не проверял (см. §5).

## 2. Сравнительная таблица (i2v 720p, ~4–5 с, звук там, где модель его умеет)

| Провайдер | Модель | Цена за секунду | Ролик 4 с | Картинка (NB 2.x 1K) | Оплата из РФ | Асинхронность |
|---|---|---|---|---|---|---|
| **Polza (сейчас)** | Seedance 2.0 Fast | 22,3 ₽ | **89 ₽** | 3 ₽ (2K 4,5 ₽) | рубли ✅ | шлюз рвёт соединение через 60 с |
| Polza | Grok Imagine 1.5 | 3,04 ₽ | 12,2 ₽ | — | рубли ✅ | — |
| Polza | Kling v3 pro | 12–18 ₽ | 48–72 ₽ | — | рубли ✅ | — |
| **kie.ai** | Seedance 2.0 Fast, без входного видео | 0,124 $ = 10,0 ₽ | **40 ₽** | NB 2.1: 0,02 $ = 1,61 ₽ (2K 2,42 ₽) | по UI есть СБП/МИР/ЮMoney (не проверено); иначе Visa/MC | taskId сразу + callback |
| kie.ai | Seedance 2.0 (std) | 0,205 $ = 16,5 ₽ | 66 ₽ | | | |
| kie.ai | Seedance 2.0 Mini | 0,041 $ = 3,3 ₽ | 13,2 ₽ | | | |
| kie.ai | Grok Imagine (1.5 preview), звук по умолчанию | 4,5 кр = 0,0225 $ = 1,81 ₽ | **7,3 ₽** | Grok image 0,02 $ | | |
| kie.ai | Kling 3.0 std / pro, со звуком | 0,10 $ / 0,135 $ = 8,1 / 10,9 ₽ | 32 / 44 ₽ | | | |
| kie.ai | Wan 3.0 | 0,08 $ = 6,4 ₽ | 26 ₽ | | | |
| kie.ai | Hailuo 2.3 std, 768p 6 с | — | 0,15 $ = 12,1 ₽ за 6 с | | | |
| **ProxyAPI** (РФ) | Seedance 2.0 Fast (звук бесплатно) | **12,63 ₽** | **50,5 ₽** | NB 2.1: 10,19 ₽ (2K 15,29 ₽) ❌ | рубли, юрлицо РФ, НДС 5 % ✅ | job id + polling_url, без вебхуков |
| ProxyAPI | Grok Imagine Video 1.0 / 1.5 / 1.5 Lite | 9,47 / 18,95 / 4,11 ₽ | 38 / 76 / 16 ₽ | | | |
| ProxyAPI | Kling 3.0 std, со звуком | 17,89 ₽ | 72 ₽ | | | |
| AITunnel (РФ) | Seedance 2.0 Fast 720×1280 | 15,42 ₽ | 62 ₽ | NB 2.1 от 6,48 ₽ | рубли ✅ | не проверял |
| AITunnel | Grok Imagine video 720p | 5,1 ₽ | 20 ₽ | | | |
| Timeweb AI Gateway (РФ) | Seedance 2.0 Fast | 39,15 ₽ | 157 ₽ | — | рубли ✅ | API-ключ, 402 при нехватке |
| Timeweb | Kling 3.0 std, со звуком | 17,55 ₽ | 70 ₽ | | | |
| fal.ai | Seedance 2.0 Fast, звук включён | 0,2419 $ = 19,5 ₽ | 78 ₽ | NB 2: 0,08 $ = 6,4 ₽ | только карта (Stripe) ❌ | queue: request_id → status → result |
| WaveSpeed | Seedance 2.0 Fast | 1,00 $ за 5 с = 16,1 ₽ | 64 ₽ (5 с: 81 ₽) | — | карта ❌ | prediction id → result |
| Replicate | Seedance 2.5 (цену 2.0 Fast не нашёл) | 0,2312 $ = 18,6 ₽ | 74 ₽ | — | карта ❌ | prediction id, polling |
| BytePlus ModelArk (официально) | Seedance 2.0 Fast | 5,6 $/1M ток ≈ 0,121 $ = 9,7 ₽ | 39 ₽ | — | зарубежная карта, аккаунт из РФ маловероятен ❌ | task id → GET task |
| Kling official API | Kling 3.0 720p, со звуком | ~0,112 $ = 9,0 ₽ | ~36 ₽ | — | предоплаченные пакеты, карта ❌ | task id → polling |
| MiniMax official | Hailuo 2.3 / 2.3 Fast, 768p 6 с | — | 0,28 $ / 0,19 $ = 22,6 / 15,3 ₽ за 6 с (в легаси после H3) | — | карта ❌ | task id → polling |
| Alibaba Model Studio (intl) | Wan 2.6 i2v / i2v-flash, со звуком | 0,10 $ / 0,05 $ = 8,1 / 4,0 ₽ | 32 / 16 ₽ | — | Alibaba Cloud intl, карта ❌ | task_id → GET tasks |
| **Google Gemini API** (ключ уже есть) | Gemini Omni Flash, 720p со звуком | ≈0,10 $ = 8,1 ₽ | 32 ₽ | NB 2.1: 0,0336 $ = 2,71 ₽, batch 1,35 ₽ (2K 4,06 ₽) | платный тариф = Google Cloud Billing, зарубежная карта; из РФ нужен прокси ⚠️ | Interactions API, `background` + опрос |

TTS (за 1000 символов): Polza Eleven v4 — 4,66 ₽; ProxyAPI Eleven v4 — 5,37 ₽; kie ElevenLabs TTS — 0,03 $ = 2,42 ₽, Eleven V3 — 0,07 $ = 5,64 ₽. Gemini 3.8 Flash TTS: бесплатно на free tier, платно ≈0,0135 $ ≈ 1,1 ₽ за минуту аудио.

### Качество для аниме/2D и постоянство персонажей (качественная оценка, по описаниям, без своих тестов)

* **Seedance 2.0 / Fast.** Первый и последний кадр, до 9 референс-картинок плюс видео и аудио. Хорошо держит стиль исходного кадра; на нём уже собран текущий пайплайн. Mini — тот же интерфейс в 3 раза дешевле, детализация ниже.
* **Grok Imagine 1.5.** По данным kie, первое место на Image-to-Video Arena (720p): 1473 против 1467 у Seedance 2.0. Звук генерируется, `@imageN` ссылается на референсы. На 2D может «оживлять в реализм», поэтому прямо прописывать в промпте 2D/аниме-стиль. При 1,8 ₽/с его выгодно гонять дублями и выбирать лучший.
* **Kling 3.0.** Сильная динамика, multi-shot до 5 планов, Elements для персонажа. Дороже Seedance Fast у kie почти не выходит (std 8,1 ₽/с со звуком).
* **Wan 2.6/3.0, Hailuo.** Дешевле, звук у Wan 2.6+ есть. Брать как запасной вариант для фоновых шотов.
* **Картинки.** Nano Banana 2.1 принимает до 10 входных картинок на kie и до 14 на fal. Google заявляет «multi-turn character consistency». Это по-прежнему лучший вариант для листов персонажей.

## 3. Локально и аренда GPU

| Модель | VRAM / железо | Скорость | Звук |
|---|---|---|---|
| Wan 2.2 A14B I2V + LightX2V 4-step | ~28,6 ГБ BF16; на 24 ГБ нужен FP8 или offload | ~27 с на 720p-клип на RTX 5090 (40 шагов без дистилляции — ~45 мин) ([HF model card](https://huggingface.co/lightx2v/Wan2.2-Distill-Models)) | нет (S2V отдельная модель) |
| LTX-2 / 2.3 / 2.5 (открытые веса) | 24 ГБ с FP8; 2.5 (22B) влезает в 24 ГБ | ~45 с на 4 с 720p со звуком на 4090 (сообщение пользователя), ~25 с на 5090 (NVIDIA) | да, нативный |
| HunyuanVideo / FramePack | 24 ГБ+ (FramePack от ~6 ГБ, но медленно) | минуты на клип | нет |
| Flux.1 Kontext dev (консистентные кадры) | FP8 ~12 ГБ, полная версия ~24 ГБ | секунды на кадр | — |

Аренда: RunPod RTX 4090 ~0,34 $/ч в community, 0,69–0,89 $/ч в secure; 5090 ~0,99 $/ч. Vast.ai: 4090 0,14–0,40 $/ч, 5090 0,34–0,64 $/ч.
Чистая генерация Wan 2.2 4-step на 5090 стоит около 1 ₽ за клип. На деле за час сессии (загрузка ~30 ГБ весов, прогрев, ComfyUI) уходит **≈30–80 ₽ за эпизод**, и звука нет: его придётся делать отдельно паком/TTS. Качество аниме у Wan 2.2 хорошее, но заметно ниже Seedance 2.0. Добавится и ручная работа.
**Вывод:** при цене kie около 7–40 ₽ за клип аренда GPU не окупается. На своей машине (31 ГБ ОЗУ, мало свободной) тяжёлые модели держать нельзя.

## 4. Топ-3

| # | Что | Почему | Эпизод |
|---|---|---|---|
| **1. Дешевле всего при приемлемом качестве** | **kie.ai: Grok Imagine 720p (1,81 ₽/с) + NB 2.1 (1,61 ₽) + Gemini TTS free или ElevenLabs у kie** | В 10 раз дешевле текущего эпизода. Звук нативный, i2v-рейтинг на уровне Seedance. Запасной вариант Seedance 2.0 Mini (3,3 ₽/с, ≈75 ₽ за эпизод) с тем же API, что у Seedance Fast | **≈45 ₽** (36 видео + 8 картинки + ~1 TTS) |
| **2. Лучшее качество за рубль** | **kie.ai: Seedance 2.0 Fast 720p (10 ₽/с)** — та же модель, что сейчас | Ноль потерь качества и промптов, цена в 2,2 раза ниже Polza. Polza сама ходит в kie, так что надёжность та же | **≈209 ₽** (200 + 8 + 1) против **≈463 ₽** у Polza сейчас |
| **3. Проще всего оплатить из РФ** | **ProxyAPI: Seedance 2.0 Fast 720p (12,63 ₽/с)**; картинки оставить на Polza (3 ₽) | Рубли, российское юрлицо, чеки, НДС. Асинхронный `/v1/videos`, деньги списываются только за готовое видео. Картинки у ProxyAPI в 3 раза дороже Polza | **≈270 ₽** (253 + 15 Polza + 2) |

Для справки: Kling 3.0 std со звуком у kie выходит ≈170 ₽ за эпизод. Gemini Omni Flash напрямую — ≈175 ₽, но без зарубежной карты и прокси не работает.
**Если оплата kie по СБП/МИР реально проходит**, пункты 1–2 становятся и самыми простыми в оплате, а ProxyAPI остаётся запасным вариантом.

## 5. Как переключиться (форма API)

**kie.ai** (docs.kie.ai; одна модель на страницу, например `docs.kie.ai/market/bytedance/seedance-2-fast`)
* `POST https://api.kie.ai/api/v1/jobs/createTask`, заголовок `Authorization: Bearer <KEY>`.
  Тело запроса: `{"model":"bytedance/seedance-2-fast","callBackUrl":"…","input":{"prompt":"…","first_frame_url":"https://…","reference_image_urls":[…],"resolution":"720p","aspect_ratio":"9:16","duration":4,"generate_audio":true}}`.
  Для Grok: `"model":"grok-imagine-video-1-5-preview"`, `image_urls`, `resolution`, `enable_audio`, `duration` 1–15. Для NB 2.1: `"model":"nano-banana-2-1"`, до 10 входных картинок.
* Ответ приходит сразу: `{"code":200,"data":{"taskId":"…"}}`. Ошибки тоже идут с HTTP 200, поэтому проверять поле `code`.
* Статус: `GET /api/v1/jobs/recordInfo?taskId=…`. Можно вместо опроса принимать callback; подпись проверять по HMAC и всё равно сверяться с recordInfo. Названия полей результата сверить с docs.
* Входные файлы задаются URL-ами (у kie есть свой upload API). Лимит 20 новых задач за 10 с. Неудачные задачи не списываются. Новым аккаунтам дают 80 кредитов (0,40 $).

**ProxyAPI** ([docs/video-generation](https://proxyapi.ru/docs/video-generation))
* `POST https://api.proxyapi.ru/v1/videos`, `Authorization: Bearer <KEY>`.
  Тело: `{"model":"bytedance/seedance-2.0-fast","prompt":"…","resolution":"720p","duration":4,"aspect_ratio":"9:16","generate_audio":true,"frame_images":[…]}` (или `input_references`).
* Ответ: `{"id":"job-…","status":"pending","polling_url":"…"}`. Опрос: `GET /v1/videos/{id}` до `completed` или `failed`. Файл: `GET /v1/videos/{id}/content`.
* `callback_url` не поддерживается (вернёт 400). Если для сочетания параметров нет цены, запрос отклоняется с 400 до начала генерации. Список моделей: `GET /v1/videos/models`. По форме это близко к video API OpenRouter.

**Google Gemini API** (ключ уже есть)
* TTS сейчас бесплатно: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent`, заголовок `x-goog-api-key`, `responseModalities:["AUDIO"]`.
* Картинки: `gemini-nano-banana-2.1` через `generateContent`, синхронно. Batch API дешевле вдвое. Free tier для картинок нет.
* Видео: `POST /v1beta/interactions` с `{"model":"gemini-omni-1.1-flash","input":[{"type":"image",…},{"type":"text",…}],"response_format":{"type":"video","aspect_ratio":"9:16"},"background":true}`, затем опрос interaction по id. Видео приходит в `steps[].content` (base64) или по URI при `delivery:"uri"`. Нужен платный тариф. Доступ из РФ только через прокси.

Остальные провайдеры: fal — `POST https://queue.fal.run/<model>` → `request_id` → `/status` → результат, заголовок `Authorization: Key <KEY>`. BytePlus — `POST /api/v3/contents/generations/tasks` → id → `GET …/tasks/{id}`. Все они асинхронные, но из РФ упираются в оплату.

**Минимальный план:** добавить в провайдер-слой адаптер kie (createTask, recordInfo, callback) с тем же контрактом, что у Polza-задачи. Модель выбирать на уровне шота: grok — для черновиков и дублей, seedance-2-fast — для финала. TTS перевести на Gemini 3.8 Flash TTS с фолбэком на ElevenLabs. Перед первым пополнением kie проверить, что в окне оплаты действительно есть СБП/МИР; кнопку оплаты не нажимать, пока не принято решение.

## 6. Конечный кадр, хвост видео и блокинг (02.11, разбор серии 3 «Утренний кофе»)

* **Модель без конечного кадра** (`first_last: False` в `VIDEO_LIMITS`: MiniMax H3, Grok) видео к нарисованному
  концу не приводит, поэтому старт следующего плана из `endframe` давал скачок на склейке. Теперь у таких моделей
  этап keyframes **не заказывает** конечные кадры: смета дешевле на их число. Старт плана, который продолжает
  прошлый (`continues_from_prev`), — **последний кадр готового видео** прошлого плана: ffmpeg вынимает его в версию
  `keyframe` с пометкой `source=video_tail; video_ref=<id>`. Видео такого плана в очереди ждёт видео прошлого
  плана и лишних POST не делает. У моделей с первым+последним кадром (Seedance) всё по-старому: `endframe`
  уходит последним кадром и служит стартом продолжения.
* **Плита локации** — пустая комната: без людей, животных, роботов и персонажей проекта (по именам и картам),
  с отдельным блоком `Avoid`. Иначе на плите появляется робот-пылесос, и в кадрах вместо Бипа серый пылесос.
* **Блокинг** — подробный макет (`app/aivideo_layout.py` + `app/aivideo_blocking.py`):
  - одна раскладка комнаты на локацию: все вещи из описания, мебель, которую называют планы, и, если есть ключ,
    разбор плиты Gemini-зрением (один раз, кэш в `aiv_locations.layout_json`);
  - манекены в реальном росте: голова, туловище, руки и ноги в позе; робот-диск ~30 см;
  - реквизит лежит в руках и на вещах;
  - камера ставится по крупности, ракурсу и объективу и никогда не оказывается внутри вещи или стены;
  - самоконтроль кадра лучами: не прошёл — блокинг в кадр не идёт, причина пишется в `params.blocking`.

## Источники

* kie.ai: [сводный прайс](https://kie.ai/pricing) (1 кредит ≈ 0,005 $; строки про оплату СБП/МИР — в этой же странице); [Seedance 2.0 / Fast](https://kie.ai/seedance-2-0); [Seedance 2.0 Mini](https://kie.ai/seedance-2-0-mini); [Grok Imagine](https://kie.ai/grok-imagine); [Grok Imagine 1.5](https://kie.ai/grok-imagine-video-1.5); [Nano Banana 2.1](https://kie.ai/nano-banana-2-1); [Kling 3.0](https://kie.ai/kling-3-0); [Hailuo 2.3](https://kie.ai/hailuo-2-3); [Wan 3.0](https://kie.ai/wan-3-0); [Wan 2.6](https://kie.ai/wan-2-6); [документация API](https://docs.kie.ai/)
* Google: [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing); [отключение Veo 3.1 preview](https://ai.google.dev/gemini-api/docs/veo); [Gemini Omni Flash](https://ai.google.dev/gemini-api/docs/omni)
* ProxyAPI: [каталог](https://proxyapi.ru/pricing); [Seedance 2.0 Fast](https://proxyapi.ru/models/bytedance/seedance-2.0-fast); [Grok Imagine Video](https://proxyapi.ru/models/x-ai/grok-imagine-video); [Grok 1.5](https://proxyapi.ru/models/x-ai/grok-imagine-video-1.5); [Grok 1.5 Lite](https://proxyapi.ru/models/x-ai/grok-imagine-video-1.5-lite); [NB 2.1](https://proxyapi.ru/models/google/gemini-nano-banana-2.1); [Kling 3.0 std](https://proxyapi.ru/models/kwaivgi/kling-v3.0-std); [video API](https://proxyapi.ru/docs/video-generation)
* AITunnel: [каталог моделей](https://aitunnel.ru/models) (цены взяты из JSON страницы). Timeweb: [AI Gateway video](https://timeweb.cloud/docs/ai-gateway/video-api.md). Polza: [каталог](https://polza.ai/models) плюс цены из задания
* fal: [Seedance 2.0 Fast i2v](https://fal.ai/models/bytedance/seedance-2.0/fast/image-to-video); [Nano Banana API](https://fal.ai/learn/tools/nano-banana-api)
* WaveSpeed: [Seedance 2.0 Fast i2v](https://wavespeed.ai/blog/de/posts/introducing-bytedance-seedance-2-0-fast-image-to-video-on-wavespeedai/)
* Replicate (Seedance 2.5) и сводки цен: [atlascloud](https://www.atlascloud.ai/blog/guides/seedance-2-cost-per-second)
* BytePlus: [Segmind о прайсе 5,60 $/1M](https://blog.segmind.com/seedance-2-fast-api-50-off-on-segmind-the-best-seedance-cost-for-developers/); [формула токенов](https://blog.segmind.com/seedance-2-vs-seedance-2-fast-how-video-token-pricing-actually-works); [ModelArk docs](https://docs.byteplus.com/docs/ModelArk/1544106)
* Kling official: [aireiter — Kling 3 API pricing](https://aireiter.com/blog/kling-3-api-pricing-guide-2026); [evolink](https://evolink.ai/blog/kling-3-api-pricing-integration-guide)
* MiniMax: [developersdigest](https://www.developersdigest.tech/models/video-generation/minimax); [minimax-ai.chat](https://minimax-ai.chat/models/minimax-hailuo-23)
* Alibaba: [wan2.6-i2v](https://www.alibabacloud.com/help/en/model-studio/wan2-6-i2v); [developersdigest (flash)](https://www.developersdigest.tech/models/video-generation/alibaba)
* xAI Grok (официальные цены разнятся): [wavespeed](https://wavespeed.ai/blog/cost-and-billing/grok-imagine-video-pricing/); [developersdigest](https://www.developersdigest.tech/models/video-generation/xai)
* GPU: [RunPod pricing](https://www.runpod.io/gpu-cloud/pricing); [computeprices Vast 4090](https://computeprices.com/providers/vast/gpus/rtx4090); [Wan2.2 Distill](https://huggingface.co/lightx2v/Wan2.2-Distill-Models); [NVIDIA LTX-2 guide](https://www.nvidia.com/en-gb/geforce/news/rtx-ai-video-generation-guide); [Flux Kontext local](https://runaihome.com/blog/flux-kontext-dev-local-comfyui-2026/)

**Не проверено:** цены GPTunnel и VseGPT (их страницы отдают пустой SPA). Официальный прайс Replicate для Seedance 2.0 Fast. Работает ли на деле оплата kie по СБП/МИР. Какой именно вариант Grok стоит у AITunnel за 5,1 ₽/с.
