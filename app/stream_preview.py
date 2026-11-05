"""▶ Смотреть монтаж: рендер 480p прямо во время показа.

Кнопка показывает ровно то, что соберёт рендер, но в 480p и сразу, пока он идёт. Как это устроено:

1. **Проход записи рецепта** (``record``, ``_make_recorder``). Настоящий ``ClipRenderService._render_clip_plan_unlocked``
   идёт как при рендере, но картинка кусков заменена чёрной заглушкой (видео записи не декодируется), база
   не трогается (``PreviewStore``), Whisper и компоновщик не запускаются. Побочные продукты: готовый звук
   ролика со всеми настоящими проходами (музыка, вставки, звуки слоёв, два прохода громкости) и параметры
   картинки каждого шага: куски, склейки, стоп-кадры, comp.json, ASS, баннер, вставки. Своей математики
   времени здесь нет: всё считает код рендера.
2. **Цепочка картинки** (``run_chain``): ffmpeg куска → сырые кадры → (Node-компоновщик) → ffmpeg-кодировщик
   (баннер, субтитры, вставки, финальный стоп-кадр) → фрагментированный MP4 → файлы по чанкам 2–4 с. Один проход
   на подряд идущие несобранные чанки, чанк = фрагмент MP4 от ключевого кадра.
3. **Кэш по содержимому**: имя чанка — хэш всего, что на него влияет. После правки пересобираются только
   изменившиеся чанки, остальное отдаётся сразу. Отмена при правке — только потеря незавершённого чанка.
4. **HLS VOD**: плейлист с полной длиной ролика известен сразу, запрос ещё не собранного чанка ждёт его
   не больше ``SEGMENT_WAIT_SEC`` и отвечает 503 с Retry-After (nginx не держит долгих ответов), а сборка
   передвигается к нужному месту. Плеер — обычный ``<video>``: hls.js или родной HLS Safari.

Ограничение «не больше двух ffmpeg» соблюдается: рабочий поток один, в цепочке один ffmpeg куска и один
кодировщик. Пока он работает, общий прокси заморожен, куски редактора ждут (``preview_proxy.stream_busy``).

Запись удалили: сборка идёт с общего прокси (те же секунды), нет и его — честное сообщение и готовый рендер.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.settings import settings

log = logging.getLogger(__name__)

PIPELINE_VERSION = 1
SHORT_SIDE = 480
MAX_FPS = 30
# Качество: быстрый кодировщик и потолок битрейта. На 3 Мбит/с поток со звуком обязан проходить.
X264_PRESET = os.getenv("STREAM_PREVIEW_PRESET", "veryfast")
X264_CRF = os.getenv("STREAM_PREVIEW_CRF", "24")
MAX_RATE_KBPS = int(os.getenv("STREAM_PREVIEW_MAXRATE_KBPS", "1800") or 1800)
ENCODER_THREADS = int(os.getenv("STREAM_PREVIEW_THREADS", "4") or 4)
CHUNK_SEC = 3.0
MIN_CHUNK_SEC = 0.8
MAX_CHUNK_SEC = 4.5
SEGMENT_WAIT_SEC = 20.0
AUDIO_SEGMENT_SEC = 4
CACHE_MAX_BYTES = int(os.getenv("STREAM_PREVIEW_CACHE_BYTES", str(6 * 1024 ** 3)) or 0)
VIEWER_IDLE_SEC = 30.0
FAIR_RUN_CHUNKS = 8
JOB_KEEP_SEC = 3 * 24 * 3600
BLUR_RE = re.compile(r"boxblur=(\d+):(\d+)")


class Cancelled(Exception):
    """Сборку отменили: правка, закрытый плеер или запрос другого места."""


def root_dir() -> Path:
    raw = os.getenv("PREVIEW_STREAM_DIR", "").strip()
    return Path(raw).resolve() if raw else settings.runtime_dir / "stream_preview"


def chunks_dir() -> Path:
    return root_dir() / "chunks"


def jobs_dir() -> Path:
    return root_dir() / "jobs"


def memo_dir() -> Path:
    return root_dir() / "memo"


def _sha(obj: Any) -> str:
    blob = obj if isinstance(obj, bytes) else json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def preview_size(width: int, height: int) -> tuple[int, int]:
    """Кадр рендера → кадр предпросмотра: короткая сторона 480, те же пропорции, чётные стороны."""
    width, height = int(width or 1080), int(height or 1920)
    k = SHORT_SIDE / max(1, min(width, height))
    return (max(2, round(width * k / 2) * 2), max(2, round(height * k / 2) * 2))


def scale_graph(graph: str, k: float) -> str:
    """Постоянные графа в пикселях кадра: радиус размытия подложки — как на кадре рендера."""
    return BLUR_RE.sub(lambda m: f"boxblur={max(1, round(int(m.group(1)) * k))}:{m.group(2)}", graph)


# ---------------------------------------------------------------- хранилище без записи в базу


class PreviewStore:
    """Хранилище для прохода записи: чтение — из настоящего, клип живёт в памяти, остальное не пишется."""

    _WRITE = ("create_", "update_", "delete_", "set_", "mark_", "finish_", "add_", "save_", "insert_", "upsert_",
              "record_", "replace_", "remove_", "restore_", "capture_")

    def __init__(self, store: Any, source_override: dict[int, str] | None = None) -> None:
        self._store = store
        self._clip: dict = {}
        self._source_override = source_override or {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith(self._WRITE):
            return lambda *a, **k: None
        return getattr(self._store, name)

    def get_source(self, source_id: int) -> dict:
        source = dict(self._store.get_source(source_id))
        path = self._source_override.get(int(source["id"]))
        if path:
            # Запись удалена, идём с прокси: дорожки в нём уже смешаны.
            source.update(local_path=path, audio_tracks=None, audio_mix=None, metadata_json=None, metadata=None)
        return source

    def create_clip(self, source_id: int, **kwargs: Any) -> dict:
        self._clip = {"id": -1, "source_id": source_id, "status": "rendering", "title": kwargs.get("title") or "",
                      "description": kwargs.get("description") or "", **kwargs}
        return dict(self._clip)

    def create_ffmpeg_preset(self, *args: Any, **kwargs: Any) -> dict:
        # Нет ни одного пресета (чистая установка): настоящий рендер создал бы тот же первый.
        return self._store.create_ffmpeg_preset(*args, **kwargs)

    def get_clip(self, clip_id: int) -> dict:
        return dict(self._clip)

    def update_clip(self, clip_id: int, **fields: Any) -> dict:
        self._clip.update(fields)
        return dict(self._clip)

    def finish_clip_render(self, clip_id: int, **fields: Any) -> dict:
        self._clip.update(fields)
        return dict(self._clip)


# ---------------------------------------------------------------- проход записи рецепта


class _Runner:
    """Запуск команд прохода записи: отмена, замер времени, кэш одинаковых команд."""

    def __init__(self, recorder: Any) -> None:
        self.rec = recorder
        self.stages: list[dict] = []

    def _memo_key(self, args: list[str]) -> str | None:
        """Команда повторима, если читает только постоянные файлы (не промежуточные файлы этого прохода)."""
        work = str(self.rec.work)
        inputs = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-i"]
        if work not in args[-1] or not inputs or any(work in p for p in inputs):
            return None
        stamp = []
        for raw in inputs:
            try:
                st = os.stat(raw)
                stamp.append([raw, st.st_size, int(st.st_mtime)])
            except OSError:
                stamp.append([raw])
        return _sha([PIPELINE_VERSION, args[:-1], stamp])

    def ffmpeg(self, args: list[str], timeout: int, cwd: Path | None = None) -> None:
        self._ffmpeg(args, timeout, cwd)
        part = self.rec.parts[-1] if self.rec.parts else None
        if part and part.get("stand_in") == args[-1]:
            # Сколько кадров у куска на самом деле (-frames:v — предел, -shortest и fps решают точнее).
            part["real_frames"] = _probe_frames(Path(args[-1]))

    def _ffmpeg(self, args: list[str], timeout: int, cwd: Path | None = None) -> None:
        self.rec.check_cancel()
        started = time.perf_counter()
        out = args[-1]
        if "libx264" in args and "-preset" not in args:
            # Картинка прохода — чёрная заглушка, её качество никому не нужно.
            args = [*args[:-1], "-preset", "ultrafast", args[-1]]
        key = self._memo_key(args)
        memo = memo_dir() / key[:2] / key if key else None
        frames = "%05d" in out
        if memo is not None and memo.exists():
            if frames:
                for item in memo.iterdir():
                    _link_or_copy(item, Path(out).parent / item.name)
            else:
                _link_or_copy(memo, Path(out))
            self.stages.append({"cmd": "memo", "sec": round(time.perf_counter() - started, 3), "out": Path(out).name})
            return
        run_killable(args, timeout, self.rec.cancel, cwd)
        if memo is not None:
            memo.parent.mkdir(parents=True, exist_ok=True)
            try:
                if frames:
                    tmp = memo.with_name(memo.name + f".{uuid4().hex}")
                    tmp.mkdir()
                    for item in Path(out).parent.iterdir():
                        _link_or_copy(item, tmp / item.name)
                    os.replace(tmp, memo)
                elif Path(out).is_file():
                    tmp = memo.with_name(memo.name + f".{uuid4().hex}")
                    _link_or_copy(Path(out), tmp)
                    os.replace(tmp, memo)
            except OSError:
                pass
        self.stages.append({"cmd": "ffmpeg", "sec": round(time.perf_counter() - started, 3), "out": Path(out).name})

    def compositor(self, args: list[str], timeout: int) -> None:
        self.rec.on_compositor(args)


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def run_killable(args: list[str], timeout: int, cancel: threading.Event | None, cwd: Path | None = None) -> None:
    """Как subprocess.run, но отмена убивает именно этот процесс (по его PID)."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=cwd)
    err: list[bytes] = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)  # type: ignore[union-attr]
    reader.start()
    deadline = time.monotonic() + max(1, int(timeout))
    try:
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if time.monotonic() > deadline:
                raise TimeoutError("ffmpeg превысил время")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    reader.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError((b"".join(err)).decode("utf-8", "replace")[-600:] or "ffmpeg упал")


def _probe_frames(path: Path) -> int:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                           "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60, check=False)
    try:
        return int((proc.stdout or "0").strip().split(",")[0])
    except ValueError:
        return 0


def _probe_colorspace(path: str) -> str | None:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space",
                           "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=60, check=False)
    value = (proc.stdout or "").strip().split("\n")[0].strip()
    return value if value and value != "unknown" else None


def _make_recorder(store: Any, work: Path, cancel: threading.Event, source_override: dict[int, str] | None = None) -> Any:
    """Настоящий рендер на заглушке картинки. Класс внутри функции: базовый тянет весь рендер при импорте."""
    from app.motion_render import compositor_command
    from app.render import ClipRenderService, _preset_with_output, build_ffmpeg_render_args, build_join_args
    from app.transitions import plan_joins

    class Recorder(ClipRenderService):
        def __init__(self) -> None:
            super().__init__(PreviewStore(store, source_override))
            self.work = work
            self.cancel = cancel
            self.parts: list[dict] = []
            self.joins: dict | None = None
            self.freezes: list[dict] = []
            self.comp: dict | None = None
            self.banner: dict | None = None
            self.preset: dict | None = None
            self.real_size = (1080, 1920)
            self.ass: str | None = None
            self.inserts: tuple[list[dict], dict] | None = None
            self.hold_frames = 0
            self.total_frames = 0
            self.notes: list[str] = []
            self.compositor_command = compositor_command

        def check_cancel(self) -> None:
            if self.cancel.is_set():
                raise Cancelled()

        def _clip_dir(self) -> Path:
            return self.work

        def _tmp_dir(self) -> Path:
            return self.work

        def _output_preset(self, preset: dict, source: dict | None, aspect: str | None, end_hold_sec: float = 0.0,
                           layers_format: str | None = None) -> dict:
            out = dict(_preset_with_output(preset, source, aspect, end_hold_sec, layers_format))
            real_w, real_h = int(out.get("output_width") or 1080), int(out.get("output_height") or 1920)
            self.real_size = (real_w, real_h)
            out["output_width"], out["output_height"] = preview_size(real_w, real_h)
            out["fps"] = min(float(out.get("fps") or 30), MAX_FPS)
            self.preset = out
            return out

        def _build_part_args(self, **kw: Any) -> list[str]:
            self.check_cancel()
            self.parts.append({"start_sec": float(kw["start_sec"]), "end_sec": float(kw["end_sec"]), "preset": kw["preset"],
                               "banner": kw.get("banner"), "source_crop": kw.get("source_crop"),
                               "reframe_x": kw.get("reframe_x"), "source_path": str(kw["source_path"]),
                               "stand_in": str(kw["output_path"])})
            return build_ffmpeg_render_args(**{**kw, "preset": {**kw["preset"], "stand_in": True},
                                               "banner": None, "source_crop": None, "reframe_x": None})

        def _build_join_args(self, parts: list[Path], durations: list[float], output_path: Path, transition: dict,
                             **kw: Any) -> list[str]:
            self.joins = {"transition": transition, "durations": list(durations),
                          "overlaps": plan_joins(transition, durations, kw.get("gaps"), kw.get("jump_joins"))}
            return build_join_args(parts, durations, output_path, transition, **kw)

        def _apply_freezes(self, base: Path, preset: dict) -> None:
            self.freezes = list(preset.get("freezes") or [])
            ClipRenderService._apply_freezes(base, preset)

        def _apply_motion(self, render_id: str, video: Path, preset: dict, banner: dict | None, motion: dict,
                          source: dict | None = None) -> list[str]:
            self.banner = banner
            return super()._apply_motion(render_id, video, preset, banner, motion, source)

        def on_compositor(self, args: list[str]) -> None:
            value = lambda flag: args[args.index(flag) + 1] if flag in args else None  # noqa: E731
            comp_path = Path(value("--comp"))  # type: ignore[arg-type]
            old_work = comp_path.parent
            keep = self.work / "motion-kept"
            shutil.move(str(old_work), str(keep))
            old_work.mkdir(parents=True, exist_ok=True)
            for name in ("--videos", "--anims"):
                if value(name):
                    p = keep / Path(value(name)).name  # type: ignore[arg-type]
                    p.write_text(p.read_text(encoding="utf-8").replace(json.dumps(str(old_work))[1:-1], json.dumps(str(keep))[1:-1]),
                                 encoding="utf-8")
            comp = json.loads((keep / "comp.json").read_text(encoding="utf-8"))
            flags: list[str] = []
            i = args.index("--comp") + 2
            skip = {"--input", "--output", "--crf", "--comp"}
            while i < len(args):
                if args[i] in skip:
                    i += 2
                    continue
                arg = args[i + 1]
                if args[i] in ("--videos", "--anims", "--assets"):
                    arg = str(keep / Path(arg).name)
                flags += [args[i], arg]
                i += 2
            self.comp = {"path": str(keep / "comp.json"), "flags": flags, "command": args[: args.index("--comp")],
                         "data": comp}
            # Рендер ждёт файл от компоновщика: вместо него заглушка кадра композиции.
            video, out = Path(value("--input")), Path(value("--output"))  # type: ignore[arg-type]
            if comp.get("baseHeight"):
                from app.render import _run_ffmpeg

                _run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"crop={comp['width']}:{comp['height']}:0:0",
                             "-c:v", "libx264", "-preset", "ultrafast", "-an", str(out)], 300)
            else:
                shutil.copyfile(video, out)

        def _render_subtitles(self, clip_id: int, input_path: Path, output_path: Path, subtitle_profile_id: int,
                              preset: dict) -> None:
            from app.ingest import probe_media
            from app.render import _apply_subtitle_timing_offset
            from app.subtitles.ass import write_ass_subtitles
            from app.subtitles.timing import normalize_subtitle_timeline

            shutil.copyfile(input_path, output_path)
            full = {**preset, "output_width": self.real_size[0], "output_height": self.real_size[1]}
            ready = self._plan_subtitles_for_clip(clip_id, float(preset.get("fps") or 30))
            if ready is None:
                # Распознавание тут не запускаем: его сделает рендер (Whisper идёт дольше ролика).
                self.notes.append("Субтитры ещё не сделаны: они появятся при рендере.")
                return
            profile = self._subtitle_profile_for_render(clip_id, subtitle_profile_id, full)
            duration = probe_media(input_path).duration_sec
            result = _apply_subtitle_timing_offset(normalize_subtitle_timeline(ready, duration), profile, duration)
            ass = self.work / "subs.ass"
            # Разметка ASS — кадра рендера (PlayRes), libass сам масштабирует на кадр 480p.
            write_ass_subtitles(result, profile, ass, width=self.real_size[0], height=self.real_size[1], fmt=preset.get("format"))
            self.ass = str(ass)

        def _apply_inserts(self, clip: dict, plan: dict, cut_plan: Any = None) -> dict:
            inserts, assets = self._final_inserts(plan, cut_plan)
            self.inserts = (inserts, assets) if inserts else None
            return super()._apply_inserts(clip, plan, cut_plan)

        def _normalize_loudness(self, clip: dict, hold: float | None = None, target: float | None = None) -> dict:
            path = Path(clip.get("output_path") or "")
            before = _probe_frames(path) if path.exists() else 0
            out = super()._normalize_loudness(clip, hold=hold, **({"target": target} if target is not None else {}))
            after = _probe_frames(path) if path.exists() else before
            self.hold_frames = max(0, after - before)
            self.total_frames = after
            return out

        def _apply_cover(self, *args: Any, **kwargs: Any) -> None:
            return None

        def _attach_qc(self, clip: dict, *args: Any, **kwargs: Any) -> dict:
            return clip

    return Recorder()


@dataclass
class Span:
    """Кусок кадров финального ролика: ``solid`` — часть подряд, ``xfade`` — наплыв, ``hold`` — один кадр много раз."""

    kind: str
    frames: int
    part: int = 0
    first: int = 0  # кадр от начала части (у xfade — сколько кадров наплыва пропустить)
    source: dict | None = None  # у hold: откуда взять кадр — Span solid/xfade на 1 кадр

    def to_json(self) -> dict:
        return {"kind": self.kind, "frames": self.frames, "part": self.part, "first": self.first, "source": self.source}


@dataclass
class Chunk:
    index: int
    first: int
    frames: int
    spans: list[Span]
    hash: str = ""
    hold: int = 0  # из них кадров финального стоп-кадра (tpad кодировщика)


def build_timeline(parts: list[dict], overlaps: list[int], fps: float, freezes: list[dict]) -> list[Span]:
    """Кадры основы → кадры ролика: части, наплывы и стоп-кадры в порядке показа."""
    from app.freeze import is_insert, normalize_freezes

    base: list[Span] = []
    for k, part in enumerate(parts):
        left = overlaps[k - 1] if k > 0 else 0
        right = overlaps[k] if k < len(overlaps) else 0
        count = part["frames"] - left - right
        if count > 0:
            base.append(Span("solid", count, k, left))
        if right > 0:
            base.append(Span("xfade", right, k, 0))
    if not freezes:
        return base
    total = sum(s.frames for s in base)

    def take(a: int, b: int) -> list[Span]:
        """Кадры основы [a, b) как список кусков."""
        out: list[Span] = []
        pos = 0
        for s in base:
            lo, hi = max(a, pos), min(b, pos + s.frames)
            if lo < hi:
                out.append(Span(s.kind, hi - lo, s.part, s.first + lo - pos))
            pos += s.frames
        return out

    out: list[Span] = []
    cursor = 0
    for f in normalize_freezes(freezes):
        at = int(round(f["at"] * fps))
        d = int(round(f["duration"] * fps))
        if at >= total - 1:
            continue
        out.extend(take(cursor, at))
        src = take(at, at + 1)
        if src:
            out.append(Span("hold", d, source=src[0].to_json()))
        cursor = at if is_insert(f) else at + d
    out.extend(take(cursor, total))
    return out


def chunk_edges(parts: list[dict], spans: list[Span], fps: float) -> list[int]:
    """Границы чанков (кадры ролика): начала частей и сетка секунд записи, чтобы подрезка части
    не сдвигала соседние чанки. Внутри наплыва границ нет."""
    edges = {0}
    pos = 0
    step = int(round(CHUNK_SEC * fps))
    for s in spans:
        if s.kind == "solid":
            part = parts[s.part]
            speed = float(part.get("speed") or 1.0)
            if s.first == 0 or pos == 0:
                edges.add(pos)
            src0 = part["start_sec"] + s.first / fps * speed
            k = int(src0 // CHUNK_SEC) + 1
            while True:
                local = (k * CHUNK_SEC - src0) / speed
                frame = int(round(local * fps))
                if frame >= s.frames:
                    break
                if frame > 0:
                    edges.add(pos + frame)
                k += 1
        else:
            edges.add(pos)
        pos += s.frames
    edges.add(pos)
    ordered = sorted(edges)
    # Без коротышей и без слишком длинных; граница не попадает внутрь наплыва, он начинается с границы.
    protected = set()
    p = 0
    for s in spans:
        if s.kind == "xfade":
            protected.update(range(p + 1, p + s.frames))
        p += s.frames
    out = [ordered[0]]
    min_frames = int(MIN_CHUNK_SEC * fps)
    for edge in ordered[1:]:
        if edge in protected:
            continue
        if edge - out[-1] < min_frames and edge != ordered[-1]:
            continue
        out.append(edge)
    if len(out) > 2 and out[-1] - out[-2] < min_frames:
        out.pop(-2)
    # Длинные дробим: части без сетки (реверс, замедление).
    final = [out[0]]
    max_frames = int(MAX_CHUNK_SEC * fps)
    for edge in out[1:]:
        while edge - final[-1] > max_frames:
            mid = final[-1] + step
            if mid in protected:
                break
            final.append(mid)
        final.append(edge)
    return final


def slice_spans(spans: list[Span], a: int, b: int) -> list[Span]:
    out: list[Span] = []
    pos = 0
    for s in spans:
        lo, hi = max(a, pos), min(b, pos + s.frames)
        if lo < hi:
            first = s.first + lo - pos if s.kind != "hold" else 0
            out.append(Span(s.kind, hi - lo, s.part, first, s.source))
        pos += s.frames
    return out


# ---------------------------------------------------------------- рецепт


def _encoder_profile() -> dict:
    return {"v": PIPELINE_VERSION, "preset": X264_PRESET, "crf": X264_CRF, "maxrate": MAX_RATE_KBPS}


def part_graph(part: dict, recipe: dict) -> tuple[str, list[str]]:
    """Граф картинки части (тот же код, что у рендера) и входы баннера."""
    from app.render import _banner_input_args, _render_filter, _retimed

    preset = part["preset"]
    width, height, fps = recipe["width"], recipe["height"], recipe["fps"]
    graph = _render_filter(width, height, fps, preset, part.get("banner"), part.get("source_crop"), part.get("reframe_x"))
    if preset.get("piece_retime"):
        graph = _retimed(graph, {**preset, "piece_retime": {**preset["piece_retime"], "has_audio": False}}, fps)
    graph = scale_graph(graph, recipe["width"] / recipe["real_width"])
    return graph, (_banner_input_args(part["banner"]) if part.get("banner") else [])


def _comp_slice(comp: dict, t0: float, t1: float, frame0: int, frames: int, fps: float) -> dict:
    """Что из композиции видно в окне: слой вне окна на этот чанк не влияет."""
    from app.freeze import motion_time

    freezes = comp.get("freezes")
    if freezes:
        lo = motion_time(t0, [{"at": f["at"], "duration": f["d"], "audio": "silence" if f["insert"] else "continue",
                               "layers": "freeze" if f["hold"] else "continue"} for f in freezes])
        hi = motion_time(t1, [{"at": f["at"], "duration": f["d"], "audio": "silence" if f["insert"] else "continue",
                               "layers": "freeze" if f["hold"] else "continue"} for f in freezes])
    else:
        lo, hi = t0, t1
    eps = 2.0 / fps
    near = lambda a, b: a <= hi + eps and b >= lo - eps  # noqa: E731
    layers = [l for l in comp.get("layers") or [] if near(float(l.get("start") or 0), float(l.get("end") or 0))]
    words = [w for w in comp.get("words") or [] if near(float(w.get("start") or 0) - 2, float(w.get("end") or 0) + 2)]
    camera = comp.get("camera") or []
    keys = [k for i, k in enumerate(camera)
            if near(float(k.get("t") or k.get("time") or k.get("start") or 0) - 0, float(camera[i + 1].get("t") or camera[i + 1].get("time") or camera[i + 1].get("start") or 0) if i + 1 < len(camera) else 1e9)]
    voice = {}
    for key, track in (comp.get("voice") or {}).items():
        vfps = float(track.get("fps") or 30)
        t = float(track.get("t0") or 0)
        a, b = max(0, int((lo - t) * vfps) - 2), int((hi - t) * vfps) + 3
        voice[key] = [t, vfps, str(track.get("states") or "")[a:b], (track.get("levels") or [])[a:b]]
    levels = {}
    for key, track in (comp.get("levels") or {}).items():
        vfps = float(track.get("fps") or 30)
        t = float(track.get("t0") or 0)
        a, b = max(0, int((lo - t) * vfps) - 2), int((hi - t) * vfps) + 3
        levels[key] = [t, vfps, (track.get("v") or [])[a:b]]
    rest = {k: v for k, v in comp.items() if k not in ("layers", "words", "camera", "voice", "levels", "chat", "duration")}
    chat = [m for m in comp.get("chat") or [] if float(m.get("t") or m.get("time") or 0) <= hi + eps]
    return {"rest": rest, "layers": layers, "words": words, "camera": keys, "voice": voice, "levels": levels,
            "chat": chat[-60:], "frame0": frame0, "frames": frames}


def _asset_stamp(path: str) -> list:
    try:
        st = os.stat(path)
        return [path, st.st_size, int(st.st_mtime)]
    except OSError:
        return [path]


def _ass_slice(ass: str | None, t0: float, t1: float) -> list[str]:
    """Заголовок ASS и строки, что видны в окне (строка видна и с запасом на fade)."""
    if not ass:
        return []
    out: list[str] = []
    for line in Path(ass).read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            out.append(line)
            continue
        m = re.match(r"Dialogue:\s*\d+,(\d+):(\d+):([\d.]+),(\d+):(\d+):([\d.]+),", line)
        if not m:
            out.append(line)
            continue
        a = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        b = int(m[4]) * 3600 + int(m[5]) * 60 + float(m[6])
        if a <= t1 and b >= t0:
            out.append(line)
    return out


def _chunk_hash(recipe: dict, chunk: Chunk) -> str:
    fps = recipe["fps"]
    t0, t1 = chunk.first / fps, (chunk.first + chunk.frames) / fps
    parts = recipe["parts"]
    spans = []
    time_dep = False

    def part_key(k: int) -> dict:
        part = parts[k]
        return {"graph": part["graph"], "file": part["file_key"], "speed": part.get("speed"), "reverse": part.get("reverse")}

    for s in chunk.spans:
        row: dict[str, Any] = {"kind": s.kind, "frames": s.frames}
        if s.kind == "solid":
            part = parts[s.part]
            # Секунда записи с точностью до кадра: подрезка части не меняет остальные её чанки.
            row.update(part_key(s.part))
            row["src"] = round((part["start_sec"] + s.first / fps * float(part.get("speed") or 1)) * fps)
            if part["time_dep"]:
                row["local"] = s.first
                row["seg"] = [part["start_sec"], part["end_sec"]]
                time_dep = True
        elif s.kind == "xfade":
            row.update(a=part_key(s.part), b=part_key(s.part + 1), skip=s.first,
                       a_src=[parts[s.part]["start_sec"], parts[s.part]["end_sec"]],
                       b_src=[parts[s.part + 1]["start_sec"], parts[s.part + 1]["end_sec"]],
                       overlap=recipe["overlap_frames"][s.part], xfade=recipe.get("xfade"))
        else:
            src = s.source or {}
            row["from"] = src
            if src.get("kind") == "solid":
                row.update(part_key(src.get("part", 0)))
                part = parts[src.get("part", 0)]
                row["src"] = round((part["start_sec"] + src.get("first", 0) / fps) * fps)
        spans.append(row)
    blob: dict[str, Any] = {"profile": _encoder_profile(), "size": [recipe["width"], recipe["height"]], "fps": fps, "spans": spans,
                            "hold": chunk.hold, "color": recipe.get("colorspace")}
    comp = recipe.get("comp")
    if comp:
        window = _comp_slice(comp["data"], t0, t1, chunk.first, chunk.frames, fps)
        blob["comp"] = window
        blob["assets"] = comp.get("asset_stamps")
    if recipe.get("ass"):
        lines = _ass_slice(recipe["ass"], t0, t1)
        blob["ass"] = lines
    if recipe.get("post_banner"):
        blob["banner"] = _asset_stamp(recipe["post_banner"].get("file_path") or "")
        blob["banner_cfg"] = recipe["post_banner"]
    inserts = [i for i in recipe.get("inserts") or [] if i["at"] < t1 and i["at"] + i["duration"] > t0]
    if inserts:
        blob["inserts"] = inserts
    if blob.get("comp") or blob.get("ass") or inserts or time_dep:
        # Содержимое зависит от секунды ролика: чанк годится только на своём месте.
        blob["t0"] = chunk.first
    return _sha(blob)[:32]


def _finish_recipe(rec: Any, plan_id: int, key: str, work: Path, source: dict, final: Path | None, audio_dir: Path | None) -> dict:
    """Рецепт из того, что записал проход: только JSON-данные."""
    preset = rec.preset or {}
    fps = float(preset.get("fps") or 30)
    width, height = int(preset.get("output_width") or 854), int(preset.get("output_height") or 480)
    parts: list[dict] = []
    for raw in rec.parts:
        p = raw["preset"]
        retime = p.get("piece_retime")
        length = raw["end_sec"] - raw["start_sec"]
        speed = float(retime["speed"]) if retime else 1.0
        reverse = bool(retime and (retime.get("segment") or {}).get("reverse"))
        frames = int(raw.get("real_frames") or 0) or max(1, int(length / speed * fps + 0.5))
        src = raw["source_path"]
        parts.append({**raw, "frames": frames, "speed": speed, "reverse": reverse, "file_key": _asset_stamp(src)})
    joins = rec.joins or {}
    overlaps = [int(round(o * fps)) for o in joins.get("overlaps") or []] or [0] * max(0, len(parts) - 1)
    recipe: dict[str, Any] = {
        "version": PIPELINE_VERSION,
        "key": key,
        "clip_plan_id": plan_id,
        "fps": fps,
        "width": width,
        "height": height,
        "real_width": rec.real_size[0],
        "real_height": rec.real_size[1],
        "overlap_frames": overlaps,
        "transition": joins.get("transition"),
        "overlaps": joins.get("overlaps"),
        "freezes": rec.freezes,
        "notes": rec.notes,
        "ass": rec.ass,
        "hold_frames": rec.hold_frames,
        "colorspace": _probe_colorspace(parts[0]["source_path"]) if parts else None,
        "stages": [],
    }
    from app.transitions import TRANSITIONS

    recipe["xfade"] = TRANSITIONS[joins["transition"]["type"]][1] if joins.get("transition") else None
    for part in parts:
        graph, banner_inputs = part_graph(part, recipe)
        part["graph"] = graph
        part["banner_inputs"] = banner_inputs
        part["time_dep"] = bool(part.get("reframe_x")) or "between(t" in graph or "(t" in graph
    if rec.comp:
        comp = dict(rec.comp)
        assets_path = next((comp["flags"][i + 1] for i, f in enumerate(comp["flags"]) if f == "--assets"), None)
        stamps = []
        if assets_path and Path(assets_path).is_file():
            stamps = [_asset_stamp(p) for p in sorted(json.loads(Path(assets_path).read_text(encoding="utf-8")).values())]
        comp["asset_stamps"] = stamps
        recipe["comp"] = comp
        colorspace = recipe["colorspace"]
        # Граф основы компоновщика (с хромакеем) — у него самого, чтобы не разойтись.
        proc = subprocess.run([*comp["command"], "--comp", comp["path"], "--print-base-filter", "1",
                               *(["--colorspace", colorspace] if colorspace else [])],
                              capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError(f"компоновщик не отдал граф основы: {proc.stderr[-300:]}")
        recipe["base_filter"] = proc.stdout.strip()
        if rec.banner:
            recipe["post_banner"] = rec.banner
            recipe["post_banner_preset"] = {k: preset.get(k) for k in ("banner_y_frac", "banner_height_frac", "format", "output_width")}
    if rec.inserts:
        inserts, assets = rec.inserts
        recipe["inserts"] = [{k: v for k, v in i.items()} for i in inserts]
        recipe["insert_assets"] = {str(k): v for k, v in assets.items()}
    spans = build_timeline(parts, overlaps, fps, rec.freezes)
    edges = chunk_edges(parts, spans, fps)
    chunks: list[Chunk] = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        chunks.append(Chunk(i, a, b - a, slice_spans(spans, a, b)))
    if chunks and rec.hold_frames:
        chunks[-1].frames += rec.hold_frames
        chunks[-1].hold = rec.hold_frames
    # Сохранённый вид: пресет кусков больше не нужен (граф уже собран).
    recipe["parts"] = [{k: v for k, v in part.items() if k not in ("preset", "stand_in")} for part in parts]
    recipe["total_frames"] = sum(c.frames for c in chunks)
    recipe["audio"] = str(audio_dir) if audio_dir else None
    for chunk in chunks:
        chunk.hash = _chunk_hash(recipe, chunk)
    recipe["chunks"] = [{"index": c.index, "first": c.first, "frames": c.frames, "hold": c.hold, "hash": c.hash,
                         "spans": [s.to_json() for s in c.spans]} for c in chunks]
    return recipe


def record(store: Any, plan_id: int, key: str, cancel: threading.Event, source_override: dict[int, str] | None = None,
           on_stage: Callable[[str], None] | None = None) -> dict:
    """Настоящий проход рендера на заглушке: рецепт картинки и готовый звук. Ничего не пишет в базу."""
    from app.clip_settings import settings_to_render_kwargs
    from app.heavy_queue import heavy_queue_nested
    from app.render import RENDER_RUNNER

    job_dir = jobs_dir() / key
    work = job_dir / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    rec = _make_recorder(store, work, cancel, source_override)
    runner = _Runner(rec)
    token = RENDER_RUNNER.set(runner)
    started = time.perf_counter()
    try:
        plan = store.get_clip_plan(plan_id, include_segments=False)
        source = rec.store.get_source(int(plan["source_id"]))
        rs = plan.get("render_settings") or {}
        kwargs = settings_to_render_kwargs(rs, _default_subtitle_profile(store)) if rs else {}
        kwargs.pop("cover", None)
        with heavy_queue_nested():
            clip = rec._render_clip_plan_unlocked(plan_id, **kwargs)  # noqa: SLF001
        if clip.get("status") != "succeeded":
            raise RuntimeError(str(clip.get("error") or "проход рендера не удался"))
        final = Path(clip.get("output_path") or "")
        if not final.is_file():
            raise RuntimeError("проход рендера не записал файл")
        if not rec.total_frames:
            rec.total_frames = _probe_frames(final)
        rec.check_cancel()
        if on_stage:
            on_stage("audio")
        audio_dir = _package_audio(final, cancel)
        recipe = _finish_recipe(rec, plan_id, key, work, source, final, audio_dir)
        if (clip.get("error") or "").strip():
            recipe["notes"] = [*recipe["notes"], str(clip["error"])[:300]]
        recipe["record_sec"] = round(time.perf_counter() - started, 2)
        recipe["stages"] = runner.stages
        recipe["real_final_frames"] = rec.total_frames
        # Постоянные файлы рецепта — рядом с рецептом, промежуточные файлы прохода не нужны.
        _persist(recipe, job_dir, work)
        return recipe
    finally:
        RENDER_RUNNER.reset(token)
        shutil.rmtree(work, ignore_errors=True)


def _persist(recipe: dict, job_dir: Path, work: Path) -> None:
    motion_keep = work / "motion-kept"
    if motion_keep.is_dir():
        dest = job_dir / "motion"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(motion_keep), str(dest))
        old, new = str(motion_keep), str(dest)
        for name in os.listdir(dest):
            if name.endswith(".json") and name != "comp.json":
                p = dest / name
                p.write_text(p.read_text(encoding="utf-8").replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]), encoding="utf-8")
        comp = recipe["comp"]
        comp["path"] = str(dest / "comp.json")
        comp["flags"] = [v.replace(old, new) if isinstance(v, str) else v for v in comp["flags"]]
    if recipe.get("ass"):
        dest_ass = job_dir / "subs.ass"
        shutil.copyfile(recipe["ass"], dest_ass)
        recipe["ass"] = str(dest_ass)
    tmp = job_dir / "recipe.json.tmp"
    tmp.write_text(json.dumps(recipe, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, job_dir / "recipe.json")


def _package_audio(final: Path, cancel: threading.Event) -> Path | None:
    """Готовый звук ролика → HLS (fMP4). Папка по содержимому: тот же звук после правки не пакуется заново."""
    from app.render import _has_audio_stream

    if not _has_audio_stream(final):
        return None
    tmp_audio = final.with_name(final.stem + ".audio.m4a")
    run_killable(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vn", "-c:a", "copy", str(tmp_audio)], 120, cancel)
    digest = _file_sha(tmp_audio)[:24]
    tmp_audio.unlink(missing_ok=True)
    out = root_dir() / "audio" / digest
    if (out / "audio.m3u8").is_file():
        return out
    tmp = out.with_name(digest + f".{uuid4().hex[:8]}")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        run_killable(["ffmpeg", "-y", "-v", "error", "-i", str(final.resolve()), "-vn", "-c:a", "copy", "-ss", "0", "-f", "hls", "-hls_time", str(AUDIO_SEGMENT_SEC),
                      "-hls_playlist_type", "vod", "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                      "-hls_segment_filename", "seg%04d.m4s", "audio.m3u8"], 120, cancel, cwd=tmp)
        if out.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            os.replace(tmp, out)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _default_subtitle_profile(store: Any) -> int | None:
    try:
        saved = int(store.get_app_setting_value("default_subtitle_profile_id") or 0)
    except (TypeError, ValueError):
        saved = 0
    if saved:
        return saved
    rows = store.list_subtitle_profiles()
    return rows[0]["id"] if rows else None


# ---------------------------------------------------------------- цепочка картинки


def _shift_input_time(graph: str, local: float) -> str:
    """Кусок читается не с начала, а выражения графа (кадрирование x(t), окошко по времени) считают секунды куска:
    вернуть кадрам эти секунды. Не -itsoffset: с ним ffmpeg перестаёт отбрасывать кадры до точки -ss."""
    count = graph.count("[0:v]")
    if not count:
        return graph
    labels = [f"[in0v{i}]" for i in range(count)]
    for label in labels:
        graph = graph.replace("[0:v]", label, 1)
    head = f"[0:v]setpts=round(PTS+{local:.6f}/TB)" + (f",split={count}" if count > 1 else "") + "".join(labels)
    return f"{head};{graph}"


def _solid_args(recipe: dict, part: dict, first: int, frames: int, sink_filter: str, pix_fmt: str, to_file: str | None = None) -> list[str]:
    fps = recipe["fps"]
    speed = float(part.get("speed") or 1.0)
    local = first / fps * speed
    ss = part["start_sec"] + local
    length = frames / fps * speed + 0.5
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "4",
            "-ss", f"{ss:.3f}", "-t", f"{length:.3f}", "-i", part["source_path"], *part["banner_inputs"]]
    graph = part["graph"].replace("[vout]", "[vbase]") if not sink_filter else part["graph"]
    if local > 0 and part.get("time_dep"):
        graph = _shift_input_time(graph, local)
    if sink_filter:
        graph = f"{graph};[vout]{sink_filter}[vsink]"
        out_label = "[vsink]"
    else:
        out_label = "[vbase]"
    args += ["-filter_complex", graph, "-map", out_label, "-an", "-frames:v", str(frames)]
    if to_file:
        return [*args, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-y", to_file]
    return [*args, "-f", "rawvideo", "-pix_fmt", pix_fmt, "pipe:1"]


def _reversed_file(recipe: dict, part: dict, cancel: threading.Event) -> str:
    """Реверс читает весь кусок: файл части один раз на рецепт, дальше чанки берут кадры из него."""
    folder = jobs_dir() / recipe["key"] / "parts"
    folder.mkdir(parents=True, exist_ok=True)
    name = folder / f"{_sha(part['graph'] + str(part['start_sec']))[:16]}.mp4"
    if name.is_file():
        return str(name)
    tmp = name.with_suffix(f".{uuid4().hex}.mp4")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{part['start_sec']:.3f}",
            "-t", f"{part['end_sec'] - part['start_sec']:.3f}", "-i", part["source_path"], *part["banner_inputs"],
            "-filter_complex", part["graph"], "-map", "[vout]", "-an", "-frames:v", str(part["frames"]),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-y", str(tmp)]
    run_killable(args, 600, cancel)
    os.replace(tmp, name)
    return str(name)


def _span_process(recipe: dict, span: Span, sink_filter: str, pix_fmt: str, cancel: threading.Event, temp: Path) -> tuple[list[str], int]:
    """Команда ffmpeg, что отдаёт кадры этого куска, и сколько кадров пропустить в начале."""
    parts = recipe["parts"]
    fps = recipe["fps"]
    if span.kind == "solid":
        part = parts[span.part]
        if part.get("reverse"):
            path = _reversed_file(recipe, part, cancel)
            return (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{span.first / fps:.3f}", "-i", path,
                     "-vf", sink_filter or "null", "-an", "-frames:v", str(span.frames), "-f", "rawvideo", "-pix_fmt", pix_fmt, "pipe:1"], 0)
        return _solid_args(recipe, part, span.first, span.frames, sink_filter, pix_fmt), 0
    # Наплыв: хвост A и голова B — во временные файлы, склейка — тем же графом, что у рендера.
    from app.transitions import join_picture_filters

    a, b = parts[span.part], parts[span.part + 1]
    overlap = recipe["overlap_frames"][span.part]
    a_file = temp / f"xa-{span.part}.mp4"
    b_file = temp / f"xb-{span.part}.mp4"
    if a.get("reverse"):
        src = _reversed_file(recipe, a, cancel)
        run_killable(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{(a['frames'] - overlap) / fps:.3f}", "-i", src,
                      "-frames:v", str(overlap), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-y", str(a_file)], 300, cancel)
    else:
        run_killable(_solid_args(recipe, a, a["frames"] - overlap, overlap, "", "", to_file=str(a_file)), 300, cancel)
    if b.get("reverse"):
        src = _reversed_file(recipe, b, cancel)
        run_killable(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", src,
                      "-frames:v", str(overlap), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-y", str(b_file)], 300, cancel)
    else:
        run_killable(_solid_args(recipe, b, 0, overlap, "", "", to_file=str(b_file)), 300, cancel)
    dur = overlap / fps
    filters = join_picture_filters(2, [dur, dur], [dur], recipe["transition"], recipe["width"], fps)
    tail = f";[vout]{sink_filter}[vsink]" if sink_filter else ""
    return (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(a_file), "-i", str(b_file),
             "-filter_complex", ";".join(filters) + tail, "-map", "[vsink]" if sink_filter else "[vout]",
             "-an", "-frames:v", str(overlap), "-f", "rawvideo", "-pix_fmt", pix_fmt, "pipe:1"], span.first)


def _merge_spans(chunks: list[dict], recipe: dict) -> list[Span]:
    """Кадры подряд в одной части — один процесс ffmpeg на все чанки прохода."""
    out: list[Span] = []
    for chunk in chunks:
        for raw in chunk["spans"]:
            s = Span(raw["kind"], raw["frames"], raw["part"], raw["first"], raw.get("source"))
            if (out and s.kind == "solid" and out[-1].kind == "solid" and out[-1].part == s.part
                    and out[-1].first + out[-1].frames == s.first):
                out[-1] = Span("solid", out[-1].frames + s.frames, s.part, out[-1].first)
            else:
                out.append(s)
    return out


def _encoder_args(recipe: dict, first_frame: int, frames: int, chunk_frames: list[int], raw_pix: str, hold: int, cwd: Path) -> list[str]:
    from app.montage_assets import build_inserts_args
    from app.render import _banner_input_args, _banner_overlay

    fps = recipe["fps"]
    W, H = recipe["width"], recipe["height"]
    t0 = first_frame / fps
    t1 = (first_frame + frames) / fps
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", raw_pix, "-s", f"{W}x{H}",
            "-r", f"{fps:g}", "-thread_queue_size", "64", "-i", "pipe:0"]
    graph: list[str] = []
    cur = "0:v"
    next_input = 1
    banner = recipe.get("post_banner")
    if banner:
        args += _banner_input_args(banner)
        graph.append(_banner_overlay(banner, recipe.get("post_banner_preset") or {}, H, "1:v", cur))
        cur = "vout"
        next_input = 2
    # Шкала времени ролика: от неё считают субтитры и вставки. Кадры сырые: отметки времени свои.
    # round: setpts обрезает до целого, и 16.9/TB = 506.99999… давало дырку в один кадр посреди прохода.
    graph.append(f"[{cur}]setpts=round(PTS+{t0:.6f}/TB)[vt]")
    cur = "vt"
    if recipe.get("ass"):
        ass = Path(recipe["ass"])
        shutil.copyfile(ass, cwd / "subs.ass")
        graph.append(f"[{cur}]ass=subs.ass[vs]")
        cur = "vs"
    inserts = [i for i in recipe.get("inserts") or [] if i["at"] < t1 and i["at"] + i["duration"] > t0]
    if inserts:
        assets = {int(k): v for k, v in (recipe.get("insert_assets") or {}).items()}
        ins_args = build_inserts_args(Path("pipe:0"), Path("x.mp4"), inserts, assets, W, H, recipe["total_frames"] / fps,
                                      clip_has_audio=False, video_only=True, base_label=f"[{cur}]", first_input=next_input)
        if ins_args:
            i_first = ins_args.index("-i") + 2  # первый -i — сам клип
            i_fc = ins_args.index("-filter_complex")
            args += ins_args[i_first:i_fc]
            graph.append(ins_args[i_fc + 1])
            cur = ins_args[ins_args.index("-map", i_fc) + 1].strip("[]")
    if hold > 0:
        graph.append(f"[{cur}]tpad=stop_mode=clone:stop_duration={hold / fps:.4f}[vh]")
        cur = "vh"
    color = ""
    if recipe.get("comp") and recipe.get("colorspace") == "bt709":
        color = "scale=out_color_matrix=bt709:out_range=tv,"
    graph.append(f"[{cur}]{color}setpts=PTS-STARTPTS,format=yuv420p[venc]")
    # Ключевой кадр — начало каждого чанка, четверть кадра запаса на округление времени.
    edges = []
    pos = 0
    for n in chunk_frames[:-1]:
        pos += n
        edges.append(f"{(pos - 0.25) / fps:.6f}")
    args += ["-filter_complex", ";".join(graph), "-map", "[venc]", "-an", "-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
             "-maxrate", f"{MAX_RATE_KBPS}k", "-bufsize", f"{MAX_RATE_KBPS * 2}k", "-profile:v", "high", "-level:v", "4.0", "-pix_fmt", "yuv420p",
             "-bf", "0", "-g", "100000", "-keyint_min", "1", "-sc_threshold", "0", "-threads", str(ENCODER_THREADS),
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-fflags", "+bitexact", "-flags", "+bitexact"]
    if edges:
        args += ["-force_key_frames", ",".join(edges)]
    args += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    return args


def _read_exact(stream: Any, size: int) -> bytes:
    chunks = []
    left = size
    while left > 0:
        block = stream.read(left)
        if not block:
            break
        chunks.append(block)
        left -= len(block)
    return b"".join(chunks)


def _iter_boxes(stream: Any) -> Iterable[tuple[bytes, bytes]]:
    while True:
        head = _read_exact(stream, 8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        ext = b""
        body_size = size - 8
        if size == 1:
            ext = _read_exact(stream, 8)
            body_size = struct.unpack(">Q", ext)[0] - 16
        body = _read_exact(stream, body_size)
        yield kind, head + ext + body


def _walk(data: bytes, start: int, end: int) -> Iterable[tuple[bytes, int, int]]:
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size < 8 or pos + size > end:
            return
        yield kind, pos + 8, pos + size
        pos += size


def fragment_samples(moof: bytes) -> int:
    """Сколько кадров во фрагменте (сумма trun)."""
    total = 0
    for kind, a, b in _walk(moof, 0, len(moof)):
        if kind != b"moof":
            continue
        for k2, a2, b2 in _walk(moof, a, b):
            if k2 != b"traf":
                continue
            for k3, a3, _b3 in _walk(moof, a2, b2):
                if k3 == b"trun":
                    total += struct.unpack_from(">I", moof, a3 + 4)[0]
    return total


def patch_tfdt(fragment: bytes, decode_time: int, sequence: int) -> bytes:
    """Время начала фрагмента: файл чанка не привязан к месту в ролике, время ставится при отдаче."""
    out = bytearray(fragment)
    for kind, a, b in _walk(fragment, 0, len(fragment)):
        if kind != b"moof":
            continue
        for k2, a2, b2 in _walk(fragment, a, b):
            if k2 == b"mfhd":
                struct.pack_into(">I", out, a2 + 4, sequence)
            if k2 != b"traf":
                continue
            for k3, a3, _b3 in _walk(fragment, a2, b2):
                if k3 == b"tfdt":
                    if out[a3] == 1:
                        struct.pack_into(">Q", out, a3 + 4, decode_time)
                    else:
                        struct.pack_into(">I", out, a3 + 4, decode_time & 0xFFFFFFFF)
    return bytes(out)


def _timescale(init: bytes) -> int:
    i = init.find(b"mdhd")
    if i < 0:
        return 15360
    version = init[i + 4]
    return struct.unpack_from(">I", init, i + 4 + 4 + (16 if version == 1 else 8))[0]


def chunk_path(digest: str) -> Path:
    return chunks_dir() / digest[:2] / f"{digest}.m4s"


def init_path(recipe: dict) -> Path:
    return chunks_dir() / f"init-{recipe['width']}x{recipe['height']}-{recipe['fps']:g}-{_sha(_encoder_profile())[:8]}.mp4"


def run_chain(recipe: dict, chunks: list[dict], cancel: threading.Event, on_chunk: Callable[[dict, float], None],
              stats: dict | None = None) -> None:
    """Один проход цепочки по подряд идущим чанкам: каждый готовый чанк сразу ложится в кэш."""
    if not chunks:
        return
    started = time.perf_counter()
    first_frame = chunks[0]["first"]
    hold = sum(int(c.get("hold") or 0) for c in chunks)
    frames = sum(int(c["frames"]) for c in chunks)
    body_frames = frames - hold
    W, H = recipe["width"], recipe["height"]
    comp = recipe.get("comp")
    band_h = int(comp["data"].get("baseHeight") or H) if comp else H
    raw_pix = "rgba" if comp else "yuv420p"
    in_size = W * band_h * 4 if comp else W * H * 3 // 2
    sink_filter = recipe["base_filter"] if comp else ""
    temp = jobs_dir() / recipe["key"] / f"run-{uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=True)
    procs: list[subprocess.Popen] = []
    errors: list[str] = []

    def spawn(args: list[str], **kw: Any) -> subprocess.Popen:
        proc = subprocess.Popen(args, **kw)
        procs.append(proc)
        return proc

    def stderr_drain(proc: subprocess.Popen, tag: str) -> None:
        def read() -> None:
            for line in iter(proc.stderr.readline, b""):  # type: ignore[union-attr]
                text = line.decode("utf-8", "replace").rstrip()
                if text and not text.startswith("progress "):
                    errors.append(f"{tag}: {text}")
        threading.Thread(target=read, daemon=True).start()

    def kill_all() -> None:
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass

    feeder_error: list[BaseException] = []
    try:
        enc_args = _encoder_args(recipe, first_frame, body_frames, [int(c["frames"]) for c in chunks], "rgba" if comp else "yuv420p", hold, temp)
        enc_cwd = temp if recipe.get("ass") else None  # субтитры фильтр ищет рядом: путь в графе не экранируем
        node: subprocess.Popen | None = None
        if comp:
            node_args = [*comp["command"], "--comp", comp["path"], *comp["flags"], "--stdin-frames", "1", "--stdout-frames", "1",
                         "--from-frame", str(first_frame), "--frame-count", str(body_frames), "--ffmpeg", "ffmpeg"]
            node = spawn(node_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            stderr_drain(node, "компоновщик")
            enc = spawn(enc_args, stdin=node.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, cwd=enc_cwd)
            node.stdout.close()  # type: ignore[union-attr]  # encoder держит свою копию: конец node = конец кодировщика
            sink = node.stdin
        else:
            enc = spawn(enc_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, cwd=enc_cwd)
            sink = enc.stdin
        stderr_drain(enc, "кодировщик")

        def feed() -> None:
            frame = b""
            try:
                for span in _merge_spans([{"spans": c["spans"]} for c in chunks], recipe):
                    if cancel.is_set():
                        return
                    if span.kind == "hold":
                        src = Span(**{k: span.source[k] for k in ("kind", "frames", "part", "first")}) if span.source else None
                        if src is not None:
                            args, skip = _span_process(recipe, src, sink_filter, raw_pix, cancel, temp)
                            proc = spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                            stderr_drain(proc, "кусок")
                            for _ in range(skip + 1):
                                got = _read_exact(proc.stdout, in_size)
                                if len(got) == in_size:
                                    frame = got
                            proc.kill()
                            proc.wait()
                        for _ in range(span.frames):
                            sink.write(frame or bytes(in_size))
                        continue
                    args, skip = _span_process(recipe, span, sink_filter, raw_pix, cancel, temp)
                    proc = spawn(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                    stderr_drain(proc, "кусок")
                    written = 0
                    index = 0
                    while written < span.frames:
                        got = _read_exact(proc.stdout, in_size)
                        if len(got) < in_size:
                            if written == 0 and span.frames > 3:
                                proc.wait()
                                raise RuntimeError(f"ffmpeg куска не отдал кадров (код {proc.returncode}): " + " | ".join(errors[-3:])[:400])
                            break
                        index += 1
                        if index <= skip:
                            continue
                        frame = got
                        sink.write(got)
                        written += 1
                    # Запись короче расчёта: последний кадр держится, шкала времени не плывёт.
                    while written < span.frames:
                        sink.write(frame or bytes(in_size))
                        written += 1
                    if proc.poll() is None:
                        proc.stdout.close()  # type: ignore[union-attr]
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
            except BaseException as exc:  # noqa: BLE001 - сюда же падает отмена (закрытый поток)
                feeder_error.append(exc)
            finally:
                try:
                    sink.close()
                except OSError:
                    pass

        feeder = threading.Thread(target=feed, daemon=True, name="stream-preview-feed")
        feeder.start()

        def watcher() -> None:
            while enc.poll() is None:
                if cancel.is_set():
                    kill_all()
                    return
                time.sleep(0.05)

        threading.Thread(target=watcher, daemon=True).start()
        init = b""
        index = 0
        moof = b""
        init_file = init_path(recipe)
        for kind, box in _iter_boxes(enc.stdout):
            if kind in (b"ftyp", b"moov"):
                init += box
                if kind == b"moov":
                    if not init_file.is_file():
                        init_file.parent.mkdir(parents=True, exist_ok=True)
                        tmp = init_file.with_suffix(f".{uuid4().hex}")
                        tmp.write_bytes(init)
                        os.replace(tmp, init_file)
                continue
            if kind == b"moof":
                moof = box
                continue
            if kind != b"mdat":
                continue
            fragment = moof + box
            count = fragment_samples(moof)
            if index >= len(chunks):
                raise RuntimeError("кодировщик отдал лишний фрагмент")
            chunk = chunks[index]
            if count != int(chunk["frames"]):
                raise RuntimeError(f"чанк {chunk['index']}: кадров {count} вместо {chunk['frames']}")
            path = chunk_path(chunk["hash"])
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{uuid4().hex}")
            tmp.write_bytes(patch_tfdt(fragment, 0, 1))
            os.replace(tmp, path)
            on_chunk(chunk, time.perf_counter() - started)
            index += 1
        code = enc.wait()
        if cancel.is_set():
            raise Cancelled()
        feeder.join(timeout=5)
        if feeder_error and not isinstance(feeder_error[0], (BrokenPipeError, OSError, ValueError)):
            raise RuntimeError(str(feeder_error[0])[:500])
        if code != 0 or index != len(chunks):
            raise RuntimeError(f"кодировщик: код {code}, чанков {index} из {len(chunks)}: " + " | ".join(errors[-6:])[:600])
    except Cancelled:
        raise
    except Exception:
        if cancel.is_set():
            raise Cancelled() from None
        raise
    finally:
        kill_all()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(temp, ignore_errors=True)
        if stats is not None:
            stats["errors"] = errors[-10:]
            stats["processes"] = len(procs)


# ---------------------------------------------------------------- задания и очередь


@dataclass
class Job:
    key: str
    plan_id: int
    state: str = "queued"  # queued | recording | rendering | ready | failed
    recipe: dict | None = None
    error: str | None = None
    focus: int = 0
    last_seen: float = field(default_factory=time.monotonic)
    created: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    run_cancel: threading.Event | None = None
    stage: str = ""
    timeline: dict = field(default_factory=dict)
    source_override: dict[int, str] | None = None
    served_at: float = 0.0
    render_info: dict | None = None


class StreamService:
    """Один рабочий поток на весь сервис: не больше двух ffmpeg."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}
        self._by_plan: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._store: Any = None

    # --- API

    def ensure(self, store: Any, plan_id: int, retry: bool = False) -> Job:
        self._store = store
        key, override = job_key(store, plan_id)
        with self._wake:
            old_key = self._by_plan.get(plan_id)
            if old_key and old_key != key:
                # Правка: старая сборка отменяется, готовые чанки остаются в кэше.
                old = self._jobs.pop(old_key, None)
                if old:
                    old.cancel.set()
                    if old.run_cancel:
                        old.run_cancel.set()
            job = self._jobs.get(key)
            if job is None or (retry and job.state == "failed"):
                job = Job(key=key, plan_id=plan_id, source_override=override)
                recipe = load_recipe(key)
                if recipe is not None:
                    job.recipe = recipe
                    job.state = "rendering"
                self._jobs[key] = job
            self._by_plan[plan_id] = key
            job.last_seen = time.monotonic()
            self._start()
            self._wake.notify_all()
            return job

    def get(self, key: str) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def touch(self, key: str, focus: int | None = None) -> None:
        """Зритель на месте; ``focus`` — чанк, который ему нужен: сборка идёт к нему."""
        with self._wake:
            job = self._jobs.get(key)
            if job is None:
                return
            job.last_seen = time.monotonic()
            if focus is not None and job.recipe and not chunk_ready(job.recipe, focus):
                old = job.focus
                job.focus = focus
                # Перемотка мимо идущей сборки: она встанет и начнёт с нужного места.
                run = job.run_cancel
                if run is not None and not self._covers(job, focus) and old != focus:
                    run.set()
                self._wake.notify_all()

    @staticmethod
    def _covers(job: Job, focus: int) -> bool:
        # Сборка уже рядом с этим местом (чанков пять вперёд — секунды): пусть идёт, иначе туда сразу.
        pos = job.timeline.get("run_pos")
        end = job.timeline.get("run_end")
        return pos is not None and end is not None and pos <= focus < min(end, pos + 5)

    def cancel_all(self) -> None:
        with self._wake:
            for job in self._jobs.values():
                job.cancel.set()
                if job.run_cancel:
                    job.run_cancel.set()
            self._jobs.clear()
            self._by_plan.clear()

    # --- рабочий поток

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="stream-preview")
            self._thread.start()

    def _pick(self) -> Job | None:
        now = time.monotonic()
        best = None
        for job in self._jobs.values():
            if job.cancel.is_set() or job.state in ("ready", "failed"):
                continue
            if now - job.last_seen > VIEWER_IDLE_SEC:
                continue
            # По очереди: зрителей несколько — каждому порция чанков.
            if best is None or job.served_at < best.served_at:
                best = job
        return best

    def _loop(self) -> None:
        from app import preview_proxy

        while True:
            with self._wake:
                job = self._pick()
                if job is None:
                    preview_proxy.stream_busy(False)
                    self._wake.wait(timeout=2.0)
                    continue
            preview_proxy.stream_busy(True)
            try:
                job.served_at = time.monotonic()
                self._step(job)
            except Cancelled:
                pass
            except Exception as exc:  # noqa: BLE001 - в плеере: честно и с кнопкой повтора
                log.warning("stream preview: клип %s", job.plan_id, exc_info=True)
                with self._lock:
                    job.state = "failed"
                    job.error = str(exc)[:500]

    def _step(self, job: Job) -> None:
        if job.recipe is None:
            job.state = "recording"
            started = time.perf_counter()
            job.stage = "base"
            job.recipe = record(self._store, job.plan_id, job.key, job.cancel, job.source_override, lambda s: setattr(job, "stage", s))
            job.timeline["record_sec"] = round(time.perf_counter() - started, 2)
            job.state = "rendering"
            _prune_cache()
        recipe = job.recipe
        chunks = recipe["chunks"]
        while True:
            if job.cancel.is_set():
                raise Cancelled()
            missing = [c["index"] for c in chunks if not chunk_ready(recipe, c["index"])]
            if not missing:
                job.state = "ready"
                job.timeline["ready_at"] = round(time.monotonic() - job.created, 2)
                return
            if time.monotonic() - job.last_seen > VIEWER_IDLE_SEC:
                return  # зритель ушёл: не жжём процессор, готовое останется
            order = [i for i in missing if i >= job.focus] or missing
            start = order[0]
            run = [start]
            with self._lock:
                others = any(j is not job and not j.cancel.is_set() and j.state not in ("ready", "failed")
                             and time.monotonic() - j.last_seen <= VIEWER_IDLE_SEC for j in self._jobs.values())
            limit = FAIR_RUN_CHUNKS if others else len(order)
            for i in order[1:]:
                if i != run[-1] + 1 or len(run) >= limit:
                    break
                run.append(i)
            job.run_cancel = threading.Event()
            job.timeline.update(run_pos=start, run_end=run[-1] + 1)

            def on_chunk(chunk: dict, sec: float) -> None:
                job.timeline["run_pos"] = chunk["index"] + 1
                job.timeline.setdefault("first_chunk_at", round(time.monotonic() - job.created, 2))
                if time.monotonic() - job.last_seen > VIEWER_IDLE_SEC and job.run_cancel:
                    job.run_cancel.set()  # зритель ушёл: готовое останется, дальше не собираем
                with self._wake:
                    self._wake.notify_all()

            try:
                run_chain(recipe, [chunks[i] for i in run], _either(job.cancel, job.run_cancel), on_chunk)
            except Cancelled:
                if job.cancel.is_set():
                    raise
                # Сдвинули место сборки: идём заново от нового места, готовое уже в кэше.
                continue
            finally:
                job.run_cancel = None
            if others:
                return  # порция готова: следующая — другому зрителю


def _either(a: threading.Event, b: threading.Event) -> threading.Event:
    """Событие, что срабатывает от любого из двух (опрос ленивый: читают is_set)."""

    class Either(threading.Event):
        def is_set(self) -> bool:  # type: ignore[override]
            return a.is_set() or b.is_set()

    return Either()


service = StreamService()


# ---------------------------------------------------------------- ключ, рецепт, готовность


def job_key(store: Any, plan_id: int) -> tuple[str, dict[int, str] | None]:
    """Ключ сборки — отпечаток монтажа плюс всё, что меняет рендер извне (пресет, стиль, файл записи)."""
    from app.clip_settings import normalize_render_settings
    from app.playback import revision
    from app.preview_proxy import is_canvas, proxy_path
    from app.render import effective_preset

    plan = store.get_clip_plan(plan_id)
    rs = normalize_render_settings(plan.get("render_settings") or {})
    source = store.get_source(int(plan["source_id"]))
    preset = effective_preset(store, rs.get("preset_id")) or {}
    raw = str(source.get("local_path") or "")
    override = None
    file_id: list[Any] = [raw]
    if not raw or not Path(raw).exists():
        proxy = proxy_path(int(source["id"]))
        if proxy.exists() and not is_canvas(source):
            override = {int(source["id"]): str(proxy)}
            file_id = [str(proxy), proxy.stat().st_size]
    else:
        file_id.append(Path(raw).stat().st_size)
    key = _sha([PIPELINE_VERSION, _encoder_profile(), plan_id, revision(store, plan_id), preset, file_id, source.get("audio_tracks"),
                source.get("content_crop"), source.get("chroma_key"), source.get("focus_preset"), _styles(store, rs)])[:24]
    return key, override


def _styles(store: Any, rs: dict) -> list[Any]:
    """Стиль субтитров и баннер клипа: их правят отдельно от монтажа."""
    out: list[Any] = []
    for getter, value in ((store.get_subtitle_profile, rs.get("sub_id") or _default_subtitle_profile(store)), (store.get_banner, rs.get("banner_id"))):
        try:
            out.append(getter(int(value)) if value else None)
        except (KeyError, TypeError, ValueError):
            out.append(None)
    return out


def has_media(store: Any, plan_id: int) -> bool:
    """Есть ли из чего собирать: запись или её прокси."""
    plan = store.get_clip_plan(plan_id, include_segments=False)
    source = store.get_source(int(plan["source_id"]))
    raw = str(source.get("local_path") or "")
    if raw and Path(raw).exists():
        return True
    from app.preview_proxy import proxy_path

    return proxy_path(int(source["id"])).exists()


def load_recipe(key: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{24}", key or ""):
        return None
    path = jobs_dir() / key / "recipe.json"
    try:
        recipe = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return recipe if recipe.get("version") == PIPELINE_VERSION else None


def chunk_ready(recipe: dict, index: int) -> bool:
    try:
        return chunk_path(recipe["chunks"][index]["hash"]).is_file()
    except IndexError:
        return False


def job_status(job: Job) -> dict:
    recipe = job.recipe
    out: dict[str, Any] = {"key": job.key, "state": job.state, "error": job.error, "stage": job.stage}
    if recipe:
        total = len(recipe["chunks"])
        ready = sum(1 for i in range(total) if chunk_ready(recipe, i))
        if ready == total and job.state != "failed":
            out["state"] = "ready"
        out.update(total=total, ready=ready, duration=round(recipe["total_frames"] / recipe["fps"], 3), notes=recipe.get("notes") or [],
                   playlist=f"/media/stream/{job.key}/master.m3u8", width=recipe["width"], height=recipe["height"])
    return out


def status_for(store: Any, plan_id: int, retry: bool = False) -> dict:
    """Что показывать плееру. Без записи и без прокси — готовый рендер с честной припиской."""
    from app.playback import media_for
    from app.quality_lint import plan_fingerprint

    try:
        key, _override = job_key(store, plan_id)
    except KeyError:
        raise
    if not has_media(store, plan_id) and load_recipe(key) is None:
        plan = store.get_clip_plan(plan_id, include_segments=False)
        source = store.get_source(int(plan["source_id"]))
        try:
            fingerprint = plan_fingerprint(store, plan_id)
        except Exception:  # noqa: BLE001 - только подпись «рендер устарел»
            fingerprint = None
        media = media_for(store, source, plan_id, fingerprint)
        return {"state": "no_source", "message": media.get("message") or "Записи нет: собирать не из чего.",
                "render": media.get("render"), "key": None}
    job = service.ensure(store, plan_id, retry)
    out = job_status(job)
    if job.render_info is None:
        job.render_info = _render_info(store, plan_id)
        _queue_proxy(store, plan_id)
    out["render"] = job.render_info.get("render")
    if "width" not in out:
        out.update(job.render_info.get("frame") or {})
    return out


def _queue_proxy(store: Any, plan_id: int) -> None:
    """Общий прокси записи — запас на случай, если запись удалят: без него собирать будет не из чего.
    Сам по себе он трансляции не мешает: стоит на паузе, пока она собирает (preview_proxy.stream_busy)."""
    from app import preview_proxy

    try:
        plan = store.get_clip_plan(plan_id, include_segments=False)
        preview_proxy.ensure(store.get_source(int(plan["source_id"])))
    except Exception:  # noqa: BLE001 - прокси необязателен
        log.warning("stream preview: прокси клипа %s не поставлен", plan_id, exc_info=True)


def _render_info(store: Any, plan_id: int) -> dict:
    """Готовый рендер (запасной выход плеера) и кадр предпросмотра, пока рецепта нет."""
    from app.clip_settings import normalize_render_settings
    from app.formats import frame_size, normalize_format, settings_render_format
    from app.playback import _latest_render
    from app.quality_lint import plan_fingerprint

    plan = store.get_clip_plan(plan_id, include_segments=False)
    width, height = preview_size(*frame_size(normalize_format(settings_render_format(normalize_render_settings(plan.get("render_settings") or {})))))
    latest = _latest_render(store, plan_id)
    render = None
    if latest:
        try:
            fingerprint = plan_fingerprint(store, plan_id)
        except Exception:  # noqa: BLE001 - только подпись «рендер устарел»
            fingerprint = None
        render = {"url": f"/media/clips/{latest['id']}", "poster": f"/media/clips/{latest['id']}/thumb", "clip_id": latest["id"],
                  "stale": bool(fingerprint and latest.get("plan_fingerprint") and latest["plan_fingerprint"] != fingerprint)}
    return {"render": render, "frame": {"width": width, "height": height}}


# ---------------------------------------------------------------- плейлисты и файлы


def master_playlist(recipe: dict) -> str:
    audio = bool(recipe.get("audio"))
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if audio:
        lines.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="main",DEFAULT=YES,AUTOSELECT=YES,CHANNELS="2",URI="audio.m3u8"')
    codecs = "avc1.640028" + (",mp4a.40.2" if audio else "")
    extra = ',AUDIO="aud"' if audio else ""
    lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={(MAX_RATE_KBPS + 200) * 1000},CODECS="{codecs}",'
                 f'RESOLUTION={recipe["width"]}x{recipe["height"]},FRAME-RATE={recipe["fps"]:.3f}{extra}')
    lines.append("video.m3u8")
    return "\n".join(lines) + "\n"


def video_playlist(recipe: dict) -> str:
    fps = recipe["fps"]
    chunks = recipe["chunks"]
    target = max((int(c["frames"]) / fps for c in chunks), default=1.0)
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS",
             f"#EXT-X-TARGETDURATION:{int(target + 0.999)}", "#EXT-X-MEDIA-SEQUENCE:0", '#EXT-X-MAP:URI="init.mp4"']
    for c in chunks:
        lines.append(f"#EXTINF:{int(c['frames']) / fps:.5f},")
        lines.append(f"v/{c['index']}.m4s")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def audio_playlist(recipe: dict) -> str | None:
    folder = recipe.get("audio")
    if not folder:
        return None
    text = (Path(folder) / "audio.m3u8").read_text(encoding="utf-8")
    text = text.replace('URI="init.mp4"', 'URI="a/init.mp4"')
    return re.sub(r"^(seg\d+\.m4s)$", r"a/\1", text, flags=re.M)


def segment_bytes(recipe: dict, index: int) -> bytes | None:
    """Файл чанка со временем его места в ролике."""
    chunk = recipe["chunks"][index]
    path = chunk_path(chunk["hash"])
    try:
        data = path.read_bytes()
    except OSError:
        return None
    try:
        os.utime(path)  # для вытеснения старых из кэша
    except OSError:
        pass
    init = init_path(recipe)
    scale = _timescale(init.read_bytes()) if init.is_file() else 15360
    return patch_tfdt(data, round(int(chunk["first"]) * scale / recipe["fps"]), index + 1)


async def wait_segment(job: Job, index: int) -> bytes | None:
    """Ждать чанк не дольше ``SEGMENT_WAIT_SEC``: дольше nginx и браузеры держать не должны."""
    deadline = time.monotonic() + SEGMENT_WAIT_SEC
    recipe = job.recipe
    if recipe is None:
        return None
    service.touch(job.key, index)
    while time.monotonic() < deadline:
        if chunk_ready(recipe, index):
            return segment_bytes(recipe, index)
        if job.state == "failed" or job.cancel.is_set():
            return None
        service.touch(job.key)
        await asyncio.sleep(0.1)
    return None


def _prune_cache() -> None:
    """Кэш не растёт без предела: сверх него уходят давно не нужные чанки и старые рецепты."""
    now = time.time()
    with service._lock:  # noqa: SLF001
        active = set(service._jobs)  # noqa: SLF001
    if jobs_dir().is_dir():
        for folder in jobs_dir().iterdir():
            try:
                if folder.name not in active and now - folder.stat().st_mtime > JOB_KEEP_SEC:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass
    if not CACHE_MAX_BYTES:
        return
    rows = []
    total = 0
    for folder in (chunks_dir(), memo_dir()):
        if not folder.is_dir():
            continue
        for base, _dirs, files in os.walk(folder):
            for name in files:
                if name.startswith("init-"):
                    continue
                path = Path(base) / name
                try:
                    st = path.stat()
                except OSError:
                    continue
                total += st.st_size
                rows.append((st.st_mtime, st.st_size, path))
    if total <= CACHE_MAX_BYTES:
        return
    for _mtime, size, path in sorted(rows, key=lambda r: r[0]):
        if total <= CACHE_MAX_BYTES * 0.8:
            break
        path.unlink(missing_ok=True)
        total -= size


def remove_for_plan(plan_id: int) -> None:
    """Клип удалили: задания нет, кэш по содержимому уйдёт сам."""
    with service._lock:  # noqa: SLF001
        key = service._by_plan.pop(plan_id, None)  # noqa: SLF001
        job = service._jobs.pop(key, None) if key else None  # noqa: SLF001
    if job:
        job.cancel.set()
