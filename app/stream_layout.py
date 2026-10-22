"""Раскладки стрима для 9:16: вебка сверху + игра снизу и т.п.

Самый частый формат стрим-шортсов — «split»: лицо стримера (вебка) сверху,
геймплей снизу. Плюс «pip_cam»: обычный кадр 9:16 и вебка поверх окошком.

``render.layout`` клипа::

    {"mode": "single" | "split" | "pip_cam" | "meme",
     "cam": {"x", "y", "w", "h"},   # прямоугольник вебки в долях ИСХОДНИКА (весь кадр)
     "ratio": 0.38,                  # split: доля высоты кадра под вебку, 0.2..0.6
     "cam_pos": "top" | "bottom",    # split: где вебка
     "pip": {"x", "y", "w"},         # pip_cam: центр и ширина окошка в долях выхода
     "border": {"color": "#000000", "width": 0},
     "gap": 0,
     "video_y": 0.04,                 # meme: верх видео в долях высоты выхода
     "video_scale": 1.0,              # meme: ширина видео в ширинах кадра (>1 — крупнее, края срезаны)
     "bg": "#0b0d14"}                 # meme: фон под видео (место для картинок и титров)

«meme» — мем-формат: горизонтальное видео целиком во всю ширину сверху, ниже —
однотонный фон под скриншоты и титры (как «Борат × сайт»). Раньше агент собирал
это из split с «вебкой» на весь кадр и закрывал низ прямоугольником.

``gap`` и ``border.width`` — пиксели кадра 1080×1920; на другом размере
выхода масштабируются пропорционально высоте (превью считает так же, см.
frontend/src/pages/workspace/layout.ts). ``single`` — ровно прежний рендер.

Здесь только чистая геометрия и автопоиск вебки; граф ffmpeg собирает
``app.render._layout_filter``.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

LAYOUT_MODES = ("single", "split", "pip_cam", "meme")
CAM_POSITIONS = ("top", "bottom")
REFERENCE_HEIGHT = 1920

DEFAULT_CAM = {"x": 0.70, "y": 0.02, "w": 0.28, "h": 0.30}
DEFAULT_PIP = {"x": 0.5, "y": 0.12, "w": 0.42}
DEFAULT_RATIO = 0.38
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(value: object, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(hi, max(lo, number))


def normalize_cam(raw: object, default: dict | None = DEFAULT_CAM) -> dict | None:
    """Прямоугольник вебки в долях кадра исходника, целиком внутри кадра."""
    if not isinstance(raw, dict):
        return dict(default) if default else None
    base = default or DEFAULT_CAM
    w = _num(raw.get("w"), base["w"], 0.03, 1.0)
    h = _num(raw.get("h"), base["h"], 0.03, 1.0)
    x = _num(raw.get("x"), base["x"], 0.0, 1.0 - w)
    y = _num(raw.get("y"), base["y"], 0.0, 1.0 - h)
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def normalize_layout(raw: object) -> dict:
    """Привести что угодно к полной форме раскладки (по умолчанию — single)."""
    src = raw if isinstance(raw, dict) else {}
    mode = src.get("mode") if src.get("mode") in LAYOUT_MODES else "single"
    pip_raw = src.get("pip") if isinstance(src.get("pip"), dict) else {}
    border_raw = src.get("border") if isinstance(src.get("border"), dict) else {}
    color = border_raw.get("color")
    return {
        "mode": mode,
        "cam": normalize_cam(src.get("cam")),
        "ratio": round(_num(src.get("ratio"), DEFAULT_RATIO, 0.2, 0.6), 4),
        "cam_pos": src.get("cam_pos") if src.get("cam_pos") in CAM_POSITIONS else "top",
        "pip": {
            "x": round(_num(pip_raw.get("x"), DEFAULT_PIP["x"], 0.0, 1.0), 4),
            "y": round(_num(pip_raw.get("y"), DEFAULT_PIP["y"], 0.0, 1.0), 4),
            "w": round(_num(pip_raw.get("w"), DEFAULT_PIP["w"], 0.1, 1.0), 4),
        },
        "border": {
            "color": color.lower() if isinstance(color, str) and _COLOR_RE.match(color) else "#000000",
            "width": int(round(_num(border_raw.get("width"), 0, 0, 40))),
        },
        "gap": int(round(_num(src.get("gap"), 0, 0, 200))),
        "video_y": round(_num(src.get("video_y"), 0.04, 0.0, 0.6), 4),
        "video_scale": round(_num(src.get("video_scale"), 1.0, 1.0, 4.0), 4),
        "bg": src["bg"].lower() if isinstance(src.get("bg"), str) and _COLOR_RE.match(src["bg"]) else "#0b0d14",
    }


def layout_active(layout: object) -> bool:
    """Раскладка что-то меняет (не single)? Только тогда рендер идёт по новому пути."""
    return isinstance(layout, dict) and layout.get("mode") in ("split", "pip_cam", "meme")


def even_px(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def scaled_px(value: float, height: int) -> int:
    """Пиксели кадра 1080×1920 → пиксели выхода высотой ``height`` (чётные; 0 остаётся 0)."""
    if value <= 0:
        return 0
    return even_px(value * height / REFERENCE_HEIGHT)


def split_geometry(layout: dict, height: int) -> tuple[int, int, int]:
    """(высота вебки, высота зазора, высота геймплея) в пикселях выхода; сумма = height."""
    cam_h = even_px(float(layout["ratio"]) * height)
    gap = scaled_px(float(layout.get("gap") or 0), height)
    gap = min(gap, max(0, height - cam_h - 2))
    return cam_h, gap, height - cam_h - gap


def gameplay_height(layout: object, width: int, height: int) -> int:
    """Высота окна геймплея: в split она меньше кадра, иначе — весь кадр."""
    if isinstance(layout, dict) and layout.get("mode") == "split":
        return split_geometry(layout, height)[2]
    return height


def ff_color(color: str) -> str:
    """``#rrggbb`` → ``0xrrggbb`` (решётка в графе фильтров лишний риск)."""
    return "0x" + (color[1:] if _COLOR_RE.match(color or "") else "000000")


# --------------------------------------------------------------------------- автопоиск вебки

_FACECAM_SAMPLES = 20
_SAMPLE_W = 640
# Лицо в вебке: не крошка (≥2% ширины кадра) и не весь кадр (≤30%).
_MIN_FACE_FRAC = 0.02
_MAX_FACE_FRAC = 0.30
# Центры лиц из разных кадров ближе этого (в долях кадра) — одно место.
_CLUSTER_DIST = 0.06


def _sample_frames(local_path: str, duration: float, count: int, out_dir: Path) -> list[Path]:
    """``count`` кадров равномерно по видео (без самых краёв — заставки/титры)."""
    frames: list[Path] = []
    if duration <= 0:
        times = [0.0]
    else:
        lo, hi = duration * 0.04, duration * 0.96
        times = [lo + (hi - lo) * (i + 0.5) / count for i in range(count)]
    for i, t in enumerate(times):
        out = out_dir / f"fc_{i:03d}.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, t):.3f}", "-i", local_path,
                "-frames:v", "1", "-vf", f"scale={_SAMPLE_W}:-2", str(out),
            ],
            capture_output=True,
            timeout=60,
        )
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def facecam_from_faces(
    faces_per_frame: list[list[tuple[float, float, float, float]]],
    frame_aspect: float,
) -> dict:
    """Самое устойчивое лицо → прямоугольник вебки.

    ``faces_per_frame`` — для каждого кадра лица ``(x, y, w, h)`` в долях кадра.
    Лицо стримера в вебке стоит на одном месте весь стрим, а лица в игре/фильме
    гуляют — поэтому берём место, где лицо есть в наибольшем числе кадров. Вебку
    достраиваем вокруг лица как типичное окно 4:3 (лицо ≈ треть ширины), а если
    окно почти касается края кадра — прижимаем к краю: оверлеи так и ставят.
    ``frame_aspect`` — ширина/высота исходника (для пропорций 4:3 в долях).
    """
    total = len(faces_per_frame)
    clusters: list[dict] = []
    for index, faces in enumerate(faces_per_frame):
        for (fx, fy, fw, fh) in faces:
            if not (_MIN_FACE_FRAC <= fw <= _MAX_FACE_FRAC):
                continue
            cx, cy = fx + fw / 2, fy + fh / 2
            for cluster in clusters:
                if abs(cluster["cx"] - cx) <= _CLUSTER_DIST and abs(cluster["cy"] - cy) <= _CLUSTER_DIST * frame_aspect:
                    cluster["boxes"].append((fx, fy, fw, fh))
                    cluster["frames"].add(index)
                    n = len(cluster["boxes"])
                    cluster["cx"] += (cx - cluster["cx"]) / n
                    cluster["cy"] += (cy - cluster["cy"]) / n
                    break
            else:
                clusters.append({"cx": cx, "cy": cy, "boxes": [(fx, fy, fw, fh)], "frames": {index}})
    if not clusters or total <= 0:
        return {"cam": None, "confidence": 0.0, "frames": total}

    def edge_score(c: dict) -> float:
        # При равной частоте выигрывает лицо ближе к краю/углу — там вебки и живут.
        return max(abs(c["cx"] - 0.5), abs(c["cy"] - 0.5))

    best = max(clusters, key=lambda c: (len(c["frames"]), edge_score(c)))
    support = len(best["frames"]) / total
    if len(best["frames"]) < max(2, round(total * 0.2)):
        return {"cam": None, "confidence": round(support, 3), "frames": total}

    fw = _median([b[2] for b in best["boxes"]])
    fh = _median([b[3] for b in best["boxes"]])
    cx = _median([b[0] + b[2] / 2 for b in best["boxes"]])
    cy = _median([b[1] + b[3] / 2 for b in best["boxes"]])
    # Окно 4:3 в пикселях: ширина ≈ 3.2 ширины лица; в долях высоты кадра
    # это w * aspect * 3/4.
    w = min(1.0, fw * 3.2)
    h = min(1.0, max(fh * 2.4, w * frame_aspect * 0.75))
    x = cx - w / 2
    y = cy - h * 0.45  # лицо чуть выше центра окна
    snap = 0.05
    if x < snap:
        x = 0.0
    if x + w > 1 - snap:
        x = 1.0 - w
    if y < snap:
        y = 0.0
    if y + h > 1 - snap:
        y = 1.0 - h
    cam = normalize_cam({"x": max(0.0, x), "y": max(0.0, y), "w": w, "h": h})
    # Уверенность: доля кадров с лицом на месте, чуть срезанная, если лицо дрожит.
    jitter = _median([abs(b[0] + b[2] / 2 - cx) for b in best["boxes"]]) / max(fw, 1e-3)
    confidence = support * (1.0 if jitter < 0.25 else 0.75)
    return {"cam": cam, "confidence": round(min(1.0, confidence), 3), "frames": total}


def detect_facecam(source: dict, samples: int = _FACECAM_SAMPLES) -> dict:
    """Найти вебку стримера: ``{"cam": {x,y,w,h} | None, "confidence": 0..1, "frames": n}``."""
    import cv2

    from app.autofocus import _detect_faces

    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("у источника нет файла видео")
    duration = float(source.get("duration_sec") or 0)
    with tempfile.TemporaryDirectory() as td:
        frames = _sample_frames(local, duration, samples, Path(td))
        if not frames:
            raise ValueError("ffmpeg не отдал ни одного кадра")
        faces_per_frame: list[list[tuple[float, float, float, float]]] = []
        aspect = 16 / 9
        for path in frames:
            img = cv2.imread(str(path))
            if img is None:
                continue
            h, w = img.shape[:2]
            aspect = w / h if h else aspect
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = _detect_faces(img, gray, 0.6)
            faces_per_frame.append([(fx / w, fy / h, fw / w, fh / h) for (fx, fy, fw, fh) in faces])
    return facecam_from_faces(faces_per_frame, aspect)
