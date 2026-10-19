"""Моушн-проход рендера: команды для Node-компоновщика и ffmpeg (docs/motion/spec.md).

Здесь только «чистые» сборщики команд и поиск файлов слоёв; сам порядок проходов
живёт в ``ClipRenderService`` (app/render.py). Так команды легко проверять в
тестах без ffmpeg и Node, а рендер остаётся одним местом, где решается порядок.

Порядок в рендере клипа с моушном:

1. основа: куски, кроп/рефрейм, лук, переходы — **без баннера**;
2. компоновщик: камера двигает только основу, поверх рисуются слои;
3. один дешёвый ffmpeg-проход: звук основы обратно (видео копией) и баннер
   поверх слоёв (если баннер есть — с перекодированием);
4. субтитры и музыка (``_finalize_render``) — поверх камеры и слоёв, не зумятся;
5. обложка, вставки «Файлов для монтажа»;
6. звуки ``audio``-слоёв — последним дешёвым проходом (видео копией): чтобы
   распознавание субтитров слышало чистую речь, как и с музыкой.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.motion import asset_key, caption_words, has_captions, has_chat, layer_asset_refs

# Насколько приглушать исходник под звуком слоя с ``duck`` (как у вставок).
AUDIO_DUCK_VOLUME = 0.3
DEFAULT_CRF = 18


class CompositorMissing(RuntimeError):
    """Компоновщик не собран или Node не найден — моушн нарисовать нечем."""


def compositor_command() -> list[str]:
    """``[node, motion-render.mjs]`` или CompositorMissing с понятной причиной."""
    from app.settings import settings

    script = Path(settings.motion_compositor)
    if not script.exists():
        raise CompositorMissing(
            f"компоновщик моушна не собран: нет {script} (соберите: npm run build:motion)"
        )
    node = settings.motion_node
    if not (Path(node).exists() or shutil.which(node)):
        raise CompositorMissing(f"Node не найден: {node!r} (MOTION_NODE)")
    return [node, str(script)]


def build_compositor_args(
    comp_path: Path,
    assets_path: Path,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    crf: Any = None,
    frame: float | None = None,
    png_path: Path | None = None,
    command: list[str] | None = None,
) -> list[str]:
    """CLI компоновщика: видео целиком (``--output``) или один кадр (``--frame --png``)."""
    from app.settings import settings

    args = list(command or compositor_command())
    args += ["--comp", str(comp_path)]
    if input_path is not None:
        args += ["--input", str(input_path)]
    if output_path is not None:
        args += ["--output", str(output_path)]
    args += ["--assets", str(assets_path), "--fonts-dir", str(settings.motion_fonts_dir), "--ffmpeg", "ffmpeg"]
    # Эмодзи внутри текста (титр «ОН ЭТО СДЕЛАЛ 🔥») не попадают в карту ассетов —
    # компоновщик берёт их прямо из пака Twemoji по имени файла.
    emoji_dir = Path(settings.motion_packs_dir) / "twemoji"
    if emoji_dir.is_dir():
        args += ["--emoji-dir", str(emoji_dir)]
    args += ["--crf", str(crf if crf not in (None, "") else DEFAULT_CRF)]
    if frame is not None:
        args += ["--frame", f"{max(0.0, float(frame)):.3f}"]
    if png_path is not None:
        args += ["--png", str(png_path)]
    return args


def write_comp(
    path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
    layers: list[dict],
    camera: list[dict],
    words: list[dict] | None = None,
    chat: list[dict] | None = None,
) -> Path:
    comp = {
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
        "duration": round(float(duration), 3),
        "layers": layers,
        "camera": camera,
    }
    if words:
        comp["words"] = words
    if chat:
        comp["chat"] = chat
    path.write_text(json.dumps(comp, ensure_ascii=False), encoding="utf-8")
    return path


def plan_caption_words(store: Any, plan: dict) -> list[dict]:
    """Слова сохранённых субтитров клипа в клиповом времени — для слоёв ``captions``.

    Те же слова и тот же сдвиг на переходы, что у прожигаемых субтитров
    (``ClipRenderService._plan_subtitles_for_clip``): анимированные субтитры
    совпадают с речью ровно так же. Нет субтитров — пусто.
    """
    from app.clip_subtitles import shift_for_transition

    try:
        payload = store.get_clip_plan_subtitles(int(plan["id"]))
    except (KeyError, TypeError, ValueError):
        return []
    if not payload or not payload.get("words"):
        return []
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    transition = (plan.get("render_settings") or {}).get("transition") or {}
    overlap = float(transition.get("duration") or 0) if transition.get("type") not in (None, "cut") else 0.0
    return caption_words(shift_for_transition(payload, pieces, overlap))


def attach_caption_words(store: Any, plan: dict, motion: dict | None) -> dict | None:
    """Добавить в моушн слова речи, если есть видимый слой субтитров.

    Слов нет (субтитры клипа ещё не сделаны) — слой ничего не покажет; это
    пометка для ошибки клипа (``host_notes``), а не сбой рендера.
    """
    if not motion or not has_captions(motion.get("layers")):
        return motion
    words = plan_caption_words(store, plan)
    if words:
        return {**motion, "words": words}
    return _with_note(motion, "у клипа нет сохранённых субтитров — слой «субтитры» пуст (сделайте субтитры в редакторе)")


def _with_note(motion: dict, note: str) -> dict:
    """Пометка для ошибки клипа: слой пуст, но рендер идёт дальше."""
    return {**motion, "host_notes": [*(motion.get("host_notes") or []), note]}


# ---------------------------------------------------------------- чат стрима

# Сколько сообщений и картинок эмоутов/значков отдаём компоновщику на клип.
MAX_CHAT_MESSAGES = 5000
MAX_CHAT_IMAGES = 400
CHAT_IMAGE_TIMEOUT = 5.0
CHAT_IMAGE_MAX_BYTES = 3 * 1024 * 1024


def chat_windows(layers: list[dict]) -> list[tuple[float, float, float]]:
    """Окна видимых слоёв чата: (start, end, delay) — сообщение t видно, если start <= t - delay < end."""
    out = []
    for layer in layers or []:
        if layer.get("type") != "chat" or layer.get("hidden"):
            continue
        delay = float((layer.get("props") or {}).get("delay") or 0)
        out.append((float(layer["start"]), float(layer["end"]), delay))
    return out


def plan_chat_messages(store: Any, plan: dict, layers: list[dict]) -> list[dict]:
    """Сообщения чата клипа (время клипа, как GET /api/clip-plans/{id}/chat), попадающие в слои чата."""
    try:
        from app.twitch_chat import clip_messages

        messages = clip_messages(store, plan, limit=20000)
    except Exception:  # noqa: BLE001 - нет чата/таблиц — слой просто пуст
        return []
    windows = chat_windows(layers)
    keep = [
        {k: m.get(k) for k in ("t", "user", "color", "text", "fragments", "badges")}
        for m in messages
        if any(start <= float(m["t"]) - delay < end for start, end, delay in windows)
    ]
    return keep[:MAX_CHAT_MESSAGES]


def chat_image_urls(messages: list[dict], layers: list[dict]) -> list[str]:
    """Адреса эмоутов и значков, которые покажут слои чата (с учётом showEmotes/showBadges)."""
    chat_layers = [layer for layer in layers or [] if layer.get("type") == "chat" and not layer.get("hidden")]
    emotes = any((layer.get("props") or {}).get("showEmotes", True) for layer in chat_layers)
    badges = any((layer.get("props") or {}).get("showBadges", True) for layer in chat_layers)
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: Any) -> None:
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            urls.append(url)

    for message in messages:
        if emotes:
            for frag in message.get("fragments") or []:
                if isinstance(frag, dict) and frag.get("emote"):
                    add(frag.get("url"))
        if badges:
            for badge in message.get("badges") or []:
                if isinstance(badge, dict):
                    add(badge.get("url"))
    return urls[:MAX_CHAT_IMAGES]


def _download(url: str) -> bytes | None:
    import httpx

    with httpx.Client(timeout=CHAT_IMAGE_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        if response.status_code != 200 or len(response.content) > CHAT_IMAGE_MAX_BYTES:
            return None
        return response.content


def fetch_chat_images(
    urls: list[str],
    cache_dir: Path,
    fetch: Callable[[str], bytes | None] | None = None,
) -> dict[str, str]:
    """Скачать эмоуты/значки в кэш (имя — sha1 адреса) → записи assets.json ``url:<адрес>``.

    Картинка, которую не скачать (таймаут, 404, CDN лёг), пропускается: эмоут
    нарисуется своим именем, значок — не нарисуется. Рендер из-за этого не падает.
    """
    fetch = fetch or _download
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(url: str) -> tuple[str, str | None]:
        path = cache_dir / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")
        if path.is_file() and path.stat().st_size > 0:
            return url, str(path)
        try:
            data = fetch(url)
        except Exception:  # noqa: BLE001 - сеть — не повод ронять рендер
            data = None
        if not data:
            return url, None
        tmp = path.with_name(f"{path.name}.{uuid4().hex}.part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return url, str(path)

    assets: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for url, path in pool.map(one, urls):
            if path:
                assets[f"url:{url}"] = path
    return assets


def attach_chat(
    store: Any,
    plan: dict,
    motion: dict | None,
    fetch: Callable[[str], bytes | None] | None = None,
) -> dict | None:
    """Сообщения чата и картинки эмоутов — в моушн, если есть видимый слой чата."""
    if not motion or not has_chat(motion.get("layers")):
        return motion
    from app.settings import settings

    layers = motion.get("layers") or []
    messages = plan_chat_messages(store, plan, layers)
    if not messages:
        return _with_note(motion, "у клипа нет чата Twitch — слой «чат» пуст (импортируйте чат источника)")
    assets = fetch_chat_images(chat_image_urls(messages, layers), settings.runtime_dir / "emote-cache", fetch)
    return {**motion, "chat": messages, "chat_assets": assets}


def attach_host_data(store: Any, plan: dict, motion: dict | None) -> dict | None:
    """Данные клипа для слоёв, которые рисуют не свои props: слова речи и чат."""
    return attach_chat(store, plan, attach_caption_words(store, plan, motion))


def pack_refs_available() -> bool:
    """Есть ли уже модуль пакетов (эмодзи/стикеры/звуки) с ``resolve_pack_ref``."""
    try:
        from app.motion_assets import resolve_pack_ref as _resolve  # type: ignore[attr-defined]  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def resolve_pack_ref(ref: str) -> Path | None:
    """``emoji:`` / ``sticker:`` / ``sfx:`` → файл пакета (модуль app.motion_assets).

    Модуль пакетов делает другая часть системы; пока его нет (или он не знает
    ссылку) — ссылка считается ненайденной, слой пропускается с пометкой.
    """
    try:
        from app.motion_assets import resolve_pack_ref as _resolve  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - модуля пакетов может ещё не быть
        return None
    try:
        found = _resolve(ref)
    except Exception:  # noqa: BLE001 - чужой модуль не должен ронять рендер
        return None
    if not found:
        return None
    path = Path(found)
    return path if path.exists() else None


# Какие файлы для монтажа годятся слою: картинке — картинка/гифка, звуку — то, где есть звук.
_IMAGE_KINDS = ("image", "gif")


def resolve_layer_assets(store: Any, layers: list[dict]) -> tuple[list[dict], dict[str, str], list[str]]:
    """Найти файлы слоёв. Возвращает (слои, которые можно рисовать, assets.json, пометки).

    Слой без своего файла пропускается: лучше клип без одного стикера, чем
    падение рендера или пустой прямоугольник.
    """
    kept: list[dict] = []
    assets: dict[str, str] = {}
    notes: list[str] = []
    for layer in layers:
        ok = True
        for ref in layer_asset_refs(layer):
            key = asset_key(ref)
            if key in assets:
                continue
            path = _resolve_ref(store, ref, layer["type"])
            if path is None:
                what = f"файл для монтажа {ref}" if isinstance(ref, int) else ref
                if layer["type"] == "callout":
                    # Значок — украшение: плашка нужна и без него.
                    notes.append(f"слой {layer['id']}: значок {what} не найден")
                    continue
                notes.append(f"слой {layer['id']}: {what} не найден — пропущен")
                ok = False
                break
            assets[key] = str(path)
        if ok:
            kept.append(layer)
    return kept, assets, notes


def _resolve_ref(store: Any, ref: int | str, layer_type: str) -> Path | None:
    if not isinstance(ref, int):
        return resolve_pack_ref(ref)
    try:
        asset = store.get_montage_asset(ref)
    except KeyError:
        return None
    path = Path(asset.get("file_path") or "")
    if not path.exists():
        return None
    kind = asset.get("kind")
    if layer_type == "audio":
        return path if kind == "audio" or asset.get("has_audio") else None
    return path if kind in _IMAGE_KINDS else None


def build_motion_mux_args(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    preset: dict,
    banner: dict | None,
) -> list[str]:
    """Видео компоновщика + звук основы; баннер — поверх слоёв (как раньше поверх видео).

    Без баннера видео копируется как есть — проход почти бесплатный.
    """
    from app.render import _banner_input_args, _banner_overlay, _json_dict

    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
    next_input = 1
    if banner:
        args += _banner_input_args(banner)
        next_input += 1
    if Path(audio_path) == Path(video_path):
        audio_index = 0
    else:
        args += ["-i", str(audio_path)]
        audio_index = next_input
    if banner:
        height = int(preset.get("output_height") or 1920)
        args += ["-filter_complex", _banner_overlay(banner, preset, height, "1:v", "0:v"), "-map", "[vout]"]
        args += ["-c:v", str(preset.get("video_codec") or "libx264")]
        video_bitrate = str(preset.get("video_bitrate") or "").strip()
        if video_bitrate:
            args += ["-b:v", video_bitrate]
        crf = _json_dict(preset.get("extra_json")).get("crf")
        if crf is not None:
            args += ["-crf", str(crf)]
    else:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    args += ["-map", f"{audio_index}:a:0?", "-c:a", "copy", "-movflags", "+faststart", str(output_path)]
    return args


def build_audio_layers_args(
    clip_path: Path,
    output_path: Path,
    layers: list[dict],
    assets: dict[str, str],
    duration: float,
    clip_has_audio: bool = True,
) -> list[str] | None:
    """Подмешать звуки ``audio``-слоёв в готовый клип (видео копией).

    Каждый звук: с ``offset`` секунды файла, громкость, fadeIn/fadeOut, сдвиг на
    ``start`` клипа (adelay). ``duck`` приглушает исходник на время звука.
    None — подмешивать нечего.
    """
    usable: list[dict] = []
    for layer in layers:
        props = layer["props"]
        path = assets.get(asset_key(props.get("asset_id")))
        start = float(layer["start"])
        end = min(float(layer["end"]), float(duration))
        if not path or end - start < 0.05:
            continue
        usable.append({"layer": layer, "path": path, "start": start, "end": end, "len": end - start})
    if not usable:
        return None

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path)]
    for item in usable:
        offset = float(item["layer"]["props"].get("offset") or 0)
        if offset > 0:
            args += ["-ss", f"{offset:.3f}"]
        args += ["-t", f"{item['len']:.3f}", "-i", item["path"]]

    graph: list[str] = []
    parts: list[str] = []
    ducks: list[str] = []
    for n, item in enumerate(usable, start=1):
        props = item["layer"]["props"]
        length = item["len"]
        chain = [f"[{n}:a]aresample=48000", "asetpts=PTS-STARTPTS", f"volume={float(props.get('volume', 1)):.3f}"]
        fade_in = min(float(props.get("fadeIn") or 0), length)
        if fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={fade_in:.3f}")
        fade_out = min(float(props.get("fadeOut") or 0), length)
        if fade_out > 0:
            chain.append(f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.3f}")
        delay = int(round(item["start"] * 1000))
        chain.append(f"adelay=delays={delay}:all=1")
        graph.append(",".join(chain) + f"[a{n}]")
        parts.append(f"[a{n}]")
        if props.get("duck"):
            ducks.append(f"between(t,{item['start']:.3f},{item['end']:.3f})")

    if clip_has_audio:
        base = "[0:a]aresample=48000"
        if ducks:
            base += f",volume=enable='{'+'.join(ducks)}':volume={AUDIO_DUCK_VOLUME}"
        graph.append(base + "[a0]")
        parts.insert(0, "[a0]")
        mix_duration = "first"
    else:
        mix_duration = "longest"
    # Без своего звука у клипа дорожка звуков короче видео — дотягиваем тишиной.
    # Звуки ложатся поверх речи и легко перегоняют 0 dBFS — площадки потом хрипят.
    # Лимитер держит пик около −1 dBFS, тише он ничего не делает.
    tail = ",alimiter=limit=0.89:attack=5:release=60:level=disabled" + ("" if clip_has_audio else ",apad")
    graph.append(
        "".join(parts)
        + f"amix=inputs={len(parts)}:duration={mix_duration}:normalize=0:dropout_transition=0{tail}[aout]"
    )
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{float(duration):.3f}", "-movflags", "+faststart", str(output_path),
    ]
    return args


# ---------------------------------------------------------------- кадр для агента/превью


def _focus_x_at(focus: list, local: float) -> float | None:
    """Где центр кадра в момент ``local`` куска: линейно между точками, ступенькой на cut."""
    points = []
    for point in focus or []:
        try:
            points.append((float(point["t"]), float(point["x"]), bool(point.get("cut"))))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return None
    points.sort()
    if local <= points[0][0]:
        return points[0][1]
    for (t0, x0, _c0), (t1, x1, cut1) in zip(points, points[1:]):
        if t0 <= local < t1:
            if cut1 or t1 - t0 <= 1e-6:
                return x0
            return x0 + (x1 - x0) * (local - t0) / (t1 - t0)
    return points[-1][1]


def render_frame_png(
    store: Any,
    clip_plan_id: int,
    t: float,
    layers: Any = None,
    camera: Any = None,
) -> bytes:
    """PNG кадра клипа в момент ``t`` с камерой и слоями — для ИИ-агента и превью.

    Основа берётся из исходника (тот же кроп/рефрейм/лук, что в рендере, без
    баннера и субтитров), а не из готового рендера: в рендере слои и камера уже
    запечены, и кадр показал бы их дважды. Готовый рендер — запасной вариант,
    если файла исходника нет. ``layers``/``camera`` — несохранённый вариант из
    редактора; не переданы — берутся сохранённые в клипе.
    """
    from app import render as _render
    from app.clip_settings import normalize_render_settings
    from app.cover import build_frame_args
    from app.motion import clip_timeline, normalize_camera, normalize_layers
    from app.settings import settings

    command = compositor_command()  # нет компоновщика — сразу понятная ошибка
    plan = store.get_clip_plan(clip_plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    layers = rs["layers"] if layers is None else normalize_layers(layers)
    camera = rs["camera"] if camera is None else normalize_camera(camera)
    layers = [layer for layer in layers if not layer["hidden"] and layer["type"] != "audio"]
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("в клипе нет кусков")
    starts, total = clip_timeline([(float(s["start_sec"]), float(s["end_sec"])) for s in segments], rs["transition"])
    t = min(max(0.0, float(t)), max(0.0, total - 0.02))
    index = max(i for i, start in enumerate(starts) if start <= t + 1e-9)
    segment = segments[index]
    local = t - starts[index]

    service = _render.ClipRenderService(store)
    preset = service._preset(rs["preset_id"])
    preset = service._preset_with_render_overrides(preset, mirror=rs["mirror"], layout=rs["layout"])
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    fps = float(preset.get("fps") or 30)

    work = settings.tmp_dir / f"motion-frame-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        base_png = work / "base.png"
        source = store.get_source(int(segment.get("source_id") or plan["source_id"]))
        source_path = Path(source.get("local_path") or "")
        if source_path.is_file():
            framed = dict(segment)
            x = _focus_x_at(segment.get("focus") or [], local)
            if x is not None:
                framed["focus"] = [{"t": 0.0, "x": x}]
            reframe = _render._segment_reframe_x(framed, preset, source)
            graph = _render._render_filter(width, height, fps, preset, None, source.get("content_crop"), reframe)
            _render._run_ffmpeg(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{float(segment['start_sec']) + local:.3f}", "-i", str(source_path),
                    "-filter_complex", graph, "-map", "[vout]", "-frames:v", "1", str(base_png),
                ],
                timeout=120,
            )
        else:
            rendered = [
                c for c in store.list_clips_for_plan(clip_plan_id)
                if c.get("status") == "succeeded" and c.get("output_path") and Path(c["output_path"]).is_file()
            ]
            if not rendered:
                raise ValueError("нет ни файла исходника, ни готового рендера — кадр брать неоткуда")
            _render._run_ffmpeg(build_frame_args(Path(rendered[0]["output_path"]), t, base_png), timeout=120)
        # Компоновщик читает видео и ищет кадр по времени — даём «видео» из одного
        # стоп-кадра длиной до t (2 кадра в секунду: дёшево даже для t = 60 с).
        still = work / "still.mp4"
        _render._run_ffmpeg(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-loop", "1", "-framerate", "2", "-i", str(base_png), "-t", f"{t + 1.0:.3f}",
                "-vf", f"scale={width}:{height}", "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "stillimage", "-pix_fmt", "yuv420p", str(still),
            ],
            timeout=120,
        )
        layers, assets, _notes = resolve_layer_assets(store, layers)
        words = plan_caption_words(store, plan) if has_captions(layers) else None
        host = attach_chat(store, plan, {"layers": layers, "camera": camera}) or {}
        assets.update(host.get("chat_assets") or {})
        comp = write_comp(
            work / "comp.json", width=width, height=height, fps=fps,
            duration=max(total, t + 0.5), layers=layers, camera=camera, words=words, chat=host.get("chat"),
        )
        assets_path = work / "assets.json"
        assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
        out = work / "frame.png"
        _render._run_compositor(
            build_compositor_args(comp, assets_path, input_path=still, frame=t, png_path=out, command=command),
            timeout=180,
        )
        if not out.is_file():
            raise RuntimeError("компоновщик не записал кадр")
        return out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def contact_sheet_jpeg(store: Any, clip_plan_id: int, n: int = 8, width: int = 240) -> bytes:
    """Лист кадров клипа: ``n`` моментов равномерно по клипу, с подписью времени.

    Весь монтаж одним взглядом — агенту (одна картинка вместо восьми запросов
    кадра) и человеку на проверке. Кадры — тем же компоновщиком, что и рендер.
    """
    import subprocess
    import tempfile

    from app.settings import settings as app_settings

    n = max(2, min(16, int(n)))
    width = max(120, min(540, int(width)))
    plan = store.get_clip_plan(clip_plan_id)
    duration = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in plan.get("segments") or [])
    if duration <= 0:
        raise ValueError("у клипа нет кусков")
    times = [round(duration * (k + 0.5) / n, 2) for k in range(n)]
    font = (Path(app_settings.motion_fonts_dir) / "Inter-SemiBold.ttf").as_posix().replace(":", "\\:")
    with tempfile.TemporaryDirectory() as tmp:
        inputs: list[str] = []
        for k, t in enumerate(times):
            path = Path(tmp) / f"f{k:02d}.png"
            path.write_bytes(render_frame_png(store, clip_plan_id, t))
            inputs += ["-i", str(path)]
        cols = min(n, 4)
        chains = [
            f"[{k}:v]scale={width}:-2,drawtext=fontfile='{font}':text='{times[k]:.1f} s':x=8:y=8:fontsize={max(14, width // 12)}"
            f":fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4[v{k}]"
            for k in range(n)
        ]
        # xstack: координаты плиток суммой ширин/высот первой плитки.
        layout = "|".join(f"{'+'.join(['w0'] * (k % cols)) or '0'}_{'+'.join(['h0'] * (k // cols)) or '0'}" for k in range(n))
        graph = ";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=black[out]"
        out = Path(tmp) / "sheet.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-q:v", "4", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"лист кадров не собран: {proc.stderr[-300:]}")
        return out.read_bytes()
