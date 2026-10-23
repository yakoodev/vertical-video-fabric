"""Пустой холст: клип без исходного видео — анимированный фон, слои, музыка, звуки.

Промо из картинок упиралось в то, что клипу нужен исходник: агент генерировал
зелёное видео, грузил его источником и вырезал хромакеем. Теперь это делает сервис:
один скрытый источник ``canvas`` (как ``clip_upload`` — FK-якорь, в «Проектах» не
виден) — тихая зелёная основа 1080×1920 длиной в максимум куска, с ключом на её
цвет. Холст — обычный клип из одного куска ``[0, duration]`` этой основы, фон —
``render_settings.backdrop``. Рендер идёт обычным путём хромакея (app/chroma_key.py):
компоновщик вырезает зелень целиком и рисует фон, слои, звук — всё как у клипа.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

from app.settings import settings

CANVAS_SOURCE_TYPE = "canvas"
CANVAS_KEY_COLOR = "#00ff00"
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_FPS = 30
CANVAS_FILENAME = "canvas-base.mp4"
# Длина основы — максимум куска клипа (store.MAX_SEGMENT_DURATION_SEC): холст любой
# длины — кусок одной и той же основы, второй файл не нужен. Сплошной цвет сжимается
# в ~0.4 МБ на 3 минуты, ffmpeg пишет его за пару секунд.
MIN_DURATION_SEC = 5.0
DEFAULT_DURATION_SEC = 15.0

_LOCK = threading.Lock()


def base_seconds() -> float:
    from app.store import MAX_SEGMENT_DURATION_SEC

    return float(MAX_SEGMENT_DURATION_SEC)


def base_ffmpeg_args(path: Path, seconds: float) -> list[str]:
    """Команда ffmpeg основы: зелёный кадр (lavfi color) и тишина (anullsrc) нужной длины."""
    color = "0x" + CANVAS_KEY_COLOR[1:]
    return [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={CANVAS_FPS}:d={seconds:g}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{seconds:g}", "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-crf", "30", "-g", "300",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", "-movflags", "+faststart",
        str(path),
    ]


def _make_base(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.mp4")
    proc = subprocess.run(base_ffmpeg_args(tmp, seconds), capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise ValueError(f"основа холста не создана: {(proc.stderr or '').strip()[-300:]}")
    tmp.replace(path)


def _find_canvas_source(store: Any) -> dict | None:
    row = store.db.query_one(
        "SELECT id FROM sources WHERE source_type = ? ORDER BY id LIMIT 1", (CANVAS_SOURCE_TYPE,)
    )
    return store.get_source(int(row["id"])) if row else None


def ensure_canvas_source(store: Any) -> dict:
    """Скрытый источник холстов: создаёт при первом вызове, пропавший файл — пишет заново."""
    from app.ingest import probe_media

    seconds = base_seconds()
    with _LOCK:
        source = _find_canvas_source(store)
        path = Path(source["local_path"]) if source else settings.source_dir / CANVAS_FILENAME
        short = source is not None and float(source.get("duration_sec") or 0) < seconds - 0.05
        fresh = short or not path.is_file()
        if fresh:
            _make_base(path, seconds)
        if source is not None and not fresh and source.get("chroma_key"):
            return source
        meta = probe_media(path)
        fields = dict(
            size_bytes=path.stat().st_size, duration_sec=meta.duration_sec, width=meta.width,
            height=meta.height, fps=meta.fps,
        )
        if source is None:
            source = store.create_source(
                CANVAS_SOURCE_TYPE, path, original_filename="Холсты", metadata={"canvas": True},
                status="ready", **fields,
            )
        else:
            store.db.execute(
                "UPDATE sources SET size_bytes = ?, duration_sec = ?, width = ?, height = ?, fps = ? WHERE id = ?",
                (fields["size_bytes"], fields["duration_sec"], fields["width"], fields["height"], fields["fps"],
                 source["id"]),
            )
        # Ключ ровно на цвет основы: вырезается весь кадр, виден только фон клипа.
        return store.update_source(source["id"], chroma_key={"color": CANVAS_KEY_COLOR})


def is_canvas(source: dict | None) -> bool:
    return str((source or {}).get("source_type") or "") == CANVAS_SOURCE_TYPE


def normalize_duration(value: Any) -> float:
    if value is None or value == "":
        return DEFAULT_DURATION_SEC
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration — число секунд") from exc
    if duration != duration or not MIN_DURATION_SEC <= duration <= base_seconds():
        raise ValueError(f"duration — от {MIN_DURATION_SEC:g} до {base_seconds():g} с")
    return round(duration, 3)


def create_canvas_clip(
    store: Any, duration: Any, background: Any = None, title: str = "", description: str = ""
) -> dict:
    """Новый клип-холст: кусок ``[0, duration]`` основы и фон ``background``
    (имя стиля ``aurora``/``grid``/``bokeh``/``waves``/``gradient``/``solid`` или объект
    ``{kind, colors, speed, intensity}``; не задан — аврора в цветах клипа)."""
    from app.backgrounds import normalize_background
    from app.clip_settings import normalize_render_settings
    from app.manual_clips import create_manual_clip

    seconds = normalize_duration(duration)
    backdrop = normalize_background(background)
    source = ensure_canvas_source(store)
    title = " ".join(str(title or "").split())[:100] or "Холст"
    plan = create_manual_clip(store, source["id"], title, [{"start_sec": 0.0, "end_sec": seconds}],
                              str(description or ""))
    render = normalize_render_settings({**(plan.get("render_settings") or {}), "backdrop": backdrop})
    store.set_clip_plan_render_settings(plan["id"], render)
    return store.get_clip_plan(plan["id"])
