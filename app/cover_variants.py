"""Варианты обложки готового клипа: несколько сильных кадров + крупный русский хук.

Голый кадр в ленте теряется — кликают на обложку с короткой крупной надписью. Здесь
для ОТРЕНДЕРЕННОГО клипа (таблица ``clips``) собирается 2–6 вариантов, владелец или агент
выбирает один:

1. **Кадры.** По готовому ролику берётся ~14 кадров (ffmpeg, маленькие), каждый оценивается
   без ИИ (OpenCV): лицо и его размер (тот же детектор, что у автофокуса, app/autofocus.py),
   резкость, контраст, цветность; слишком тёмные/пересвеченные — штраф. Берутся лучшие с
   разносом по времени — не три кадра одной секунды.
2. **Основа кадра.** Если у клипа есть план и компоновщик моушна — чистый кадр готового вида
   (кроп, раскладка, камера, картинки/видео/PNGtuber клипа) БЕЗ текстовых слоёв и субтитров:
   свой титр не спорит с хуком клипа. Нет — кадр готового файла.
3. **Текст.** 2–5 слов: явные ``texts`` → ``cover_hooks`` из метаданных клипа
   (app/ai/publish_meta.py, пишутся после рендера) → отдельный короткий запрос Gemini (если
   есть ключ) → из заголовка клипа. Без эмодзи, хэштегов, названий площадок и чужих сервисов.
4. **Место текста.** Лица на основе ищутся заново; титр встаёт в ту треть кадра, где лиц нет
   (обычно противоположную), и в безопасной зоне формата (app/safe_zones.py).
5. **Стиль.** Шрифт и цвета бренда проекта (app/brand_kit.py), иначе Montserrat Black; стили
   yellow / white / plate / accent чередуются по вариантам. Рисует ffmpeg drawtext.

Размер: 9:16 — 1080×1920, 16:9 — 1280×720 (миниатюра YouTube, < 2 МБ), 1:1 — 1080×1080,
4:5 — 1080×1350. Файлы — ``clip_dir/covers``. ``choose_cover`` делает вариант обложкой
клипа (``clips.cover_path``: превью, карточка одобрения, миниатюра горизонтального видео
YouTube — app/publish_format.landscape_cover) и, по умолчанию, обложкой в настройках плана
(``render.cover`` image + burn) — следующий рендер вошьёт её первым кадром.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

_log = logging.getLogger(__name__)

COVER_SIZES = {"9:16": (1080, 1920), "16:9": (1280, 720), "1:1": (1080, 1080), "4:5": (1080, 1350)}
COUNT_MIN, COUNT_MAX, COUNT_DEFAULT = 1, 6, 3
SAMPLES = 14
SCORE_W = 360
STYLES = ("yellow", "white", "plate", "accent")
MAX_WORDS = 5
MAX_HOOK_CHARS = 42
# Слои клипа, что остаются на основе обложки: картинка героя, а не текст (титр — свой).
BASE_LAYER_TYPES = ("image", "video", "pngtuber")
# Названия площадок и сторонних сервисов нарезки на обложке не пишем.
BLOCKED_WORDS = {
    "tiktok", "тикток", "тиктока", "youtube", "ютуб", "ютуба", "ютубе", "shorts", "шортс", "шортсы", "reels",
    "рилс", "рилсы", "instagram", "инстаграм", "инсте", "инста", "opus", "opusclip", "vizard", "capcut",
    "submagic", "klap", "veed", "descript", "капкат",
}
FALLBACK_HOOKS = ("СМОТРИ ДО КОНЦА", "ЭТО НАДО ВИДЕТЬ", "ВОТ ЭТО ПОВОРОТ", "ТАКОГО НЕ ЖДАЛИ", "ЧЕМ ВСЁ КОНЧИЛОСЬ?")
# Где может стоять центр титра (доля высоты кадра), по порядку предпочтения.
BANDS = {"9:16": (0.24, 0.68, 0.46), "16:9": (0.22, 0.78, 0.5), "1:1": (0.2, 0.8, 0.5), "4:5": (0.2, 0.8, 0.5)}

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS clip_covers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        format TEXT NOT NULL DEFAULT '9:16',
        t REAL NOT NULL DEFAULT 0,
        text TEXT NOT NULL DEFAULT '',
        style TEXT NOT NULL DEFAULT '',
        text_y REAL NOT NULL DEFAULT 0,
        faces INTEGER NOT NULL DEFAULT 0,
        score REAL NOT NULL DEFAULT 0,
        renderer TEXT NOT NULL DEFAULT '',
        chosen INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_clip_covers_clip ON clip_covers(clip_id, id)",
]


def ensure_cover_variants_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Текст
# --------------------------------------------------------------------------- #


def clean_hook(text: Any) -> str:
    """Текст обложки: без эмодзи, хэштегов, площадок и чужих сервисов, не длиннее 5 слов."""
    from app.ai.publish_meta import strip_emoji

    raw = re.sub(r"#\S+", " ", str(text or ""))
    raw = strip_emoji(raw).replace("«", "").replace("»", "").replace('"', "")
    words = [w for w in raw.split() if re.sub(r"[^\w]", "", w).lower() not in BLOCKED_WORDS]
    words = [w for w in words if re.sub(r"[^\w]", "", w)][:MAX_WORDS]
    out = " ".join(words).strip(" ,.;:-–—")
    while len(out) > MAX_HOOK_CHARS and " " in out:
        out = out.rsplit(" ", 1)[0].strip(" ,.;:-–—")
    return out[:MAX_HOOK_CHARS].strip()


def fallback_hooks(title: str, count: int) -> list[str]:
    """Без ИИ: из заголовка клипа (начало, конец) и нейтральных зазывалок."""
    whole = clean_hook(title).split()
    words = re.sub(r"#\S+", " ", str(title or "")).split()
    out: list[str] = []
    if whole:
        out.append(" ".join(whole[:4]))
    if len(words) > 4:
        out.append(clean_hook(" ".join(words[-4:])))
    if len(whole) > 2:
        out.append(" ".join(whole[:3]))
    out.extend(FALLBACK_HOOKS)
    seen: list[str] = []
    for hook in out:
        if hook and hook.upper() not in {s.upper() for s in seen}:
            seen.append(hook)
    return seen[: max(1, count)]


def cover_texts(store: Any, clip: dict, count: int, texts: list | None = None, client: Any = None) -> tuple[list[str], str]:
    """Тексты вариантов и откуда они: texts | meta | gemini | title."""
    if texts:
        hooks = [h for h in (clean_hook(t) for t in texts) if h]
        if not hooks:
            raise ValueError("texts: после чистки (эмодзи, хэштеги, площадки) ничего не осталось")
        return _pad(hooks, clip, count), "texts"
    meta_hooks = [h for h in (clean_hook(t) for t in ((clip.get("publish_meta") or {}).get("cover_hooks") or [])) if h]
    if len(meta_hooks) >= min(count, 2):
        return _pad(meta_hooks, clip, count), "meta"
    if client is not None or settings.gemini_api_key:
        try:
            from app.ai.publish_meta import generate_cover_hooks

            hooks = generate_cover_hooks(store, int(clip["id"]), count, client=client)
            if hooks:
                return _pad(hooks, clip, count), "gemini"
        except Exception as exc:  # noqa: BLE001 - без ИИ — из заголовка
            _log.warning("cover hooks for clip %s: %s", clip.get("id"), str(exc)[:200])
    return fallback_hooks(clip.get("title") or "", count), "title"


def _pad(hooks: list[str], clip: dict, count: int) -> list[str]:
    out = list(dict.fromkeys(hooks))
    if len(out) < count:
        for extra in fallback_hooks(clip.get("title") or "", count + len(out)):
            if extra.upper() not in {h.upper() for h in out}:
                out.append(extra)
            if len(out) >= count:
                break
    return out[:count]


def wrap_lines(text: str, max_chars: int) -> list[str]:
    words = text.upper().split()
    lines: list[str] = []
    for word in words:
        if lines and len(lines[-1]) + 1 + len(word) <= max_chars:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines or [""]


def text_layout(text: str, fmt: str, width: int, height: int) -> dict:
    """Строки, кегль и высота блока титра в px кадра обложки."""
    landscape = fmt == "16:9"
    max_chars = 16 if landscape else 11
    lines = wrap_lines(text, max_chars)
    while len(lines) > 3:
        max_chars += 3
        lines = wrap_lines(text, max_chars)
    base = height * 0.16 if landscape else width * (0.12 if fmt == "9:16" else 0.11)
    longest = max(len(line) for line in lines) or 1
    size = int(min(base, width * 0.86 / (0.76 * longest)))
    size = max(28, size)
    line_h = round(size * 1.14)
    return {"lines": lines, "size": size, "line_h": line_h, "block_h": line_h * len(lines)}


def band_y(faces: list[list[float]], fmt: str, block_frac: float) -> float:
    """Центр титра по высоте (доля кадра): из BANDS — где меньше всего лиц, в безопасной зоне."""
    from app.safe_zones import safe_zone

    zone = safe_zone(fmt)
    half = block_frac / 2
    lo, hi = zone["top"] + half, max(zone["top"] + half, zone["bottom"] - half)
    best_y, best_cost = None, None
    for y in BANDS.get(fmt, BANDS["9:16"]):
        y = min(max(y, lo), hi)
        top, bottom = y - half - 0.02, y + half + 0.02
        cost = 0.0
        for x0, y0, x1, y1 in faces:
            pad = (y1 - y0) * 0.15
            overlap = max(0.0, min(bottom, y1 + pad) - max(top, y0 - pad))
            cost += overlap * max(0.0, x1 - x0)
        if best_cost is None or cost < best_cost - 1e-9:
            best_y, best_cost = y, cost
    return round(float(best_y if best_y is not None else lo), 4)


# --------------------------------------------------------------------------- #
# Кадры
# --------------------------------------------------------------------------- #


def sample_times(duration: float, n: int = SAMPLES) -> list[float]:
    d = max(0.0, float(duration or 0))
    if d < 1.5:
        return [round(d / 2, 3)]
    start, end = min(0.6, d * 0.1), max(min(0.6, d * 0.1), d - 0.6)
    if n <= 1:
        return [round((start + end) / 2, 3)]
    step = (end - start) / (n - 1)
    return [round(start + i * step, 3) for i in range(n)]


def _cv2():
    """OpenCV, если стоит (в образе — да); нет — оценка кадров без лиц, на numpy."""
    try:
        import cv2
    except ImportError:  # pragma: no cover - зависит от окружения
        return None
    return cv2


def small_size(width: int, height: int) -> tuple[int, int]:
    """Размер кадра для оценки: ширина SCORE_W, высота чётная по пропорции."""
    w, h = max(1, int(width or 1080)), max(1, int(height or 1920))
    return SCORE_W, max(2, round(SCORE_W * h / w / 2) * 2)


def read_small(src: Path, size: tuple[int, int], t: float | None = None, crop: tuple[int, int] | None = None):
    """Кадр (секунда ``t`` видео или картинка) как массив BGR размера ``size``; ``crop`` — сперва
    заполнить и обрезать под кадр обложки (как build_overlay_args). Не вышло — None."""
    import numpy as np

    w, h = size
    chain = []
    if crop:
        chain += [f"scale={crop[0]}:{crop[1]}:force_original_aspect_ratio=increase", f"crop={crop[0]}:{crop[1]}"]
    chain.append(f"scale={w}:{h}")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if t is not None:
        args += ["-ss", f"{max(0.0, t):.3f}"]
    args += ["-i", str(src), "-frames:v", "1", "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or len(proc.stdout) != w * h * 3:
        return None
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(h, w, 3)


def detect_faces(img) -> list[list[float]]:
    """Лица на кадре — доли кадра [x0, y0, x1, y1]; мелкие (фон, ложные) отброшены. Без OpenCV — []."""
    cv2 = _cv2()
    if cv2 is None or img is None:
        return []
    from app.autofocus import _MIN_FACE_W_FRAC, _detect_faces

    h, w = img.shape[:2]
    if w > SCORE_W:
        img = cv2.resize(img, (SCORE_W, max(1, round(h * SCORE_W / w))))
        h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out = []
    for x, y, fw, fh in _detect_faces(img, gray):
        if fw / w < _MIN_FACE_W_FRAC:
            continue
        out.append([round(x / w, 4), round(y / h, 4), round(min(w, x + fw) / w, 4), round(min(h, y + fh) / h, 4)])
    return out


def frame_metrics(img) -> dict:
    """Чем кадр хорош для обложки (0..1+): лицо крупно, резко, контрастно, сочно."""
    import numpy as np

    faces = detect_faces(img)
    small = img.astype(np.float64)
    gray = 0.114 * small[..., 0] + 0.587 * small[..., 1] + 0.299 * small[..., 2]
    # Резкость — дисперсия лапласиана (4 соседа), как cv2.Laplacian, только на numpy.
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    sharp = float(lap.var()) if lap.size else 0.0
    contrast = float(gray.std())
    bright = float(gray.mean())
    b, g, r = (small[..., i] for i in range(3))
    rg, yb = r - g, 0.5 * (r + g) - b
    colorful = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    face = max(((f[2] - f[0]) for f in faces), default=0.0)
    score = (
        (0.1 if faces else 0.0)
        + 0.4 * min(1.0, face / 0.25)
        + 0.2 * min(1.0, sharp / 300.0)
        + 0.15 * min(1.0, contrast / 64.0)
        + 0.15 * min(1.0, colorful / 80.0)
    )
    if bright < 40 or bright > 215:
        score -= 0.3
    return {"score": round(score, 4), "faces": faces, "sharp": round(sharp, 1), "bright": round(bright, 1)}


def pick_moments(candidates: list[dict], count: int, duration: float) -> list[dict]:
    """Лучшие кадры с разносом по времени (сначала строгий, потом мягче)."""
    ranked = sorted(candidates, key=lambda c: (-float(c["score"]), float(c["t"])))
    gap = max(1.0, float(duration or 0) / (max(1, count) * 2.5))
    chosen: list[dict] = []
    for factor in (1.0, 0.5, 0.0):
        for cand in ranked:
            if len(chosen) >= count:
                break
            if cand in chosen:
                continue
            if all(abs(float(cand["t"]) - float(c["t"])) >= gap * factor for c in chosen):
                chosen.append(cand)
        if len(chosen) >= count:
            break
    return sorted(chosen, key=lambda c: -float(c["score"]))


# --------------------------------------------------------------------------- #
# Стиль и рисование
# --------------------------------------------------------------------------- #


def _hex(color: Any, default: str) -> str:
    text = str(color or "").strip()
    return text.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else default


def _luma(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def style_spec(style: str, brand: dict | None = None) -> dict:
    """Цвета drawtext стиля: текст, обводка, плашка (у plate)."""
    colors = (brand or {}).get("colors") or {}
    accent = _hex(colors.get("accent"), "#ffd600")
    if style == "plate":
        box = _hex(colors.get("accent"), "#7c5cff") if brand else "#7c5cff"
        text = "#0b0d14" if _luma(box) > 160 else "#ffffff"
        return {"color": text, "border": None, "box": box}
    if style == "white":
        return {"color": "#ffffff", "border": "#000000", "box": None}
    if style == "accent":
        return {"color": accent, "border": "#000000", "box": None}
    return {"color": "#ffd600", "border": "#000000", "box": None}


def default_styles(brand: dict | None) -> list[str]:
    return ["accent", "plate", "white"] if brand else ["yellow", "plate", "white"]


def font_path(family: str | None = None) -> Path:
    """Файл шрифта пака по семейству (самое жирное начертание); нет — Montserrat Black."""
    fonts = Path(settings.motion_fonts_dir)
    key = re.sub(r"\s+", "", str(family or "")).lower()
    order = ("black", "extrabold", "bold", "semibold", "regular", "medium")
    if key and fonts.is_dir():
        found = [p for p in fonts.glob("*.ttf") if p.stem.split("-")[0].lower() == key]
        found.sort(key=lambda p: order.index(p.stem.split("-")[-1].lower()) if p.stem.split("-")[-1].lower() in order else 99)
        if found:
            return found[0]
    return fonts / "Montserrat-Black.ttf"


def _ffcolor(color: str, alpha: float | None = None) -> str:
    out = "0x" + color.lstrip("#")
    return f"{out}@{alpha:g}" if alpha is not None else out


def build_overlay_args(base: str, out: Path, *, width: int, height: int, layout: dict, y_center: float,
                       spec: dict, font: str = "font.ttf", textfiles: list[str] | None = None) -> list[str]:
    """ffmpeg: основа под кадр обложки + строки титра (drawtext, текст из файлов — без экранирования).
    Пути ``base``/``font``/``textfiles`` — относительно рабочей папки (cwd), чтобы в фильтре не было
    двоеточий и обратных слешей путей Windows."""
    size, line_h, lines = layout["size"], layout["line_h"], layout["lines"]
    files = textfiles or [f"line{i}.txt" for i in range(len(lines))]
    top = y_center * height - layout["block_h"] / 2
    chain = [f"scale={width}:{height}:force_original_aspect_ratio=increase", f"crop={width}:{height}", "setsar=1"]
    for i, _line in enumerate(lines):
        y = round(top + i * line_h + (line_h - size) / 2)
        parts = [
            f"fontfile={font}", f"textfile={files[i]}", "expansion=none", f"fontsize={size}",
            f"fontcolor={_ffcolor(spec['color'])}", "x=(w-text_w)/2", f"y={y}",
            f"shadowcolor={_ffcolor('#000000', 0.5)}", "shadowx=0", f"shadowy={max(2, round(size * 0.06))}",
        ]
        if spec.get("border"):
            parts += [f"borderw={max(3, round(size * 0.1))}", f"bordercolor={_ffcolor(spec['border'])}"]
        if spec.get("box"):
            parts += ["box=1", f"boxcolor={_ffcolor(spec['box'])}", f"boxborderw={max(8, round(size * 0.2))}"]
        chain.append("drawtext=" + ":".join(parts))
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", base,
        "-vf", ",".join(chain), "-frames:v", "1", "-q:v", "3", str(out),
    ]


def _run(args: list[str], cwd: Path | None = None, timeout: int = 120) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed").strip()[-300:])


def _frame_args(video: Path, t: float, out: Path, width: int | None = None) -> list[str]:
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
            "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    return args + [str(out)]


# --------------------------------------------------------------------------- #
# Клип, бренд, основа кадра
# --------------------------------------------------------------------------- #


def clip_brand(store: Any, clip: dict) -> dict | None:
    """Бренд проекта клипа (шрифт, цвета) или None."""
    try:
        from app import projects as P

        project_id = None
        if clip.get("clip_plan_id"):
            project_id = P.project_of_plan(store, int(clip["clip_plan_id"]))
        if project_id is None and clip.get("source_id"):
            project_id = P.project_of_source(store, int(clip["source_id"]))
        if project_id is None:
            return None
        brand_id = P.get_project(store, int(project_id), include_sources=False).get("brand_id")
        return store.get_brand(int(brand_id)) if brand_id else None
    except Exception:  # noqa: BLE001 - без бренда — стиль по умолчанию
        return None


def latest_render(store: Any, clip_plan_id: int) -> dict:
    """Свежий готовый рендер плана (MCP: clip_id — план)."""
    for clip in store.list_clips_for_plan(int(clip_plan_id)):
        if clip.get("status") == "succeeded":
            return clip
    raise ValueError(f"у клипа #{clip_plan_id} нет готового рендера — сначала render_clip (MCP; дождись render_status done)")


def _base_frame(store: Any, clip: dict, video: Path, t: float, out: Path) -> str:
    """Чистая основа (компоновщик: вид клипа без текста и субтитров) или кадр готового файла."""
    plan_id = clip.get("clip_plan_id")
    if plan_id:
        try:
            from app.agent_tools import plan_clock
            from app.clip_settings import normalize_render_settings
            from app.motion_render import render_frame_png

            plan = store.get_clip_plan(int(plan_id))
            rs = normalize_render_settings(plan.get("render_settings") or {})
            layers = [layer for layer in rs.get("layers") or [] if layer.get("type") in BASE_LAYER_TYPES]
            t_clip = plan_clock(store, plan).final_to_clip(t)
            png = render_frame_png(store, int(plan_id), t_clip, layers=layers, camera=None)
            if png:
                out.write_bytes(png)
                return "compositor"
        except Exception as exc:  # noqa: BLE001 - нет компоновщика/исходника — кадр файла
            _log.info("cover base via compositor failed for clip %s: %s", clip.get("id"), str(exc)[:200])
    _run(_frame_args(video, t, out))
    return "render"


# --------------------------------------------------------------------------- #
# Хранение
# --------------------------------------------------------------------------- #


def _cover_from_row(row: Any) -> dict:
    data = dict(row)
    data["chosen"] = bool(data.get("chosen"))
    data["url"] = f"/media/clips/{data['clip_id']}/covers/{data['id']}"
    data.pop("path", None)
    return data


def list_covers(store: Any, clip_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM clip_covers WHERE clip_id = ? ORDER BY id", (int(clip_id),))
    return [_cover_from_row(r) for r in rows]


def get_cover(store: Any, cover_id: int, clip_id: int | None = None) -> dict:
    row = store.db.query_one("SELECT * FROM clip_covers WHERE id = ?", (int(cover_id),))
    if not row or (clip_id is not None and int(row["clip_id"]) != int(clip_id)):
        raise KeyError(f"cover not found: {cover_id}")
    return {**_cover_from_row(row), "path": row["path"]}


def cover_file(store: Any, clip_id: int, cover_id: int) -> Path:
    """Файл варианта — только внутри clip_dir (путь на сервере, наружу не отдаётся)."""
    path = Path(get_cover(store, cover_id, clip_id)["path"])
    path.resolve(strict=False).relative_to(settings.clip_dir.resolve())
    if not path.is_file():
        raise KeyError(f"cover file missing: {cover_id}")
    return path


def _drop_unchosen(store: Any, clip_id: int, keep: set[int]) -> None:
    rows = store.db.query_all("SELECT id, path FROM clip_covers WHERE clip_id = ? AND chosen = 0", (int(clip_id),))
    for row in rows:
        if int(row["id"]) in keep:
            continue
        try:
            Path(row["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        store.db.execute("DELETE FROM clip_covers WHERE id = ?", (int(row["id"]),))


# --------------------------------------------------------------------------- #
# Главное
# --------------------------------------------------------------------------- #


def generate_covers(store: Any, clip_id: int, count: int = COUNT_DEFAULT, texts: list | None = None,
                    styles: list | None = None, client: Any = None, replace: bool = True) -> dict:
    """Сделать ``count`` вариантов обложки готового клипа. ``replace`` — прежние невыбранные
    варианты удаляются (выбранный остаётся)."""
    from app.publish_format import clip_format

    count = max(COUNT_MIN, min(COUNT_MAX, int(count or COUNT_DEFAULT)))
    clip = store.get_clip(int(clip_id))
    if clip.get("status") != "succeeded":
        raise ValueError("клип ещё не отрендерен")
    video = Path(clip.get("output_path") or "")
    if not video.is_file():
        raise ValueError("файла готового клипа нет")
    fmt = clip_format(store, clip) or "9:16"
    width, height = COVER_SIZES.get(fmt, COVER_SIZES["9:16"])
    duration = float(clip.get("duration_sec") or 0)
    bad = [s for s in styles or [] if s not in STYLES]
    if bad:
        raise ValueError(f"стиль {bad[0]!r}: {', '.join(STYLES)}")
    work = settings.tmp_dir / f"covers-{clip_id}-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    out_dir = settings.clip_dir / "covers"
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[int] = []
    try:
        candidates = []
        scan = small_size(int(clip.get("width") or width), int(clip.get("height") or height))
        for t in sample_times(duration):
            img = read_small(video, scan, t)
            if img is not None:
                candidates.append({"t": t, **frame_metrics(img)})
        if not candidates:
            raise RuntimeError("не удалось взять кадры из клипа")
        picks = pick_moments(candidates, count, duration)
        hooks, source = cover_texts(store, clip, len(picks), texts, client)
        brand = clip_brand(store, clip)
        order = list(styles or default_styles(brand))
        shutil.copyfile(font_path((brand or {}).get("font")), work / "font.ttf")
        for i, pick in enumerate(picks):
            base = work / f"base{i}.png"
            renderer = _base_frame(store, clip, video, float(pick["t"]), base)
            # Лица — на самой основе, уже обрезанной под кадр обложки (её доли = доли обложки).
            img = read_small(base, small_size(width, height), crop=(width, height))
            faces = detect_faces(img) if img is not None else pick["faces"]
            text = hooks[i % len(hooks)]
            style = order[i % len(order)]
            layout = text_layout(text, fmt, width, height)
            y = band_y(faces, fmt, layout["block_h"] / height)
            files = []
            for n, line in enumerate(layout["lines"]):
                name = f"v{i}l{n}.txt"
                (work / name).write_text(line, encoding="utf-8")
                files.append(name)
            out = out_dir / f"{int(clip_id)}-{uuid4().hex[:12]}.jpg"
            _run(build_overlay_args(base.name, out, width=width, height=height, layout=layout, y_center=y,
                                    spec=style_spec(style, brand), textfiles=files), cwd=work)
            cur = store.db.execute(
                "INSERT INTO clip_covers (clip_id, path, width, height, format, t, text, style, text_y, faces, score, "
                "renderer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(clip_id), str(out), width, height, fmt, float(pick["t"]), text.upper(), style, y, len(faces),
                 float(pick["score"]), renderer),
            )
            made.append(int(cur.lastrowid))
        if replace:
            _drop_unchosen(store, int(clip_id), set(made))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    covers = list_covers(store, int(clip_id))
    return {"clip_id": int(clip_id), "format": fmt, "size": [width, height], "texts_source": source,
            "new": made, "covers": covers}


def choose_cover(store: Any, cover_id: int, apply_to_render: bool = True, clip_id: int | None = None) -> dict:
    """Вариант → обложка клипа (превью, карточка одобрения, миниатюра YouTube 16:9). С
    ``apply_to_render`` — ещё и обложка плана (image + burn): следующий рендер вошьёт её."""
    from app.clip_settings import normalize_cover

    cover = get_cover(store, cover_id, clip_id)
    clip_id = int(cover["clip_id"])
    path = cover_file(store, clip_id, int(cover_id))
    store.db.execute("UPDATE clip_covers SET chosen = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE clip_id = ?",
                     (int(cover_id), clip_id))
    store.set_clip_cover(clip_id, path, error=None)
    out: dict[str, Any] = {"ok": True, "clip_id": clip_id, "cover_id": int(cover_id),
                           "cover_url": f"/media/clips/{clip_id}/cover", "render_cover": None}
    clip = store.get_clip(clip_id)
    plan_id = clip.get("clip_plan_id")
    if apply_to_render and plan_id:
        from app.clip_history import snapshot

        covers = settings.data_dir / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}.jpg"
        shutil.copyfile(path, covers / name)
        plan = store.get_clip_plan(int(plan_id))
        render_settings = dict(plan.get("render_settings") or {})
        snapshot(store, int(plan_id), "обложка из вариантов")
        render_settings["cover"] = normalize_cover({"mode": "image", "image": name, "burn": True, "burn_sec": 0.1})
        store.set_clip_plan_render_settings(int(plan_id), render_settings)
        out["render_cover"] = render_settings["cover"]
        out["note"] = ("Обложка клипа сменена сразу; в первые кадры видео она вошьётся при следующем рендере "
                       "(площадки, что берут превью с первого кадра, покажут её).")
    else:
        out["note"] = "Обложка клипа сменена (превью, одобрение, миниатюра YouTube); в видео не вшита."
    out["cover"] = {k: v for k, v in get_cover(store, cover_id).items() if k != "path"}
    return out


def contact_sheet(store: Any, clip_id: int, cover_ids: list[int] | None = None) -> bytes:
    """Варианты рядом одной картинкой (JPEG) — агенту посмотреть перед выбором."""
    covers = [c for c in list_covers(store, clip_id) if not cover_ids or c["id"] in cover_ids]
    if not covers:
        raise ValueError("вариантов обложки нет — сначала generate_covers")
    paths = [cover_file(store, clip_id, c["id"]) for c in covers]
    landscape = covers[0]["width"] > covers[0]["height"]
    h = 360 if landscape else 640
    inputs: list[str] = []
    for p in paths:
        inputs += ["-i", str(p)]
    n = len(paths)
    chain = ";".join(
        f"[{i}:v]scale=-2:{h},setsar=1" + (",pad=iw+12:ih:0:0:color=0x15171c" if i < n - 1 else "") + f"[v{i}]"
        for i in range(n)
    )
    stack = "".join(f"[v{i}]" for i in range(n)) + (f"hstack=inputs={n}[out]" if n > 1 else "null[out]")
    out = settings.tmp_dir / f"cover-sheet-{uuid4().hex[:8]}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex", f"{chain};{stack}",
              "-map", "[out]", "-frames:v", "1", "-q:v", "4", str(out)])
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def brief(cover: dict) -> dict:
    """Вариант для ответа агенту."""
    keys = ("id", "text", "style", "t", "text_y", "faces", "score", "renderer", "chosen", "url", "width", "height")
    return {k: cover.get(k) for k in keys}
