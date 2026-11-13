"""Инструменты слоёв: справка по сценам и слоям, схема параметров (из ``app/layer_tools.py``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import motion
from app.tool_core import _current_render, _layers_of, _num, _obj, ImageResult, ToolContext, ToolError

SCENE_TEMPLATES = {
    "scene-iso-title": "изометрический 3D-титр на плите (ортокамера)",
    "scene-logo-spin": "вращающийся выдавленный значок с toon-обводкой",
    "scene-parallax-2d": "2D-иллюстрация слоями, пролёт камеры с параллаксом",
    "scene-lowpoly-orbit": "low-poly остров, облёт камеры, туман",
    "scene-kinetic-text": "кинетический 3D-текст по буквам",
    "scene-confetti-3d": "3D-конфетти и титр",
    "scene-card-flip": "карточка-переворот «до/после»",
    "scene-subscribe": "концовка «Подпишись»: кнопка, колокольчик, ник",
}


def h_get_scene_reference(_ctx: ToolContext, args: dict) -> dict:
    """Схема JSON-сцены слоя scene + примеры (docs/motion/scene-engine.md)."""
    from app import scene_engine as se

    examples = se.scene_examples()
    name = str(args.get("example") or "").strip()
    if name:
        if name not in examples:
            raise ToolError(f"примера «{name}» нет; есть: {', '.join(examples)}")
        return {"example": name, **examples[name]}
    return {
        "reference": se.scene_reference(),
        "examples": examples,
        "gallery_templates": SCENE_TEMPLATES,
        "doc": "docs/motion/scene-engine.md",
        "next": "собери сцену → preview_scene_frame(scene, t: [0, 1, 2]) → add_layers([{type: 'scene', start, end, "
                "props: {h?, scene}}]) → view_frame в клипе",
    }


def _scene_times(args: dict) -> list[float]:
    raw = args.get("t", 0)
    values = raw if isinstance(raw, list) else [raw]
    times = [max(0.0, v) for v in (_num(x) for x in values) if v is not None]
    return times[:4] or [0.0]


def h_preview_scene_frame(ctx: ToolContext, args: dict) -> ImageResult:
    """Кадр(ы) JSON-сцены без клипа (или слоя scene клипа по layer_id) — тем же компоновщиком, что рендер."""
    from app import scene_engine as se
    from app.motion_render import CompositorMissing

    props: dict = {}
    duration = _num(args.get("duration"))
    if args.get("layer_id"):
        layer = next((l for l in _layers_of(_current_render(ctx)) if str(l.get("id")) == str(args["layer_id"])), None)
        if layer is None:
            raise ToolError(f"слоя {args['layer_id']} нет в клипе")
        if layer.get("type") != "scene":
            raise ToolError(f"слой {args['layer_id']} — {layer.get('type')}, а нужен scene (кадр клипа — view_frame)")
        props = layer.get("props") or {}
        scene = props.get("scene")
        duration = duration or max(0.1, (_num(layer.get("end"), 3.0) or 3.0) - (_num(layer.get("start"), 0.0) or 0.0))
    else:
        scene = args.get("scene")
    if isinstance(scene, str):
        try:
            scene = json.loads(scene)
        except ValueError as exc:
            raise ToolError(f"scene — не JSON: {exc}") from exc
    if not isinstance(scene, dict):
        raise ToolError("нужен scene (объект сцены, см. get_scene_reference) или layer_id слоя scene")
    structural: list[str] = []
    scene = se.SceneSpec().norm(scene, motion._Report(structural), "scene")
    w = _num(args.get("w")) or _num(props.get("w")) or 1080.0
    h = _num(args.get("h")) or _num(props.get("h")) or 1920.0
    w, h = min(4000.0, max(16.0, w)), min(4000.0, max(16.0, h))
    times = _scene_times(args)
    duration = duration or max(times) + 1.0
    bg = args.get("background", "#202028")
    background = None if bg in (False, None, "", "none", "transparent") else (bg if isinstance(bg, str) else "#202028")
    width = int(_num(args.get("width"), se.PREVIEW_WIDTH) or se.PREVIEW_WIDTH)
    check = se.check_scene(scene, w, h, times[0], duration)
    try:
        frames, info = se.render_scene_png(ctx.store, scene, times, w=w, h=h, dur=duration, background=background,
                                           width=width)
    except CompositorMissing as exc:
        raise ToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc
    errors, warnings = se.format_problems(check)
    info.update({"errors": structural + errors, "warnings": warnings, "stats": (check or {}).get("stats", {}),
                 "viewport": [w, h], "time": "секунды слоя (0 = start)"})
    if len(frames) == 1:
        return ImageResult(data=frames[0], mime_type="image/png", info=info)
    import tempfile

    from app.motion_render import tile_frames_jpeg

    with tempfile.TemporaryDirectory(prefix="vvf-scene-sheet-") as tmp:
        paths = []
        for k, data in enumerate(frames):
            path = Path(tmp) / f"f{k}.png"
            path.write_bytes(data)
            paths.append(path)
        sheet = tile_frames_jpeg(paths, [f"t={t:g}" for t in times], width=min(info["width"], 400), cols=len(paths))
    return ImageResult(data=sheet, mime_type="image/jpeg", info=info)


def h_get_motion_reference(ctx: ToolContext, args: dict) -> dict:
    """Шпаргалка под формат: из аргументов, иначе — формата текущего клипа (если он есть)."""
    from app.formats import settings_layers_format, settings_render_format

    fmt, layers_fmt = args.get("format"), args.get("layers_format")
    if not fmt and ctx.clip_plan_id is not None:
        try:
            render = _current_render(ctx)
            fmt, layers_fmt = settings_render_format(render), layers_fmt or settings_layers_format(render)
        except KeyError:
            pass
    return {"reference": motion_reference(fmt, layers_fmt), "spec": "docs/motion/spec.md"}


# --------------------------------------------------------------------------- #
# Справка по слоям: из констант app.motion — для системного промпта и MCP
# --------------------------------------------------------------------------- #


def motion_enums() -> dict:
    return {
        "types": list(motion.LAYER_TYPES),
        "easings": list(motion.EASINGS),
        "anims": list(motion.ANIMATIONS),
        "loops": list(motion.LOOPS),
        "shapes": list(motion.SHAPE_KINDS),
        "charts": list(motion.CHART_KINDS),
        "callouts": list(motion.CALLOUT_STYLES),
    }


def motion_reference(fmt: Any = None, layers_format: Any = None) -> str:
    """Компактная шпаргалка по слоям (полный контракт — docs/motion/spec.md). Кадр — формата
    клипа (app/formats.py); 9:16 со слоями в 9:16 (по умолчанию) — прежний текст."""
    from app.formats import frame_size, ref_size, settings_format

    fmt, layers_format = settings_format(fmt), settings_format(layers_format)
    text = _motion_reference_text()
    if fmt == "9:16" and layers_format == "9:16":
        return text
    (width, height), (rw, rh) = frame_size(fmt), ref_size(layers_format)
    if layers_format == fmt:
        head = f"КАДР {width}×{height} (формат {fmt}, опорные px)"
    else:
        head = (f"КАДР {width}×{height} (формат {fmt}); px слоёв — опорные кадра разметки {rw}×{rh} (layers_format "
                f"{layers_format}, при рендере слои сами перекомпонуются в {fmt})")
    # Безопасная зона площадок формата разметки слоёв (app/safe_zones.py) — её же проверяет add_layers.
    from app.safe_zones import safe_zone

    zone = safe_zone(layers_format)
    head += (f". БЕЗОПАСНАЯ ЗОНА {layers_format} ({zone['platforms']}): края текста в {zone['top']:g}–{zone['bottom']:g} "
             f"по высоте и {zone['left']:g}–{zone['right']:g} по ширине")
    if zone["watermark"]:
        head += ", не в правом нижнем углу (водяной знак канала)"
    text = text.replace("КАДР 1080×1920 (опорные px)", head, 1)
    return text.replace("Кусок ролика 4:3/16:9 во весь кадр (w 1080)", f"Кусок ролика шире кадра во весь кадр (w {rw})")


def _motion_reference_text() -> str:
    enums = motion_enums()
    fields = "; ".join(f"{kind}: {', '.join(props)}" for kind, props in motion.PROPS.items())
    return f"""\
КАДР 1080×1920 (опорные px). x,y — центр слоя в долях кадра (0..1). Время — секунды клипа.
ПРЕДЕЛЫ: слоёв в клипе ≤ {motion.MAX_LAYERS} (add_layers сверх предела откажет и скажет, сколько осталось), ключей камеры ≤ {motion.MAX_CAMERA_KEYS},
  точек ключей на свойство ≤ {motion.MAX_KEYS_PER_PROP}. Россыпь звёздочек/искр — ОДИН слой particles, не десятки shape.
Слой: {{id?, type, name?, start, end, track 0..7 (выше = поверх), behind (за персонажем, только при хромакее),
  followCamera (едет и зумится вместе с видео по ключам камеры — фон/скрин за персонажем, рамка у объекта; по умолчанию слой стоит), x, y, scale, rotation, opacity,
  keys {{prop: [{{t (от start слоя), v, ease?}}]}} — prop: x,y,scale,rotation,opacity,progress, cropX,cropY,cropW,cropH (image/video: окно crop в долях — скролл экрана в неподвижном телефоне),
  in {{type, duration}}, out {{type, duration}} (null или duration 0 — без анимации; у слоя до конца клипа out не играет), loop {{type, speed 0.1..5, amount 0..3}},
  shadow {{color, blur, x, y, opacity}}, blend normal|multiply|screen|overlay|add, props {{...}}}}
type: {', '.join(enums['types'])}
in/out: {', '.join(enums['anims'])} (typewriter/words/rise — только text; draw — shape line/arrow/ellipse/scribble)
  rise — каждое слово выезжает снизу из-под невидимой маски своей строки с проявлением, по очереди (титры промо, «дорогой» текст)
  glitch/scramble — только text (RGB-расслоение со срезами / буквы перебираются и «расшифровываются»); stomp — удар у любого слоя
loop: {', '.join(enums['loops'])} (glitch — редкие сбои, wave — буквы волной: только text)
react {{source voice|music|mix, prop scale|glow|opacity|y, amount 0..3, smooth 0..1, color?}} у любого слоя — дышит громкостью клипа
  (огибающую считает сервис; music — фоновый трек клипа). Не больше 1–2 «дышащих» слоёв на сцену
3D: у слоя z (глубина, опорн. px: −300 ближе … 600 дальше; задана — слой в сцене, едет с камерой), rotX/rotY (наклон
  с перспективой, °; ключи z/rotX/rotY); у ключа камеры z (dolly), rotX/rotY (наклон/облёт ±12°), fov (40), baseZ (видео, 1000).
  Проще camera_preset(preset, start): parallax_push, orbit, dolly_zoom, tilt_reveal, float, card_flip (layer_id),
  screen_tilt (группа layer_ids / layer_prefix — блоки страницы «wp<id>_»: наклон экрана + наезд в точку x/y).
  pivot {{x, y}} (доли кадра) у слоя — rotX/rotY и ключи scale вокруг этой точки: одинаковые pivot и ключи у группы
  слоёв — наклон и наезд группы как одного экрана
ease: {', '.join(enums['easings'])} или bezier(x1,y1,x2,y2)
props по типу:
  text: text (≤400, \\n), font (family из list_fonts; свой — upload_font), weight 100..900, size 12..400, color, gradient [c1,c2]|null, stroke {{color,width}},
        bg {{color,opacity,radius,padX,padY}}|null, align, maxWidth, lineHeight, letterSpacing, uppercase, italic,
        highlight {{words:[индексы слов], color, bg, style plate|marker (маркер под буквами), draw (с, прорисовка слева направо), delay}},
        backdrop {{color #000000, opacity 0.55, spread px}}|true|null — мягкое затемнение-градиент ЗА текстом (scrim):
        титр читается, когда под него наездом заезжает телефон/пёстрый фон. Символы-декор {motion.DECOR_GLYPHS}
        внутри текста рисуются векторно цветом текста (в шрифтах их нет — без этого был бы квадрат)
  shape: kind {'|'.join(enums['shapes'])} (star4 — четырёхлучевая искра ✦), w, h, fill|null, stroke {{color,width}}, radius, points, dash
         rough 0..1 — «от руки» (ellipse/rect/line/arrow/scribble: линия дрожит, круг заходит сам на себя, наконечник — два штриха),
         seed, boil (дрожит 8 раз/с); scribble — штриховка маркером по w×h; cursor — курсор мыши (остриё — левый верх w×h)
  image: asset_id (из библиотеки) ИЛИ src ("emoji:1f525", "sticker:<pack>/<file>"), w, radius, border, crop, flipX,
         device "phone" (картинка в корпусе телефона; корпус от w), deviceColor, island,
         statusBar (true или доля ширины экрана 0..0.4: полоса «9:41 · связь · батарея» НАД записью, экран выше на неё;
         нужна, когда в записи нет строки статуса — иначе островок ляжет на шапку приложения), statusBarColor, screenGlare (блик стекла).
         island по умолчанию: у image — да, у video — только со statusBar
  video: asset_id (видео из библиотеки: upload_video / add_video_from_source / add_video_from_url), from (секунда файла на start слоя),
         speed 0.25..4, loop, freeze (по умолч. true — стоп-кадр в конце), hold (true — стоп-кадр секунды from на ВЕСЬ слой:
         «замереть на экране оплаты» = слой video с hold и нужным from), + всё от image (w, radius, crop, device, statusBar…).
         По умолчанию НЕМОЕ; audio: true — звук файла в миксе (тот же from/speed/loop; hold и speed вне {motion.VIDEO_AUDIO_MIN_SPEED:g}..{motion.VIDEO_AUDIO_MAX_SPEED:g} — тишина)
         дорожкой: volume 0..2 × gain_db {motion.TRACK_MIN_GAIN_DB:g}..+{motion.TRACK_MAX_GAIN_DB:g}, без автоподгонки — отдельный audio-слой со звуком ролика не нужен.
         Кусок ролика 4:3/16:9 во весь кадр (w 1080) — auto_crop_layer(layer_id): окно crop + ключи cropX по планам (step на склейках).
         Промо приложения: запись экрана в device "phone"; нарезка 1–3 с = несколько video-слоёв подряд
         с разными from; свайп между экранами = ключи x (0.5→−0.5 / 1.5→0.5); скролл = ключи cropY;
         наезд = ключи scale+y слоя (камера на холсте телефон не зумит)
  emoji: char ("🔥"), size
  particles: kind {'|'.join(motion.PARTICLE_KINDS)} (burst — разлёт из центра; sparkle — мерцание искр по кругу spread;
         confetti — конфетти сверху вниз; rise — искорки вверх), count 1..{motion.MAX_PARTICLES}, colors [до 6], shape {'|'.join(motion.PARTICLE_SHAPES)}
         (или asset_id картинки), size (px, ±45%), spread (радиус/полуширина области, px), duration (жизнь/цикл, с; burst 1.2,
         confetti 3, sparkle 1.6, rise 3), gravity (px/с², burst 500, confetti 900), repeat (burst/confetti каждые duration),
         glow (свечение), seed (раскладка; нет — от id). ОДИН слой рисует все частицы — кульминация, «вау», итог промо
  counter: from, to, decimals, prefix, suffix, thousands + стили text; рост по progress (по умолч. первая половина);
         roll: true — одометр (каждая цифра крутится барабаном, stagger 0.06 с между разрядами, laps 2 лишних оборота)
  РЕКЛАМНЫЙ ПАКЕТ (промо продукта/сервиса; готовое — list_motion_templates(category: "Реклама") → add_motion_template).
         Значки icon — свои: {', '.join(motion.AD_ICONS)} или 1–3 буквы (эмодзи нельзя); картинки — id файла / sticker:…;
         theme dark|light, accent (#6c5cff), font (Manrope):
  bubbles: items [{{label, value (число, считает от 0), prefix, suffix, icon|image, color}}] ≤12, w, h (рамка облака 960),
         seed (раскладка без наложений), stagger 0.12, float 12, focus (номер; растёт ×focusScale, остальные гаснут), focusAt, style glass|solid
  carousel: cards [{{image, title, subtitle, meta («1,2 млн прослушиваний»), metaIcon (play)}}] ≤12, cardW 600, cardH 780,
         interval 1.8 или switchAt [секунды слоя], switchDur, sideScale 0.8, sideOpacity 0.5, halo (кольца за центральной), wrap
  waveform: source auto (audio_layer — id звукового слоя / asset_id — файл; иначе образец) | voice | music | mix | fake,
         style bars|wave|dots, motion center|scroll, bars 36, w 820, h 220, color, gradient, glow, sensitivity, span
  tile_wall: images [id…] (пусто — свои плитки-значки), tile 230, gap, tiltX 26, tiltZ −16, speed 46 px/с, angle −45,
         overlay 0.55 (затемнение под текст) — фон сцены во весь кадр (на видео закрывает его — только на холсте/заставке)
  flow_diagram: nodes [{{label, sub, icon|image}}] 2..6, center (щит с замком), layout row|column, stepGap 0.8 (шаги
         появления), done «Готово» (плашка с галочкой в конце; "" — без), doneAt, w 1000, h 620
  ПЛОСКИЙ UI (плашки, карточки, окна в одном стиле; готовое — list_motion_templates(category: "Плоский UI") →
         add_motion_template, theme red|dark|light|brand): ui: component {'|'.join(motion.FLAT_COMPONENTS)},
         theme {'|'.join(motion.FLAT_THEMES)} (red — чёрно-красная как реф «ошибки», dark — графит+индиго, light — светлая,
         brand — accent + plate бренда), accent (перекрасить акцент), variant (chip solid|soft|outline|neutral, title
         plain|card, hook plain|fill|accent, counter pill|accent|plain, checklist card|window, window app|browser|chat, toast
         light|dark), w, size, label (подпись капсом / чип над заголовком / имя приложения), title, text, icon (Lucide: eye,
         hourglass, circle-check, trending-up, message-circle, bell, zap, lock… или «глаз», «галочка»), value/from/prefix/
         suffix/unit/delta (число растёт по progress), progress 0..1 (полоса в карточке), items [{{text, sub, note, value, max,
         dim, done, at, from me|them}}] ≤10 (пункты чек-листа / полосы / сообщения), marker 0..1 + markerLabel (порог на
         полосах), url (браузер), time, badge (уведомление), delay 0.5 + stagger 0.6 (галочки/полосы/сообщения по очереди),
         reveal check|appear. Хук: component hook, title — одно слово, без in (читается с кадра 0), variant fill — своя
         заставка во весь кадр (≤ 3 с). Фон под плашками: fx kind backdrop (size — размытие 22, intensity — затемнение 0.45)
         на track ниже карточки — кадр размыт, субтитры резкие
  transition (перетекающий переход, сам не рисуется — ставь инструментом flow_transition): kind reveal_from|zoom_through|
         shared_element|push_slide, from [id слоёв A], to [id слоёв B] (пусто — по времени), target, match, ease, shape,
         w, h, radius, feather, depth, edge, edgeWidth, fill, direction, dip; start..end слоя — отрезок перехода
  progress: w, h, value 0..1, track, fill (цвет или [c1,c2]), radius, label, font, labelColor
  chart: kind {'|'.join(enums['charts'])}, data [{{label,value,color?}}] ≤12, w, h, palette, font, labelColor, showValues, title
  callout: title, subtitle, accent, style {'|'.join(enums['callouts'])}, font, w
  social (карточка «в стиле соцсети», без логотипов): style comment|post, theme dark|light, name, handle, time, text (≤500,
         перенос по словам, сверх maxLines — «…»), avatar (эмодзи/буквы), accent, avatarImage, image (post, 5:3), likes, likesTo
         (счёт вверх по progress), replies, reposts (post), liked, verified, w (900)
  page (страница сайта из page_snapshot — готовый слой в поле layer ответа page_snapshot/page_edit): page_id, version,
         pageWidth (CSS px снимка: 390 телефон, 1280 компьютер), region {{x,y,w,h}} — окно в CSS px страницы (null — первый
         экран), scroll [{{t,y}}] — верх окна по секундам СЛОЯ (листать к цене/кнопке: y из outline box), zoom [{{t,z,cx?,cy?}}]
         (z 1..6, центр — доли окна), taps [{{t,x,y}}] — касание пальцем в точке страницы, w, device "phone", radius, border;
         экран по блокам (фон + кнопки, цены, карточки отдельными image-слоями) — page_to_layers
  nameplate (плашка канала, весь ролик): platform {'|'.join(motion.NAMEPLATE_PLATFORMS)} (свой векторный значок), handle (ник),
         style glass (тёмное стекло + обводка accent) | solid (заливка accent), accent (нет — цвет площадки), size (высота
         пилюли, px, {motion.NAMEPLATE_SIZE}), font, weight, color, align + boxWidth (пилюля у края рамки), anim
         {'|'.join(motion.NAMEPLATE_ANIMS)} (своя анимация: вход от значка, редкий блик и тик; по умолч. subtle), live (точка LIVE),
         outro none|collapse (последние {motion.NAMEPLATE_OUTRO_SEC:g} с — свёртка в значок). Проще set_nameplate —
         сам найдёт место под разделителем раскладки / в безопасном углу
  pngtuber: states {{idle, talk, shout?}} (id картинок/GIF/APNG/webm-alpha из библиотеки: молчит / говорит / кричит;
         анимация состояния циклится по времени клипа), voice {{track (номер звуковой дорожки источника: NVIDIA/OBS
         0 — игра, 1 — микрофон) | "speech" (speech_tracks; по умолчанию) | asset_id (голос из файла), threshold_db
         "auto" (по умолчанию: шум дорожки + 12, не выше речи − 12, не ниже −55) | дБ, hold {motion.VOICE_HOLD_SEC:g} (рот не хлопает между слогами), attack {motion.VOICE_ATTACK_SEC:g},
         shout_db {motion.VOICE_SHOUT_DB:g}}}, w (опорные px; высота — по пропорции idle), pixelated (по умолч. — если исходник ≤ 256 px:
         пиксель-арт без мыла), bounce {{amount 0..40}} (подскок в такт голосу), flipX. Состояние по кадрам считает сервис
         (comp.voice) — проще add_pngtuber(idle_asset, talk_asset, track): правый нижний угол над безопасной зоной, w ≈ 300
  image с анимированным файлом (GIF/APNG/webm с альфой) играет его по кругу с прозрачностью с начала слоя
  fx: kind {'|'.join(motion.FX_KINDS)}, intensity 0..1, speed 0.1..4, blend (нет — свой у вида), color (flash_frame),
         colors (light_leak), size (зерно/строки) — эффект кадра во весь кадр поверх видео и слоёв НИЖЕ по track
         (титры выше по track — чистые); in/out только fade; ≤ {motion.FX_MAX_AT_ONCE} разом, 1–2 на ролик.
         grayscale — ч/б на отрезке (панчлайн 1–2 с): intensity 1 — полностью ч/б, 0.5 — полуцвет; плавно —
         in/out fade 0.2 или ключи scale (0→1→0); после end цвет возвращается сам
  audio: asset_id ИЛИ "sfx:<pack>/<file>", fadeIn, fadeOut, offset, duck (не рисуется); mode:
         effect (по умолчанию, эффект: громкие места сервис ставит под голос клипа, volume 0..{motion.EFFECT_MAX_VOLUME:g} — только тише) |
         track (дорожка: песня/звук ролика — без автоподгонки, volume 0..2 × gain_db {motion.TRACK_MIN_GAIN_DB:g}..+{motion.TRACK_MAX_GAIN_DB:g};
         итог всё равно выравнивает общая нормализация клипа);
         role — роль в сведении (auto_mix, audio_mix_report, check_voiceover): voice — голос/озвучка (над ним музыка
         на 12–18 дБ тише, ducking по фразам), music — музыка, sfx — звуковой эффект (под голосом), ambience — фон;
         нет поля — по имени слоя (озвучка/голос/voice → voice, музыка → music) и длине (длинная дорожка → music, иначе sfx).
         Голос: mode track + role voice, end = длина файла (без end у audio с файлом — так и ставится) — или сразу
         add_voiceover; эффект: mode effect, volume 0.3–0.6
Все допустимые поля props (остальное отбрасывается): {fields}
Камера: [{{t (время клипа), zoom 1..4, x, y, ease?, shake? 0..1}}] — двигает только видео (в раскладках pip_cam/split — только игру, окошко вебки на месте)
  и слои с followCamera: true (путь камеры в их ключи не копируй).
  ease ключа — переход ОТ него к следующему ключу (по умолчанию inOutCubic); step — держать до следующего ключа и прыгнуть. zoom < 1 нет: шире окна — set_piece_fit.
Конец ролика: последний кадр стоит ~0.5 с (стоп-кадр, пока затухает звук); мему без «замирания» — set_end_hold(sec: 0) (0..1.5).
Субтитры captions: в hormozi/mrbeast/karaoke звучащее слово жёлтое — highlightWords жёлтым там не видны, бери #FF3B30 или #22C55E.
Цвета: #rrggbb или #rrggbbaa."""


_AT_WORD_SCHEMA = {
    "type": "object",
    "description": "вместо времени: начало слова субтитров — {line, word} из get_clip или {text, nth}",
    "properties": {
        "line": {"type": "integer"}, "word": {"type": "integer"},
        "text": {"type": "string"}, "nth": {"type": "integer", "description": "какое по счёту вхождение, с 1"},
    },
}


def _layer_schema() -> dict:
    enums = motion_enums()
    anim = {**_obj({"type": {"type": "string", "enum": enums["anims"]}, "duration": {"type": "number"}}),
            "description": "null, type none или duration 0 — без анимации"}
    key_point = _obj({"t": {"type": "number"}, "v": {"type": "number"},
                      "ease": {"type": "string", "enum": enums["easings"]}}, ["t", "v"])
    return _obj(
        {
            "id": {"type": "string", "description": "необязательно; ^[A-Za-z0-9_-]{1,32}$"},
            "type": {"type": "string", "enum": enums["types"]},
            "name": {"type": "string"},
            "start": {"type": "number", "description": "секунды клипа"},
            "end": {"type": "number", "description": "секунды клипа, end - start ≥ 0.1"},
            "track": {"type": "integer", "description": "0..7, выше = поверх"},
            "behind": {"type": "boolean", "description": "за вырезанным персонажем (только источник с хромакеем)"},
            "followCamera": {"type": "boolean",
                             "description": "едет и зумится вместе с видео по ключам камеры (set_camera); нет — стоит"},
            "x": {"type": "number", "description": "центр по ширине, доля кадра"},
            "y": {"type": "number", "description": "центр по высоте, доля кадра (безопасно 0.15..0.72 для центра текста; края текста — не выше 10% и не ниже 80% кадра)"},
            "scale": {"type": "number"},
            "rotation": {"type": "number"},
            "opacity": {"type": "number"},
            "keys": {
                "type": "object",
                "description": "ключевые кадры: {x|y|scale|rotation|opacity|progress: [{t, v, ease?}]}, t от start слоя",
                "additionalProperties": {"type": "array", "items": key_point},
            },
            "in": anim,
            "out": anim,
            "loop": _obj({"type": {"type": "string", "enum": enums["loops"]}, "speed": {"type": "number"},
                          "amount": {"type": "number"}}),
            "shadow": {"type": "object"},
            "blend": {"type": "string", "enum": ["normal", "multiply", "screen", "overlay", "add"]},
            "props": {"type": "object", "description": "свои поля у каждого типа — см. get_motion_reference"},
            "at_word": _AT_WORD_SCHEMA,
            "word_offset": {"type": "number", "description": "сдвиг от начала слова, с (−0.1 — чуть раньше)"},
            "duration": {"type": "number", "description": "с at_word: длина слоя, с (иначе end-start или 1.2)"},
            "until_word": {**_AT_WORD_SCHEMA,
                           "description": "вместо end: слой держится до начала этого слова ({line, word} или {text, nth})"},
        },
        ["type"],  # start/end или at_word — проверяет _validate_new_layers
    )
