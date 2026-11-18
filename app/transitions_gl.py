"""Шейдерные переходы склеек из коллекции gl-transitions (одобрено владельцем 18.11).

Шейдеры — файлы ``node/gltrans/shaders/*.glsl`` без правок, с заголовками авторов и лицензий (только MIT и BSD;
список и коммит источника — ``docs/third-party/gl-transitions.md``). Рисует их ``node/gltrans/render.mjs``: один
headless Chromium с WebGL (SwiftShader) на весь рендер, кадр за кадром между последними ``n`` кадрами уходящей части
и первыми ``n`` входящей — то же окно, что у переходов кадрами (``app/transitions_fx_render.py``): прогресс кадра
``j`` — ``j / (n − 1)``, первый кадр окна ровно A, последний ровно B, середина — точка склейки.

Сбой браузера или шейдера — не сбой рендера: этот стык идёт запасным xfade (``TRANSITIONS``), как у остальных
переходов кадрами. Браузер берёт общий замок Chromium (``app/browser_lock.py``): рядом с другим Chromium не
поднимается.

Ключи — ``gl_…`` (латиницей, в настройках клипа), в инструменте ``set_transition`` можно и по-русски
(«шторка», «стекло», «волна», ``GL_ALIASES``).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "node" / "gltrans" / "render.mjs"
SHADERS_DIR = ROOT / "node" / "gltrans" / "shaders"
# Источник шейдеров: github.com/gl-transitions/gl-transitions, коммит от 2026-11-01 (старше двух недель).
SOURCE_COMMIT = "1886f07392b173b66ddc75d8d948ca0553626096"
READY_TIMEOUT_SEC = 60.0
LOCK_TIMEOUT_SEC = 300.0


@dataclass(frozen=True)
class GlKind:
    shader: str             # файл node/gltrans/shaders/<shader>.glsl
    label: str              # как в редакторе
    duration: float         # длина по умолчанию, с
    xfade: str              # запасной xfade, если WebGL не сработал
    license: str            # из заголовка шейдера (сверяется тестом)
    author: str
    params: dict = field(default_factory=dict)  # свои значения параметров шейдера поверх умолчаний
    aliases: tuple[str, ...] = ()


GL_KINDS: dict[str, GlKind] = {
    "gl_curtains": GlKind("Curtains", "Занавес", 0.6, "horzopen", "MIT", "gre", aliases=("занавес", "кулисы")),
    "gl_wipe": GlKind("DirectionalWipe", "Шторка мягкая", 0.45, "wipeleft", "MIT", "gre", aliases=("шторка",)),
    "gl_glass": GlKind("Refraction", "Стекло", 0.6, "dissolve", "MIT", "gre", aliases=("стекло", "жидкое стекло")),
    "gl_shatter": GlKind("Shatter", "Осколки", 0.6, "dissolve", "MIT", "lbl", aliases=("осколки", "разбитое стекло")),
    "gl_ripple": GlKind("Ripple", "Волна", 0.6, "fade", "MIT", "gre", aliases=("волна", "рябь")),
    "gl_drop": GlKind("WaterDrop", "Капля", 0.6, "fade", "MIT", "Paweł Płóciennik", aliases=("капля",)),
    "gl_tear": GlKind("PaperTear", "Разрыв", 0.6, "horzopen", "MIT", "gre", aliases=("разрыв", "разрыв бумаги")),
    "gl_pixels": GlKind("Pixelize", "Пиксели", 0.45, "pixelize", "MIT", "gre", aliases=("пиксели", "пикселизация")),
    "gl_circle": GlKind("CircleOpen", "Круг", 0.45, "circleopen", "MIT", "gre", aliases=("круг",)),
    "gl_squeeze": GlKind("Squeeze", "Сжатие", 0.4, "squeezev", "MIT", "gre", aliases=("сжатие",)),
    "gl_cube": GlKind("Cube", "Куб", 0.6, "slideleft", "MIT", "gre", aliases=("куб",)),
    "gl_doors": GlKind("Doorway", "Двери", 0.6, "horzopen", "MIT", "gre", aliases=("двери",)),
    "gl_swirl": GlKind("Swirl", "Вихрь", 0.6, "radial", "MIT", "Sergey Kosarevsky", aliases=("вихрь", "водоворот")),
    "gl_glitch": GlKind("GlitchDisplace", "Помехи", 0.35, "pixelize", "MIT", "Matt DesLauriers",
                        aliases=("помехи", "глитч-блоки")),
    "gl_page": GlKind("InvertedPageCurl", "Страница", 0.7, "wipeleft", "BSD 3 Clause", "Hewlett-Packard",
                      aliases=("страница", "перелистывание")),
    "gl_honeycomb": GlKind("Honeycomb", "Соты", 0.6, "dissolve", "MIT", "gre", aliases=("соты",)),
    # Вертикальный кадр: 3×5 плиток — почти квадратные (умолчание шейдера 4×4 — узкие полосы с чёрными щелями).
    "gl_grid": GlKind("GridFlip", "Плитки", 0.7, "dissolve", "MIT", "TimDonselaar", params={"size": [3, 5]},
                      aliases=("плитки",)),
    "gl_tvoff": GlKind("TvTurnOff", "Выключение ТВ", 0.6, "fadeblack", "MIT", "gre",
                       aliases=("телевизор", "выключение тв", "тв")),
    "gl_burn": GlKind("BurnThrough", "Прожиг", 0.6, "dissolve", "MIT", "liubailin2020@gmail.com",
                      aliases=("прожиг", "огонь")),
    # amplitude 2 (умолчание) стекает за первую четверть окна — 0.6 растягивает на всё окно.
    "gl_melt": GlKind("DoomMelt", "Таяние", 0.7, "wipedown", "MIT", "Zeh Fernando",
                      params={"amplitude": 0.6, "bars": 24}, aliases=("таяние", "стекает")),
}
GL_RANGE = (0.25, 1.0)
# Направление картинки → направление шторки в uv спецификации (y вверх): край идёт по вектору.
_WIPE_VECTORS = {"left": [-1.0, 0.0], "right": [1.0, 0.0], "up": [0.0, 1.0], "down": [0.0, -1.0]}
WIPE_XFADES = {"left": "wipeleft", "right": "wiperight", "up": "wipeup", "down": "wipedown"}
GL_DIRECTIONAL = ("gl_wipe",)
GL_COLORED = {"gl_burn": "burnColor", "gl_tvoff": "color", "gl_grid": "bgcolor"}

GL_ALIASES: dict[str, str] = {alias: key for key, kind in GL_KINDS.items() for alias in kind.aliases}


def resolve_gl(name: object) -> str | None:
    """Ключ ``gl_…`` по ключу, имени шейдера или русскому названию; иначе None."""
    text = " ".join(str(name or "").strip().lower().replace("ё", "е").split())
    if text in GL_KINDS:
        return text
    if text in GL_ALIASES:
        return GL_ALIASES[text]
    for key, kind in GL_KINDS.items():
        if text in (kind.shader.lower(), kind.label.lower().replace("ё", "е")):
            return key
    return None


def gl_params(kind: str, direction: str | None = None, color: str | None = None) -> dict[str, Any]:
    """Параметры шейдера стыка: свои у вида + направление шторки и цвет (у прожига, ТВ, плиток)."""
    spec = GL_KINDS[kind]
    out: dict[str, Any] = dict(spec.params)
    if kind == "gl_wipe" and direction in _WIPE_VECTORS:
        out["direction"] = list(_WIPE_VECTORS[direction])
    name = GL_COLORED.get(kind)
    text = str(color or "").strip().lstrip("#")
    if name and len(text) == 6:
        try:
            rgb = [round(int(text[i:i + 2], 16) / 255.0, 4) for i in (0, 2, 4)]
        except ValueError:
            rgb = []
        if rgb:
            out[name] = rgb if kind == "gl_burn" else rgb + [1.0]
    return out


def gl_xfade(kind: str, direction: str | None = None) -> str:
    """Запасной xfade стыка (WebGL не сработал)."""
    if kind == "gl_wipe" and direction in WIPE_XFADES:
        return WIPE_XFADES[direction]
    return GL_KINDS[kind].xfade


def progress_of(n: int) -> list[float]:
    """Прогресс кадров окна: 0 — ровно A, 1 — ровно B (как ``cut_progress`` переходов склеек)."""
    return [j / (n - 1) if n > 1 else 1.0 for j in range(n)]


class GlError(RuntimeError):
    pass


class GlSession:
    """Один браузер с WebGL на все шейдерные стыки рендера (контекстный менеджер).

    ``render(kind, a, b, params)`` — кадры (n, h, w, 3) uint8 RGB; файлы-обмен лежат в ``work_dir`` и удаляются
    сразу после задания (окно 1080×1920 — ~6 МБ на кадр)."""

    def __init__(self, work_dir: Path, *, node: str | None = None, chrome: str | None = None,
                 lock: bool = True) -> None:
        self.work_dir = Path(work_dir)
        self.node = node
        self.chrome = chrome
        self.use_lock = lock
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.locked = False
        self.seq = 0
        self.stderr_tail = ""

    def __enter__(self) -> "GlSession":
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _start(self) -> None:
        if not RENDERER.exists():
            raise GlError(f"нет {RENDERER}")
        node = self.node
        if node is None:
            from app.settings import settings

            node = settings.motion_node
        chrome = self.chrome
        if chrome is None:
            try:
                from app.scene3d import chromium_path

                chrome = chromium_path() or ""
            except Exception:  # noqa: BLE001 - нет поиска браузера — пусть ищет сам render.mjs
                chrome = ""
        if self.use_lock:
            from app.browser_lock import BROWSER_LOCK

            if not BROWSER_LOCK.acquire(timeout=LOCK_TIMEOUT_SEC):
                raise GlError("браузер занят другой задачей")
            self.locked = True
        self.work_dir.mkdir(parents=True, exist_ok=True)
        args = [str(node), str(RENDERER)] + (["--chrome", chrome] if chrome else [])
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
                                     env={**os.environ})
        threading.Thread(target=self._pump, args=(self.proc.stdout, True), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, False), daemon=True).start()
        ready = self._reply(READY_TIMEOUT_SEC)
        if not ready.get("ok"):
            raise GlError(str(ready.get("error") or "браузер не поднялся"))

    def _pump(self, stream: Any, main: bool) -> None:
        for line in stream:
            if main:
                self.lines.put(line)
            else:
                self.stderr_tail = (self.stderr_tail + line)[-2000:]
        if main:
            self.lines.put(None)

    def _reply(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise GlError("рендер шейдера не ответил вовремя")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty as exc:
                raise GlError("рендер шейдера не ответил вовремя") from exc
            if line is None:
                tail = self.stderr_tail.strip().splitlines()[-2:]
                raise GlError("рендер шейдера завершился" + (f": {' '.join(tail)[:300]}" if tail else ""))
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue  # посторонний вывод — не ответ

    def render(self, kind: str, a: "Iterable[np.ndarray]", b: "Iterable[np.ndarray]", params: dict | None = None,
               progress: list[float] | None = None) -> "np.ndarray":
        """Кадры перехода ``kind`` между кадрами ``a`` и ``b`` (каждый (h, w, 3) uint8 RGB, поровну) → (n, h, w, 3).

        Кадры пишутся в файл обмена по одному: весь массив RGB окна в памяти не собирается."""
        import numpy as np

        if self.proc is None or self.proc.stdin is None:
            raise GlError("браузер не запущен")
        spec = GL_KINDS[kind]
        self.seq += 1
        stem = self.work_dir / f"gl-{self.seq:03d}"
        files = {k: stem.with_name(f"{stem.name}-{k}.raw") for k in ("a", "b", "out")}
        try:
            counts: dict[str, int] = {}
            shapes: set[tuple[int, ...]] = set()
            for key, frames in (("a", a), ("b", b)):
                with open(files[key], "wb") as fh:
                    counts[key] = 0
                    for frame in frames:
                        arr = np.ascontiguousarray(frame, np.uint8)
                        if arr.ndim != 3 or arr.shape[2] != 3:
                            raise GlError(f"кадр {key}: нужен (h, w, 3), пришёл {arr.shape}")
                        fh.write(arr.tobytes())
                        counts[key] += 1
                        shapes.add(arr.shape)
            n = counts["a"]
            if len(shapes) != 1 or counts["b"] != n or not n:
                raise GlError("кадры A и B — разного размера или числа")
            h, w = next(iter(shapes))[:2]
            job = {"id": self.seq, "shader": spec.shader, "width": int(w), "height": int(h), "frames": n,
                   "progress": progress or progress_of(n), "a": str(files["a"]), "b": str(files["b"]),
                   "out": str(files["out"]), "params": params or {}}
            self.proc.stdin.write(json.dumps(job) + "\n")
            self.proc.stdin.flush()
            reply = self._reply(30.0 + n * max(1.0, w * h / 400_000.0))
            if not reply.get("ok"):
                raise GlError(str(reply.get("error") or "шейдер не нарисован"))
            out = np.fromfile(files["out"], np.uint8)
            if out.size != n * h * w * 3:
                raise GlError("кадров перехода меньше, чем заказано")
            return out.reshape(n, h, w, 3)
        finally:
            for path in files.values():
                path.unlink(missing_ok=True)

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=30)
            except Exception:  # noqa: BLE001 - не закрылся сам — гасим свой процесс
                proc.kill()
                proc.wait(timeout=10)
        if self.locked:
            from app.browser_lock import BROWSER_LOCK

            self.locked = False
            BROWSER_LOCK.release()
