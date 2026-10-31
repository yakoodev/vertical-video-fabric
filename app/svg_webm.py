"""SVG с анимацией (CSS @keyframes / SMIL) → WebM VP9 с альфой — анимированная картинка слоёв.

Перенос svg-to-webm владельца (https://github.com/yakoodev/svg-to-webm, MIT): SVG открывается в
headless Chromium, каждая анимация ставится на паузу и перематывается на время кадра (не запись в
реальном времени), кадры с прозрачным фоном кодирует ffmpeg (libvpx-vp9, yuva420p, alpha_mode=1).
Сам рендер — ``node/svg-to-webm.mjs`` (без зависимостей: CDP через --remote-debugging-pipe,
браузер системный — в образе сервиса ``/usr/bin/chromium``, он там уже стоит для TikTok).

Результат — обычный файл «Файлов для монтажа» (kind ``video``, webm с альфой): слой ``image``
играет его как анимацию с альфой (app/anim_frames.py, раздел «Анимированные картинки с альфой» в
docs/motion/spec.md), PNGtuber — как состояние, баннер рендера — как оверлей. Кэш — по хешу SVG и
параметров: тот же баннер второй раз не считается, а файл в библиотеке тот же (тот же asset_id).

Браузер — один экземпляр за раз (``_LOCK``) и закрывается после каждой конвертации: памяти мало.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

CONVERTER_VERSION = "1"  # меняется — кэш считается заново
FPS_DEFAULT = 30
FPS_MIN, FPS_MAX = 1, 60
DURATION_MIN, DURATION_MAX = 0.2, 30.0
SIDE_MAX = 2160  # сторона выхода, px
SCALE_MIN, SCALE_MAX = 0.25, 3.0
CRF_DEFAULT = 18
MAX_SVG_BYTES = 4 * 1024 * 1024
MAX_FRAMES = 1800
TIMEOUT_SEC = 300
CACHE_TAG = "svg-webm"

_LOCK = threading.Lock()
_ROOT = Path(__file__).resolve().parent.parent
_CHROME_CANDIDATES = (
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
)


class SvgWebmError(RuntimeError):
    """Конвертация не удалась (нет браузера/Node, битый SVG, ffmpeg) — текст для человека."""


def _num(value: Any, default: float | None, lo: float, hi: float) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(hi, max(lo, number))


def normalize_params(raw: Any) -> dict:
    """Параметры конвертации в полной форме. ``duration`` None — авто по анимациям SVG
    (доминирующая длительность, как у оригинала); ``width``/``height`` None — из SVG."""
    src = raw if isinstance(raw, dict) else {}
    duration = _num(src.get("duration"), None, DURATION_MIN, DURATION_MAX)
    fps = int(round(_num(src.get("fps"), FPS_DEFAULT, FPS_MIN, FPS_MAX) or FPS_DEFAULT))
    width = _num(src.get("width"), None, 2, SIDE_MAX)
    height = _num(src.get("height"), None, 2, SIDE_MAX)
    strip = src.get("strip_bg")
    return {
        "fps": fps,
        "duration": round(duration, 3) if duration is not None else None,
        "width": int(round(width)) if width else None,
        "height": int(round(height)) if height else None,
        "scale": round(_num(src.get("scale"), 1.0, SCALE_MIN, SCALE_MAX) or 1.0, 3),
        "crf": int(round(_num(src.get("crf"), CRF_DEFAULT, 0, 63))),
        "strip_bg": strip is True or strip in (1, "1", "true"),
    }


def svg_size(svg: str) -> tuple[int, int]:
    """Размер SVG по корню: width/height, иначе viewBox, иначе 800×600 (как в node/svg-to-webm.mjs)."""
    found = re.search(r"<svg\b[^>]*>", svg, re.I) if isinstance(svg, str) else None
    root = found.group(0) if found else ""

    def length(name: str) -> float | None:
        m = re.search(rf"\b{name}\s*=\s*[\"']\s*([0-9.]+)\s*(px)?\s*[\"']", root, re.I)
        return float(m.group(1)) if m and float(m.group(1)) > 0 else None

    vb = re.search(r"viewBox\s*=\s*[\"']([^\"']+)[\"']", root, re.I)
    nums = [float(x) for x in re.split(r"[\s,]+", vb.group(1).strip())] if vb else []
    vw, vh = (nums[2], nums[3]) if len(nums) == 4 and nums[2] > 0 and nums[3] > 0 else (None, None)
    return int(round(length("width") or vw or 800)), int(round(length("height") or vh or 600))


def check_svg(svg: Any) -> str:
    """Текст SVG или ValueError с причиной."""
    if isinstance(svg, bytes):
        svg = svg.decode("utf-8", errors="replace")
    if not isinstance(svg, str) or not svg.strip():
        raise ValueError("пустой SVG")
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError(f"SVG больше {MAX_SVG_BYTES // (1024 * 1024)} МБ")
    if not re.search(r"<svg\b", svg, re.I):
        raise ValueError("это не SVG: нет <svg>")
    return svg


def output_size(svg: str, params: dict) -> tuple[int, int]:
    w, h = svg_size(svg)
    w, h = params.get("width") or w, params.get("height") or h
    s = float(params.get("scale") or 1.0)
    return max(2, int(round(w * s)) & ~1), max(2, int(round(h * s)) & ~1)


def cache_key(svg: str, params: dict) -> str:
    """Хеш SVG и параметров (и версии конвертера) — имя файла кэша."""
    body = json.dumps({"v": CONVERTER_VERSION, **normalize_params(params)}, sort_keys=True)
    return hashlib.sha256((body + "\n" + svg).encode("utf-8")).hexdigest()[:32]


def cache_dir() -> Path:
    """Кэш живёт в папке «Файлов для монтажа»: файл кэша — это и есть файл библиотеки."""
    from app.montage_assets import assets_dir

    path = assets_dir() / CACHE_TAG
    path.mkdir(parents=True, exist_ok=True)
    return path


def chromium_path() -> str | None:
    """Headless Chromium: SVG_CHROMIUM, PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH (образ сервиса), PATH, известные места."""
    for env in ("SVG_CHROMIUM", "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
        value = os.environ.get(env, "").strip()
        if value and Path(value).exists():
            return value
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        hit = shutil.which(name)
        if hit:
            return hit
    return next((p for p in _CHROME_CANDIDATES if Path(p).exists()), None)


def converter_command() -> list[str]:
    from app.settings import settings

    script = _ROOT / "node" / "svg-to-webm.mjs"
    if not script.is_file():
        raise SvgWebmError(f"нет конвертера {script}")
    node = settings.motion_node
    if not (Path(node).exists() or shutil.which(node)):
        raise SvgWebmError(f"Node не найден: {node!r} (MOTION_NODE)")
    return [node, str(script)]


def build_args(svg_path: Path, out_path: Path, params: dict, chrome: str, command: list[str] | None = None) -> list[str]:
    from app.settings import settings
    from app.user_fonts import registry_path

    p = normalize_params(params)
    args = list(command or converter_command())
    args += ["--in", str(svg_path), "--out", str(out_path), "--fps", str(p["fps"]), "--scale", str(p["scale"]),
             "--crf", str(p["crf"]), "--chrome", chrome, "--ffmpeg", "ffmpeg", "--timeout", str(TIMEOUT_SEC)]
    if p["duration"] is not None:
        args += ["--duration", f"{p['duration']:g}"]
    if p["width"]:
        args += ["--width", str(p["width"])]
    if p["height"]:
        args += ["--height", str(p["height"])]
    if p["strip_bg"]:
        args.append("--strip-bg")
    args += ["--fonts-dir", str(settings.motion_fonts_dir)]
    if registry_path().is_file():
        args += ["--fonts-dir", str(registry_path().parent)]
    return args


def _run(args: list[str], timeout: float) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SvgWebmError(f"SVG → webm не уложился в {int(timeout)} с") from exc
    except OSError as exc:
        raise SvgWebmError(f"не запустился конвертер: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise SvgWebmError("SVG → webm: " + (" ".join(tail) or f"код {proc.returncode}"))
    return proc.stdout


def convert(svg: Any, params: Any = None, *, run: Callable[[list[str], float], str] | None = None) -> dict:
    """SVG → webm с альфой в кэше. ``{path, key, cached, frames, width, height, fps, duration}``.

    Тот же SVG с теми же параметрами — сразу файл из кэша (``cached: True``), без браузера.
    Ошибки: ValueError — сам SVG/параметры; SvgWebmError — среда (браузер, Node, ffmpeg)."""
    svg = check_svg(svg)
    p = normalize_params(params)
    w, h = output_size(svg, p)
    if w > SIDE_MAX or h > SIDE_MAX:
        raise ValueError(f"кадр {w}×{h} больше {SIDE_MAX} px по стороне — уменьши scale или width/height")
    if p["duration"] is not None and p["duration"] * p["fps"] > MAX_FRAMES:
        raise ValueError(f"больше {MAX_FRAMES} кадров — короче duration или меньше fps")
    key = cache_key(svg, p)
    folder = cache_dir()
    out, meta_path = folder / f"{key}.webm", folder / f"{key}.json"
    with _LOCK:  # один браузер за раз: ОЗУ мало
        if out.is_file() and out.stat().st_size > 0 and meta_path.is_file():
            try:
                return {**json.loads(meta_path.read_text("utf-8")), "path": str(out), "key": key, "cached": True}
            except (OSError, ValueError):
                pass
        chrome = chromium_path()
        if not chrome:
            raise SvgWebmError("нет headless Chromium: задай SVG_CHROMIUM (в образе сервиса — /usr/bin/chromium)")
        src = folder / f"{key}.svg"
        src.write_text(svg, "utf-8")
        part = folder / f"{key}.part.webm"
        stdout = (run or _run)(build_args(src, part, p, chrome), TIMEOUT_SEC + 30)
        try:
            info = json.loads((stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError):
            info = {}
        if not part.is_file() or part.stat().st_size == 0:
            raise SvgWebmError("SVG → webm: конвертер не записал файл")
        part.replace(out)
        meta = {k: info.get(k) for k in ("frames", "width", "height", "fps", "duration", "duration_source")}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
        return {**meta, "path": str(out), "key": key, "cached": False}


def find_asset(store: Any, path: str | Path) -> dict | None:
    """Файл библиотеки, который уже смотрит на этот файл кэша (тот же SVG → тот же asset_id)."""
    target = str(Path(path))
    return next((a for a in store.list_montage_assets() if str(Path(a.get("file_path") or "")) == target), None)


def svg_to_asset(store: Any, svg: Any, params: Any = None, *, label: str = "", description: str = "",
                 run: Callable[[list[str], float], str] | None = None) -> tuple[dict, dict]:
    """SVG → webm с альфой → файл «Файлов для монтажа» (kind video). Повтор — тот же asset."""
    from app.montage_assets import probe_asset

    info = convert(svg, params, run=run)
    hit = find_asset(store, info["path"])
    if hit is not None:
        return hit, info
    path = Path(info["path"])
    meta = probe_asset(path)
    asset = store.create_montage_asset(
        kind="video",
        label=(label or "SVG-анимация")[:120],
        description=(description or f"SVG → webm с альфой: {info.get('frames')} кадров, {info.get('fps')} fps")[:500],
        tags=CACHE_TAG,
        file_path=str(path),
        original_filename=f"{info['key']}.webm",
        mime_type="video/webm",
        size_bytes=path.stat().st_size,
        duration_sec=meta.get("duration_sec") or info.get("duration") or 0,
        width=meta.get("width") or info.get("width") or 0,
        height=meta.get("height") or info.get("height") or 0,
        has_audio=False,
    )
    return asset, info


def asset_svg_text(store: Any, asset_id: Any) -> str:
    """Текст SVG из «Файлов для монтажа» (ValueError — нет такого или это не SVG)."""
    try:
        asset = store.get_montage_asset(int(asset_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"файла для монтажа {asset_id!r} нет") from exc
    path = Path(asset.get("file_path") or "")
    if path.suffix.lower() != ".svg" or not path.is_file():
        raise ValueError(f"файл #{asset['id']} — не SVG")
    return check_svg(path.read_text("utf-8", errors="replace"))
