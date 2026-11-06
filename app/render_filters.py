"""Рендер: граф фильтров ffmpeg. Кадр, раскладка, баннер, цвет, скорость и звук части (``app/render.py``)."""

from __future__ import annotations

import math

from app.backgrounds import fill_color
from app.render_exec import _clamp, _ff_number, _fps, _stream_index, _volume
from app.stream_layout import even_px, ff_color, layout_active, normalize_layout, scaled_px, split_geometry


def _filter_complex(
    width: int,
    height: int,
    fps: float,
    preset: dict,
    banner: dict | None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> str:
    filters = [_render_filter(width, height, fps, preset, banner, source_crop, reframe_x)]
    audio_filter = _audio_filter(preset)
    if audio_filter:
        filters.append(audio_filter)
    return ";".join(filters)


def _freeze_source(freezes: list[dict], copies: int, fps: float = 30.0) -> tuple[str, list[str]]:
    """Исходник части со стоп-кадрами ``[{t0, t1}]`` (секунды от начала части): кадры t0..t1 — последний кадр до
    t0 (``tpad`` clone), дальше исходник с t1. → (граф с «;» на конце или "", ``copies`` меток входа).
    Счёт — кадрами на сетке ``fps`` (trim по номеру кадра, tpad числом кадров): по секундам стык терял кадр,
    и рамка старого места ловила кадр новой сцены. Без стоп-кадров — прежний ``0:v`` (граф ровно прежний)."""
    if not freezes:
        return "", ["0:v"] * copies
    rate = float(fps or 30.0)
    # Кадр k (время k/fps) — стоп-кадр, если t0 ≤ k/fps < t1: та же граница, что у enable рамок (t ≥ t1 — новая).
    holds = sorted((math.ceil(float(f["t0"]) * rate - 1e-6), math.ceil(float(f["t1"]) * rate - 1e-6)) for f in freezes)
    holds = [(a, b) for a, b in holds if b > a > 0]
    if not holds:
        return "", ["0:v"] * copies
    pieces: list[str] = []
    cursor = 0
    for f0, f1 in holds:
        if f0 <= cursor:
            continue
        trim = f"trim=start_frame={cursor}:end_frame={f0}" if cursor else f"trim=end_frame={f0}"
        pieces.append(f"{trim},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop={f1 - f0}")
        cursor = f1
    pieces.append(f"trim=start_frame={cursor},setpts=PTS-STARTPTS")
    labels = [f"fz{i}" for i in range(len(pieces))]
    graph = f"[0:v]fps={_fps(rate)},split={len(pieces)}{''.join(f'[{x}]' for x in labels)};"
    graph += "".join(f"[{label}]{chain}[fp{i}];" for i, (label, chain) in enumerate(zip(labels, pieces)))
    outs = [f"vfz{i}" for i in range(copies)]
    graph += (f"{''.join(f'[fp{i}]' for i in range(len(pieces)))}concat=n={len(pieces)}:v=1:a=0,"
              f"split={copies}{''.join(f'[{x}]' for x in outs)};")
    return graph, outs


def _crop_prefix(source_crop: dict | None, copies: int) -> tuple[str, list[str]]:
    """Build the optional content-crop filter and the input label(s) to consume.

    An input pad (``0:v``) can feed several filters, but a filter *output* label
    can be read only once — so when the crop is active and we need the frame more
    than once (blur background) we ``split`` it.
    """
    if not source_crop:
        return "", ["0:v"] * copies
    c = source_crop
    crop = f"[0:v]crop=iw*{c['w']:.5f}:ih*{c['h']:.5f}:iw*{c['x']:.5f}:ih*{c['y']:.5f}"
    if copies == 1:
        return f"{crop}[vsrc];", ["vsrc"]
    labels = [f"vsrc{i}" for i in range(copies)]
    return f"{crop},split={copies}{''.join(f'[{label}]' for label in labels)};", labels


def _render_filter(
    width: int,
    height: int,
    fps: float,
    preset: dict,
    banner: dict | None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> str:
    if preset.get("stand_in"):
        # Предпросмотр-трансляция (app/stream_preview.py): проход собирает только звук, картинка — чёрная
        # заглушка размера кадра, видео записи вообще не декодируется.
        band = preset.get("pin_band")
        return f"color=c=black:s={width}x{height + int(band['h']) if band else height}:r={_fps(fps)},format=yuv420p,setsar=1[vout]"
    mode = preset.get("scale_mode") or "cover"
    anchor = preset.get("crop_anchor") or "center"
    layout = preset.get("layout")
    if preset.get("base_fit") == "fit" and mode == "cover":
        # Исходник заметно уже кадра формата (вертикаль в 16:9, app/formats.source_strategy):
        # целиком по высоте на размытой подложке; с хромакеем — поля цвета ключа.
        mode = "contain" if preset.get("key_fill") else "blur_background"
    if preset.get("piece_fit"):
        # Кусок «шире окна 9:16» (_preset_for_segment): своя основа, раскладка не действует.
        base = _piece_fit_filter(width, height, fps, preset["piece_fit"], source_crop, reframe_x)
    elif layout_active(layout):
        # Раскладка стрима (вебка + геймплей): своя основа, дальше — как всегда.
        base = _layout_filter(width, height, fps, mode, anchor, normalize_layout(layout), source_crop, reframe_x,
                              game_out=bool(preset.get("pin_band")), avatar_spans=preset.get("avatar_spans"),
                              avatar_freezes=preset.get("avatar_freezes"))
    elif mode == "blur_background":
        prefix, (bg_in, fg_in) = _crop_prefix(source_crop, 2)
        base = (
            f"{prefix}[{bg_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[bg];"
            f"[{fg_in}]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    elif mode == "contain":
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        base = (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{ff_color(preset['key_fill']) if preset.get('key_fill') else 'black'},"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    else:
        crop_y = {
            "top": "0",
            "bottom": f"ih-{height}",
            "center": f"(ih-{height})/2",
        }.get(anchor, f"(ih-{height})/2")
        # Dynamic reframe overrides the horizontal crop offset with a smoothed x(t).
        crop_x = reframe_x if reframe_x else f"(iw-{width})/2"
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        base = (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{crop_x}:{crop_y},"
            f"fps={_fps(fps)},format=yuv420p[vbase]"
        )
    source_label = "vbase"
    # Полоса окошка под кадром (pin_band): игра без окошка идёт своей веткой [vgame] — её
    # зумит камера; лук и SAR — на обеих ветках одинаково, как на прежнем кадре.
    band = preset.get("pin_band")
    game_label = None
    if band:
        game_label = "vgame"
        if "[vgame]" not in base:  # не окошко раскладки (кусок «шире окна») — кадр как есть
            base = f"{base};[vbase]split=2[vbase0][vgame]"
            source_label = "vbase0"
    if preset.get("normalize_sar"):
        # scale с force_original_aspect_ratio подгоняет SAR под пропорцию (10240:10239), а у
        # куска «шире окна» SAR 1:1 — concat таких кусков падал. Квадратный пиксель всем.
        base = f"{base};[{source_label}]setsar=1[vsar]"
        source_label = "vsar"
        if game_label:
            base = f"{base};[{game_label}]setsar=1[vgsar]"
            game_label = "vgsar"
    style = _video_style_filter(preset)
    if style:
        base = f"{base};[{source_label}]{style}[vstyled]"
        source_label = "vstyled"
        if game_label:
            base = f"{base};[{game_label}]{style}[vgstyled]"
            game_label = "vgstyled"
    if game_label:
        base = (
            f"{base};[{source_label}]crop={width}:{int(band['h'])}:0:{int(band['y'])}[vband];"
            f"[{game_label}][vband]vstack=inputs=2[vpinned]"
        )
        source_label = "vpinned"
    if not banner:
        return f"{base};[{source_label}]copy[vout]"
    return f"{base};{_banner_overlay(banner, preset, height, '1:v', source_label)}"


def _piece_fit_filter(
    width: int,
    height: int,
    fps: float,
    fit: dict,
    source_crop: dict | None,
    reframe_x: str | None,
) -> str:
    """Основа ``[vbase]`` куска с ``fit``: исходник в ширину ``width / fit.w`` (в кадр
    входит доля ``w`` его ширины, центр — тот же трек x(t) рефрейма), по вертикали
    посередине; сверху и снизу — размытый тот же кадр или цвет. w = 1 — весь кадр."""
    w = min(1.0, max(0.05, float(fit.get("w") or 1.0)))
    video_w = even_px(width / w)
    crop_x = reframe_x if reframe_x else f"(iw-{width})/2"
    bg = fit.get("bg") or "blur"
    if bg == "blur":
        prefix, (bg_in, fg_in) = _crop_prefix(source_crop, 2)
        background = (
            f"[{bg_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2,setsar=1[pbg]"
        )
    else:
        prefix, (fg_in,) = _crop_prefix(source_crop, 1)
        # Анимированный фон (app/backgrounds.py) кладёт компоновщик, тут — его первый цвет.
        color = "black" if bg == "black" else ff_color(fill_color(bg))
        background = f"color=c={color}:s={width}x{height}:r={_fps(fps)}[pbg]"
    video = (
        f"[{fg_in}]scale={video_w}:-2,"
        f"crop={width}:min(ih\\,{height}):{crop_x}:(ih-oh)/2,setsar=1[pfg]"
    )
    return (
        f"{prefix}{background};{video};"
        f"[pbg][pfg]overlay=x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2:shortest=1,"
        f"fps={_fps(fps)},format=yuv420p[vbase]"
    )


def _fit_box(
    mode: str,
    anchor: str,
    width: int,
    height: int,
    source_crop: dict | None,
    reframe_x: str | None,
    out: str,
) -> str:
    """Геймплей в коробку ``width``×``height`` тем же способом, что и обычная
    основа (cover с x(t) рефрейма / contain / blur_background) → ``[out]``."""
    if mode == "blur_background":
        prefix, (bg_in, fg_in) = _crop_prefix(source_crop, 2)
        return (
            f"{prefix}[{bg_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=24:2[{out}bg];"
            f"[{fg_in}]scale={width}:{height}:force_original_aspect_ratio=decrease[{out}fg];"
            f"[{out}bg][{out}fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,setsar=1[{out}]"
        )
    prefix, (vin,) = _crop_prefix(source_crop, 1)
    if mode == "contain":
        return (
            f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]"
        )
    crop_y = {"top": "0", "bottom": f"ih-{height}"}.get(anchor, f"(ih-{height})/2")
    crop_x = reframe_x if reframe_x else f"(iw-{width})/2"
    return (
        f"{prefix}[{vin}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:{crop_x}:{crop_y},setsar=1[{out}]"
    )


def _layout_filter(
    width: int,
    height: int,
    fps: float,
    mode: str,
    anchor: str,
    layout: dict,
    source_crop: dict | None,
    reframe_x: str | None,
    game_out: bool = False,
    avatar_spans: list[dict] | None = None,
    avatar_freezes: list[dict] | None = None,
) -> str:
    """Основа ``[vbase]`` для раскладки стрима (app/stream_layout.py).

    ``game_out`` — у ``pip_cam`` ещё и ``[vgame]``: игра без окошка (для камеры, см. ``pin_band``).
    ``avatar_spans`` — avatar_full: где в части другая рамка аватара (``stream_layout.avatar_cam_spans``,
    секунды от начала части) — кадр с ней кладётся поверх основы на эти секунды. ``avatar_freezes`` —
    ``[{t0, t1}]`` (``stream_layout.avatar_freezes``): там вместо исходника стоп-кадр последнего кадра до ``t0``.

    Вебка вырезается из ВСЕГО кадра исходника (``cam`` — доли исходника, до
    content-crop), геймплей идёт обычным путём (content-crop, рефрейм x(t)).
    Зеркало, лук, баннер, моушн и склейки работают уже с собранным кадром.
    """
    cam = layout["cam"]
    cam_crop = (
        f"[0:v]crop=iw*{cam['w']:.5f}:ih*{cam['h']:.5f}:iw*{cam['x']:.5f}:ih*{cam['y']:.5f}"
    )
    border = scaled_px(float(layout["border"]["width"]), height, width)
    color = ff_color(layout["border"]["color"])
    tail = f"fps={_fps(fps)},format=yuv420p[vbase]"
    if layout["mode"] == "split":
        cam_h, gap, game_h = split_geometry(layout, height, width)
        top = layout["cam_pos"] != "bottom"
        game = _fit_box(mode, anchor, width, game_h, source_crop, reframe_x, "lgame")
        # cover: вебка заполняет полосу целиком, лишнее срезается по центру.
        cam_chain = (
            f"{cam_crop},scale={width}:{cam_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{cam_h},setsar=1"
        )
        if border:
            cam_chain += f",drawbox=x=0:y=0:w=iw:h=ih:color={color}:t={border}"
        if gap:
            cam_chain += f",pad={width}:{cam_h + gap}:0:{0 if top else gap}:color={color}"
        order = "[lcam][lgame]" if top else "[lgame][lcam]"
        return f"{game};{cam_chain}[lcam];{order}vstack=inputs=2,{tail}"
    if layout["mode"] == "avatar_full":
        # Только аватар (app/stream_layout.avatar_geometry): прямоугольник cam из ВСЕГО кадра
        # исходника шириной video_scale ширин кадра, центр по высоте avatar_y; вокруг — размытый
        # тот же аватар (bg_blur) или первый цвет фона (анимированный рисует компоновщик).
        ay = float(layout.get("avatar_y", 0.5))
        uses = 2 * (1 + len(avatar_spans or []))
        source, inputs = _freeze_source(avatar_freezes or [], uses, fps)

        def avatar_frame(box: dict, scale: float, tag: str) -> str:
            src = inputs.pop(0)
            crop = f"[{src}]crop=iw*{box['w']:.5f}:ih*{box['h']:.5f}:iw*{box['x']:.5f}:ih*{box['y']:.5f}"
            if inputs and inputs[0] != "0:v":
                src_bg = inputs.pop(0)
                bg_crop = f"[{src_bg}]crop=iw*{box['w']:.5f}:ih*{box['h']:.5f}:iw*{box['x']:.5f}:ih*{box['y']:.5f}"
            else:
                if inputs:
                    inputs.pop(0)
                bg_crop = crop
            video_w = even_px(width * float(scale or 1.0))
            avatar = f"{crop},scale={video_w}:-2,setsar=1[l{tag}av]"
            if layout.get("bg_blur", True):
                background = (f"{bg_crop},scale={width}:{height}:force_original_aspect_ratio=increase,"
                              f"crop={width}:{height},boxblur=24:2,setsar=1[l{tag}bg]")
            else:
                background = (f"color=c={ff_color(fill_color(layout['bg']))}:s={width}x{height}:r={_fps(fps)}"
                              f"[l{tag}bg]")
            return f"{avatar};{background};[l{tag}bg][l{tag}av]overlay=x=(W-w)/2:y=trunc({ay:.4f}*H-h/2):shortest=1"

        if not avatar_spans:
            return f"{source}{avatar_frame(cam, float(layout.get('video_scale') or 1.0), '')},{tail}"
        # Аватар переезжает внутри части: кадр каждой рамки собирается целиком, поверх основы — на свои секунды.
        chain = [f"{source}{avatar_frame(cam, float(layout.get('video_scale') or 1.0), '0')}[lc0]"]
        last = "lc0"
        for index, span in enumerate(avatar_spans, start=1):
            chain.append(f"{avatar_frame(span['cam'], float(span.get('video_scale') or 1.0), str(index))}[lf{index}]")
            chain.append(f"[{last}][lf{index}]overlay=x=0:y=0:shortest=1:"
                         f"enable='between(t,{float(span['t0']):.3f},{float(span['t1']):.3f})'[lc{index}]")
            last = f"lc{index}"
        return ";".join(chain) + f";[{last}]{tail}"
    if layout["mode"] == "meme":
        # Видео целиком (после обрезки полей) во всю ширину, сверху; ниже — фон.
        prefix, (vin,) = _crop_prefix(source_crop, 1)
        # video_scale > 1 — видео шире кадра по центру (персонаж на зелёнке крупнее).
        top = even_px(float(layout["video_y"]) * height) if layout["video_y"] > 0 else 0
        video_w = even_px(width * float(layout.get("video_scale") or 1.0))
        video = f"{prefix}[{vin}]scale={video_w}:-2,setsar=1[lvid]"
        # Анимированный фон (app/backgrounds.py) рисует компоновщик поверх — тут его первый цвет.
        background = f"color=c={ff_color(fill_color(layout['bg']))}:s={width}x{height}:r={_fps(fps)}[lbg]"
        return f"{video};{background};[lbg][lvid]overlay=x={(width - video_w) // 2}:y={top}:shortest=1,{tail}"
    # pip_cam: обычный кадр + окошко вебки (центр и ширина — доли выхода).
    pip = layout["pip"]
    game = _fit_box(mode, anchor, width, height, source_crop, reframe_x, "lgame")
    box_w = min(width, even_px(float(pip["w"]) * width))
    inner = max(2, box_w - 2 * border)
    cam_chain = f"{cam_crop},scale={inner}:-2,setsar=1"
    if border:
        cam_chain += f",pad=iw+{2 * border}:ih+{2 * border}:{border}:{border}:color={color}"
    pos_x = f"clip({float(pip['x']):.4f}*W-w/2\\,0\\,W-w)"
    pos_y = f"clip({float(pip['y']):.4f}*H-h/2\\,0\\,H-h)"
    if game_out:
        return (
            f"{game};{cam_chain}[lcam];[lgame]split=2[lgame0][lgame1];"
            f"[lgame1][lcam]overlay=x={pos_x}:y={pos_y},{tail};[lgame0]fps={_fps(fps)},format=yuv420p[vgame]"
        )
    return f"{game};{cam_chain}[lcam];[lgame][lcam]overlay=x={pos_x}:y={pos_y},{tail}"


BANNER_MAX_WIDTH_SHARE = 0.9  # баннер в формате не 9:16 — не шире этой доли ширины кадра


def _banner_overlay(banner: dict, preset: dict, height: int, banner_input: str, source_label: str) -> str:
    """Баннер поверх ``source_label`` → ``[vout]``.

    Отдельно от основы, потому что с моушном баннер кладётся позже — поверх
    камеры и слоёв (см. app/motion_render.py), а выглядеть должен так же.
    """
    opacity = max(0, min(1, float(banner.get("opacity") if banner.get("opacity") is not None else 1)))
    x_expr, y_expr = _banner_position_expr(banner)
    # Per-render vertical position overrides the banner asset's saved placement so
    # it matches the preview band.
    y_frac = preset.get("banner_y_frac")
    if y_frac is not None:
        y_expr = str(int(round(float(y_frac) * height)))
    banner_chain = f"[{banner_input}]format=rgba,colorchannelmixer=aa={opacity}"
    bh_frac = preset.get("banner_height_frac")
    if bh_frac:
        banner_h = max(1, int(round(float(bh_frac) * height)))
        if preset.get("format"):
            # Формат не 9:16 (app/formats.py): высота — та же доля высоты кадра, но баннер
            # не шире 90 % кадра (широкая плашка в квадрате/4:5 вылезла бы за края).
            max_w = max(2, int(round(int(preset.get("output_width") or 1080) * BANNER_MAX_WIDTH_SHARE)))
            banner_chain += f",scale='min({max_w},iw*{banner_h}/ih)':-1:flags=lanczos"
        else:
            banner_chain += f",scale=-1:{banner_h}:flags=lanczos"
    banner_chain += "[banner]"
    return (
        f"{banner_chain};"
        f"[{source_label}][banner]overlay={x_expr}:{y_expr}:shortest=1,format=yuv420p[vout]"
    )


def _video_style_filter(preset: dict) -> str:
    """Build the color-grade / vignette / film-grain filter chain for a preset.

    Returns a comma-joined ffmpeg filter string (no leading/trailing comma), or
    an empty string when the preset asks for no look. Strength scales the
    intensity of the parametric color styles so the same preset can be subtle or
    heavy. Vignette and grain are added on top of the color grade.
    """

    parts: list[str] = []
    if preset.get("piece_color") and not preset.get("key_fill"):
        # Цветокоррекция куска (app/piece_color.py): сначала свести куски друг к другу, потом лук.
        # С хромакеем — нет: сдвиг цвета зелёнки сорвал бы ключ.
        from app.piece_color import video_filter

        chain = video_filter(preset["piece_color"])
        if chain:
            parts.append(chain)
    # Horizontal mirror (swap left/right) — applied after the 9:16 crop so only the
    # final frame is flipped. Handy for making reposts look different.
    if preset.get("mirror"):
        parts.append("hflip")
    style = str(preset.get("color_style") or "none").strip().lower()
    strength = _clamp(preset.get("color_strength"), default=1.0, low=0.0, high=2.0)
    if style != "none" and strength > 0:
        parts.extend(_color_style_filters(style, strength))
    vignette = _clamp(preset.get("vignette"), default=0.0, low=0.0, high=1.0)
    if vignette > 0:
        # Larger angle = darker corners. Map 0..1 onto a tasteful range.
        angle = 0.62 + vignette * 0.95
        parts.append(f"vignette=angle={angle:.4f}")
    grain = _clamp(preset.get("grain"), default=0.0, low=0.0, high=1.0)
    if grain > 0:
        strength_px = max(1, int(round(grain * 22)))
        parts.append(f"noise=alls={strength_px}:allf=t")
    return ",".join(parts)


def _color_style_filters(style: str, strength: float) -> list[str]:
    if style == "noir":
        contrast = 1 + 0.25 * strength
        brightness = -0.02 * strength
        return [f"hue=s=0", f"eq=contrast={_ff_number(contrast)}:brightness={_ff_number(brightness)}"]
    if style == "vintage":
        # ffmpeg's built-in vintage curve, eased toward the original by strength.
        eq = f"eq=saturation={_ff_number(1 - 0.15 * strength)}:contrast={_ff_number(1 + 0.05 * strength)}"
        return ["curves=preset=vintage", eq]
    if style == "warm":
        balance = f"colorbalance=rm={_ff_number(0.08 * strength)}:gm={_ff_number(0.02 * strength)}:bm={_ff_number(-0.06 * strength)}"
        eq = f"eq=contrast={_ff_number(1 + 0.06 * strength)}:saturation={_ff_number(1 + 0.08 * strength)}"
        return [balance, eq]
    if style == "cold":
        balance = f"colorbalance=rm={_ff_number(-0.06 * strength)}:bm={_ff_number(0.08 * strength)}"
        eq = f"eq=contrast={_ff_number(1 + 0.05 * strength)}:saturation={_ff_number(1 + 0.05 * strength)}"
        return [balance, eq]
    if style == "vibrant":
        return [f"eq=saturation={_ff_number(1 + 0.35 * strength)}:contrast={_ff_number(1 + 0.08 * strength)}"]
    # cinematic teal-orange: push shadows toward teal, highlights toward orange.
    balance = (
        f"colorbalance=rs={_ff_number(-0.06 * strength)}:bs={_ff_number(0.06 * strength)}:"
        f"rh={_ff_number(0.06 * strength)}:bh={_ff_number(-0.06 * strength)}"
    )
    eq = f"eq=contrast={_ff_number(1 + 0.08 * strength)}:saturation={_ff_number(1 + 0.06 * strength)}"
    return [balance, eq]


def _source_edit(preset: dict) -> dict | None:
    """Операции звука части (app/audio_edit.part_edit), если граф звука их понимает: смесь
    дорожек источника или первая/своя дорожка пресета (старые режимы mix/secondary — без них)."""
    from app.audio_edit import has_source_ops

    edit = preset.get("audio_edit")
    if not has_source_ops(edit):
        return None
    mode = str(preset.get("audio_mix_mode") or "primary").strip().lower()
    return edit if preset.get("source_audio_tracks") or mode == "primary" else None


def _audio_filter(preset: dict) -> str:
    edit = _source_edit(preset)
    if edit:
        # Звуковой монтаж части: дорожки по отдельности (тишина/громкость/эффекты дорожки), потом
        # общие эффекты и ключи громкости исходника (app/audio_edit.source_graph).
        from app.audio_edit import source_graph

        tracks = preset.get("source_audio_tracks") or [_stream_index(preset.get("audio_primary_stream"), 0)]
        volume = _volume(preset.get("audio_primary_volume"), 1)
        return f"{source_graph(0, list(tracks), edit, 'a_src')};[a_src]volume={_ff_number(volume)},aresample=48000[aout]"
    tracks = preset.get("source_audio_tracks")
    if tracks:
        # Выбор источника важнее пресета: запись с игрой и микрофоном смешивается всегда.
        from app.source_audio import mix_chain

        volume = _volume(preset.get("audio_primary_volume"), 1)
        return f"{mix_chain(0, list(tracks), 'a_src')};[a_src]volume={_ff_number(volume)},aresample=48000[aout]"
    mode = str(preset.get("audio_mix_mode") or "primary").strip().lower()
    primary_stream = _stream_index(preset.get("audio_primary_stream"), 0)
    primary_volume = _volume(preset.get("audio_primary_volume"), 1)
    secondary_stream = _stream_index(preset.get("audio_secondary_stream"), 1)
    secondary_volume = _volume(preset.get("audio_secondary_volume"), 1)
    if mode == "mix":
        return (
            f"[0:a:{primary_stream}]volume={_ff_number(primary_volume)}[a_primary];"
            f"[0:a:{secondary_stream}]volume={_ff_number(secondary_volume)}[a_secondary];"
            "[a_primary][a_secondary]amix=inputs=2:duration=longest:dropout_transition=0,"
            "aresample=48000[aout]"
        )
    if mode == "secondary":
        return f"[0:a:{secondary_stream}]volume={_ff_number(secondary_volume)},aresample=48000[aout]"
    if primary_stream != 0 or primary_volume != 1:
        return f"[0:a:{primary_stream}]volume={_ff_number(primary_volume)},aresample=48000[aout]"
    return ""


def _retimed(graph: str, preset: dict, fps: float) -> str:
    """Граф куска + скорость/реверс: хвост картинки после кадрирования и лука (кадр уже в
    размере выхода — reverse держит в памяти меньше), хвост звука после смешивания дорожек."""
    from app.piece_speed import audio_chain, video_chain

    retime = preset.get("piece_retime") or {}
    segment = retime.get("segment") or {}
    head, sep, tail = graph.rpartition("[vout]")
    if not sep:
        return graph
    # Заглушка картинки бесконечна: реверс её не дождался бы, длину режет -frames:v.
    video = "null" if preset.get("stand_in") else (video_chain(segment, fps) or "null")
    graph = f"{head}[vretime_in]{tail};[vretime_in]{video}[vout]"
    if not retime.get("has_audio"):
        return graph
    chain = audio_chain(segment) or "anull"
    if "[aout]" in graph:
        a_head, _sep, a_tail = graph.rpartition("[aout]")
        return f"{a_head}[aretime_in]{a_tail};[aretime_in]{chain}[aout]"
    stream = _stream_index(preset.get("audio_primary_stream"), 0)
    return f"{graph};[0:a:{stream}]aresample=48000,{chain}[aout]"


def _audio_map(preset: dict) -> str:
    if preset.get("source_audio_tracks") or _source_edit(preset):
        return "[aout]"
    if (preset.get("piece_retime") or {}).get("has_audio"):
        return "[aout]"  # скорость/реверс куска — звук всегда через граф (_retimed)
    mode = str(preset.get("audio_mix_mode") or "primary").strip().lower()
    primary_stream = _stream_index(preset.get("audio_primary_stream"), 0)
    primary_volume = _volume(preset.get("audio_primary_volume"), 1)
    if mode in {"mix", "secondary"} or primary_stream != 0 or primary_volume != 1:
        return "[aout]"
    return f"0:a:{primary_stream}?"


def _banner_position_expr(banner: dict) -> tuple[str, str]:
    if banner.get("position") == "custom":
        return str(int(banner.get("x") or 0)), str(int(banner.get("y") or 0))
    x_expr = "(main_w-overlay_w)/2"
    if banner.get("position") == "top":
        return x_expr, "0"
    if banner.get("position") == "center":
        return x_expr, "(main_h-overlay_h)/2"
    return x_expr, "main_h-overlay_h"
