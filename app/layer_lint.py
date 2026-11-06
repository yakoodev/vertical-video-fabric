"""Проверки и геометрия моушн-слоёв: безопасные зоны, размеры текста, полоса вебки, перекрытие кадра, заметность
подсветки субтитров. Сами слои не меняют, только считают и предупреждают; инструменты слоёв — ``app/layer_tools.py``."""

from __future__ import annotations

import math
from typing import Any

from app import motion
from app.promo_lint import has_device_layers, promo_warnings
from app.tool_core import _camera_of, _current_render, _layers_of, _num, _source_canvas, _source_keyed, ToolContext


# Безопасные зоны вертикального ролика: сверху — шапка площадки, снизу — подпись,
# кнопки и прогресс-бар TikTok/Shorts/Reels. Текст там читается плохо или закрыт.
# Зоны других форматов (1:1, 4:5 — лента, 16:9 — YouTube) — app/safe_zones.py.
SAFE_TOP = 0.10
SAFE_BOTTOM = 0.80
# Не больше стольких графических слоёв на экране одновременно — дальше каша.
MAX_SIMULTANEOUS = 3


TEXTUAL_TYPES = {"text", "counter", "callout"}


def _cover_problems(ctx: ToolContext, layers: list[dict]) -> list[str]:
    """Правило «не закрывать видео» — на холсте видео нет: картинка во весь кадр и есть монтаж."""
    if _source_canvas(ctx):
        return []
    from app.formats import settings_layers_format

    return covering_problems(layers, _source_keyed(ctx), settings_layers_format(_current_render(ctx)))


def _lint_scene(ctx: ToolContext, render: dict) -> dict:
    """Что нужно линту кроме слоёв: раскладка, холст, его фон (контраст финала), камера (followCamera)
    и формат разметки слоёв (опорный кадр и безопасная зона площадок, app/safe_zones.py)."""
    from app.formats import settings_layers_format

    canvas = _source_canvas(ctx)
    scene = {"layout": render.get("layout"), "canvas": canvas, "camera": _camera_of(render),
             "backdrop": render.get("backdrop") if canvas else None, "fmt": settings_layers_format(render)}
    layers = _layers_of(render)
    if canvas or has_device_layers(layers):
        # Пропорции картинок и записей экрана из библиотеки — для геометрии правил промо.
        wanted = {str((l.get("props") or {}).get("asset_id")) for l in layers if l.get("type") in ("image", "video")}
        try:
            scene["sizes"] = {str(a["id"]): (a.get("width"), a.get("height"))
                              for a in ctx.store.list_montage_assets() if str(a["id"]) in wanted
                              and a.get("width") and a.get("height")}
        except Exception:  # noqa: BLE001 - без размеров линт берёт обычные пропорции
            pass
    return scene


def text_lines(props: dict) -> int:
    """Сколько строк займёт текст слоя: явные переносы + перенос по ширине ``maxWidth``.

    Агент-монтажёр: хук на y 0.13 в две строки вылез за верхнюю безопасную зону, а
    проверка считала его однострочным (видела только переносы «\\n»). Ширину буквы
    берём с запасом: жирные заглавные (хук) ≈ 0.68 кегля, обычный текст ≈ 0.58."""
    text = str(props.get("text") or "")
    size = _num(props.get("size"), 96.0) or 96.0
    width = _num(props.get("maxWidth"), 920.0) or 920.0
    heavy = bool(props.get("uppercase")) or text.upper() == text
    factor = 0.68 if heavy and (_num(props.get("weight"), 800.0) or 800.0) >= 700 else 0.58
    per_line = max(1, int(width / (size * factor)))
    total = 0
    for chunk in text.split("\n"):
        line, count = 0, 1
        for word in chunk.split():
            need = len(word) if line == 0 else line + 1 + len(word)
            if line and need > per_line:
                count, line = count + 1, len(word)
            else:
                line = need
        total += count
    return max(1, total)


def _ref(fmt: Any = None) -> tuple[float, float]:
    """Опорный кадр слоёв формата (app/formats.py): 9:16 — 1080×1920, 16:9 — 1920×1080."""
    from app.formats import ref_size, settings_format

    w, h = ref_size(settings_format(fmt))
    return float(w), float(h)


def _zone(fmt: Any = None) -> dict:
    """Безопасная зона формата (app/safe_zones.py): у 9:16 — SAFE_TOP/SAFE_BOTTOM, как всегда."""
    from app.safe_zones import safe_zone

    return safe_zone(fmt)


def text_half_height(props: dict, scale: float = 1.0, ref_h: float = 1920.0) -> float:
    """Половина высоты текстового слоя в долях кадра высотой ``ref_h`` (опорный кадр формата;
    9:16 — 1920): строки × кегль × межстрочный."""
    size = _num(props.get("size"), 96.0) or 96.0
    line_height = _num(props.get("lineHeight"), 1.15) or 1.15
    return size * line_height * text_lines(props) * scale / ref_h / 2


def text_half_width(props: dict, scale: float = 1.0) -> float:
    """Половина ширины текстового слоя в опорных px: самая длинная строка (ширина буквы — как
    в ``text_lines``), не шире ``maxWidth``."""
    text = str(props.get("text") or "")
    size = _num(props.get("size"), 96.0) or 96.0
    width = _num(props.get("maxWidth"), 920.0) or 920.0
    heavy = bool(props.get("uppercase")) or text.upper() == text
    factor = 0.68 if heavy and (_num(props.get("weight"), 800.0) or 800.0) >= 700 else 0.58
    longest = max((len(chunk) for chunk in text.split("\n")), default=0)
    return min(width, longest * size * factor) * scale / 2


def safe_text_y(props: dict, y: float, scale: float = 1.0, top: float = SAFE_TOP, ref_h: float = 1920.0) -> float:
    """Центр ``y`` текста, опущенный так, чтобы верх строк был не выше ``top`` (по умолчанию
    SAFE_TOP; в split с вебкой сверху — низ полосы вебки, ``text_band``). ``ref_h`` — высота
    опорного кадра формата слоёв (9:16 — 1920)."""
    return round(max(y, math.ceil((top + text_half_height(props, scale, ref_h)) * 1000) / 1000), 3)


def cam_strip(layout: Any, fmt: Any = None) -> tuple[float, float] | None:
    """Полоса вебки раскладки split: (верх, низ) в долях высоты кадра формата ``fmt`` (нет —
    9:16); иначе None. В 16:9 полосы нет — вебка окошком в углу (stream_layout.layout_for_format)."""
    from app.formats import frame_size, is_landscape, settings_format
    from app.stream_layout import frame_px_scale, normalize_layout

    if not isinstance(layout, dict) or layout.get("mode") != "split":
        return None
    fmt = settings_format(fmt)
    if is_landscape(fmt):
        return None
    layout = normalize_layout(layout)
    gap = float(layout.get("gap") or 0)
    if fmt == "9:16":
        gap /= 1920
    else:  # зазор — px кадра 1080×1920 по короткой стороне (stream_layout.scaled_px)
        w, h = frame_size(fmt)
        gap = gap * frame_px_scale(w, h) / h
    height = float(layout["ratio"]) + gap
    return (0.0, round(height, 4)) if layout["cam_pos"] != "bottom" else (round(1.0 - height, 4), 1.0)


def text_band(render: dict | None) -> tuple[float, float]:
    """Где можно ставить текст по высоте (верх, низ): безопасная зона площадки минус полоса
    вебки в split (агент стримов: хук на y 0.13 лёг на лицо в полосе вебки). Зона — формата,
    в котором размечены слои (``layers_format``; у 9:16 — SAFE_TOP/SAFE_BOTTOM)."""
    from app.formats import settings_layers_format

    fmt = settings_layers_format(render)
    zone = _zone(fmt)
    top, bottom = zone["top"], zone["bottom"]
    strip = cam_strip((render or {}).get("layout"), fmt)
    if strip:
        if strip[0] <= 0.0:
            top = max(top, strip[1] + 0.015)
        else:
            bottom = min(bottom, strip[0] - 0.015)
    return round(top, 4), round(bottom, 4)


def _layer_box(layer: dict, ref_h: float = 1920.0) -> tuple[float, float]:
    """Примерная половина высоты слоя в долях кадра (высота опорного кадра ``ref_h``) — для
    проверки безопасных зон."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    scale = _num(layer.get("scale"), 1.0) or 1.0
    kind = layer.get("type")
    height = 0.0
    if kind in ("text", "counter"):
        return text_half_height(props, scale, ref_h), 0.0
    if kind == "emoji":
        height = _num(props.get("size"), 160.0) or 160.0
    elif kind in ("shape", "progress", "chart"):
        height = _num(props.get("h"), 120.0) or 120.0
    elif kind == "callout":
        height = 180.0
    elif kind == "nameplate":
        height = _num(props.get("size"), float(motion.NAMEPLATE_SIZE)) or float(motion.NAMEPLATE_SIZE)
    elif kind == "image":
        height = _num(props.get("w"), 400.0) or 400.0
    elif kind == "video":
        height = video_height(props)
    elif kind == "particles":
        height = 2 * ((_num(props.get("spread"), 360.0) or 0.0) + (_num(props.get("size"), 28.0) or 0.0))
    elif kind == "pngtuber":
        height = _num(props.get("w"), 300.0) or 300.0  # аватар обычно квадратный
    elif kind == "scene":
        height = _num(props.get("h"), ref_h) or ref_h  # вьюпорт сцены; нет — весь кадр
    return height * scale / ref_h / 2, 0.0


def video_height(props: dict) -> float:
    """Примерная высота видео-слоя в опорных px: пропорция файла неизвестна — запись экрана
    телефона (9:19.5) в корпусе, иначе вертикальное 9:16; окно crop по высоте её режет."""
    w = _num(props.get("w"), 600.0) or 600.0
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else {}
    ratio = (_num(crop.get("h"), 1.0) or 1.0) / max(0.01, _num(crop.get("w"), 1.0) or 1.0)
    if props.get("device") == "phone":
        bar = props.get("statusBar")
        bar = motion.STATUS_BAR_AUTO if bar is True else (_num(bar, 0.0) or 0.0)
        return w * 19.5 / 9 * ratio + w * 0.094 + w * bar  # корпус: пояс + рамка сверху и снизу (+ строка статуса)
    return w * 16 / 9 * ratio


MAX_COVER_SHARE = 0.4  # непрозрачная картинка/фигура больше 40 % кадра — «закрыли видео»
# Короткая вставка-иллюстрация во весь кадр (картинка/видео, не плашка-фигура) — монтаж, а не «закрыли
# видео»: голос идёт под ней, а через ≤ 3 с снова герой (плейбук vtuber-story, QA 01.11).
FULL_INSERT_MAX_SEC = 3.0


def cover_share(layer: dict, fmt: Any = None) -> float:
    """Какую долю кадра непрозрачно закрывает слой-картинка/фигура (0, если прозрачный); кадр —
    опорный формата слоёв ``fmt`` (нет — 9:16, 1080×1920)."""
    if layer.get("type") not in ("shape", "image", "video", "scene"):
        return 0.0
    ref_w, ref_h = _ref(fmt)
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    if layer.get("type") == "scene":
        # Сцена закрывает кадр только своим фоном: без background она прозрачна (графика поверх).
        scene = props.get("scene") if isinstance(props.get("scene"), dict) else {}
        if scene.get("background") in (None, "", "none", "transparent") or (_num(layer.get("opacity"), 1.0) or 0.0) < 0.6:
            return 0.0
        scale = _num(layer.get("scale"), 1.0) or 1.0
        w = (_num(props.get("w"), ref_w) or ref_w) * scale
        h = (_num(props.get("h"), ref_h) or ref_h) * scale
        return min(1.0, (w * h) / (ref_w * ref_h))
    if layer.get("type") == "video" and (_num(layer.get("opacity"), 1.0) or 0.0) >= 0.6:
        scale = _num(layer.get("scale"), 1.0) or 1.0
        w = (_num(props.get("w"), 600.0) or 600.0) * scale
        return min(1.0, (w * video_height(props) * scale) / (ref_w * ref_h))
    if (_num(layer.get("opacity"), 1.0) or 0.0) < 0.6:
        return 0.0
    if layer.get("type") == "shape" and props.get("fill") in (None, "", "none"):
        return 0.0  # контур (рамка, стрелка) видео не закрывает
    scale = _num(layer.get("scale"), 1.0) or 1.0
    w = (_num(props.get("w"), 400.0) or 400.0) * scale
    h = (_num(props.get("h"), w) or w) * scale
    return min(1.0, (w * h) / (ref_w * ref_h))


def covering_problems(layers: list[dict], keyed: bool = False, fmt: Any = None) -> list[str]:
    """Ошибки: слой закрывает собой видео. Владелец: «плашки перекрывают видео — говно».
    Картинки и плашки ставим на пустое место, а не поверх героя/кадра.

    ``keyed`` — источник на зелёнке: слой ``behind`` лежит ЗА персонажем и закрывает
    только фон (скрин сайта во весь кадр за втубером — это и есть монтаж)."""
    out = []
    for layer in layers:
        if layer.get("id") == "end_banner":
            continue  # баннер концовки (app/end_screen.py) лежит на уже размытом видео
        if layer.get("behind"):
            if not keyed:
                out.append(f"{layer.get('id') or layer.get('type')}: behind работает только у источника с хромакеем "
                           "(set_chroma_key) — без него убери behind")
            continue
        share = cover_share(layer, fmt)
        if share > MAX_COVER_SHARE and not is_full_insert(layer):
            out.append(f"{layer.get('id') or layer.get('type')}: закрывает {share:.0%} кадра — видео не перекрывать, "
                       f"картинки и плашки ≤ {MAX_COVER_SHARE:.0%} кадра и на пустом месте (раскладка «Мем» даёт "
                       f"свободный фон под видео). Вставка-иллюстрация на весь кадр — можно, если короткая: "
                       f"картинка/видео ≤ {FULL_INSERT_MAX_SEC:g} с (end − start) или add_insert(asset_id, "
                       f"mode: full, duration ≤ {FULL_INSERT_MAX_SEC:g})")
    return out


def is_full_insert(layer: dict) -> bool:
    """Картинка/видео на ≤ FULL_INSERT_MAX_SEC — короткая вставка-иллюстрация, ей можно закрыть кадр."""
    if layer.get("type") not in ("image", "video"):
        return False
    start, end = _num(layer.get("start"), 0.0) or 0.0, _num(layer.get("end"), 0.0) or 0.0
    return 0.0 < end - start <= FULL_INSERT_MAX_SEC + 1e-6


def is_decor_layer(layer: dict, canvas: bool = False, ref_w: float = 1080.0) -> bool:
    """Фон и декор, а не «графика поверх» — в перегруз не считается.

    Фигуры (подложки), субтитры, частицы, слой за персонажем (behind), полупрозрачная картинка/видео
    во всю ширину кадра. На холсте (``canvas``) любая картинка во всю ширину — это фон промо."""
    kind = layer.get("type")
    if kind in ("shape", "captions", "particles", "nameplate", "fx") or layer.get("behind"):
        # Плашка канала висит весь ролик маленькой подписью в углу — не «графика поверх».
        return True
    if kind in ("image", "video"):
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        if canvas and props.get("device") == "phone":
            return True  # телефон на холсте — сама сцена промо (на смене сцен их два), а не графика поверх
        width = (_num(props.get("w"), 600.0) or 0.0) * (_num(layer.get("scale"), 1.0) or 0.0)
        if width >= 0.9 * ref_w and (canvas or (_num(layer.get("opacity"), 1.0) or 0.0) <= 0.6):
            return True
    return False


def lint_layers(layers: list[dict], duration: float | None = None, only_ids: set[str] | None = None,
                layout: dict | None = None, canvas: bool = False, backdrop: dict | None = None,
                camera: list | None = None, sizes: dict | None = None, fmt: Any = None) -> list[str]:
    """Предупреждения ремесла (не ошибки): безопасные зоны, перегруз, мелкий текст.

    Не блокируют правку — модель (или человек) решает сама, но видит, где
    интерфейс площадки закроет титр или экран превратился в кашу. ``layout`` — раскладка
    клипа: в split текст на полосе вебки ложится на лицо стримера. ``canvas`` — клип-холст
    (промо): картинки во всю ширину там фон и в перегруз не идут. На холсте и с телефоном
    (device) — ещё правила промо (app/promo_lint.py): ``backdrop`` — фон холста для
    контраста финального текста, ``camera`` — наезд для слоёв followCamera. ``fmt`` — формат,
    в котором размечены слои (``layers_format``; нет — 9:16): его опорный кадр и безопасная
    зона площадок (app/safe_zones.py); у 1:1, 4:5, 16:9 — ещё поля по бокам, у 16:9 — угол
    водяного знака YouTube.
    """
    from app.formats import settings_format
    from app.safe_zones import lint_limits

    fmt = settings_format(fmt)
    ref_w, ref_h = _ref(fmt)
    zone = _zone(fmt)
    limits = lint_limits(fmt)  # кегль и перегруз — по площадке (16:9 — экран, не телефон)
    safe_top, safe_bottom = zone["top"], zone["bottom"]
    sides = fmt != "9:16"
    warnings: list[str] = []
    strip = cam_strip(layout, fmt)
    warnings += motion.fx_crowding(layers)  # эффекты кадра: больше двух разом — каша
    # Ч/б до конца клипа / дольше 2.5 с — цвет не вернётся (QA 02.11, клип 892).
    warnings += motion.fx_grayscale_long([l for l in layers if only_ids is None or str(l.get("id")) in only_ids],
                                         duration)
    visible = [l for l in layers if l.get("type") not in ("audio", "fx") and not l.get("hidden")]
    for layer in visible:
        lid = str(layer.get("id") or "?")
        if only_ids is not None and lid not in only_ids:
            continue
        y = _num(layer.get("y"), 0.5) or 0.0
        half, _ = _layer_box(layer, ref_h)
        textual = layer.get("type") in TEXTUAL_TYPES
        if textual and y + half > safe_bottom and y - half < 1.0:
            warnings.append(f"{lid}: текст заходит в нижние {round((1 - safe_bottom) * 100)}% кадра (y={y:.2f}) — "
                            f"{zone['bottom_reason']}" + (f" ({fmt}, {zone['platforms']}) — подними до "
                            f"y≤{math.floor((safe_bottom - half) * 1000) / 1000:.3f}" if sides else ""))
        if textual and y - half < safe_top and y + half > 0.0:
            lines = text_lines(layer.get("props") or {}) if layer.get("type") in ("text", "counter") else 1
            warnings.append(
                f"{lid}: верх текста на {y - half:.3f} — выше безопасной зоны {safe_top:.2f} ({zone['top_reason']}; "
                f"центр y={y:.2f}, ≈{lines} стр.) — опусти до y≥{math.ceil((safe_top + half) * 1000) / 1000:.3f} или сократи текст")
        if textual and sides:
            warnings.extend(_side_warnings(layer, lid, fmt, zone, ref_w, half))
        if textual and strip and y - half < strip[1] and y + half > strip[0]:
            where = "сверху" if strip[0] <= 0 else "снизу"
            edge = (f"опусти до y≥{math.ceil((strip[1] + 0.015 + half) * 1000) / 1000:.3f}" if strip[0] <= 0
                    else f"подними до y≤{math.floor((strip[0] - 0.015 - half) * 1000) / 1000:.3f}")
            warnings.append(f"{lid}: текст на полосе вебки ({where}, {strip[0]:.2f}–{strip[1]:.2f} кадра) — ляжет на "
                            f"лицо стримера; {edge} (positions в get_clip учитывают раскладку)")
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        if layer.get("type") == "text":
            size = (_num(props.get("size"), 80.0) or 80.0) * (_num(layer.get("scale"), 1.0) or 1.0)
            if size < limits["min_font"]:
                warnings.append(f"{lid}: шрифт {size:.0f}px мелковат для {limits['screen']} "
                                f"(лучше ≥ {limits['good_font']})")
            if len(str(props.get("text") or "")) > 60:
                warnings.append(f"{lid}: длинный титр — на экране читают 3–6 слов")
        start, end = _num(layer.get("start"), 0.0) or 0.0, _num(layer.get("end"), 0.0) or 0.0
        whole_clip = layer.get("type") == "captions" or start <= 0.05
        if duration and end > duration + 0.05 and not (whole_clip and start < duration):
            # Слой «на весь клип» (субтитры, плашка с 0 с) просто обрежется по концу — не шум.
            warnings.append(f"{lid}: заканчивается на {end:.2f} с, а клип длится {duration:.2f} с")
        if layer.get("type") == "audio" and (_num(props.get("volume"), 1.0) or 0.0) > 1.0:
            # Громкость эффектов выравнивает сервис (на 6 дБ тише речи); выше 1 — срежется.
            warnings.append(f"{lid}: volume {_num(props.get('volume'), 1.0):.2f} срежется до 1 — звуки уже "
                            "выровнены ниже речи, обычно 0.3–0.6")
        if textual and end - start < 0.6:
            warnings.append(f"{lid}: титр виден {end - start:.2f} с — не успеют прочитать")
        if layer.get("type") == "captions":
            warnings.extend(f"{lid}: {w}" for w in caption_highlight_warnings(props))
            if sides:
                warnings.extend(f"{lid}: {w}" for w in caption_zone_warnings(layer, fmt))
    # Перегруз: сколько видимых слоёв одновременно в момент начала каждого слоя.
    graphics = [o for o in visible if not is_decor_layer(o, canvas, ref_w)]  # фон, декор и субтитры — не «графика» поверх
    for layer in graphics:
        lid = str(layer.get("id") or "?")
        if only_ids is not None and lid not in only_ids:
            continue
        t = (_num(layer.get("start"), 0.0) or 0.0) + 0.05
        together = [
            o for o in graphics
            if (_num(o.get("start"), 0.0) or 0.0) <= t < (_num(o.get("end"), 0.0) or 0.0)
        ]
        if len(together) > limits["max_graphics"]:
            warnings.append(f"{lid}: на {t:.1f} с на экране {len(together)} слоя графики — перегруз "
                            f"(≤ {limits['max_graphics']})")
    warnings.extend(caption_overlap_warnings(visible, only_ids, ref_h))
    promo = promo_warnings(layers, duration, only_ids, canvas=canvas, backdrop=backdrop, camera=camera, sizes=sizes,
                           fmt=fmt)
    if promo:
        # Мелкий шрифт финала сказан правилом промо (про CTA) — общее «мелковат» не дублируем.
        final_small = {w.split(":", 1)[0] for w in promo if "в финале шрифт" in w}
        warnings = [w for w in warnings if not (w.split(":", 1)[0] in final_small and "мелковат" in w)]
        warnings.extend(promo)
    return list(dict.fromkeys(warnings))


def _side_warnings(layer: dict, lid: str, fmt: str, zone: dict, ref_w: float, half_h: float) -> list[str]:
    """Текст у левого/правого края и в углу водяного знака (форматы не 9:16: поля площадки)."""
    from app.safe_zones import in_watermark

    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    x = _num(layer.get("x"), 0.5) or 0.0
    y = _num(layer.get("y"), 0.5) or 0.0
    scale = _num(layer.get("scale"), 1.0) or 1.0
    if layer.get("type") in ("text", "counter"):
        half_w = text_half_width(props, scale) / ref_w
    else:  # плашка-выноска: ширина по умолчанию как у движка
        half_w = (_num(props.get("w"), 800.0) or 800.0) * scale / ref_w / 2
    out = []
    if x - half_w < zone["left"] and x + half_w > 0.0:
        out.append(f"{lid}: левый край текста на {x - half_w:.3f} — левее безопасной зоны {zone['left']:.2f} "
                   f"({fmt}, {zone['platforms']}: край кадра режется) — сдвинь до "
                   f"x≥{math.ceil((zone['left'] + half_w) * 1000) / 1000:.3f} или сократи строку")
    if x + half_w > zone["right"] and x - half_w < 1.0:
        out.append(f"{lid}: правый край текста на {x + half_w:.3f} — правее безопасной зоны {zone['right']:.2f} "
                   f"({fmt}, {zone['platforms']}: край кадра режется) — сдвинь до "
                   f"x≤{math.floor((zone['right'] - half_w) * 1000) / 1000:.3f} или сократи строку")
    if in_watermark(fmt, x - half_w, y - half_h, x + half_w, y + half_h):
        out.append(f"{lid}: текст в правом нижнем углу — там водяной знак канала {zone['platforms']} "
                   "(и длительность ролика на превью); перенеси левее или выше")
    return out


def caption_zone_warnings(layer: dict, fmt: Any) -> list[str]:
    """Субтитры-слой ниже безопасной зоны формата (не 9:16): движок поднимет блок сам, но
    место в редакторе и в кадре разойдутся — лучше сразу поставить ``y`` выше."""
    zone = _zone(fmt)
    y = _num(layer.get("y"), 0.5) or 0.0
    limit = round(zone["captions_bottom"] - CAPTIONS_ZONE_ROOM, 3)
    if y <= limit:
        return []
    return [f"субтитры на y={y:.2f} — блок уйдёт ниже безопасной зоны {zone['captions_bottom']:.2f} "
            f"({zone['platforms']}), движок поднимет его; поставь y≤{limit}"]


CAPTIONS_ZONE_ROOM = 0.08  # центр субтитров-слоя — не ниже низа зоны минус столько (полблока)
CAPTIONS_MIN_GAP = 0.12  # две строки главных субтитров — ~0.1 высоты кадра


def caption_overlap_warnings(layers: list[dict], only_ids: set[str] | None = None,
                             ref_h: float = 1920.0) -> list[str]:
    """Два слоя субтитров (главный и «другие голоса») ближе ~0.12 по высоте — строки лягут
    друг на друга («ВОЗЬМИ ДЕЛАЕШЬ» поверх «МНЕ НУЖНО ВЫТЯНУТЬ» в проверочном прогоне).
    Порог — px кадра 1920: у кадра ниже (1:1, 16:9) это бо́льшая доля высоты."""
    min_gap = CAPTIONS_MIN_GAP if ref_h == 1920.0 else round(CAPTIONS_MIN_GAP * 1920 / ref_h, 3)
    caps = [l for l in layers if l.get("type") == "captions"]
    out = []
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            if only_ids is not None and str(a.get("id")) not in only_ids and str(b.get("id")) not in only_ids:
                continue
            ya, yb = _num(a.get("y"), 0.5) or 0.5, _num(b.get("y"), 0.5) or 0.5
            if abs(ya - yb) < min_gap:
                main, other = (a, b) if not _secondary_captions(a) else (b, a)
                y = voice_caption_y(_num(main.get("y"), 0.5) or 0.5, ref_h)
                out.append(f"субтитры {a.get('id')} (y {ya:.2f}) и {b.get('id')} (y {yb:.2f}) ближе "
                           f"{min_gap} по высоте — строки лягут друг на друга; разведи y: update_layer "
                           f"{other.get('id')} patch {{\"y\": {y}}}")
    return out


VOICE_CAPTIONS_TOP = 0.2  # выше — зона хука
VOICE_CAPTIONS_BOTTOM = 0.8  # ниже — подпись площадок
VOICE_GAP_ABOVE = 0.15
VOICE_GAP_BELOW = 0.13


def _secondary_captions(layer: dict) -> bool:
    """Слой «других голосов»: speakers без главного (A) — гость, донат, ролик."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    speakers = props.get("speakers") or []
    return bool(speakers) and "A" not in speakers


def voice_caption_y(main_y: float, ref_h: float = 1920.0) -> float:
    """Где поставить слой голосов при главных субтитрах на ``main_y``: на зазор выше (если над ними
    есть место ниже хука), иначе ниже; не помещается ни там, ни там — дальше от края."""
    k = 1.0 if ref_h == 1920.0 else 1920 / ref_h
    # Над главными — на две их строки и сам слой (0.15), под ними — чуть больше зазора (0.13).
    above, below = main_y - VOICE_GAP_ABOVE * k, main_y + VOICE_GAP_BELOW * k
    fits_above, fits_below = above >= VOICE_CAPTIONS_TOP, below <= VOICE_CAPTIONS_BOTTOM
    if main_y < 0.6:
        pick = above if fits_above else below if fits_below else None
    else:
        pick = below if fits_below else above if fits_above else None
    if pick is None:  # не помещается нигде — дальше от ближнего края
        pick = below if main_y < 0.5 else above
    return round(pick, 3)


def separate_voice_captions(layers: list[dict], ref_h: float = 1920.0) -> list[str]:
    """Развести слой «других голосов» с главными субтитрами (на месте, ``layers`` меняется):
    ближе зазора — голоса встают на ``voice_caption_y``. QA 01.11 (клип 889): auto_edit поставил
    голоса на 0.20 при главных на 0.30 (зажал у хука), предупреждение агент проигнорировал."""
    min_gap = CAPTIONS_MIN_GAP if ref_h == 1920.0 else round(CAPTIONS_MIN_GAP * 1920 / ref_h, 3)
    caps = [l for l in layers if l.get("type") == "captions" and not l.get("hidden")]
    main = next((l for l in caps if not _secondary_captions(l)), None)
    notes = []
    if main is None:
        return notes
    main_y = _num(main.get("y"), 0.5) or 0.5
    for index, layer in enumerate(layers):
        if layer.get("type") != "captions" or layer.get("hidden") or not _secondary_captions(layer):
            continue
        y = _num(layer.get("y"), 0.5) or 0.5
        # Ближе зазора или уехали в зону хука/подписи площадок (следом за главными) — разводим.
        if abs(y - main_y) >= min_gap and VOICE_CAPTIONS_TOP - 1e-6 <= y <= VOICE_CAPTIONS_BOTTOM + 1e-6:
            continue
        new_y = voice_caption_y(main_y, ref_h)
        if abs(new_y - y) < 1e-6:
            continue
        layers[index] = {**layer, "y": new_y}
        notes.append(f"субтитры голосов {layer.get('id')} сдвинуты с y {y:.2f} на {new_y} — не лягут на главные "
                     f"{main.get('id')} (y {main_y:.2f})")
    return notes


# Цвет звучащего слова у стилей субтитров (frontend/src/motion/layers/captions.ts).
CAPTION_ACTIVE_COLORS = {
    "hormozi": "#ffe81f", "mrbeast": "#ffd400", "karaoke": "#ffd400", "box": "#ffffff", "neon": "#ff4fd8",
    "minimal": "#ffffff", "typewriter": "#111111", "comic": "#ff3b5c", "gradient": "#ffffff", "outline": "#ffffff",
}


def _rgb(color: Any) -> tuple[int, int, int] | None:
    text = str(color or "").strip().lstrip("#")
    if len(text) not in (6, 8):
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


HIGHLIGHT_MIN_DISTANCE = 90  # цвета ближе этого (евклидово по RGB) на глаз не различить в подсветке


def colors_close(a: Any, b: Any) -> bool:
    """Два цвета почти одинаковые — подсветка одним на фоне другого не видна."""
    ra, rb = _rgb(a), _rgb(b)
    return bool(ra and rb) and sum((x - y) ** 2 for x, y in zip(ra, rb)) < HIGHLIGHT_MIN_DISTANCE ** 2


def caption_highlight_warnings(props: dict) -> list[str]:
    """Подсветка ключевых слов цветом звучащего слова не видна (hormozi красит его жёлтым)."""
    active = _rgb(props.get("activeColor") or CAPTION_ACTIVE_COLORS.get(str(props.get("style") or ""), "#ffe81f"))
    if not active:
        return []
    close = sorted({
        str(h.get("color")) for h in props.get("highlightWords") or []
        if isinstance(h, dict) and (rgb := _rgb(h.get("color"))) and sum((a - b) ** 2 for a, b in zip(rgb, active))
        < HIGHLIGHT_MIN_DISTANCE ** 2
    })
    if not close:
        return []
    return [f"highlightWords цветом {', '.join(close)} почти как звучащее слово этого стиля — подсветки не видно; "
            "бери контрастный (#FF3B30 красный, #22C55E зелёный)"]
