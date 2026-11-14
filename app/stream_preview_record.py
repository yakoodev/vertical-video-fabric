"""Смотреть монтаж: каталоги и запись рецепта — проход рендера без записи в сервис (из ``app/stream_preview.py``)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

PIPELINE_VERSION = 1
SHORT_SIDE = 480
MAX_FPS = 30


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
            inserts, assets = self._final_inserts(plan, cut_plan, self._clip_fps(clip))
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
