"""🎬 Пакет монтажа для Premiere и DaVinci Resolve: zip без рендера и без исходного видео.

В архиве: ``timeline/*.xml`` (FCP7 xmeml) и ``timeline/*.otio`` с дорожками на готовом таймлайне,
``subtitles/*.srt`` с таймингами после вырезов, текст слоёв (.srt и .csv, PNG с прозрачностью на
своей дорожке, если есть компоновщик), файлы картинок, мемов, звуков и музыки, ``README.md`` на русском,
``relink.py`` и ``motion_keys.csv``. Времена берутся из ``app/playback.py`` — того же кода, что у
рендера и предпросмотра.

Запись стрима весит 10+ ГБ и в архив не кладётся: монтаж ссылается на ``media/source/<имя файла>``.
С ``with_pieces`` в архив идут короткие куски 720p, и монтаж ссылается на них.

Что формат не держит, сказано в README пакета (``_readme``): это часть результата, а не сноска.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib.parse import quote
from xml.etree import ElementTree as ET

import opentimelineio as otio

from app.settings import settings

SAFE = re.compile(r"[^\w\-. ]+", re.UNICODE)
# Дорожка видео из слоёв: PNG текста на клип — не больше стольких (каждый — запуск компоновщика).
MAX_TEXT_PNG = 24
# Запас куска-прокси по краям: наплывы и подгонка в монтажке.
PIECE_HANDLE_SEC = 1.0
PIECE_HEIGHT = 720
TEXT_TYPES = ("text", "callout", "nameplate", "counter")


def _safe(text: Any, fallback: str = "clip") -> str:
    name = SAFE.sub("", str(text or "")).strip().replace(" ", "-")[:60]
    return name or fallback


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, float(t)) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _tc(t: float) -> str:
    return _srt_time(t).replace(",", ".")


def srt_text(rows: list[tuple[float, float, str]]) -> str:
    out = []
    for index, (a, b, text) in enumerate(sorted(rows, key=lambda r: r[0]), 1):
        out.append(f"{index}\n{_srt_time(a)} --> {_srt_time(max(b, a + 0.05))}\n{text.strip()}\n")
    return "\n".join(out)


def _layer_text(layer: dict) -> str:
    props = layer.get("props") or {}
    for key in ("text", "title", "label", "name", "handle"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# ---------------------------------------------------------------- модель пакета: дорожки

class Item(dict):
    """Клип на дорожке: start/end — секунды таймлайна, file — путь в архиве (или None)."""


def _level_keys(spans: list[tuple[float, float, float]], a: float, b: float) -> list[tuple[float, float]]:
    """Ключи громкости (секунда таймлайна, множитель) на отрезке [a, b] по отрезкам тишины и усиления."""
    points = {a, b}
    for s, e, _v in spans:
        for t in (s - 0.03, s, e, e + 0.03):
            if a < t < b:
                points.add(t)

    def at(t: float) -> float:
        value = 1.0
        for s, e, v in spans:
            if s <= t < e or (t == e and t == b):
                value *= v
        return value

    keys = [(t, at(t)) for t in sorted(points)]
    if all(abs(v - 1.0) < 1e-6 for _t, v in keys):
        return []
    return keys


def _camera_view(camera: list[dict], t: float) -> tuple[float, float, float]:
    from app.promo_lint import camera_view

    return camera_view(camera, t)


def _base_motion(plan: dict, part: dict, t: float, src_w: int, src_h: int) -> dict:
    """Масштаб (%) и центр кадра исходника в кадре таймлайна в момент ``t``: кроп, фокус и камера.

    Центр — смещение от центра кадра в долях его сторон (см. README: это надо проверить глазами)."""
    frame_w, frame_h = plan["frame"]["w"], plan["frame"]["h"]
    crop = plan["source"].get("content_crop") or {"x": 0, "y": 0, "w": 1, "h": 1}
    content_w, content_h = src_w * crop["w"], src_h * crop["h"]
    cover = max(frame_w / content_w, frame_h / content_h)
    window = frame_w / cover / content_w  # доля ширины контента в кадре
    fx = _focus_at(part, t)
    left = min(max(fx - window / 2, 0.0), max(0.0, 1 - window))
    center_x = crop["x"] + (left + window / 2) * crop["w"]  # центр окна, доля ширины исходника
    center_y = crop["y"] + crop["h"] / 2
    zoom, cam_x, cam_y = _camera_view(plan.get("camera") or [], t)
    scale = cover * zoom
    # Кадр: сначала окно исходника во весь кадр, потом камера вокруг своей точки.
    dx = (0.5 - center_x) * src_w * cover
    dy = (0.5 - center_y) * src_h * cover
    dx = dx * zoom + (0.5 - cam_x) * frame_w * zoom
    dy = dy * zoom + (0.5 - cam_y) * frame_h * zoom
    return {"t": t, "scale": round(scale * 100, 3), "dx": dx, "dy": dy,
            "horiz": round(dx / frame_w, 5), "vert": round(dy / frame_h, 5)}


def _focus_at(part: dict, t: float) -> float:
    points = sorted((p for p in part.get("focus") or [] if isinstance(p, dict) and "x" in p), key=lambda p: float(p.get("t", 0)))
    if not points:
        return 0.5
    local = (t - part["start"]) * (part.get("speed") or 1)
    value = float(points[0]["x"])
    for a, b in zip(points, points[1:]):
        ta, tb = float(a.get("t", 0)), float(b.get("t", 0))
        if ta <= local < tb:
            if b.get("cut"):
                return float(a["x"])
            return float(a["x"]) + (float(b["x"]) - float(a["x"])) * (local - ta) / max(tb - ta, 1e-3)
        if local >= tb:
            value = float(b["x"])
    return value


def _motion_keys(plan: dict, part: dict, src_w: int, src_h: int) -> list[dict]:
    """Ключи масштаба и центра куска: края, ключи камеры внутри и точки фокуса."""
    a, b = part["start"], part["end"]
    times = {a, b}
    for key in plan.get("camera") or []:
        k = float(key.get("t", 0))
        if a < k < b:
            times.add(k)
    for p in part.get("focus") or []:
        if isinstance(p, dict):
            k = a + float(p.get("t", 0)) / (part.get("speed") or 1)
            if a < k < b:
                times.add(k)
    keys = [_base_motion(plan, part, t, src_w, src_h) for t in sorted(times)]
    # Без движения — одно значение, а не пачка одинаковых ключей.
    if all(abs(k["scale"] - keys[0]["scale"]) < 1e-3 and abs(k["horiz"] - keys[0]["horiz"]) < 1e-5
           and abs(k["vert"] - keys[0]["vert"]) < 1e-5 for k in keys):
        return keys[:1]
    return keys


# ---------------------------------------------------------------- FCP7 xmeml


def _sub(parent: ET.Element, tag: str, text: Any = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent: ET.Element, fps: float) -> None:
    rate = _sub(parent, "rate")
    _sub(rate, "timebase", int(round(fps)))
    _sub(rate, "ntsc", "FALSE")


def _pathurl(rel: str) -> str:
    return quote(rel, safe="/")


class _Xml:
    """Сборка xmeml: файл описывается целиком при первом упоминании, дальше — по id."""

    def __init__(self, fps: float):
        self.fps = fps
        self.files: dict[str, str] = {}
        self.clip_no = 0

    def frames(self, t: float) -> int:
        return int(round(float(t) * self.fps))

    def file(self, parent: ET.Element, media: dict) -> None:
        rel = media["file"]
        if rel in self.files:
            _sub(parent, "file", id=self.files[rel])
            return
        file_id = f"file-{len(self.files) + 1}"
        self.files[rel] = file_id
        el = _sub(parent, "file", id=file_id)
        _sub(el, "name", Path(rel).name)
        _sub(el, "pathurl", _pathurl(rel))
        _rate(el, media.get("fps") or self.fps)
        _sub(el, "duration", int(round((media.get("duration") or 0) * (media.get("fps") or self.fps))))
        meta = _sub(el, "media")
        if media.get("width"):
            video = _sub(meta, "video")
            chars = _sub(video, "samplecharacteristics")
            _sub(chars, "width", media["width"])
            _sub(chars, "height", media["height"])
        if media.get("has_audio"):
            audio = _sub(meta, "audio")
            chars = _sub(audio, "samplecharacteristics")
            _sub(chars, "depth", 16)
            _sub(chars, "samplerate", 48000)
            _sub(audio, "channelcount", media.get("channels") or 2)

    def clipitem(self, track: ET.Element, item: dict, media: dict) -> ET.Element:
        self.clip_no += 1
        clip = _sub(track, "clipitem", id=f"clipitem-{self.clip_no}")
        _sub(clip, "name", item.get("name") or Path(media["file"]).name)
        _sub(clip, "enabled", "TRUE")
        duration = self.frames(media.get("duration") or (item["end"] - item["start"]))
        _sub(clip, "duration", duration)
        _rate(clip, self.fps)
        _sub(clip, "start", self.frames(item["start"]))
        _sub(clip, "end", self.frames(item["end"]))
        _sub(clip, "in", self.frames(item.get("in", 0.0)))
        _sub(clip, "out", self.frames(item.get("in", 0.0)) + self.frames(item.get("source_len", item["end"] - item["start"])))
        self.file(clip, media)
        return clip


def _param(parent: ET.Element, pid: str, name: str, lo: Any = None, hi: Any = None) -> ET.Element:
    p = _sub(parent, "parameter")
    _sub(p, "parameterid", pid)
    _sub(p, "name", name)
    if lo is not None:
        _sub(p, "valuemin", lo)
        _sub(p, "valuemax", hi)
    return p


def _motion_filter(clip: ET.Element, keys: list[dict], base_frame: int, frames) -> None:
    """Basic Motion: scale и center, у нескольких значений — keyframe (``when`` — кадр от начала клипа)."""
    flt = _sub(clip, "filter")
    effect = _sub(flt, "effect")
    _sub(effect, "name", "Basic Motion")
    _sub(effect, "effectid", "basic")
    _sub(effect, "effectcategory", "motion")
    _sub(effect, "effecttype", "motion")
    _sub(effect, "mediatype", "video")
    scale = _param(effect, "scale", "Scale", 0, 1000)
    _sub(scale, "value", keys[0]["scale"])
    center = _param(effect, "center", "Center")
    value = _sub(center, "value")
    _sub(value, "horiz", keys[0]["horiz"])
    _sub(value, "vert", keys[0]["vert"])
    if keys[0].get("rotation"):
        rotation = _param(effect, "rotation", "Rotation", -8640, 8640)
        _sub(rotation, "value", keys[0]["rotation"])
    if len(keys) > 1:
        for k in keys:
            kf = _sub(scale, "keyframe")
            _sub(kf, "when", frames(k["t"]) - base_frame)
            _sub(kf, "value", k["scale"])
            kf = _sub(center, "keyframe")
            _sub(kf, "when", frames(k["t"]) - base_frame)
            v = _sub(kf, "value")
            _sub(v, "horiz", k["horiz"])
            _sub(v, "vert", k["vert"])


def _speed_filter(clip: ET.Element, speed: float, reverse: bool) -> None:
    flt = _sub(clip, "filter")
    effect = _sub(flt, "effect")
    _sub(effect, "name", "Time Remap")
    _sub(effect, "effectid", "timeremap")
    _sub(effect, "effectcategory", "motion")
    _sub(effect, "effecttype", "motion")
    _sub(effect, "mediatype", "video")
    p = _param(effect, "variablespeed", "variablespeed", 0, 1)
    _sub(p, "value", 0)
    p = _param(effect, "speed", "speed", -100000, 100000)
    _sub(p, "value", round(speed * 100, 2))
    p = _param(effect, "reverse", "reverse")
    _sub(p, "value", "TRUE" if reverse else "FALSE")
    p = _param(effect, "frameblending", "frameblending")
    _sub(p, "value", "FALSE")


def _levels_filter(clip: ET.Element, keys: list[tuple[float, float]], volume: float, base_frame: int, frames) -> None:
    """Audio Levels: множитель 1 — 0 дБ (FCP7/Premiere понимают 0..3.98)."""
    if not keys and abs(volume - 1) < 1e-3:
        return
    flt = _sub(clip, "filter")
    effect = _sub(flt, "effect")
    _sub(effect, "name", "Audio Levels")
    _sub(effect, "effectid", "audiolevels")
    _sub(effect, "effectcategory", "audiolevels")
    _sub(effect, "effecttype", "audiolevels")
    _sub(effect, "mediatype", "audio")
    p = _param(effect, "level", "Level", 0, 3.98109)
    _sub(p, "value", round(min(3.98109, volume), 4))
    for t, v in keys:
        kf = _sub(p, "keyframe")
        _sub(kf, "when", frames(t) - base_frame)
        _sub(kf, "value", round(min(3.98109, v * volume), 4))


def build_xml(model: dict) -> bytes:
    fps = model["fps"]
    xml = _Xml(fps)
    root = ET.Element("xmeml", version="4")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "name", model["name"])
    _sub(seq, "duration", xml.frames(model["duration"]))
    _rate(seq, fps)
    tc = _sub(seq, "timecode")
    _rate(tc, fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")
    media = _sub(seq, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    chars = _sub(fmt, "samplecharacteristics")
    _rate(chars, fps)
    _sub(chars, "width", model["width"])
    _sub(chars, "height", model["height"])
    _sub(chars, "anamorphic", "FALSE")
    _sub(chars, "pixelaspectratio", "square")
    _sub(chars, "fielddominance", "none")
    for track_model in model["video_tracks"]:
        track = _sub(video, "track")
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")
        for item in track_model["items"]:
            if item.get("transition"):
                tr = _sub(track, "transitionitem")
                _sub(tr, "start", xml.frames(item["start"]))
                _sub(tr, "end", xml.frames(item["end"]))
                _sub(tr, "alignment", "center")
                effect = _sub(tr, "effect")
                _sub(effect, "name", "Cross Dissolve")
                _sub(effect, "effectid", "Cross Dissolve")
                _sub(effect, "effecttype", "transition")
                _sub(effect, "mediatype", "video")
                continue
            clip = xml.clipitem(track, item, model["media"][item["media"]])
            if item.get("motion"):
                _motion_filter(clip, item["motion"], xml.frames(item["start"]), xml.frames)
            if item.get("speed") and (abs(item["speed"] - 1) > 1e-3 or item.get("reverse")):
                _speed_filter(clip, item["speed"], bool(item.get("reverse")))
    audio = _sub(media, "audio")
    fmt = _sub(audio, "format")
    chars = _sub(fmt, "samplecharacteristics")
    _sub(chars, "depth", 16)
    _sub(chars, "samplerate", 48000)
    for track_model in model["audio_tracks"]:
        track = _sub(audio, "track")
        _sub(track, "enabled", "TRUE")
        _sub(track, "locked", "FALSE")
        for item in track_model["items"]:
            clip = xml.clipitem(track, item, model["media"][item["media"]])
            source_track = _sub(clip, "sourcetrack")
            _sub(source_track, "mediatype", "audio")
            _sub(source_track, "trackindex", 1)
            _levels_filter(clip, item.get("levels") or [], float(item.get("volume", 1.0)), xml.frames(item["start"]), xml.frames)
    markers = model.get("markers") or []
    for mark in markers:
        el = _sub(seq, "marker")
        _sub(el, "name", mark["name"][:60])
        _sub(el, "comment", mark.get("comment") or "")
        _sub(el, "in", xml.frames(mark["t"]))
        _sub(el, "out", -1)
    ET.indent(root, "  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n").encode("utf-8")


# ---------------------------------------------------------------- OTIO


def build_otio(model: dict) -> otio.schema.Timeline:
    fps = float(model["fps"])

    def rt(t: float) -> otio.opentime.RationalTime:
        return otio.opentime.RationalTime(round(float(t) * fps), fps)

    timeline = otio.schema.Timeline(name=model["name"])
    timeline.metadata["vvf"] = model["metadata"]

    def fill(track: otio.schema.Track, items: list[dict]) -> None:
        cursor = 0.0
        pending: dict | None = None
        for item in items:
            if item.get("transition"):
                pending = item
                continue
            if item["start"] > cursor + 1e-3:
                track.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(rt(0), rt(item["start"] - cursor))))
            media = model["media"][item["media"]]
            available = otio.opentime.TimeRange(rt(0), rt(media.get("duration") or (item["end"] - item["start"])))
            clip = otio.schema.Clip(
                name=item.get("name") or Path(media["file"]).name,
                media_reference=otio.schema.ExternalReference(target_url=_pathurl(media["file"]), available_range=available),
                source_range=otio.opentime.TimeRange(rt(item.get("in", 0.0)), rt(item["end"] - item["start"])),
            )
            clip.metadata["vvf"] = {k: item[k] for k in ("motion", "levels", "volume", "speed", "reverse", "kind", "layer_id") if k in item}
            if pending is not None and len(track):
                half = (pending["end"] - pending["start"]) / 2
                track.append(otio.schema.Transition(
                    name="Cross Dissolve",
                    transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
                    in_offset=rt(half),
                    out_offset=rt(half),
                ))
            pending = None
            track.append(clip)
            cursor = item["end"]

    for index, track_model in enumerate(model["video_tracks"]):
        track = otio.schema.Track(name=f"V{index + 1} {track_model['name']}", kind=otio.schema.TrackKind.Video)
        fill(track, track_model["items"])
        timeline.tracks.append(track)
    for index, track_model in enumerate(model["audio_tracks"]):
        track = otio.schema.Track(name=f"A{index + 1} {track_model['name']}", kind=otio.schema.TrackKind.Audio)
        fill(track, track_model["items"])
        timeline.tracks.append(track)
    for mark in model.get("markers") or []:
        if timeline.tracks and len(timeline.tracks[0]):
            marker = otio.schema.Marker(
                name=mark["name"][:60],
                marked_range=otio.opentime.TimeRange(rt(mark["t"]), rt(1 / fps)),
                color=otio.schema.MarkerColor.YELLOW,
            )
            marker.metadata["vvf"] = {"comment": mark.get("comment") or ""}
            timeline.tracks.markers.append(marker)
    return timeline


# ---------------------------------------------------------------- файлы


def _probe(path: Path) -> dict:
    from app.ingest import probe_media  # лёгкий ffprobe (тот же, что у рендера)

    try:
        meta = probe_media(path)
    except Exception:  # noqa: BLE001 - файл без метаданных всё равно кладём
        return {}
    streams = (meta.raw or {}).get("streams") or []
    return {
        "width": meta.width or 0,
        "height": meta.height or 0,
        "duration": meta.duration_sec or 0,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
    }


def _sniff_ext(path: Path) -> str:
    """Файлы библиотеки хранятся и без расширения, а монтажка выбирает импорт по нему."""
    try:
        head = path.read_bytes()[:16] if path.stat().st_size < 4096 else path.open("rb").read(16)
    except OSError:
        return ""
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    if head[4:8] == b"ftyp":
        return ".mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    return ""


class _Files:
    """Файлы архива: один файл — одна запись, имена без столкновений."""

    def __init__(self) -> None:
        self.media: dict[str, dict] = {}
        self.copies: list[tuple[Path, str]] = []
        self.by_path: dict[str, str] = {}

    def add(self, path: Path, folder: str, label: str = "") -> str:
        key = str(path)
        if key in self.by_path:
            return self.by_path[key]
        ext = path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", path.suffix or "") else _sniff_ext(path)
        stem = _safe(label or path.stem, "file")
        if stem.lower().endswith(ext.lower()):
            stem = stem[: -len(ext)] or "file"
        rel = f"media/{folder}/{stem}{ext}"
        n = 2
        while rel in self.media:
            rel = f"media/{folder}/{stem}-{n}{ext}"
            n += 1
        self.media[rel] = {"file": rel, **_probe(path)}
        self.copies.append((path, rel))
        self.by_path[key] = rel
        return rel


def _package_dir() -> Path:
    # runtime — том с видео (D:), как и прокси: архив с кусками большой.
    return settings.runtime_dir / "timeline-packages"


def _cut_piece(source_path: Path, start: float, end: float, out: Path) -> None:
    """Короткий кусок записи 720p с запасом по краям (монтажка получает настоящие кадры и звук)."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source_path),
         "-t", f"{end - start:.3f}", "-vf", f"scale=-2:'min({PIECE_HEIGHT},ih)'", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-pix_fmt", "yuv420p", "-g", "15", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
        check=True, capture_output=True, timeout=60 * 20,
    )


def _text_pngs(store: Any, plan: dict, layers: list[dict], work: Path, frame: dict) -> tuple[dict[str, Path], list[str]]:
    """PNG с прозрачностью для текстовых слоёв: тем же компоновщиком, что рендер; нет его — без PNG."""
    from app.formats import comp_ref
    from app.motion_render import build_compositor_args, compositor_command, resolve_layer_assets, write_comp

    out: dict[str, Path] = {}
    notes: list[str] = []
    if not layers:
        return out, notes
    try:
        compositor_command()
    except Exception as exc:  # noqa: BLE001 - компоновщик в среде необязателен
        return out, [f"PNG текста не собраны: нет компоновщика ({str(exc)[:80]})"]
    if len(layers) > MAX_TEXT_PNG:
        notes.append(f"PNG текста: первые {MAX_TEXT_PNG} слоёв из {len(layers)} (остальные — в text_layers.csv)")
    # Основа с альфой: кодек qtrle хранит прозрачность, компоновщик рисует слой поверх неё.
    base = work / "transparent.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c=black@0.0:s={frame['w']}x{frame['h']}:r=2:d=4,format=argb", "-c:v", "qtrle", str(base)],
        check=True, capture_output=True, timeout=60,
    )
    for layer in layers[:MAX_TEXT_PNG]:
        length = max(0.1, float(layer["end"]) - float(layer["start"]))
        at = min(1.0, length / 2)  # анимация входа кончилась, выхода ещё нет
        shifted = {**layer, "start": 0.0, "end": length}
        try:
            one = work / f"text-{_safe(layer.get('id'), 'x')}"
            one.mkdir(parents=True, exist_ok=True)
            kept, assets, _notes = resolve_layer_assets(store, [shifted])
            comp = write_comp(one / "comp.json", width=frame["w"], height=frame["h"], ref=comp_ref(plan["format"]),
                              fps=float(plan.get("fps") or 30), duration=length, layers=kept, camera=[])
            assets_path = one / "assets.json"
            assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
            png = one / "layer.png"
            subprocess.run(
                build_compositor_args(comp, assets_path, input_path=base, frame=at, png_path=png),
                check=True, capture_output=True, timeout=120,
            )
            if png.is_file():
                out[str(layer.get("id"))] = png
        except Exception as exc:  # noqa: BLE001 - один слой не стоит всего пакета
            notes.append(f"слой {layer.get('id')}: PNG не собран ({str(exc)[:100]})")
    return out, notes


# ---------------------------------------------------------------- сборка модели


def _layer_motion(layer: dict, natural: dict, plan: dict, sizes: dict) -> list[dict]:
    """Статичное положение слоя на дорожке (анимация слоя в этот формат не едет — см. README)."""
    from app.aspect import layer_size
    from app.formats import ref_size

    frame_w, frame_h = plan["frame"]["w"], plan["frame"]["h"]
    ref_w, _ref_h = ref_size(plan["format"])
    size = layer_size(layer, sizes)
    nat_w = natural.get("width") or 0
    scale = 100.0
    if size and nat_w:
        scale = size[0] * (frame_w / ref_w) / nat_w * 100 * float(layer.get("scale", 1) or 1)
    x, y = float(layer.get("x", 0.5)), float(layer.get("y", 0.5))
    return [{"t": float(layer["start"]), "scale": round(scale, 3), "horiz": round(x - 0.5, 5), "vert": round(y - 0.5, 5),
             "rotation": float(layer.get("rotation", 0) or 0), "dx": (x - 0.5) * frame_w, "dy": (y - 0.5) * frame_h}]


def build_model(store: Any, clip_plan_id: int, with_pieces: bool, work: Path) -> dict:
    """Всё содержимое пакета, кроме записи на диск."""
    from app.aspect import asset_sizes
    from app.motion import layer_asset_refs, asset_key
    from app.motion_render import resolve_layer_assets, resolve_pack_ref
    from app.playback import playback_plan

    plan = playback_plan(store, clip_plan_id)
    source = store.get_source(int(plan["source"]["id"]))
    files = _Files()
    notes: list[str] = []
    name = _safe(plan.get("title"), f"clip-{clip_plan_id}")
    fps = float(plan.get("fps") or 30)
    src_w, src_h = int(plan["source"]["width"] or 0), int(plan["source"]["height"] or 0)
    source_file = Path(str(source.get("local_path") or ""))
    has_record = source_file.is_file()
    original_name = _safe(Path(str(source.get("original_filename") or source_file.name or "record.mp4")).stem, "record") + (source_file.suffix or ".mp4")
    source_rel = f"media/source/{original_name}"
    canvas = bool(plan["source"].get("canvas"))

    # ---- основа: V1 и звук исходника
    piece_files: dict[int, tuple[str, float]] = {}
    pieces_note = ""
    if with_pieces:
        from app.preview_proxy import media_path

        cut_from = media_path(source)
        if cut_from is None:
            notes.append("куски-прокси не нарезаны: нет ни записи, ни прокси")
        else:
            pieces_dir = work / "pieces"
            pieces_dir.mkdir(parents=True, exist_ok=True)
            for part in plan["parts"]:
                start = max(0.0, part["src_start"] - PIECE_HANDLE_SEC)
                end = part["src_end"] + PIECE_HANDLE_SEC
                out = pieces_dir / f"piece-{part['index'] + 1:02d}.mp4"
                _cut_piece(cut_from, start, end, out)
                rel = f"media/pieces/{out.name}"
                files.media[rel] = {"file": rel, **_probe(out)}
                files.copies.append((out, rel))
                piece_files[part["index"]] = (rel, start)
            pieces_note = "В архиве куски записи 720p с запасом 1 с по краям (`media/pieces/`)."
    if not canvas and not piece_files:
        files.media[source_rel] = {
            "file": source_rel, "width": src_w, "height": src_h, "duration": float(plan["source"]["duration"] or 0),
            "fps": float(source.get("fps") or fps), "has_audio": True, "has_video": True,
        }
    if canvas:
        notes.append("клип на холсте: основы-видео нет, фон холста (анимированный) в монтажку не едет")

    mute_spans = [(float(m["start"]), float(m["end"]), float(m["value"])) for m in plan["audio"]["mutes"]]
    mute_spans += [(float(g["start"]), float(g["end"]), 10 ** (float(g["value"]) / 20)) for g in plan["audio"]["gains"]]
    v1: list[dict] = []
    a_source: list[dict] = []
    motion_rows: list[dict] = []
    parts = plan["parts"]
    for part in parts:
        index = part["index"]
        prev_overlap = parts[index - 1]["overlap"] if index else 0.0
        # На FCP-дорожке наплыв центрирован на склейке: кусок кончается в середине наплыва.
        start = part["start"] + prev_overlap / 2
        end = part["end"] - part["overlap"] / 2
        speed = float(part.get("speed") or 1)
        src_in = part["src_start"] + (prev_overlap / 2) * speed
        if piece_files.get(index):
            rel, piece_start = piece_files[index]
            media_in = src_in - piece_start
        elif canvas:
            continue
        else:
            rel, media_in = source_rel, src_in
        keys = _motion_keys(plan, part, src_w, src_h) if src_w and src_h else []
        for k in keys:
            motion_rows.append({"t": k["t"], "part": index + 1, "scale": k["scale"], "dx": round(k["dx"], 1), "dy": round(k["dy"], 1),
                                "horiz": k["horiz"], "vert": k["vert"]})
        item = {"media": rel, "start": start, "end": end, "in": media_in, "source_len": (end - start) * speed,
                "name": f"кусок {part['piece'] + 1}", "speed": speed, "reverse": bool(part.get("reverse")), "motion": keys, "kind": "piece"}
        v1.append(item)
        if part["overlap"] > 0 and index + 1 < len(parts):
            v1.append({"transition": True, "start": part["end"] - part["overlap"], "end": part["end"], "type": plan["transition"]["type"]})
        if not part.get("reverse") and part.get("speed_audio") != "mute":
            a_source.append({"media": rel, "start": start, "end": end, "in": media_in, "source_len": (end - start) * speed,
                             "name": f"кусок {part['piece'] + 1}", "levels": _level_keys(mute_spans, start, end), "kind": "piece"})

    video_tracks: list[dict] = [{"name": "Запись", "items": v1}] if v1 else []
    audio_tracks: list[dict] = [{"name": "Звук записи", "items": a_source}] if a_source else []

    # ---- слои-файлы и текст
    layers = [dict(l) for l in plan.get("layers") or [] if not l.get("hidden")]
    resolved, assets, missing = resolve_layer_assets(store, layers)
    notes.extend(missing)
    sizes = asset_sizes(resolved, assets)
    dropped: dict[str, int] = {}
    picture_items: list[dict] = []
    for layer in resolved:
        kind = layer.get("type")
        if kind in ("image", "video"):
            refs = layer_asset_refs(layer)
            path = assets.get(asset_key(refs[0])) if refs else None
            if not path:
                continue
            props = layer.get("props") or {}
            rel = files.add(Path(path), "images" if kind == "image" else "video", str(props.get("label") or Path(path).stem))
            natural = files.media[rel]
            in_point = float(props.get("from", 0) or 0) if kind == "video" else 0.0
            length = float(layer["end"]) - float(layer["start"])
            if kind == "image" and not natural.get("duration"):
                natural["duration"] = max(length, 10.0)
            picture_items.append({
                "media": rel, "start": float(layer["start"]), "end": float(layer["end"]), "in": in_point, "source_len": length,
                "name": f"{'мем' if kind == 'image' else 'видео'} {layer.get('id')}", "motion": _layer_motion(layer, natural, plan, sizes),
                "kind": kind, "layer_id": layer.get("id"),
            })
        elif kind not in TEXT_TYPES and kind != "audio":
            dropped[str(kind)] = dropped.get(str(kind), 0) + 1

    text_layers = [l for l in layers if l.get("type") in TEXT_TYPES and _layer_text(l)]
    pngs, png_notes = _text_pngs(store, plan, text_layers, work, plan["frame"])
    notes.extend(png_notes)
    text_items: list[dict] = []
    for layer in text_layers:
        png = pngs.get(str(layer.get("id")))
        if png is None:
            continue
        label = _layer_text(layer)[:24] or str(layer.get("id"))
        rel = files.add(png, "text", f"text-{label}")
        files.media[rel]["duration"] = max(float(layer["end"]) - float(layer["start"]), 10.0)
        text_items.append({
            "media": rel, "start": float(layer["start"]), "end": float(layer["end"]), "in": 0.0,
            "name": f"текст: {label}", "motion": [{"t": float(layer["start"]), "scale": 100.0, "horiz": 0.0, "vert": 0.0, "dx": 0.0, "dy": 0.0}],
            "kind": "text", "layer_id": layer.get("id"),
        })

    # ---- вставки поверх
    insert_items: list[dict] = []
    sound_items: list[dict] = []
    for ins in plan.get("inserts") or []:
        try:
            asset = store.get_montage_asset(int(ins["asset_id"]))
        except KeyError:
            notes.append(f"вставка {ins['asset_id']}: файла нет в библиотеке")
            continue
        path = Path(str(asset.get("file_path") or ""))
        if not path.is_file():
            notes.append(f"вставка {asset.get('label') or ins['asset_id']}: файла нет на диске")
            continue
        folder = {"audio": "sounds", "video": "video"}.get(asset.get("kind"), "images")
        rel = files.add(path, folder, asset.get("label") or path.stem)
        length = float(ins["duration"])
        if ins.get("mode") != "sound" and asset.get("kind") != "audio":
            natural = files.media[rel]
            if not natural.get("duration"):
                natural["duration"] = max(length, 10.0)
            nat_w, nat_h = natural.get("width") or 0, natural.get("height") or 0
            frame_w, frame_h = plan["frame"]["w"], plan["frame"]["h"]
            if ins.get("mode") == "full" and nat_w and nat_h:
                scale, hz, vt = min(frame_w / nat_w, frame_h / nat_h) * 100, 0.0, 0.0
            elif nat_w and nat_h:
                scale = min(frame_w * float(ins.get("scale") or 0.82) / nat_w, frame_h * 0.9 / nat_h) * 100
                hz, vt = float(ins.get("x") or 0.5) - 0.5, float(ins.get("y") or 0.32) - 0.5
            else:
                scale, hz, vt = 100.0, 0.0, 0.0
            insert_items.append({"media": rel, "start": float(ins["at"]), "end": float(ins["at"]) + length, "in": 0.0,
                                 "name": f"вставка: {asset.get('label') or rel}", "kind": "insert",
                                 "motion": [{"t": float(ins["at"]), "scale": round(scale, 3), "horiz": round(hz, 5), "vert": round(vt, 5), "dx": hz * frame_w, "dy": vt * frame_h}]})
        if ins.get("mode") == "sound" or asset.get("kind") == "audio" or asset.get("has_audio"):
            sound_items.append({"media": rel, "start": float(ins["at"]), "end": float(ins["at"]) + length, "in": 0.0,
                                "name": f"вставка: {asset.get('label') or rel}", "volume": float(ins.get("volume") or 1.0), "kind": "insert"})

    # Дорожки видео без наложений: слои, что идут одновременно, — на разные дорожки.
    def stack(items: list[dict], title: str) -> list[dict]:
        tracks: list[list[dict]] = []
        for item in sorted(items, key=lambda i: i["start"]):
            for track in tracks:
                if track[-1]["end"] <= item["start"] + 1e-3:
                    track.append(item)
                    break
            else:
                tracks.append([item])
        return [{"name": f"{title} {i + 1}" if len(tracks) > 1 else title, "items": t} for i, t in enumerate(tracks)]

    video_tracks += stack(picture_items, "Мемы и картинки") + stack(insert_items, "Вставки") + stack(text_items, "Текст")

    # ---- звуки
    sfx_items: list[dict] = []
    for s in plan["audio"].get("sfx") or []:
        probe = {"id": s["id"], "type": "audio", "props": {"asset_id": s["ref"]}}
        _kept, sfx_assets, _missing = resolve_layer_assets(store, [probe])
        path = next(iter(sfx_assets.values()), None)
        if not path:
            notes.append(f"звук {s['id']}: файл не найден")
            continue
        rel = files.add(Path(path), "sounds", Path(path).stem)
        length = float(s["end"]) - float(s["start"])
        dur = files.media[rel].get("duration") or length
        length = min(length, max(0.05, dur - float(s["offset"])))
        volume = float(s["volume"]) * (10 ** (float(s["gain_db"]) / 20) if s.get("track") else 1.0)
        sfx_items.append({"media": rel, "start": float(s["start"]), "end": float(s["start"]) + length, "in": float(s["offset"]),
                          "name": Path(path).stem, "volume": volume, "kind": "sfx", "layer_id": s["id"]})
    for j in plan["audio"].get("join_sfx") or []:
        path = resolve_pack_ref(f"sfx:{j['url'].split('/api/motion/packs/')[-1]}")
        if not path:
            notes.append(f"звук стыка {j['url']}: файл пака не найден")
            continue
        rel = files.add(Path(path), "sounds", Path(path).stem)
        dur = files.media[rel].get("duration") or 0.5
        sfx_items.append({"media": rel, "start": float(j["at"]), "end": float(j["at"]) + dur, "in": 0.0, "name": "звук стыка",
                          "volume": float(j["volume"]), "kind": "join_sfx"})
    audio_tracks += stack(sfx_items, "Звуки") + stack(sound_items, "Звук вставок")

    music = plan["audio"].get("music")
    if music:
        track = store.get_audio_track(int(music["track_id"]))
        path = Path(str(track.get("file_path") or ""))
        if path.is_file():
            rel = files.add(path, "music", music.get("label") or path.stem)
            dur = float(files.media[rel].get("duration") or 0)
            volume = 10 ** (float(music["fixed_db"]) / 20) if music.get("fixed_db") is not None else float(music["volume"])
            items: list[dict] = []
            cursor, offset = 0.0, float(music["start_sec"])
            total = float(plan["duration"])
            while cursor < total - 1e-3 and dur > 0 and len(items) < 100:
                length = min(total - cursor, dur - offset)
                if length <= 0.01:
                    break
                items.append({"media": rel, "start": cursor, "end": cursor + length, "in": offset, "name": music.get("label") or "музыка",
                              "volume": volume, "kind": "music"})
                cursor += length
                offset = 0.0
                if not music.get("loop"):
                    break
            if items:
                audio_tracks.append({"name": "Музыка", "items": items})
        else:
            notes.append("музыка: файла трека нет на диске")

    # ---- метки того, что на дорожках не показать
    markers: list[dict] = []
    for f in plan.get("freezes_raw") or []:
        markers.append({"t": float(f["at"]), "name": f"стоп-кадр {f['duration']} с", "comment": "В монтаже — руками: Frame Hold"})
    if plan.get("hold"):
        markers.append({"t": float(plan["duration"]) - float(plan["hold"]), "name": f"конечный стоп-кадр {plan['hold']} с", "comment": "В рендере ролик замирает на этом кадре"})

    return {
        "plan": plan,
        "source": source,
        "name": name,
        "fps": fps,
        "width": plan["frame"]["w"],
        "height": plan["frame"]["h"],
        "duration": float(plan["duration"]),
        "media": files.media,
        "copies": files.copies,
        "video_tracks": video_tracks,
        "audio_tracks": audio_tracks,
        "markers": markers,
        "motion_rows": motion_rows,
        "text_layers": text_layers,
        "dropped": dropped,
        "notes": notes,
        "source_rel": None if canvas or piece_files else source_rel,
        "has_record": has_record,
        "with_pieces": bool(piece_files),
        "pieces_note": pieces_note,
        "metadata": {
            "schema": "vvf.package/1",
            "clip_plan_id": clip_plan_id,
            "source_id": source["id"],
            "format": plan["format"],
            "transition": plan["transition"],
            "layout": plan["layout"],
            "revision": plan["revision"],
        },
    }


# ---------------------------------------------------------------- README и сопутствующее


RELINK_PY = '''#!/usr/bin/env python3
"""Сделать из timeline/*.xml версию с абсолютными путями: Premiere и Resolve тогда находят файлы сами.

    python relink.py                          файлы рядом с этим скриптом
    python relink.py "D:/записи/запись.mp4"   запись стрима лежит в другом месте

Только стандартная библиотека. Создаёт timeline/<имя>.absolute.xml, исходный файл не меняет.
"""
import pathlib
import re
import sys
from urllib.parse import quote, unquote

here = pathlib.Path(__file__).resolve().parent
record = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None


def absolute(match):
    rel = unquote(match.group(1))
    path = here / rel
    if record is not None and rel.startswith("media/source/"):
        path = record
    return "<pathurl>file://localhost/" + quote(path.as_posix().lstrip("/"), safe="/:") + "</pathurl>"


for xml in (here / "timeline").glob("*.xml"):
    if xml.name.endswith(".absolute.xml"):
        continue
    out = xml.with_name(xml.stem + ".absolute.xml")
    out.write_text(re.sub(r"<pathurl>(.*?)</pathurl>", absolute, xml.read_text(encoding="utf-8")), encoding="utf-8")
    print("готово:", out)
'''


def _readme(model: dict, counts: dict, public_link: str) -> str:
    plan = model["plan"]
    name = model["name"]
    layout = (plan.get("layout") or {}).get("mode") if isinstance(plan.get("layout"), dict) else None
    transition = plan["transition"]["type"]
    fx_list = sorted({str(f.get("fx")) for f in plan["audio"].get("fx") or []})
    lines = [
        f"# {plan.get('title') or name}: пакет монтажа",
        "",
        f"Кадр {model['width']}×{model['height']} ({plan['format']}), {model['fps']:g} к/с, длина {_tc(model['duration'])}.",
        "Собрано сервисом без рендера. Времена — как в готовом ролике: вырезы и наплывы уже учтены.",
        "",
        "## Как открыть",
        "",
    ]
    if model["with_pieces"]:
        lines += [model["pieces_note"], ""]
    elif model["source_rel"]:
        lines += [
            f"Запись стрима в архив не положена (размер). Положите её сюда: `{model['source_rel']}` — имя файла как здесь.",
            f"Где взять: {public_link}" if public_link else "",
            "",
        ]
    lines += [
        "1. Распакуйте архив целиком. Самый надёжный путь: `python relink.py` рядом с README сделает "
        f"`timeline/{name}.absolute.xml` с полными путями (запись в другом месте: `python relink.py \"D:/путь/запись.mp4\"`).",
        f"2. **Premiere Pro**: File → Import → `timeline/{name}.absolute.xml` (или `{name}.xml`, тогда Link Media и укажите файл).",
        f"3. **DaVinci Resolve**: File → Import → Timeline → выберите xml; включите «Automatically import source clips into media pool» "
        "и «Use sizing information». Не нашёл файлы: Reconform from Media Pool, предварительно импортировав папку `media`.",
        f"4. **Kdenlive и др.**: `timeline/{name}.otio` (пути относительно этого файла на уровень выше).",
        "",
        "## Что доехало",
        "",
        f"- Куски на дорожке V1 (штук: {counts['parts']}), с настоящими таймкодами записи, после вырезов.",
    ]
    if counts["transitions"]:
        lines.append(f"- Наплывы как Cross Dissolve той же длины (штук: {counts['transitions']}). Выбран переход «{transition}».")
    if counts["srt_lines"]:
        extra = "" if plan["subtitles"].get("on") else " В самом клипе прожиг субтитров выключен, файл — на случай, если понадобятся."
        lines.append(f"- Субтитры: `subtitles/{name}.srt` (строк: {counts['srt_lines']}), тайминги после вырезов.{extra}")
    if counts["pictures"]:
        lines.append(f"- Картинки и мемы на своих дорожках (клипов: {counts['pictures']}), файлы в `media/images` и `media/video`, положение и размер заданы (статично).")
    if counts["inserts"]:
        lines.append(f"- Вставки поверх записи на нужных таймкодах (клипов: {counts['inserts']}).")
    if counts["text_tracks"]:
        lines.append(f"- Текст: PNG с прозрачностью на дорожках «Текст» (штук: {counts['text_tracks']}), выглядит как в кадре. Менять текст — заново, данные в `text_layers.csv`.")
    if counts["text_layers"]:
        lines.append(f"- Текст слоёв списком: `subtitles/text_layers.srt` и `subtitles/text_layers.csv` (слоёв: {counts['text_layers']}; таймкоды, текст, положение, шрифт, цвет).")
    if counts["sfx"]:
        lines.append(f"- Звуки на дорожках «Звуки» (клипов: {counts['sfx']}), файлы в `media/sounds`, громкость выставлена.")
    if counts["music"]:
        lines.append("- Музыка: своя дорожка, начало с той же секунды трека, с повтором, если трек короче клипа. Файл в `media/music`.")
    if counts["level_keys"]:
        lines.append("- Тишина и громкость отрезков: ключи Audio Levels на дорожке звука записи.")
    lines += [
        f"- Кроп под формат и наезды камеры: Basic Motion (масштаб и центр, ключи там, где кадр движется). Те же числа по таймкодам: `motion_keys.csv`.",
        "",
        "## Что НЕ доехало (делается руками или остаётся за рендером)",
        "",
        "- Караоке-подсветка слов и стиль субтитров: в .srt только текст и время.",
        "- Анимации текста и слоёв (вход, выход, пульсации): на дорожке слой стоит статично.",
        "- Анимированные субтитры (слой captions), чат, PNGtuber (рот по голосу), частицы, эффекты кадра (fx), фигуры, графики: "
        + ("в этом клипе не перенесены слои: " + ", ".join(f"{k} ×{v}" for k, v in sorted(model["dropped"].items())) + "." if model["dropped"] else "в этом клипе таких слоёв нет."),
        "- Цвет: лук пресета и цветокоррекция кусков. Хромакей и анимированные фоны холста.",
    ]
    if layout and layout != "single":
        lines.append(f"- Раскладка кадра «{layout}» (вебка отдельным окном, split): на V1 лежит цельный кадр записи, раскладку собрать руками.")
    if transition not in ("cut", "fade", "dissolve"):
        lines.append(f"- Форма перехода «{transition}»: в файле Cross Dissolve, замените своим.")
    if plan.get("freezes_raw") or plan.get("hold"):
        lines.append("- Стоп-кадры: только метки на таймлайне, нужен Frame Hold.")
    if any(p.get("speed", 1) != 1 or p.get("reverse") for p in plan["parts"]):
        lines.append("- Скорость и реверс кусков: Time Remap записан, но у разных монтажек он читается по-разному, проверьте длины.")
    if fx_list:
        lines.append(f"- Эффекты звука на отрезках ({', '.join(fx_list)}), J/L-склейки, автоматика и ducking музыки: не переносятся.")
    else:
        lines.append("- J/L-склейки, автоматика и ducking музыки, сведение: не переносятся.")
    lines += [
        "- Итоговая громкость −14 LUFS: рендер выравнивает в конце, в монтажке выставьте сами.",
    ]
    if model["notes"]:
        lines += ["", "## Замечания сборки", ""] + [f"- {n}" for n in model["notes"]]
    lines += [
        "",
        "## Что проверено, а что нет",
        "",
        "Файлы проверены парсером OpenTimelineIO (и `.otio`, и адаптером `fcp_xml` для `.xml`): дорожки, клипы и длина читаются. "
        "Настоящего импорта в Premiere и Resolve при сборке не было.",
        "Не подтверждено на живой монтажке: **центр в Basic Motion**. Здесь смещение от центра кадра в долях его сторон, "
        "а описания формата расходятся. Если картинка уехала, но масштаб верный, сверьте с `motion_keys.csv`: `dx` и `dy` там в пикселях кадра. "
        "Для ключей `when` — кадр от начала клипа на таймлайне.",
    ]
    return "\n".join(line for line in lines if line is not None) + "\n"


# ---------------------------------------------------------------- пакет


def build_package(store: Any, clip_plan_id: int, with_pieces: bool = False) -> dict:
    """Собрать zip: ``{path, filename, url, size_bytes, files, summary, notes}``."""
    work = settings.tmp_dir / f"timeline-package-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        model = build_model(store, clip_plan_id, with_pieces, work)
        name = model["name"]
        plan = model["plan"]
        directory = _package_dir()
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "-pieces" if model["with_pieces"] else ""
        filename = f"{clip_plan_id}-{name}-package{suffix}.zip"
        target = directory / filename
        part = directory / f"{filename}.{uuid4().hex}.part"

        xml_bytes = build_xml(model)
        timeline = build_otio(model)
        otio_path = work / f"{name}.otio"
        otio.adapters.write_to_file(timeline, str(otio_path), adapter_name="otio_json")

        srt_rows = [(float(l["start"]), float(l["end"]), str(l.get("text") or "")) for l in plan["subtitles"]["lines"] if str(l.get("text") or "").strip()]
        text_rows = [(float(l["start"]), float(l["end"]), _layer_text(l)) for l in model["text_layers"]]
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["start", "end", "start_tc", "end_tc", "type", "text", "x", "y", "font", "size", "color", "id"])
        for l in sorted(model["text_layers"], key=lambda r: float(r["start"])):
            props = l.get("props") or {}
            writer.writerow([l["start"], l["end"], _tc(l["start"]), _tc(l["end"]), l.get("type"), _layer_text(l), l.get("x"), l.get("y"),
                             props.get("font") or props.get("fontFamily") or "", props.get("size") or "", props.get("color") or "", l.get("id")])
        motion_buf = io.StringIO()
        writer = csv.writer(motion_buf)
        writer.writerow(["t", "tc", "piece", "scale_pct", "dx_px", "dy_px", "center_horiz", "center_vert"])
        for row in model["motion_rows"]:
            writer.writerow([round(row["t"], 3), _tc(row["t"]), row["part"], row["scale"], row["dx"], row["dy"], row["horiz"], row["vert"]])

        source = model["source"]
        link_lines = []
        if source.get("original_url"):
            link_lines.append(str(source["original_url"]))
        link_lines.append(f"в сервисе: /media/sources/{source['id']} (нужен вход)")
        if plan["media"].get("proxy", {}).get("url"):
            link_lines.append(f"лёгкий прокси 480p: {plan['media']['proxy']['url']}")
        public_link = "; ".join(link_lines)

        video_items = [i for t in model["video_tracks"] for i in t["items"]]
        audio_items = [i for t in model["audio_tracks"] for i in t["items"]]
        counts = {
            "parts": sum(1 for i in video_items if i.get("kind") == "piece"),
            "transitions": sum(1 for i in video_items if i.get("transition")),
            "srt_lines": len(srt_rows),
            "pictures": sum(1 for i in video_items if i.get("kind") in ("image", "video")),
            "inserts": sum(1 for i in video_items if i.get("kind") == "insert"),
            "text_tracks": sum(1 for i in video_items if i.get("kind") == "text"),
            "text_layers": len(text_rows),
            "sfx": sum(1 for i in audio_items if i.get("kind") in ("sfx", "join_sfx", "insert")),
            "music": sum(1 for i in audio_items if i.get("kind") == "music"),
            "level_keys": sum(1 for i in audio_items if i.get("levels")),
            "motion_rows": len(model["motion_rows"]),
        }
        readme = _readme(model, counts, public_link)

        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.md", readme)
            z.writestr("relink.py", RELINK_PY)
            z.writestr(f"timeline/{name}.xml", xml_bytes)
            z.write(otio_path, f"timeline/{name}.otio")
            z.writestr("motion_keys.csv", motion_buf.getvalue().encode("utf-8-sig"))
            if srt_rows:
                z.writestr(f"subtitles/{name}.srt", srt_text(srt_rows))
            if text_rows:
                z.writestr("subtitles/text_layers.srt", srt_text(text_rows))
                z.writestr("subtitles/text_layers.csv", csv_buf.getvalue().encode("utf-8-sig"))
            if model["source_rel"]:
                z.writestr("media/source/ПОЛОЖИТЕ_ЗАПИСЬ_СЮДА.txt", f"Файл: {Path(model['source_rel']).name}\nГде взять: {public_link}\n")
            for src, rel in model["copies"]:
                # Видео и звук уже сжаты: не тратим процессор на второй раз.
                z.write(src, rel, compress_type=zipfile.ZIP_STORED)
        part.replace(target)
        return {
            "path": target,
            "filename": filename,
            "url": f"/api/clip-plans/{clip_plan_id}/timeline/package" + ("?pieces=true" if with_pieces else ""),
            "size_bytes": target.stat().st_size,
            "files": len(model["copies"]),
            "summary": counts,
            "notes": model["notes"],
            "tracks": {"video": [t["name"] for t in model["video_tracks"]], "audio": [t["name"] for t in model["audio_tracks"]]},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
