# Аниме-истории 9:16: как это делают люди и что взять нам

Дата: 08.11.2026. Только веб-ресерч, ничего не запускалось. Цены из листингов посредников (fal, Atlas, Krea),
они часто меняются, поэтому перед бюджетом надо проверить. Пометка **[не проверено]** стоит там, где я
не нашёл первоисточник.

Наш случай: 60–90 с, озвучка во втором лице, 2–4 персонажа, комната с мониторами, оверлеи чата и
доната. Рендер у нас свой: Python и headless Chromium, кадр за кадром, плюс ffmpeg.

---

## 1. Как делают ролики «ты стример с тремя зрителями… часть 1/2/3»

- Разбора конкретных серий я не нашёл: ни одна такая серия не публикует свой пайплайн **[не проверено]**.
  Всё ниже собрано из гайдов и обсуждений в Reddit о «AI anime series / POV shorts».
- **Основной дешёвый конвейер 2026 года — ИИ.** Сначала делают лист персонажа или эталонный кадр
  (Nano Banana, Midjourney, Seedream). Потом каждый план отдельно прогоняют через image-to-video
  (Kling, Seedance, Vidu, Hailuo). Озвучка через ElevenLabs, монтаж в CapCut.
  Источники: [обзор тредов Reddit, Nano Banana + Kling](https://reddit.sentinel-team.org/posts/1t868dw/snapshots/2026-05-16T00%3A42%3A25.170287Z),
  [гайд по AI-сериалу для Shorts](https://easyviral.ai/blog/how-to-make-ai-animated-series),
  [гайд по POV-видео](https://productify.beehiiv.com/p/how-to-make-trending-ai-pov-videos-step-by-step-guide),
  [Anijam: TikTok short drama](https://www.anijam.ai/blog/tiktok-short-dramas/).
- Самое трудное везде одно: удержать одинакового персонажа во всех планах. Один автор пишет, что выбрасывает
  примерно половину генераций ([тред](https://reddit.sentinel-team.org/posts/1t868dw/snapshots/2026-05-16T00%3A42%3A25.170287Z)).
  Лист со всеми ракурсами, поданный как референс, часто работает не хуже LoRA (там же).
  У Kling есть «Element Library»: персонажа собирают из 2–4 картинок и закрепляют за ним голос.
- **Рабочий пайплайн VTuber-ов — Live2D и VTube Studio.** В VTube Studio анимацию записывают в
  `.motion3.json`, потом дочищают в Cubism Animator ([VTS wiki: Record Animations](https://github.com/DenchiSoft/VTubeStudio/wiki/Record-Animations)).
  Готового рецепта «скетч для Shorts на Live2D» я не нашёл **[не проверено]**.
- Moho, Toon Boom и Blender Grease Pencil встречаются только у студий с аниматорами. Для дешёвого потока
  их никто не советует (подтверждения ценой я не искал).
- Viggle переносит движение с видео на персонажа. Цены и условия коммерческого использования разнятся
  от источника к источнику, официальные страницы отдают 403
  ([clipjury](https://clipjury.pages.dev/can-you-monetize/viggle)) **[не проверено]**.

**Вывод.** Типичный объём сегодня делают как «ИИ-стоп-кадры персонажа, к ним короткие i2v-клипы, сверху
озвучка». Чистый риг (Live2D или Spine) берут, когда персонаж постоянный и в кадре много говорящих голов.

---

## 2. Готовые аниме-ассеты с коммерческой лицензией

### Live2D: официальные сэмпл-модели
- Общие пользователи и малые предприятия с выручкой **меньше 10 млн иен в год** могут использовать
  материалы свободно, в том числе коммерчески. Средним и крупным компаниям это запрещено
  ([help 2449](https://help.live2d.com/en/?p=2449), [help 2546](https://help.live2d.com/en/?p=2546)).
  Нужно принять Free Material License и Sample Data Terms (v1.7, 29.01.2026,
  [условия](https://www.live2d.com/eula/live2d-sample-model-terms_en.html)).
- Исключения: Unity-chan и Hatsune Miku. Хиёри нельзя менять ни в чём. Запрещены эротика, насилие и всё,
  что «вредит образу персонажа» ([условия](https://www.live2d.com/en/learn/sample/model-terms/)).
- В каталоге около 28 моделей ([список](https://www.live2d.com/en/learn/sample/)). Это маскоты Live2D,
  а не наши герои. Почти все показаны по пояс, рук и поз у них мало.
- **Cubism SDK for Web.** Это JS/WebGL, нужен Cubism Core из официального пакета
  ([docs](https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web)). Удобная обёртка:
  [pixi-live2d-display](https://github.com/RaSan147/pixi-live2d-display) (PixiJS 7, Cubism 2.1/4,
  в форке есть `model.speak()` для липсинка). Рендер в headless Chromium с WebGL официально не описан.
  Технически препятствий не вижу, но сам не проверял **[не проверено]**.
- **Лицензия SDK на видео.** Видео из инструмента на SDK формально попадает в «Running Royalty, Plan C».
  Но там же сказано, что физлица и малые предприятия от лицензии и оплаты освобождены, кроме случая
  Expandable Application ([SDK license](https://www.live2d.com/en/sdk/license/)). Видео, экспортированные
  из Editor, договора не требуют вовсе ([SDK](https://www.live2d.com/en/download/cubism-sdk/)).
  Выручку vtubika.store я не знаю, а порог около 10 млн иен (примерно $65k) **[уточнить у владельца]**.

### Inochi2D
- Это открытая замена Live2D, Creator под BSD-2. Пример-модели Aka и Midori идут под **CC BY 4.0**, автор seagetch
  ([example-models](https://awesome.ecosyste.ms/projects/github.com%2Finochi2d%2Fexample-models)).
- Веб-рантайм [Inox2D](https://github.com/Inochi2D/inox2d) (Rust/WASM, WebGL) сами авторы называют
  прототипом «не для продакшна». MeshGroup и анимации в нём ещё не работают. Готовых моделей почти нет.

### VRoid Studio и VRoid Hub, VRM
- Пресеты и всё, что сделано в VRoid Studio, можно использовать коммерчески, в том числе в видео,
  если у конкретного ассета нет особых условий
  ([VRoid Studio Guidelines](https://vroid.com/en/studio/guidelines)). Купленные чужие текстуры и одежда
  подчиняются своей лицензии.
- У моделей с VRoid Hub в мете VRM 1.0 есть флаги «Corporate Use» и «Commercial Use by Individuals»
  ([conditions of use](https://developer.vroid.com/en/guidelines/conditions_of_use.html),
  [VRM license meta](https://vrm.dev/en/vrm/meta/license/)). Флаг проверяем на каждой модели.
  Есть CC0-модели из VRoid ([OpenGameArt](https://opengameart.org/content/vroid-studio-cc0-models)).
- **three-vrm и MToon.** MToon задуман под «японское аниме-выражение»: два цвета, свет и тень, жёсткость тени,
  контур ([MToon](https://dwango.github.io/en/vrm/univrm/shaders/mtoon)). В three-vrm есть
  expressions/visemes, lookAt, springbone и пальцы. VRMA проигрывается через
  [`@pixiv/three-vrm-animation`](https://pixiv.github.io/three-vrm/docs/modules/three-vrm-animation)
  (`createVRMAnimationClip`). Mixamo ретаргетят своим загрузчиком
  ([пример loadMixamoAnimation](https://huggingface.co/spaces/AIbee999/CharacterGen_test/blob/604b72580a799f334d82bcc49c9075dce80694f4/render_script/three-js/src/loadMixamoAnimation.js)).
- **Похоже ли это на 2D-аниме?** Это всё тот же 3D-тун. Свет и тень бегают по поверхности не так, как
  нарисованная тень в 2D ([80.lv](https://80.lv/articles/making-3d-cel-shading-move-more-naturally-stay-consistent)).
  Дефолтные текстуры VRoid дают узнаваемый «VRoid-лук»
  ([zenn](https://zenn.dev/toshikun/scraps/cbb69521efbccb)). Владелец уже забраковал 3D с тун-проходом,
  поэтому риск повторного «не нравится» высокий.

### Спрайты для визуальных новелл (CC0 и бесплатные)
- [DoubleFree: 4 персонажа](https://doublefree.itch.io/4-character-sprites-for-visual-novels): «free for any use»,
  набор эмоций.
- [Breezy: VN sprites](https://breezy-the-cat.itch.io/visual-novel-sprites): **CC0**, PSD и PNG, больше 5 эмоций.
  Автор просит не пускать их в генеративный ИИ.
- [Kioshi/Haruo](https://potat0master.itch.io/free-character-sprite-for-visual-novels-kioshi-haruo):
  коммерческое использование можно, перепродавать нельзя. Сиёкой идёт под CC BY-ND, то есть менять нельзя.
- Минусы: стиль у всех разный, позы стоячие, комнаты стримера и мониторов нет. Годятся разве что для
  второстепенных зрителей-аватарок в чате.

---

## 3. Руки: как с ними справляются 2D-риги

- **Кадрирование.** VTuber-модель почти всегда обрезана по пояс или грудь, кисти уходят за край кадра
  ([r/Live2D](https://redlib.snake-cloud.ts.net/r/Live2D/comments/1hwv5v9/arm_help)).
- **Готовые наборы рук и тогглы.** Каждый жест (машет, показывает, большой палец, клавиатура) рисуют
  отдельным комплектом предплечья с кистью и переключают. Одновременно виден только один.
  В Cubism для этого есть Pose: группы деталей с фейдом, по умолчанию 500 мс
  ([Pose Settings](https://docs.live2d.com/en/cubism-editor-manual/pose-setting/)). Жесты продают
  именно как «toggles» ([пример](https://fiverr.com/jamesgideonsam/create-live2d-vtuber-expressions-and-toggles-hand-gestures-model-matched-emotes)).
- **Hand-tracking-риги** (пальцы по отдельности под трекинг VTube Studio) дороги и делаются отдельными
  продуктами ([пример рига](https://moleworks.itch.io/live2d-advanced-hand-rig)). Нам такое не нужно.
- **Что красиво и дёшево:** кисть никогда не деформируется. Каждую позу руки рисуют целиком: кисть с
  предметом (мышь, кружка, телефон, руки на клавиатуре) или жест. Меняют их резкой сменой или
  1–2 кадрами смаза. Наш код-кукольный путь сломался ровно на том, что руки рисовались кодом.
- В ИИ-видео руки остаются главным артефактом. Помогает исходник, где руки хорошо видны, два дубля и
  перегенерация по маске ([hackernoon](https://hackernoon.com/how-to-fix-warped-hands-and-faces-in-ai-generated-video),
  [higgsfield](https://higgsfield.ai/blog/ai-video-hands-faces)).

---

## 4. ИИ-видео для аниме-движения (ноябрь 2026)

| Модель | Аниме и персонаж | Цена (посредник) | Источник |
|---|---|---|---|
| Vidu Q3 (reference-to-video) | Krea прямо пишет «excelling at anime», принимает 1–4 референса (Q3-Mix до 7) | $0.042–0.05/с (Atlas), $0.074/с 540p / $0.16/с 720p (Krea) | [Krea](https://www.krea.ai/docs/api-reference/video/vidu-q3.md), [Atlas](https://www.atlascloud.ai/providers/shengshu), [Aliyun](https://help.aliyun.com/en/model-studio/viduq3-mix-reference2video) |
| Hailuo 2.3 (MiniMax) | хвалят за то, что держит лицо и цвета костюма в i2v-аниме (вендорский обзор) | около $0.28 за 6 с 768p (≈$0.047/с), Fast около $0.19 | [Krea](https://www.krea.ai/docs/api-reference/video/hailuo-23.md), [magichour](https://magichour.ai/blog/hailuo-23-pricing), [PixVerse](https://pixverse.ai/en/blog/best-ai-anime-video-generators-2026) |
| Kling 3.0 | i2v хорошо держит персонажа, Elements из 2–4 картинок | $0.112/с 720p, $0.168/с с аудио (fal) | [fal](https://fal.ai/learn/tools/how-to-use-kling-3-0-pro), [fal 4.0 vs 3.0](https://fal.ai/learn/devs/kling-4-0-vs-kling-3-0) |
| Seedance 2.0 (reference-to-video) | до 9 референсов, мультишот «Shot 1/Shot 2» в одном клипе | $0.07/с 480p, $0.15–0.30/с 720p (разброс между источниками) | [fal](https://fal.ai/models/bytedance/seedance-2.0/reference-to-video/playground), [Segmind](https://www.segmind.com/seedance2), [Runflow](https://www.runflow.io/models/bytedance/seedance-2.0/reference-to-video) |
| Wan 2.2 Turbo | дёшево, про удержание персонажа данных нет | около $0.02/с | [Atlas](https://www.atlascloud.ai/blog/ai-image-to-video-models-compared) |
| **Index-AniSora** (Bilibili) | **модель специально под аниме**, открытая, Apache 2.0, на базе Wan 2.2, есть i2v с масками | своё железо, $0 | [GitHub](https://github.com/bilibili/Index-anisora), [HF](https://huggingface.co/IndexTeam/Index-anisora) |
| Hedra Character-3 | говорящая голова по фото или арту, заявлены «illustrated characters» | 3–6 кредитов/с | [Hedra](https://hedra.com/docs/pages/studio/getting-started/video-models) |

- Персонажа держат референсы (лист ракурсов) плюс старт с нашего же кадра (i2v). LoRA для API-моделей не
  нужна. Стиль рисовки всё равно может гулять между планами, проверять надо на своём арте
  ([Atlas](https://www.atlascloud.ai/blog/ai-image-to-video-models-compared)).
- Хватит ли нашей видеокарты для AniSora, я не проверял, а память на ПК и так в дефиците
  **[не проверено]**.
- **Ограничение у нас:** 02.11 владелец остановил тесты ИИ-видео. Тратить деньги можно только по его команде.

---

## 5. Гибриды, которыми пользуются

- **ИИ-арт, разложенный на слои, плюс риг.** [See-through](https://github.com/shitagaki-lab/see-through)
  раскладывает одну аниме-картинку на слои (до 23: волосы, глаза, одежда), дорисовывает скрытое и
  отдаёт PSD. Учили на 9 102 Live2D-моделях. Сам риг See-through не делает.
  Его дополняет [Stretchy Studio](https://github.com/MangoLion/stretchystudio): MIT, в браузере, авторигает
  PSD от See-through (скелет через DWPose, скиннинг рук и ног, таймлайн). По отзыву из Reddit
  инструмент «ранний, но рабочий», только гуманоиды
  ([тред](https://reddit.sentinel-team.org/posts/1sjj7ta/snapshots/2026-04-17T21%3A26%3A14.714661Z)).
  Можно вести PSD в Live2D Cubism или в Spine. Spine стоит $69/$369 разово, рантайм требует лицензии
  при выручке меньше $500k ([Spine purchase](https://esotericsoftware.com/spine-purchase),
  [форум](https://esotericsoftware.com/forum/d/29824-spine-43--2d-animation-buy-form)).
- **VRM 3D с аниме-шейдером на 2D-фоне.** Это делают инструменты для MV (VRM Dance Dream MV, Mocasta)
  и демо на three.js ([dev.to](https://dev.to/masaakiimai/how-to-make-your-favorite-anime-character-dance-in-3d-on-the-web-threejs-deepmotion-57fp)).
  По сути это наш забракованный 3D-тун.
- **ИИ-видео для ключевых планов, остальное риг или стоп-кадры.** Так советуют гайды по AI-сериям:
  дорогую модель оставляют на «важные» сцены, массу делают дёшево
  ([Atlas](https://www.atlascloud.ai/blog/ai-image-to-video-models-compared), [easyviral](https://easyviral.ai/blog/how-to-make-ai-animated-series)).

---

## 6. Рейтинг вариантов для нас

Оценки 1–5, где 5 лучше. «Цена» означает деньги за историю 60–90 с, «Интеграция» — простоту встраивания в
Chromium и ffmpeg, «Лицензия» — низкий риск.

| # | Вариант | Качество | Цена/ролик | Интеграция | Лицензия | Итог |
|---|---|---|---|---|---|---|
| **1** | **Позы-ключи ИИ (limited animation) и 2–4 ИИ-видео-вставки** | 4 | 4 (≈$1–4) | 5 | 4 | **лучший** |
| 2 | See-through → Stretchy/Live2D-риг, руки отдельными спрайтами | 3–4 | 5 ($0) | 3 | 5 | **второй** |
| 3 | Полностью ИИ-видео (Seedance 2.0 / Vidu Q3 ref, мультишот) | 4–5 | 2 (≈$8–45 с дублями) | 5 | 3 | на крупные эпизоды |
| 4 | Live2D-сэмплы через pixi-live2d-display | 3 | 5 | 4 | 3 (порог 10 млн иен, чужие маскоты) | не наши герои |
| 5 | AniSora локально | ? | 5 | 2 | 5 | проверить железо |
| 6 | VRoid и three-vrm с MToon | 3 | 5 | 4 | 4 | риск «опять 3D» |
| 7 | CC0-спрайты для визуальных новелл | 2 | 5 | 5 | 5 | только массовка |

Как считал цену в вариантах 1 и 3. В варианте 1 ИИ-видео около 15–25 с из 75, по $0.05–0.11/с
(Vidu Q3, Hailuo, Kling) с двойным запасом на дубли. В варианте 3 все 75 с по $0.05–0.30/с, дублей ×2.

### Главная рекомендация: «pose-to-pose» на ИИ-кадрах и точечное ИИ-видео
1. Делаем лист персонажа в nano-banana (у нас уже есть). Из него получаем **библиотеку целых поз** на каждого
   героя, примерно 10–15 штук: печатает, откинулся, ладони к лицу, держит телефон, тянется к мыши, ликует.
   Руки нарисованы внутри позы и кодом не трогаются. Это решает «че с руками».
2. Движение даём по-анимешному, «лимитированно». Позы меняются на бит озвучки: 2–3 кадра смаза или
   промежуточной позы, отскок (squash), тряска камеры, параллакс. Поверх этого то, что у нас уже работает
   (дыхание, моргание, рот патчами). Чат, донаты и мониторы остаются нашими HTML-оверлеями.
3. Только на 2–4 плана с настоящим телом (вскочил, развернулся, упал на кровать) берём i2v от нашего же кадра
   с референсами: Vidu Q3 ref или Hailuo 2.3 как дешёвые, Kling 3.0 или Seedance 2.0 как дорогие.
   Клипы по 3–6 с вклеиваем через ffmpeg.
4. Для этого нужно разрешение владельца на траты, ориентир 100–400 ₽ за историю **[оценка]**.

### Второй вариант: слоёный ИИ-арт и бесплатный риг
See-through раскладывает наш nano-banana-арт на слои, Stretchy Studio (MIT, браузер) или Cubism ригают
корпус и голову. Руки делаем не мешем, а набором готовых кистей с Pose-переключением, как у VTuber-ов.
Денег не стоит, но на каждого героя уходит день-два работы. Качество движения ниже ИИ-видео, а
авториг ещё сырой.

### Что не делать
- Не рисовать руки и одежду кодом, это уже провалилось.
- Не возвращаться к 3D-туну через VRM, если владелец сам не попросит.
- Не брать Live2D-маскоты (Хиёри и других) как наших героев: это чужой бренд, менять их нельзя, и стоит порог по выручке.
