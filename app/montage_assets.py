"""«Файлы для монтажа»: the owner's library of memes, reaction clips, stickers
and sounds — and the render pass that drops them into a finished clip.

An *insert* puts one library file into a clip at a moment of the clip's own
timeline:

    {"asset_id": 7, "at": 3.2, "duration": 1.6, "mode": "full", "volume": 1.0,
     "duck": true, "reason": "…"}

* ``full``  — the meme covers the whole 9:16 frame (around another aspect — a blurred copy of itself;
  ``fill``: ``cover`` crops it to the frame, ``black`` — the old letterbox),
* ``pip``   — a smaller window in the upper part of the frame, the clip stays visible,
* ``sound`` — audio only (a sting, a laugh track, a «bruh»).

Video/GIF/audio inserts bring their sound in at ``volume``; ``duck`` lowers the
clip's own audio under a full-screen meme so the punchline lands; ``duck_volume`` (0..1) — how low: 0 cuts the
clip's sound for the insert (a video-meme cut-in, app/video_memes.py), and set explicitly it ducks under a ``pip``
window too. ``level: "speech"`` levels the insert's sound to the clip's voice instead of «a quieter effect» (a
reaction that speaks). The pass runs
once on the rendered file (after subtitles and music), so it works the same for
single-piece clips, montages, batch renders and the auto pipeline.
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.settings import settings

KINDS = ("image", "gif", "video", "audio")
MODES = ("full", "pip", "sound")
# Мем-монтаж игры (врезки видеомемов каждые 4–6 с, app/video_memes.py) — до 12 вставок на клип.
MAX_INSERTS = 12
MAX_INSERT_SEC = 8.0
MIN_INSERT_SEC = 0.3
DUCK_VOLUME = 0.2
LEVELS = ("speech",)  # громкость вставки вровень с голосом клипа (видеомем говорит), а не «эффект под голосом»
SPEECH_TARGET_DB = -14.0  # «голос», когда своего звука у клипа нет
PIP_WIDTH = 0.82  # доля ширины кадра — ширина вставки по умолчанию
PIP_TOP = 0.14  # of the frame height
# Где стоит вставка: центр в долях кадра плюс ширина в долях ширины. Раньше
# коробка была прибита гвоздями (по центру, сверху), и подвинуть мем было нельзя.
INSERT_DEFAULT_X = 0.5
INSERT_DEFAULT_Y = 0.32
MIN_INSERT_SCALE = 0.1
MAX_INSERT_SCALE = 1.0
# Сколько высоты кадра вставке разрешено занять: очень высокая картинка иначе
# вылезет за кадр целиком.
INSERT_MAX_HEIGHT = 0.9



# Чем заполнить кадр вокруг полнокадровой вставки другой пропорции (горизонтальная гифка в 9:16):
# blur — размытая копия самой вставки (по умолчанию), cover — вставка обрезана во весь кадр, black — поля.
# QA 02.11 (клип 892): у гифки «LIVE MEAL CAM» ≈55 % кадра были чёрные поля.
# clip — вставка-стикер поверх РАЗМЫТОГО КАДРА КЛИПА под ней. QA 4 (клип 896): гифка «dancing cockroach» —
# синий таракан на сплошном чёрном фоне (прозрачность «сплющена» в чёрное) — во весь кадр стояла на чёрном:
# размытая копия чёрной гифки — тоже чёрная. Теперь у файла с прозрачностью (альфа) и у файла со сплошным
# чёрным фоном (чёрная рамка по краю кадра — сплющенный стикер; чёрное выбивается colorkey) по умолчанию
# подложка — размытый кадр клипа.
FULL_FILLS = ("blur", "cover", "black", "clip")
DEFAULT_FULL_FILL = "blur"
ALPHA_FILL = "clip"  # по умолчанию для прозрачных вставок и стикеров на чёрном (asset_matte)
CLIP_BLUR = "boxblur=24:2"
ALPHA_SHARE = 0.02  # доля заметно прозрачных пикселей первого кадра, с которой файл «с прозрачностью»
BLACK_EDGE_SHARE = 0.9  # доля почти чёрных пикселей по краю кадра — фон «сплющенного» стикера
BLACK_ALL_SHARE = 0.5  # …и во всём кадре не меньше этой доли: тёмное фото с виньеткой не выбиваем
BLACK_LEVEL = 24
BLACK_KEY = "colorkey=0x000000:0.08:0.04"


def asset_matte(path: str | Path) -> str | None:
    """Фон стикера: ``alpha`` — у файла есть прозрачность, ``black`` — сплошной чёрный фон (край кадра
    почти весь чёрный и чёрного ≥ половины), None — обычная картинка. Первый кадр, кэш по файлу."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return _matte(str(path), st.st_mtime_ns, st.st_size)


def asset_has_alpha(path: str | Path) -> bool:
    return asset_matte(path) == "alpha"


@lru_cache(maxsize=256)
def _matte(path: str, _mtime: int, _size: int) -> str | None:
    side = 48
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-vf", f"scale={side}:{side},format=rgba",
             "-f", "rawvideo", "-"], capture_output=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if len(raw) < side * side * 4:
        return None
    alpha = raw[3::4]
    if sum(1 for a in alpha if a < 250) / len(alpha) >= ALPHA_SHARE:
        return "alpha"

    def dark(i: int) -> bool:
        return max(raw[i * 4], raw[i * 4 + 1], raw[i * 4 + 2]) < BLACK_LEVEL

    edge = [y * side + x for y in range(side) for x in range(side) if x in (0, side - 1) or y in (0, side - 1)]
    if (sum(dark(i) for i in edge) / len(edge) >= BLACK_EDGE_SHARE
            and sum(dark(i) for i in range(side * side)) / (side * side) >= BLACK_ALL_SHARE):
        return "black"
    return None


def resolve_fill(ins: dict, asset: dict | None) -> str:
    """Чем заполнить кадр вокруг полнокадровой вставки: заданный ``fill``, иначе у стикера (прозрачность или
    сплошной чёрный фон) — размытый кадр клипа (``clip``), иначе размытая копия самой вставки (``blur``)."""
    fill = ins.get("fill")
    if fill in FULL_FILLS:
        return str(fill)
    if asset and asset.get("kind") in ("image", "gif") and asset_matte(asset.get("file_path") or ""):
        return ALPHA_FILL
    return DEFAULT_FULL_FILL


def key_black(ins: dict, asset: dict | None) -> bool:
    """Выбить чёрный фон стикера (colorkey) — подложка ``clip`` и сплошной чёрный фон у файла."""
    return (resolve_fill(ins, asset) == ALPHA_FILL and bool(asset)
            and asset_matte(asset.get("file_path") or "") == "black")


def crop_filter(crop: Any) -> str:
    """``crop=…,`` ffmpeg по окну вставки ``{x, y, w, h}`` (доли её кадра) или пусто — без окна."""
    box = normalize_crop(crop)
    if not box:
        return ""
    return (f"crop=trunc(iw*{box['w']:.4f}/2)*2:trunc(ih*{box['h']:.4f}/2)*2:"
            f"trunc(iw*{box['x']:.4f}):trunc(ih*{box['y']:.4f}),")


def normalize_crop(raw: Any) -> dict | None:
    """Окно вставки в долях её кадра: срезать вшитые чёрные поля мема и/или подрезать под кадр клипа
    (app/meme_frame.py). Мусор, окно меньше 10 % или весь кадр — None."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y, w, h = (float(raw.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", 1), ("h", 1)))
    except (TypeError, ValueError):
        return None
    if not all(v == v for v in (x, y, w, h)):
        return None
    x, y = min(0.9, max(0.0, x)), min(0.9, max(0.0, y))
    w, h = min(1.0 - x, max(0.1, w)), min(1.0 - y, max(0.1, h))
    if w > 0.995 and h > 0.995:
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def full_insert_chain(src: str, width: int, height: int, fill: str | None, tag: str, key: bool = False,
                      crop: Any = None) -> str:
    """Граф полнокадровой вставки ``src`` (метка входа, например ``[1:v]``) в кадр ``width``×``height`` —
    без выходной метки (дописывает вызывающий). ``tag`` — уникальная часть внутренних меток. ``clip`` —
    вставка по центру на прозрачном поле (``key`` — чёрный фон выбит): под неё вызывающий кладёт размытый
    кадр клипа. ``crop`` — окно вставки (доли её кадра) до всего остального: вшитые поля мема прочь."""
    fill = fill if fill in FULL_FILLS else DEFAULT_FULL_FILL
    window = crop_filter(crop)
    if window:
        src = f"{src}{window}"
    if fill == "clip":
        return (f"{src}format=rgba,{BLACK_KEY + ',' if key else ''}"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2,format=rgba,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1")
    if fill == "cover":
        return (f"{src}scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1")
    if fill == "black":
        return (f"{src}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    return (f"{src}split=2[fb{tag}][ff{tag}];"
            f"[fb{tag}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
            f"boxblur=24:2,setsar=1[fbb{tag}];"
            f"[ff{tag}]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[fff{tag}];"
            f"[fbb{tag}][fff{tag}]overlay=x=(W-w)/2:y=(H-h)/2")


def _insert_volume(ins: dict, speech_db: float | None = None) -> float:
    """Звук вставки — как звуки-слои: тише голоса клипа (motion_render.effect_volume) × volume ≤ 1.
    ``level: speech`` — вровень с голосом клипа (громкие места вставки = громкие места клипа), пик не выше
    EFFECT_PEAK_DB: реакция-видеомем говорит вместо клипа, а не звучит эффектом под ним."""
    from app.motion_render import audio_levels, EFFECT_PEAK_DB, effect_volume

    path = str(ins["asset"]["file_path"])
    if ins.get("level") == "speech":
        user = _num_or_none(ins.get("volume"))
        user = 1.0 if user is None else max(0.0, min(2.0, user))
        levels = audio_levels(path)
        if not levels:
            return user
        rms, peak = levels
        target = speech_db if speech_db is not None else SPEECH_TARGET_DB
        return user * 10 ** (max(-40.0, min(12.0, target - rms, EFFECT_PEAK_DB - peak)) / 20)
    return effect_volume(path, ins.get("volume", 1.0), speech_db)

def assets_dir() -> Path:
    path = settings.data_dir / "montage_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_for(filename: str, mime: str = "") -> str | None:
    name = filename.lower()
    mime = (mime or mimetypes.guess_type(name)[0] or "").lower()
    if name.endswith(".gif") or mime == "image/gif":
        return "gif"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return None


def probe_asset(path: Path) -> dict:
    """duration / size / whether it carries sound — best effort, never raises."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration_sec": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _fraction(value: Any, default: float) -> float:
    """Доля кадра 0..1; мусор и пустое — значение по умолчанию."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(1.0, max(0.0, number))


def _num_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def normalize_inserts(raw: Any) -> list[dict]:
    """Coerce stored/requested inserts into a safe list (junk entries dropped)."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            asset_id = int(item.get("asset_id"))
            at = max(0.0, float(item.get("at", 0)))
            duration = float(item.get("duration", 1.5))
        except (TypeError, ValueError):
            continue
        mode = item.get("mode") if item.get("mode") in MODES else "full"
        try:
            volume = min(2.0, max(0.0, float(item.get("volume", 1.0))))
        except (TypeError, ValueError):
            volume = 1.0
        entry = {
            "asset_id": asset_id,
            "at": round(at, 2),
            "duration": round(min(MAX_INSERT_SEC, max(MIN_INSERT_SEC, duration)), 2),
            "mode": mode,
            "volume": round(volume, 2),
            "duck": bool(item.get("duck", mode == "full")),
            "x": round(_fraction(item.get("x"), INSERT_DEFAULT_X), 4),
            "y": round(_fraction(item.get("y"), INSERT_DEFAULT_Y), 4),
            "scale": round(
                min(MAX_INSERT_SCALE, max(MIN_INSERT_SCALE, _fraction(item.get("scale"), PIP_WIDTH))), 4
            ),
        }
        duck_volume = _num_or_none(item.get("duck_volume"))
        if duck_volume is not None:
            entry["duck_volume"] = round(min(1.0, max(0.0, duck_volume)), 2)  # 0 — звук клипа выключен
        if item.get("level") in LEVELS:
            entry["level"] = item["level"]
        if item.get("fill") in FULL_FILLS and item.get("fill") != DEFAULT_FULL_FILL:
            entry["fill"] = item["fill"]  # чем заполнить кадр вокруг полнокадровой вставки (по умолчанию — размытие)
        crop = normalize_crop(item.get("crop"))
        if crop:
            entry["crop"] = crop  # окно вставки: вшитые поля мема срезаны, подрезка под кадр клипа
        reason = item.get("reason")
        if isinstance(reason, str) and reason.strip():
            entry["reason"] = reason.strip()[:200]
        out.append(entry)
        if len(out) >= MAX_INSERTS:
            break
    return sorted(out, key=lambda e: e["at"])


def build_inserts_args(
    clip_path: Path,
    output_path: Path,
    inserts: list[dict],
    assets: dict[int, dict],
    width: int,
    height: int,
    duration: float,
    clip_has_audio: bool = True,
    video_only: bool = False,
    base_label: str = "[0:v]",
    first_input: int = 1,
) -> list[str] | None:
    """ffmpeg command overlaying the inserts on the clip, or None if nothing applies.

    ``video_only`` — только картинка (предпросмотр-трансляция, app/stream_preview.py: звук у неё свой проход),
    ``base_label``/``first_input`` — откуда брать кадр и с какого номера входа идут файлы вставок."""
    from app.motion_render import speech_level_db

    speech_db = speech_level_db(clip_path) if clip_has_audio and not video_only else None
    usable = []
    for ins in inserts:
        asset = assets.get(ins["asset_id"])
        if not asset or not Path(asset["file_path"]).exists():
            continue
        if ins["at"] >= duration - 0.05:
            continue
        end = min(duration, ins["at"] + ins["duration"])
        kind = asset["kind"]
        mode = "sound" if kind == "audio" else ins["mode"]
        if mode == "sound" and not (kind == "audio" or asset.get("has_audio")):
            continue  # a silent picture has nothing to play as a sound
        if video_only and (mode == "sound" or kind == "audio"):
            continue
        usable.append({**ins, "end": end, "len": end - ins["at"], "mode": mode, "asset": asset})
    if not usable:
        return None

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path)]
    for ins in usable:
        kind = ins["asset"]["kind"]
        path = ins["asset"]["file_path"]
        if kind == "image":
            args += ["-loop", "1", "-t", f"{ins['len']:.3f}", "-i", path]
        elif kind == "gif":
            args += ["-ignore_loop", "0", "-t", f"{ins['len']:.3f}", "-i", path]
        elif kind == "video":
            args += ["-stream_loop", "-1", "-t", f"{ins['len']:.3f}", "-i", path]
        else:
            args += ["-t", f"{ins['len']:.3f}", "-i", path]

    graph: list[str] = []
    v_last = base_label
    audio_parts: list[str] = []
    ducks: list[str] = []
    for n, ins in enumerate(usable, start=first_input):
        at, end, mode = ins["at"], ins["end"], ins["mode"]
        kind = ins["asset"]["kind"]
        if mode in ("full", "pip") and kind != "audio":
            if mode == "full":
                # Во весь кадр: вокруг другой пропорции — размытая копия вставки (fill), не чёрные поля;
                # прозрачная гифка/картинка — поверх размытого кадра клипа (resolve_fill → clip).
                x, y = "(W-w)/2", "(H-h)/2"
                fill = resolve_fill(ins, ins["asset"])
                chain = full_insert_chain(f"[{n}:v]", width, height, fill, str(n), key=key_black(ins, ins["asset"]),
                                          crop=ins.get("crop"))
                graph.append(
                    f"{chain},format=yuva420p,"
                    f"setpts=PTS-STARTPTS+{at:.3f}/TB[ov{n}]"
                )
                if fill == ALPHA_FILL:
                    graph.append(f"{v_last}split=2[bm{n}][bs{n}];[bs{n}]{CLIP_BLUR}[bb{n}];"
                                 f"[bm{n}][bb{n}]overlay=enable='between(t,{at:.3f},{end:.3f})'[bg{n}]")
                    v_last = f"[bg{n}]"
            else:
                # Вставка шириной scale от кадра, высота по пропорциям картинки;
                # x/y — центр вставки в долях кадра (их двигают ручками в редакторе).
                box_w = max(2, int(width * ins["scale"]) // 2 * 2)
                box_h = max(2, int(height * INSERT_MAX_HEIGHT) // 2 * 2)
                x, y = f"(W*{ins['x']:.4f})-(w/2)", f"(H*{ins['y']:.4f})-(h/2)"
                graph.append(
                    f"[{n}:v]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,"
                    f"scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuva420p,"
                    f"setpts=PTS-STARTPTS+{at:.3f}/TB[ov{n}]"
                )
            graph.append(
                f"{v_last}[ov{n}]overlay=x={x}:y={y}:eof_action=pass:"
                f"enable='between(t,{at:.3f},{end:.3f})'[v{n}]"
            )
            v_last = f"[v{n}]"
            if ins.get("duck") and (mode == "full" or "duck_volume" in ins):
                ducks.append((f"between(t,{at:.3f},{end:.3f})", float(ins.get("duck_volume", DUCK_VOLUME))))
        if not video_only and (kind == "audio" or ins["asset"].get("has_audio")):
            delay = int(round(at * 1000))
            graph.append(
                f"[{n}:a]aresample=48000,atrim=0:{ins['len']:.3f},asetpts=PTS-STARTPTS,"
                # Громкость выравнивается системно, как у звуков-слоёв (motion_render.effect_volume).
                f"volume={_insert_volume(ins, speech_db):.4f},adelay={delay}|{delay}[a{n}]"
            )
            audio_parts.append(f"[a{n}]")

    if clip_has_audio and not video_only:
        base = "[0:a]aresample=48000"
        # Своя глубина у каждой вставки (0 — звук клипа выключен на врезку видеомема); одинаковые — одним фильтром.
        for level in sorted({vol for _span, vol in ducks}):
            spans = "+".join(span for span, vol in ducks if vol == level)
            base += f",volume=enable='{spans}':volume={level:g}"
        graph.append(base + "[a0]")
        audio_parts.insert(0, "[a0]")
    if len(audio_parts) > 1:
        graph.append(
            "".join(audio_parts)
            + f"amix=inputs={len(audio_parts)}:duration=first:normalize=0:dropout_transition=0[aout]"
        )
        a_map = "[aout]"
    elif audio_parts:
        a_map = audio_parts[0]
    else:
        a_map = None

    args += ["-filter_complex", ";".join(graph), "-map", v_last]
    if a_map:
        args += ["-map", a_map, "-c:a", "aac", "-b:a", "192k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    return args
