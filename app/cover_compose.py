"""Обложка с заголовком: кадр клипа + крупный титр (+ эмодзи) тем же движком слоёв.

Голый кадр на обложке теряется в ленте; кликают на обложку с короткой крупной
надписью. Собираем её из кадра готового вида (кроп, раскладка, лук — как в
рендере) и титра в верхней трети, сохраняем как загруженную обложку
(1080×1920 JPEG в /data/covers) и включаем её в клипе — с «вшиванием» в первые
кадры, чтобы площадки без загрузки обложки показали её тоже.
"""

from __future__ import annotations

import subprocess
from typing import Any
from uuid import uuid4

from app.motion import normalize_layers
from app.settings import settings

STYLES = {
    "yellow": {"color": "#ffd600", "gradient": None, "stroke": {"color": "#000000", "width": 16}},
    "fire": {"color": "#ffffff", "gradient": ["#ffd600", "#ff3b30"], "stroke": {"color": "#000000", "width": 16}},
    "white": {"color": "#ffffff", "gradient": None, "stroke": {"color": "#000000", "width": 16}},
    "plate": {"color": "#ffffff", "gradient": None, "stroke": None,
              "bg": {"color": "#7c5cff", "opacity": 1, "radius": 24, "padX": 36, "padY": 18, "skew": -8}},
}


def cover_layers(title: str, t: float, *, emoji: str = "", style: str = "yellow", y: float = 0.2) -> list[dict]:
    look = STYLES.get(style, STYLES["yellow"])
    props: dict[str, Any] = {
        "text": f"{title.strip().upper()} {emoji}".strip(),
        "font": "Montserrat", "weight": 900, "size": 132, "uppercase": True, "maxWidth": 960, "lineHeight": 1.05,
        "color": look["color"],
    }
    for key in ("gradient", "stroke", "bg"):
        if look.get(key) is not None:
            props[key] = look[key]
    return normalize_layers([{
        "id": "cover_title", "type": "text", "start": max(0.0, t - 1.0), "end": t + 1.0, "track": 7,
        "x": 0.5, "y": y, "rotation": -3, "in": {"type": "none"}, "out": {"type": "none"},
        "shadow": {"color": "#000000", "blur": 30, "x": 0, "y": 10, "opacity": 0.55},
        "props": props,
    }])


def compose_cover(
    store: Any, clip_plan_id: int, t: float, title: str, *, emoji: str = "", style: str = "yellow", y: float = 0.2
) -> dict:
    """Нарисовать обложку и включить её в клипе; вернуть {image, url, cover}."""
    from app.clip_history import snapshot
    from app.clip_settings import normalize_cover
    from app.motion_render import render_frame_png

    if not title.strip():
        raise ValueError("нужен текст заголовка")
    png = render_frame_png(store, clip_plan_id, t, layers=cover_layers(title, t, emoji=emoji, style=style, y=y), camera=[])
    covers = settings.data_dir / "covers"
    covers.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}.jpg"
    raw = settings.tmp_dir / f"cover-compose-{name}.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(png)
    out = covers / name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    finally:
        raw.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"обложка не сохранилась: {proc.stderr[-200:]}")
    plan = store.get_clip_plan(clip_plan_id)
    render_settings = dict(plan.get("render_settings") or {})
    snapshot(store, clip_plan_id, "обложка с заголовком")
    render_settings["cover"] = normalize_cover({"mode": "image", "image": name, "burn": True, "burn_sec": 0.1})
    store.set_clip_plan_render_settings(clip_plan_id, render_settings)
    return {"image": name, "url": f"/media/covers/{name}", "cover": render_settings["cover"]}
