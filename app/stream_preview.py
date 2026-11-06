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

Где что лежит. Здесь — служба показа: очередь ``StreamService``, ключ и готовность, плейлисты и файлы. Вынесено: запись рецепта рендером
``app/stream_preview_record.py``, рецепт и куски ``app/stream_preview_recipe.py``, цепочка картинки ``app/stream_preview_chain.py``.
Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.stream_preview_record import (  # noqa: F401
    PIPELINE_VERSION, SHORT_SIDE, MAX_FPS, BLUR_RE, Cancelled, root_dir, chunks_dir, jobs_dir, memo_dir, _sha,
    preview_size, scale_graph, PreviewStore, _Runner, _link_or_copy, run_killable, _probe_frames, _probe_colorspace,
    _make_recorder,
)
from app.stream_preview_recipe import (  # noqa: F401
    X264_PRESET, X264_CRF, MAX_RATE_KBPS, CHUNK_SEC, MIN_CHUNK_SEC, MAX_CHUNK_SEC, AUDIO_SEGMENT_SEC, Span, Chunk,
    build_timeline, chunk_edges, slice_spans, _encoder_profile, part_graph, _comp_slice, _asset_stamp, _ass_slice,
    _chunk_hash, _finish_recipe, record, _persist, _package_audio, _file_sha, _default_subtitle_profile,
)
from app.stream_preview_chain import (  # noqa: F401
    ENCODER_THREADS, _shift_input_time, _solid_args, _reversed_file, _span_process, _merge_spans, _encoder_args,
    _read_exact, _iter_boxes, _walk, fragment_samples, patch_tfdt, _timescale, chunk_path, init_path, run_chain,
)

log = logging.getLogger(__name__)


SEGMENT_WAIT_SEC = 20.0


CACHE_MAX_BYTES = int(os.getenv("STREAM_PREVIEW_CACHE_BYTES", str(6 * 1024 ** 3)) or 0)
VIEWER_IDLE_SEC = 30.0
FAIR_RUN_CHUNKS = 8
JOB_KEEP_SEC = 3 * 24 * 3600


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
