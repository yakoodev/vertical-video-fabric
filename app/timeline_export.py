"""Отдать клип в монтажку: клип-файл → OpenTimelineIO и его диалекты.

Наш ``vvf.clip/1`` описывает и монтаж, и рендер, а OTIO — общий язык монтажных
программ. Kdenlive 25.04+ открывает ``.otio`` нативно, DaVinci Resolve и Premiere
едят ``.edl`` (CMX 3600) и FCP7 ``.xml``, которые OTIO умеет писать адаптерами.

Чего в этих форматах нет: караоке-субтитры, мем-вставки, динамическое
кадрирование 9:16, наши переходы и музыка. Чтобы это не пропадало молча, всё
кладётся в ``metadata["vvf"]`` таймлайна — .otio остаётся полным, а обратный
импорт сможет вернуть настройки как были. В .edl и .xml метаданные не едут,
там остаются только куски: это осознанный размен на совместимость.

Пути к медиа абсолютные (``/data/sources/…``) — редактор в соседнем контейнере
монтирует тот же том, поэтому ссылки открываются без перепривязки.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import opentimelineio as otio

from app.clip_spec import export_spec
from app.settings import settings

# формат → (расширение, имя адаптера OTIO)
TIMELINE_FORMATS: dict[str, tuple[str, str]] = {
    "otio": (".otio", "otio_json"),
    "edl": (".edl", "cmx_3600"),
    "fcpxml": (".xml", "fcp_xml"),
}
DEFAULT_FPS = 30.0
SAFE_NAME = re.compile(r"[^\w\-. ]+", re.UNICODE)


def projects_dir() -> Path:
    """Куда кладём проекты, чтобы их увидел редактор в соседнем контейнере."""
    path = settings.data_dir / "projects"
    path.mkdir(parents=True, exist_ok=True)
    try:
        # Редактор в соседнем контейнере ходит под своим пользователем — ему
        # нужно не только прочитать проект, но и сохранить правки.
        path.chmod(0o777)
    except OSError:
        pass
    return path


def _safe_name(text: str, fallback: str) -> str:
    name = SAFE_NAME.sub("", str(text or "")).strip().replace(" ", "-")[:60]
    return name or fallback


def build_timeline(store: Any, clip_plan_id: int) -> otio.schema.Timeline:
    """Собрать OTIO-таймлайн из клип-файла (ничего не пишет на диск)."""
    spec = export_spec(store, clip_plan_id)
    source = store.get_source(spec["source"]["id"])
    fps = float(source.get("fps") or 0) or DEFAULT_FPS
    media_path = str(source.get("local_path") or "")
    source_duration = float(source.get("duration_sec") or 0)

    timeline = otio.schema.Timeline(name=spec["title"] or f"clip-{clip_plan_id}")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    available = otio.opentime.TimeRange(
        otio.opentime.RationalTime(0, fps),
        otio.opentime.RationalTime(round(source_duration * fps), fps),
    )
    for index, piece in enumerate(spec["pieces"]):
        start = float(piece["start_sec"])
        end = max(start + 1.0 / fps, float(piece["end_sec"]))
        clip = otio.schema.Clip(
            name=piece.get("title") or f"кусок {index + 1}",
            media_reference=otio.schema.ExternalReference(
                target_url=Path(media_path).as_uri() if media_path else "",
                available_range=available,
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(round(start * fps), fps),
                duration=otio.opentime.RationalTime(max(1, round((end - start) * fps)), fps),
            ),
        )
        clip.metadata["vvf"] = {"segment_id": piece.get("segment_id"), "focus": piece.get("focus") or []}
        track.append(clip)

    _add_insert_markers(track, spec, fps)
    timeline.metadata["vvf"] = {
        "schema": spec["schema"],
        "clip_plan_id": clip_plan_id,
        "source_id": source["id"],
        "title": spec["title"],
        "description": spec["description"],
        "notes": spec.get("notes") or "",
        "render": spec.get("render") or {},
        "subtitles": store.get_clip_plan_subtitles(clip_plan_id) or {},
    }
    return timeline


def _add_insert_markers(track: otio.schema.Track, spec: dict, fps: float) -> None:
    """Мем-вставки метками на таймлайне: в чужой монтажке их не видно иначе."""
    inserts = (spec.get("render") or {}).get("inserts") or []
    if not inserts:
        return
    bounds = []
    elapsed = 0.0
    for piece in spec["pieces"]:
        length = float(piece["end_sec"]) - float(piece["start_sec"])
        bounds.append((elapsed, elapsed + length))
        elapsed += length
    for insert in inserts:
        at = float(insert.get("at") or 0)
        for index, (left, right) in enumerate(bounds):
            if left <= at < right:
                clip = track[index]
                marker = otio.schema.Marker(
                    name=f"вставка: {insert.get('reason') or insert.get('asset_id')}",
                    marked_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(
                            clip.source_range.start_time.value + round((at - left) * fps), fps
                        ),
                        duration=otio.opentime.RationalTime(
                            max(1, round(float(insert.get("duration") or 1) * fps)), fps
                        ),
                    ),
                    color=otio.schema.MarkerColor.YELLOW,
                )
                marker.metadata["vvf"] = dict(insert)
                clip.markers.append(marker)
                break


def write_timeline(store: Any, clip_plan_id: int, fmt: str = "otio", out_dir: Path | None = None) -> Path:
    """Записать таймлайн в файл и вернуть путь."""
    if fmt not in TIMELINE_FORMATS:
        raise ValueError(f"неизвестный формат: {fmt}")
    suffix, adapter = TIMELINE_FORMATS[fmt]
    timeline = build_timeline(store, clip_plan_id)
    directory = out_dir or (settings.tmp_dir / "timelines")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{clip_plan_id}-{_safe_name(timeline.name, 'clip')}{suffix}"
    otio.adapters.write_to_file(timeline, str(path), adapter_name=adapter)
    return path


def save_project(store: Any, clip_plan_id: int) -> Path:
    """Положить .otio туда, где его откроет веб-редактор (общий том /data)."""
    return write_timeline(store, clip_plan_id, "otio", out_dir=projects_dir())


EDITOR_START_CMD = "docker compose --profile editor up -d editor"


def editor_is_up(timeout: float = 1.5) -> bool:
    """Поднят ли контейнер редактора.

    Он живёт под отдельным профилем и по умолчанию выключен — незачем держать
    GUI-контейнер в памяти ради кнопки. Проверяем перед тем, как открывать
    вкладку, иначе владелец упрётся в «сайт недоступен» и будет гадать.
    Самоподписанный сертификат и 401 basic-auth — признаки, что он как раз жив.
    """

    import httpx

    try:
        response = httpx.get(settings.editor_url, timeout=timeout, verify=False)  # noqa: S501
    except httpx.HTTPError:
        return False
    return response.status_code < 500
