"""Смотреть монтаж: рецепт — таймлайн, куски, хэши, запись (из ``app/stream_preview.py``)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.stream_preview_record import (
    PIPELINE_VERSION, root_dir, jobs_dir, _sha, scale_graph, _Runner, run_killable, _probe_frames,
    _probe_colorspace, _make_recorder
)

# Качество: быстрый кодировщик и потолок битрейта. На 3 Мбит/с поток со звуком обязан проходить.
X264_PRESET = os.getenv("STREAM_PREVIEW_PRESET", "veryfast")
X264_CRF = os.getenv("STREAM_PREVIEW_CRF", "24")
MAX_RATE_KBPS = int(os.getenv("STREAM_PREVIEW_MAXRATE_KBPS", "1800") or 1800)


CHUNK_SEC = 3.0
MIN_CHUNK_SEC = 0.8
MAX_CHUNK_SEC = 4.5


AUDIO_SEGMENT_SEC = 4


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
