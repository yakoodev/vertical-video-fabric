"""Смотреть монтаж: цепочка картинки — процессы, кодировщик, фрагменты mp4 (из ``app/stream_preview.py``)."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.stream_preview_record import Cancelled, chunks_dir, jobs_dir, _sha, run_killable
from app.stream_preview_recipe import X264_PRESET, X264_CRF, MAX_RATE_KBPS, Span, _encoder_profile

ENCODER_THREADS = int(os.getenv("STREAM_PREVIEW_THREADS", "4") or 4)


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
                                      clip_has_audio=False, video_only=True, base_label=f"[{cur}]", first_input=next_input,
                                      fps=fps)
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
