"""Пакет монтажа: файлы форматов FCP7 xmeml и OTIO (из ``app/timeline_package.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import opentimelineio as otio

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
