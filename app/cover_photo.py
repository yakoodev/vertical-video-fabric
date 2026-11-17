"""Фоторедактор документа обложки: ролик документа, сильные кадры, полосы, интерфейс игры, файлы проекта.

Документ обложки (app/cover_docs.py) теперь не только превью клипа: фото проекта (``project_id``) и превью версии
«Ленты» (``feed_variant_id`` — кадры из её файла). Здесь серверная часть, которой нет у холста:

* **Ролик документа** (``doc_video``): готовый клип или файл версии ленты.
* **Полосы** (``video_bars``): чёрные поля леттербокса по нескольким кадрам ролика — кадр в документ берётся без них.
* **Сильные кадры** (``frame_candidates``): ~48 кадров ролика, оценка без ИИ — резкость (размытые склейки и
  переходы — штраф относительно медианы ролика), контраст, цветность, лицо (YuNet) и крупный объект (YOLOX);
  лучшие с разносом по времени, маленькими JPEG в папке документа.
* **Интерфейс игры** (``hud_boxes``): HUD стоит на месте, а мир движется. По ~40 кадрам ролика — частота края в
  каждом пикселе; у текстур игры она тоже высокая, поэтому интерфейс — где край бывает заметно чаще, чем рядом (рамка
  миникарты, панели отряда, лента убийств), плюс яркие строки текста выбранного кадра на таких местах (плашки
  «НОКАУТ +150»). Ответ — прямоугольники (доли кадра), их видно и можно поправить в редакторе.
* **Убрать** (``clean_frame``): прямоугольники (свои и/или найденные) закрашиваются ``cv2.inpaint`` (TELEA) —
  результат новым файлом документа, исходный кадр не трогается.
* **В файлы проекта** (``save_to_project``): готовая картинка (JPEG < 2 МБ — лимит YouTube) в «Файлы для монтажа»
  проекта с пометкой, что это превью, а не вставка в клип; документ остаётся — правка по той же ссылке.

Про каждый файл кадра документ помнит, из какого ролика, секунды и области он взят (``meta.json`` в папке
документа): так поиск интерфейса знает, какие кадры ролика сравнивать.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

_log = logging.getLogger(__name__)

CAND_SAMPLES = 48
CAND_COUNT = 12
CAND_WIDTH = 320
HUD_SAMPLES = 40
HUD_WIDTH = 480
BAR_SAMPLES = 8
BAR_LEVEL = 24
YOUTUBE_MAX_BYTES = 2 * 1024 * 1024
READ_WORKERS = 4
_meta_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Ролик документа
# --------------------------------------------------------------------------- #


def feed_variant(store: Any, variant_id: int) -> dict:
    """Вариант «Ленты» с проектом и названием работы и полным файлом (``path``)."""
    from app import feed as FEED

    variant = FEED._variant_row(store, int(variant_id))
    version = FEED._version_row(store, int(variant["version_id"]))
    work = FEED._work_row(store, int(version["work_id"]))
    label = variant.get("label") or ""
    return {**variant, "project_id": work.get("project_id"), "work_title": f"{work['title']} {label}".strip(),
            "path": FEED.variant_media_path(store, variant)}


def feed_version_variant(store: Any, version_id: int) -> int:
    """Вариант версии ленты для превью: горизонтальный, если есть (16:9 — миниатюра YouTube), иначе первый с файлом."""
    from app import feed as FEED

    version = FEED.get_version(store, int(version_id))
    present = [v for v in version["variants"] if v["file_present"]]
    if not present:
        raise ValueError(f"у версии #{version_id} нет файлов на диске")
    wide = [v for v in present if (v.get("width") or 0) > (v.get("height") or 0)]
    return int((wide or present)[0]["id"])


def doc_project(store: Any, *, clip: dict | None = None, variant: dict | None = None, project_id: Any = None) -> int | None:
    """Проект документа: явный → проект клипа → проект работы ленты → нет."""
    if project_id not in (None, "", 0):
        row = store.db.query_one("SELECT id FROM projects WHERE id = ?", (int(project_id),))
        if not row:
            raise ValueError(f"проекта #{project_id} нет")
        return int(project_id)
    if clip and clip.get("clip_plan_id"):
        from app.asset_scope import clip_project

        try:
            found = clip_project(store, int(clip["clip_plan_id"]))
        except Exception:  # noqa: BLE001 - без проекта документ всё равно живёт
            found = None
        if found is not None:
            return int(found)
    if variant and variant.get("project_id") is not None:
        return int(variant["project_id"])
    return None


def doc_video(store: Any, doc: dict) -> tuple[dict, Path]:
    """(клип или его подобие с длиной и размером, файл ролика). Нет ролика — ValueError."""
    if doc.get("clip_id"):
        clip = store.get_clip(int(doc["clip_id"]))
        video = Path(clip.get("output_path") or "")
        if clip.get("status") != "succeeded" or not video.is_file():
            raise ValueError("файла готового клипа нет")
        return clip, video
    if doc.get("feed_variant_id"):
        try:
            variant = feed_variant(store, int(doc["feed_variant_id"]))
        except KeyError:
            raise ValueError("версии ленты больше нет") from None
        if variant.get("clip_id"):
            try:
                clip = store.get_clip(int(variant["clip_id"]))
                if clip.get("status") == "succeeded" and Path(clip.get("output_path") or "").is_file():
                    return clip, Path(clip["output_path"])
            except KeyError:
                pass
        path = variant.get("path")
        if not path:
            raise ValueError("файла версии ленты нет на диске")
        info = probe(Path(path))
        return {"duration_sec": variant.get("duration_sec") or info["duration"], "width": info["width"],
                "height": info["height"], "title": variant.get("work_title") or ""}, Path(path)
    raise ValueError("документ не привязан к ролику: кадры брать неоткуда — загрузите картинку")


def video_key(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]


def probe(path: Path) -> dict:
    return dict(_probe(str(path), video_key(path)))


@lru_cache(maxsize=64)
def _probe(path: str, _key: str) -> tuple:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height:format=duration", "-of", "json", path],
                          capture_output=True, text=True, timeout=60, check=False)
    data = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or 0)
    return (("width", int(stream.get("width") or 0)), ("height", int(stream.get("height") or 0)), ("duration", duration))


def read_frame(video: Path, t: float, width: int, crop: list[int] | None = None, size: tuple[int, int] | None = None):
    """Кадр ``t`` (BGR) шириной ``width``; ``crop`` [x, y, w, h] — сперва вырезать область (px ролика)."""
    import numpy as np

    info = probe(video)
    src_w, src_h = (crop[2], crop[3]) if crop else (info["width"], info["height"])
    if not src_w or not src_h:
        return None
    w, h = size or (int(width), max(2, round(int(width) * src_h / src_w / 2) * 2))
    chain = ([f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}"] if crop else []) + [f"scale={w}:{h}"]
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
            "-frames:v", "1", "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or len(proc.stdout) != w * h * 3:
        return None
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(h, w, 3)


def _read_many(video: Path, times: list[float], width: int, crop: list[int] | None = None,
               size: tuple[int, int] | None = None) -> list[tuple[float, Any]]:
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        frames = list(pool.map(lambda t: read_frame(video, t, width, crop, size), times))
    return [(t, img) for t, img in zip(times, frames) if img is not None]


def spread_times(duration: float, n: int, edge: float = 0.03) -> list[float]:
    d = max(0.0, float(duration or 0))
    if d < 1.0:
        return [round(d / 2, 3)]
    start, end = d * edge, d * (1 - edge)
    return [round(start + (end - start) * i / max(1, n - 1), 3) for i in range(n)]


# --------------------------------------------------------------------------- #
# Полосы леттербокса
# --------------------------------------------------------------------------- #


def bars_from_frames(frames: list[Any], level: int = BAR_LEVEL) -> tuple[int, int, int, int] | None:
    """Полезная область (x, y, w, h) в px этих кадров или None (полос нет). Строка/столбец — полоса, если во
    ВСЕХ кадрах в ней почти всё тёмное (95-й процентиль яркости ниже ``level``)."""
    import numpy as np

    if not frames:
        return None
    gray = np.stack([f.mean(axis=2) if f.ndim == 3 else f for f in frames]).astype(np.float32)
    h, w = gray.shape[1:]
    rows = np.percentile(gray, 95, axis=2).max(axis=0) < level
    cols = np.percentile(gray, 95, axis=1).max(axis=0) < level

    def run(mask: Any) -> int:
        n = 0
        for v in mask:
            if not v:
                break
            n += 1
        return n

    top, bottom, left, right = run(rows), run(rows[::-1]), run(cols), run(cols[::-1])
    if top + bottom >= h * 0.7 or left + right >= w * 0.7:
        return None  # тёмная сцена целиком — это не полосы
    keep = [v if v >= max(2, 0.01 * size) else 0 for v, size in ((top, h), (bottom, h), (left, w), (right, w))]
    top, bottom, left, right = keep
    if not any(keep):
        return None
    return left, top, w - left - right, h - top - bottom


def video_bars(video: Path) -> list[int] | None:
    """Область без чёрных полос в px ролика [x, y, w, h] (чётные) или None."""
    return _bars(str(video), video_key(video))


@lru_cache(maxsize=64)
def _bars(path: str, _key: str) -> list[int] | None:
    video = Path(path)
    info = probe(video)
    if not info["width"] or not info["height"]:
        return None
    width = 480
    frames = [img for _t, img in _read_many(video, spread_times(info["duration"], BAR_SAMPLES, 0.1), width)]
    found = bars_from_frames(frames)
    if not found:
        return None
    k = info["width"] / width
    x, y, w, h = found
    # С запасом в пару px внутрь: край полосы после масштаба размыт.
    x0, y0 = int(x * k) + 2, int(y * k) + 2
    x1, y1 = int((x + w) * k) - 2, int((y + h) * k) - 2
    x0, y0 = x0 + x0 % 2, y0 + y0 % 2
    w2, h2 = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    if w2 < info["width"] * 0.3 or h2 < info["height"] * 0.3:
        return None
    return [x0, y0, w2, h2]


# --------------------------------------------------------------------------- #
# Файлы документа: откуда взят кадр
# --------------------------------------------------------------------------- #


def _meta_path(doc_id: int) -> Path:
    from app.cover_docs import doc_dir

    return doc_dir(doc_id) / "meta.json"


def read_meta(doc_id: int) -> dict:
    try:
        data = json.loads(_meta_path(doc_id).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def note_asset(doc_id: int, name: str, info: dict) -> None:
    with _meta_lock:
        meta = read_meta(doc_id)
        meta[name] = info
        path = _meta_path(doc_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def add_frame(store: Any, doc_id: int, t: Any, origin: str = "clean", trim_bars: bool = True) -> dict:
    """Кадр ролика в файлы документа (app/cover_docs.add_frame) — без чёрных полос и с пометкой, откуда взят."""
    import cv2

    from app import cover_docs as D

    doc = D.get_doc(store, doc_id)
    made = D.add_frame(store, doc_id, t, origin)
    _clip, video = doc_video(store, doc)
    if origin == "source":
        video, _at = D._source_moment(store, _clip, float(made["t"]))
    info: dict = {"t": made["t"], "origin": origin, "video": str(video), "crop": None}
    bars = video_bars(video) if trim_bars else None
    path = D.doc_dir(doc_id) / made["name"]
    if bars:
        img = cv2.imread(str(path))
        vw, vh = probe(video)["width"], probe(video)["height"]
        if img is not None and vw and vh:
            kx, ky = img.shape[1] / vw, img.shape[0] / vh
            x, y, w, h = (int(round(bars[0] * kx)), int(round(bars[1] * ky)), int(round(bars[2] * kx)),
                          int(round(bars[3] * ky)))
            # Компоновщик (origin clean) мог дать кадр другой пропорции — тогда полосы не наши.
            if abs(img.shape[1] / img.shape[0] - vw / vh) < 0.02:
                cv2.imwrite(str(path), img[y:y + h, x:x + w], [cv2.IMWRITE_JPEG_QUALITY, 95])
                info["crop"] = bars
    note_asset(doc_id, made["name"], info)
    return {**D.describe_asset(doc_id, made["name"]), "t": made["t"], "origin": origin, "bars": bool(info["crop"])}


# --------------------------------------------------------------------------- #
# Сильные кадры
# --------------------------------------------------------------------------- #


def subject_score(img: Any) -> float:
    """Крупный уверенный объект (YOLOX; нет модели — 0): человек/зверь весомее."""
    try:
        from app.object_detect import _CLASS_BONUS, detect_objects
    except Exception:  # noqa: BLE001
        return 0.0
    best = 0.0
    for box in detect_objects(img):
        area = float(box["w"]) * float(box["h"])
        best = max(best, min(1.0, (area ** 0.5) * 1.6) * float(box["score"]) * min(1.5, _CLASS_BONUS.get(int(box["cls"]), 1.0)))
    return min(1.0, best)


def score_frames(frames: list[tuple[float, Any]]) -> list[dict]:
    """Оценка кадров для превью: метрики обложки (лицо, резкость, контраст, цвет) + объект; резкость — от
    медианы этого ролика: кадр склейки/перехода размыт сильнее соседей и получает штраф."""
    import numpy as np

    from app import cover_variants as V

    rows = [{"t": t, **V.frame_metrics(img), "subject": subject_score(img)} for t, img in frames]
    if not rows:
        return []
    median = float(np.median([r["sharp"] for r in rows])) or 1.0
    for row in rows:
        rel = row["sharp"] / median
        score = row["score"] + 0.25 * row["subject"] + 0.1 * min(1.5, rel)
        if rel < 0.45:
            score -= 0.35  # размытый переход или смаз
        row["score"] = round(score, 4)
        row["sharp_rel"] = round(rel, 3)
    return rows


def frame_candidates(store: Any, doc_id: int, count: int = CAND_COUNT, refresh: bool = False) -> list[dict]:
    """Лучшие кадры ролика документа маленькими JPEG: [{t, score, file, faces, subject}] (сильнее — раньше)."""
    import cv2

    from app import cover_docs as D
    from app import cover_variants as V
    from app.heavy_queue import heavy_slot

    doc = D.get_doc(store, doc_id)
    clip, video = doc_video(store, doc)
    key = video_key(video)
    folder = D.doc_dir(doc_id) / "cands"
    index = folder / "index.json"
    if not refresh and index.is_file():
        try:
            cached = json.loads(index.read_text(encoding="utf-8"))
            if cached.get("video") == key and all((folder / c["file"]).is_file() for c in cached["items"]):
                return cached["items"][:count]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    duration = float(clip.get("duration_sec") or 0) or probe(video)["duration"]
    bars = video_bars(video)
    with heavy_slot("cover_frames", f"кадры документа {doc_id}"):
        frames = _read_many(video, spread_times(duration, CAND_SAMPLES), CAND_WIDTH, bars)
        rows = score_frames(frames)
    if not rows:
        raise RuntimeError("не удалось взять кадры из ролика")
    picks = V.pick_moments(rows, max(1, min(24, int(count or CAND_COUNT))), duration)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    by_t = dict(frames)
    items = []
    for row in picks:
        name = f"{int(round(row['t'] * 1000))}.jpg"
        cv2.imwrite(str(folder / name), by_t[row["t"]], [cv2.IMWRITE_JPEG_QUALITY, 85])
        items.append({"t": row["t"], "score": row["score"], "file": name, "faces": len(row["faces"]),
                      "subject": round(row["subject"], 3), "sharp_rel": row["sharp_rel"]})
    index.write_text(json.dumps({"video": key, "items": items}, ensure_ascii=False), encoding="utf-8")
    return items


def candidate_path(doc_id: int, name: str) -> Path | None:
    from app.cover_docs import doc_dir

    if not isinstance(name, str) or not name.endswith(".jpg") or not name[:-4].isdigit():
        return None
    path = doc_dir(doc_id) / "cands" / name
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# Интерфейс игры
# --------------------------------------------------------------------------- #


def hud_stats(video: Path, crop: list[int] | None) -> dict:
    """Карта «интерфейса» ролика (кэш на диске): ``edge_freq`` — доля кадров с краем в пикселе, ``std`` — разброс
    яркости; размер — HUD_WIDTH по ширине области ``crop``."""
    import numpy as np

    from app.heavy_queue import heavy_slot

    tag = hashlib.sha1(json.dumps([video_key(video), crop]).encode()).hexdigest()[:16]
    cache = settings.tmp_dir / "cover_hud" / f"{tag}.npz"
    if cache.is_file():
        try:
            data = np.load(cache)
            return {"edge_freq": data["edge_freq"].astype(np.float32), "std": data["std"].astype(np.float32)}
        except (OSError, ValueError, KeyError):
            pass
    import cv2

    duration = probe(video)["duration"]
    with heavy_slot("cover_hud", video.name):
        frames = [img for _t, img in _read_many(video, spread_times(duration, HUD_SAMPLES), HUD_WIDTH, crop)]
    if len(frames) < 6:
        raise RuntimeError("мало кадров ролика, чтобы найти интерфейс")
    grays = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames])
    edges = np.stack([cv2.Canny(g, 60, 160) > 0 for g in grays])
    out = {"edge_freq": edges.mean(axis=0).astype(np.float32), "std": grays.astype(np.float32).std(axis=0)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, edge_freq=out["edge_freq"].astype(np.float16), std=out["std"].astype(np.float16))
    return out


def hud_mask(frame: Any, stats: dict) -> Any:
    """Маска интерфейса на этом кадре (uint8 0/255, размер stats).

    Края игрового мира тоже частые (текстуры), поэтому частота края берётся ОТНОСИТЕЛЬНО окрестности: интерфейс —
    там, где край бывает заметно чаще, чем рядом, и он есть на этом кадре, или где край стоит почти всегда (рамка
    миникарты). Такие пиксели густо — область интерфейса. Плюс яркие строки текста этого кадра (плашки «НОКАУТ»,
    лента), если на их месте края бывают чаще, чем вокруг."""
    import cv2
    import numpy as np

    freq = stats["edge_freq"]
    h, w = freq.shape
    img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    now = cv2.Canny(gray, 60, 160) > 0
    excess = freq - cv2.blur(freq, (41, 41))
    hud_px = ((now & (excess > 0.1)) | (freq > 0.5)).astype(np.float32)
    area = cv2.blur(hud_px, (9, 9)) > 0.1
    top = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)))
    text = cv2.morphologyEx((top > 50).astype(np.uint8), cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
    count, labels, comp, _cent = cv2.connectedComponentsWithStats(text, 8)
    lines = np.zeros((h, w), bool)
    for i in range(1, count):
        x, y, bw, bh, px = (int(v) for v in comp[i])
        if bw >= 2.5 * bh and 4 <= bh <= max(24, h // 9) and px >= 15 and excess[y:y + bh, x:x + bw].mean() > 0.02:
            lines |= labels == i
    mask = ((area | lines) * 255).astype(np.uint8)
    # Буквы и значки — в сплошные блоки: строка ленты, плашка, миникарта.
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 9)))


def mask_boxes(mask: Any, max_share: float = 0.2, limit: int = 16) -> list[dict]:
    """Прямоугольники маски (доли кадра), мелочь и почти весь кадр — прочь, пересекающиеся — слиты."""
    import cv2

    h, w = mask.shape
    count, _labels, stats, _cent = cv2.connectedComponentsWithStats((mask > 0).astype("uint8"), 8)
    boxes = []
    for i in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area < max(12, h * w * 0.0006) or bw * bh > h * w * max_share:
            continue
        pad = 3
        boxes.append([max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)])
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    boxes = [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) <= h * w * max_share]
    boxes.sort(key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    return [{"x": round(b[0] / w, 4), "y": round(b[1] / h, 4), "w": round((b[2] - b[0]) / w, 4),
             "h": round((b[3] - b[1]) / h, 4)} for b in boxes[:limit]]


def _asset_origin(doc_id: int, src: str) -> dict:
    meta = read_meta(doc_id).get(src)
    if not meta or not meta.get("video") or not Path(meta["video"]).is_file():
        raise ValueError("у этой картинки нет ролика: интерфейс ищется по видео — отметьте прямоугольники руками")
    return meta


def hud_boxes(store: Any, doc_id: int, src: str) -> list[dict]:
    """Где на кадре ``src`` (файл документа, взятый из ролика) интерфейс игры: прямоугольники в долях кадра."""
    import cv2

    from app import cover_docs as D

    path = D.asset_path(doc_id, src)
    if path is None:
        raise ValueError("нет такого файла в документе")
    meta = _asset_origin(doc_id, src)
    stats = hud_stats(Path(meta["video"]), meta.get("crop"))
    frame = cv2.imread(str(path))
    if frame is None:
        raise ValueError("картинка не читается")
    return mask_boxes(hud_mask(frame, stats))


def _rect_px(rect: Any, w: int, h: int) -> tuple[int, int, int, int] | None:
    if not isinstance(rect, dict):
        return None
    try:
        x, y, rw, rh = (float(rect.get(k, 0)) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        return None
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + rw) * w + 0.999)), min(h, int((y + rh) * h + 0.999))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def clean_frame(store: Any, doc_id: int, src: str, rects: list | None = None, auto: bool = False) -> dict:
    """Закрасить прямоугольники (доли кадра) на ``src`` (inpaint TELEA) → новый файл документа."""
    import cv2
    import numpy as np

    from app import cover_docs as D
    from app.heavy_queue import frame_slot

    path = D.asset_path(doc_id, src)
    if path is None:
        raise ValueError("нет такого файла в документе")
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError("картинка не читается")
    h, w = img.shape[:2]
    boxes = [r for r in (rects or []) if isinstance(r, dict)][:64]
    if auto:
        boxes += hud_boxes(store, doc_id, src)
    mask = np.zeros((h, w), np.uint8)
    for rect in boxes:
        px = _rect_px(rect, w, h)
        if px:
            mask[px[1]:px[3], px[0]:px[2]] = 255
    if not mask.any():
        raise ValueError("нечего убирать: нет прямоугольников")
    if mask.mean() / 255 > 0.6:
        raise ValueError("закрашивать больше 60 % кадра — не выйдет: возьмите другой кадр")
    D._check_room(doc_id)
    with frame_slot(f"чистка кадра {doc_id}"):
        out = cv2.inpaint(img, mask, max(5, round(min(w, h) / 100)), cv2.INPAINT_TELEA)
    name = f"{uuid4().hex}.jpg"
    cv2.imwrite(str(D.doc_dir(doc_id) / name), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    origin = read_meta(doc_id).get(src) or {}
    note_asset(doc_id, name, {**origin, "parent": src, "cleaned": boxes})
    return {**D.describe_asset(doc_id, name), "rects": boxes, "parent": src}


# --------------------------------------------------------------------------- #
# Готовая картинка: JPEG для YouTube, файлы проекта
# --------------------------------------------------------------------------- #


def export_limited(store: Any, doc_id: int, max_bytes: int = YOUTUBE_MAX_BYTES) -> Path:
    """JPEG документа не больше ``max_bytes`` (YouTube: 2 МБ): при нужде качество ниже, размер тот же."""
    import cv2

    from app import cover_docs as D

    jpg = D.export_jpeg(store, doc_id)
    if jpg.stat().st_size <= max_bytes:
        return jpg
    img = cv2.imread(str(D.render_doc(store, doc_id)))
    for quality in (90, 85, 80, 72, 64, 55):
        ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok and len(data) <= max_bytes:
            jpg.write_bytes(data.tobytes())
            return jpg
    raise RuntimeError("картинка не влезла в 2 МБ даже с низким качеством")


def save_to_project(store: Any, doc_id: int, kind: str = "jpg", label: str = "") -> dict:
    """Готовая картинка документа → «Файлы для монтажа» его проекта (без проекта — общие) с пометкой, что это
    превью/фото, а не вставка в клип. Документ остаётся: правка — в редакторе /covers/<id>."""
    from app import cover_docs as D
    from app.montage_assets import assets_dir

    doc = D.get_doc(store, doc_id)
    src = export_limited(store, doc_id) if kind != "png" else D.render_doc(store, doc_id)
    ext = "png" if kind == "png" else "jpg"
    dest = assets_dir() / f"cover-doc-{int(doc_id)}-{uuid4().hex[:10]}.{ext}"
    shutil.copyfile(src, dest)
    title = (label or "").strip() or f"Превью: {doc['title']}"
    asset = store.create_montage_asset(
        kind="image", label=title[:120],
        description=(f"Превью/фото из редактора обложек (документ #{doc_id}, правится там же: /covers/{doc_id}). "
                     "Это обложка ролика, не картинка для вставок в клип."),
        tags=f"превью,фото,cover_doc:{int(doc_id)}", file_path=str(dest), original_filename=f"preview-{doc_id}.{ext}",
        mime_type="image/png" if ext == "png" else "image/jpeg", width=doc["doc"]["width"], height=doc["doc"]["height"],
        size_bytes=dest.stat().st_size, project_id=doc.get("project_id"),
    )
    return {"asset": asset, "doc_id": int(doc_id), "editor": f"/covers/{int(doc_id)}", "bytes": dest.stat().st_size,
            "project_id": doc.get("project_id")}
