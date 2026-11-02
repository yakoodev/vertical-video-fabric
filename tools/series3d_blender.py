"""3D-серия в Blender от сценария до mp4 — тем же конвейером, что «Истории» (``engine: 3d, renderer: blender``), но
во временной базе, без сервиса и Docker, с рендером здесь же (``SERIES3D_BLENDER_LOCAL=1``).

    python tools/series3d_blender.py SCRIPT.json|- OUT.mp4 [--sheet SHEET.jpg] [--env ../.env] [--voice Charon]
        [--premise «завязка»: SCRIPT «-» — следующая часть мира из --data, сценарий пишет Gemini] [--max-mb 50]
        [--quality fast|fast720|final|preview|draft] [--workers N] [--lipsync speaking|all|off] [--data DIR]
        [--cache DIR] [--keep] [--reassemble]

Шаги: сценарий → озвучка (Gemini TTS, бесплатно; без ключа — тон) → слова (Whisper) → режиссура
(``app.series3d_blender``: действие/эмоция/план на фразу — Gemini по схеме, иначе правила; события на словах;
лип-синк) → сцена Blender (лифт один на всю серию) → рендер кусками по планам в кэш (``--cache``; упал —
повторный запуск дорисует) → склейка → холст сервиса (субтитры 1–3 слова, HUD, название, «ЧАСТЬ N+1», звуки
событий, тревожный фон) → mp4. ``--env`` — файл KEY=VALUE, берутся только GEMINI_*. ``--data`` с недоделанной
серией — продолжить её (та же озвучка и режиссура, готовые кадры из кэша).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    for line in path.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("GEMINI_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="3D-серия в Blender: сценарий → mp4")
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--sheet")
    ap.add_argument("--env")
    ap.add_argument("--voice", default="")
    ap.add_argument("--quality", default="fast", choices=["final", "fast", "fast720", "preview", "draft"],
                    help="пресет рендера (app.series3d_blender.QUALITY): final — кино, fast — финал быстро, draft — черновик")
    ap.add_argument("--workers", type=int, default=0, help="процессов Blender (0 — из пресета)")
    ap.add_argument("--lipsync", default="speaking", choices=["speaking", "all", "off"])
    ap.add_argument("--data", help="папка временной базы (по умолчанию — временная)")
    ap.add_argument("--cache", help="кэш кадров (по умолчанию D:/vvf-offload/render3d/cache или <data>/render3d_cache)")
    ap.add_argument("--keep", action="store_true", help="не удалять временную базу")
    ap.add_argument("--premise", default="", help="SCRIPT = «-»: сценарий пишет Gemini по завязке (следующая часть мира "
                    "из --data: «ранее» и крючок прошлой части)")
    ap.add_argument("--max-mb", type=float, default=50.0, help="больше — пережать (Telegram/площадки)")
    ap.add_argument("--episode", type=int, default=0, help="--reassemble: какой эпизод (по умолчанию последний готовый)")
    ap.add_argument("--reassemble", action="store_true",
                    help="--data с готовой серией: пересобрать (озвучка та же, перерисуются только изменённые планы)")
    args = ap.parse_args(argv)
    if args.env:
        _load_env(Path(args.env))
    os.environ["SERIES3D_BLENDER_LOCAL"] = "1"
    os.environ.setdefault("STORIES_WORKER", "0")
    data = Path(args.data or tempfile.mkdtemp(prefix="series3d-blender-")).resolve()

    from app.settings import settings

    settings.data_dir = data
    settings.upload_dir = data / "uploads"
    settings.source_dir = data / "sources"
    settings.clip_dir = data / "clips"
    settings.banner_dir = data / "banners"
    settings.subtitle_dir = data / "subtitles"
    settings.tmp_dir = data / "tmp"
    settings.runtime_dir = data / "runtime"
    settings.log_dir = data / "logs"
    hf = Path.home() / ".cache" / "huggingface" / "hub"
    settings.whisper_model_dir = hf if hf.is_dir() else data / "whisper-models"
    settings.aivideo_enabled = False
    settings.polza_api_key = ""   # платного нет: картинок у 3D-серии нет, платные запуски выключены
    settings.ensure_dirs()
    cache = Path(args.cache) if args.cache else (Path("D:/vvf-offload/render3d/cache") if Path("D:/").exists()
                                                 else data / "render3d_cache")
    cache.mkdir(parents=True, exist_ok=True)

    from app import aivideo as V
    from app import aivideo_gen as G
    from app import series3d_blender as SB
    from app import stories as S
    from app import stories_3d as S3D
    from app import stories_gen as X
    from app.crypto import CookieCipher
    from app.db import Database
    from app.render import ClipRenderService
    from app.scene3d import contact_sheet
    from app.store import AppStore

    if not SB.find_blender():
        print(json.dumps({"ok": False, "error": "нет Blender (BLENDER_BIN)"}, ensure_ascii=False))
        return 2
    from app.motion_render import CompositorMissing, compositor_command

    try:   # без компоновщика холст «успешно» отдаёт зелёную основу без слоёв — проверяем до часа рендера
        compositor_command()
    except CompositorMissing as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    # рендер в общий кэш кадров (повторный запуск после правки фразы перерисует только её планы)
    orig_render = SB.render_local

    timing: dict = {}

    def render_cached(job, out, *, work, cache=None, **kw):
        t = time.time()
        kw.setdefault("progress", _printer())
        if args.workers:
            kw.setdefault("workers", args.workers)
        res = orig_render(job, out, work=work, cache=cache_dir, **kw)
        timing.update({"blender_sec": round(time.time() - t, 1), "rendered": res.get("rendered"),
                       "cached": res.get("cached"), "frames": res.get("frames"), "chunks": res.get("chunks"),
                       "per_frame_sec": round(res.get("seconds", 0) / max(1, res.get("rendered") or 1), 2),
                       "report": res.get("report"), "director": job.get("director"),
                       "shots": [{k: s.get(k) for k in ("n", "t0", "t1", "action", "button", "emotion", "camera", "speaks", "text")}
                                 for s in job["shots"]]})
        return res

    cache_dir = cache
    SB.render_local = render_cached

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    script = None if args.script == "-" else json.loads(Path(args.script).read_text("utf-8"))
    started = time.time()
    # Время по этапам (wall-clock): сценарий, озвучка, слова, режиссура, Blender, холст, финальный рендер.
    stages: dict[str, float] = {}

    def timed(module, name: str, key: str) -> None:
        fn = getattr(module, name)

        def wrap(*a, **kw):
            t = time.time()
            try:
                return fn(*a, **kw)
            finally:
                stages[key] = round(stages.get(key, 0.0) + time.time() - t, 1)
        setattr(module, name, wrap)

    from app import stories_gen as SG

    timed(X, "write_script", "script_sec")
    timed(SG, "transcribe_words", "words_sec")
    timed(SB, "build_job", "direction_sec")
    timed(S3D, "assemble", "assemble_sec")
    # Та же папка данных (--data) с недоделанной серией — продолжить её: озвучка и режиссура те же, кадры из кэша.
    old = [e for e in S.list_episodes(store) if e["status"] not in ("done", "cancelled")]
    if not old and args.reassemble:
        done = [e for e in S.list_episodes(store) if e["status"] == "done"
                and (not args.episode or int(e["id"]) == args.episode)]
        if done:   # пересобрать готовую: та же озвучка и режиссура, перерисуются только изменённые планы
            opts = {**(done[0].get("options") or {}), "quality": args.quality}
            S.update_episode(store, int(done[0]["id"]), options=opts)   # другой пресет — та же режиссура
            S.set_status(store, int(done[0]["id"]), "assembling", ("done",))
            old = [S.get_episode(store, int(done[0]["id"]))]
    worlds = store.db.query_all("SELECT id, project_id FROM story_worlds ORDER BY id")
    if not old and script is None:
        if not worlds:
            print(json.dumps({"ok": False, "error": "SCRIPT «-» — нужна --data с миром прошлой части"}, ensure_ascii=False))
            return 2
        w = worlds[0]
        pid = int(w["project_id"])
        options = {"engine": S.ENGINE_3D, "renderer": S.RENDERER_BLENDER, "quality": args.quality,
                   "lipsync": args.lipsync, "format": "disaster", "title_card": True}
        ep = X.create(store, int(w["id"]), args.premise or "продолжение", None, options=options)
        eid = int(ep["id"])
        print(f"новая часть {ep.get('options', {}).get('part')}: эпизод {eid}", flush=True)
    elif old:
        ep = old[0]
        eid = int(ep["id"])
        pid = int(ep["project_id"])
        if ep["status"] == "failed":
            X.resume(store, eid)
        print(f"продолжаю эпизод {eid} ({ep['status']})", flush=True)
    else:
        provider = "polza" if settings.gemini_api_key else "none"   # ярлык провайдера: голос мира — Gemini TTS
        pid = V.create_ai_project(store, "3D-сериал (Blender)", provider=provider)["project"]["id"]
        voice = {"provider": "gemini", **({"voice": args.voice} if args.voice else {})}
        world = S.save_world(store, pid, name="Блэкаут", format=S.FORMAT_DISASTER, voice=voice,
                             narrator="спокойный напряжённый закадровый голос")
        options = {"engine": S.ENGINE_3D, "renderer": S.RENDERER_BLENDER, "quality": args.quality,
                   "lipsync": args.lipsync, "format": "disaster", "title_card": True}
        ep = X.create(store, world["id"], script.get("logline") or script.get("title") or "серия", None, script=script,
                      options=options)
        eid = int(ep["id"])
    status = ""
    for _ in range(60):
        ep = X.advance(store, eid)
        status = ep["status"]
        if status in ("done", "failed", "cancelled"):
            break
        t_tts = time.time()
        res = G.run_pending(store, project_id=pid)   # озвучка (Gemini TTS) — задание этапа генерации
        stages["tts_sec"] = round(stages.get("tts_sec", 0.0) + time.time() - t_tts, 1)
        if not any(res.get(k) for k in ("done", "polling", "waiting")):
            time.sleep(1)
    if status != "done":
        print(json.dumps({"ok": False, "status": status, "error": ep.get("error"), "notes": ep.get("notes")},
                         ensure_ascii=False))
        return 1
    built = time.time()
    clip = ClipRenderService(store).render_clip_plan(int(ep["clip_id"]))
    if clip.get("status") != "succeeded":
        print(json.dumps({"ok": False, "render": clip.get("status"), "error": clip.get("error")}, ensure_ascii=False))
        return 1
    if SB.video_flat(clip["output_path"]):
        print(json.dumps({"ok": False, "error": "готовый клип однотонный (холст без слоёв?)", "clip": clip["output_path"],
                          "notes": clip.get("error") or clip.get("notes")}, ensure_ascii=False))
        return 1
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(clip["output_path"], out)
    except PermissionError:   # файл открыт — пишем рядом
        out = out.with_name(out.stem + f"-{int(time.time())}.mp4")
        shutil.copyfile(clip["output_path"], out)
    if out.stat().st_size > args.max_mb * 1e6:
        # пережать под лимит: битрейт по длине, звук как есть
        import subprocess

        dur = float(ep["timeline"]["duration"])
        kbps = int(args.max_mb * 8e3 * 0.93 / dur) - 192
        tmp = out.with_suffix(".tmp.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-c:v", "libx264", "-preset", "slow",
                        "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k",
                        "-c:a", "copy", "-movflags", "+faststart", str(tmp)], check=True)
        try:
            tmp.replace(out)
        except PermissionError:
            try:   # открыт плеером без права удаления: переписать содержимое на месте
                shutil.copyfile(tmp, out)
                tmp.unlink()
            except PermissionError:   # совсем занят — новый рядом
                out = out.with_name(out.stem + f"-{int(time.time())}.mp4")
                tmp.replace(out)
    final_sec = round(time.time() - built, 1)
    bl = timing.get("blender_sec") or 0.0
    st = {"script_sec": stages.get("script_sec", 0.0), "tts_sec": stages.get("tts_sec", 0.0),
          "words_sec": stages.get("words_sec", 0.0), "direction_sec": stages.get("direction_sec", 0.0),
          "blender_sec": bl,
          "canvas_build_sec": round(max(0.0, stages.get("assemble_sec", 0.0) - bl - stages.get("words_sec", 0.0)
                                        - stages.get("direction_sec", 0.0)), 1),
          "final_render_sec": final_sec, "total_sec": round(time.time() - started, 1)}
    summary = {"ok": True, "out": str(out), "size_mb": round(out.stat().st_size / 1e6, 1),
               "duration": ep["timeline"]["duration"], "assemble_sec": round(built - started, 1),
               "final_render_sec": round(time.time() - built, 1), "total_sec": round(time.time() - started, 1),
               "stages": st, "blender": timing, "music_bed": ep["timeline"].get("music_bed"),
               "title": (ep.get("script") or {}).get("title"), "part": (ep.get("options") or {}).get("part"),
               "part_title": (ep.get("script") or {}).get("part_title"),
               "notes": ep.get("notes"), "data": str(data), "cache": str(cache)}
    if args.sheet:
        rep = (timing.get("report") or {}).get("shots") or []
        if rep and timing.get("shots"):
            raw = settings.tmp_dir / f"series3d-blender-{eid}" / "series.mp4"
            summary["sheet"], summary["seen"], checks = phrase_sheet(out, timing["shots"], rep, Path(args.sheet), raw=raw)
            summary["dark_shots"] = [c["n"] for c in checks if c["dark"]]
            summary["no_hero_shots"] = [c["n"] for c in checks if not c["hero"]]
            summary["ots_bad"] = [c["n"] for c in checks if c["kind"] == "ots" and (not c["hero"] or c["missing"])]
        else:
            summary["sheet"] = str(contact_sheet(out, args.sheet, count=12, cols=6, width=300))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    _ = S3D
    if not args.keep and not args.data:
        shutil.rmtree(data, ignore_errors=True)
    return 0


PLACE_RU = {"cabin": "кабина", "shaft": "шахта", "landing": "площадка", "techfloor": "тех. этаж", "corridor": "коридор"}


DARK_MEAN, DARK_SPREAD = 38.0, 14.0   # область героя: средняя яркость / разброс (0..255) ниже — «ТЕМНО»
FRAME_DARK = 22.0                     # весь кадр темнее — «ТЕМНО»


def luma_stats(video: Path, t: float, box: tuple[float, float, float, float] | None) -> dict:
    """Яркость кадра в момент ``t``: среднее и разброс по всему кадру и по области ``box`` (x0, y0, x1, y1 — доли)."""
    import subprocess

    w, h = 108, 192
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                           "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True, check=False)
    px = proc.stdout
    if len(px) < w * h:
        return {}

    def stats(vals: list[int]) -> tuple[float, float]:
        mean = sum(vals) / max(1, len(vals))
        return round(mean, 1), round((sum((v - mean) ** 2 for v in vals) / max(1, len(vals))) ** 0.5, 1)
    out = {"frame": stats(list(px[: w * h]))}
    if box:
        x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
        vals = [px[y * w + x] for y in range(max(0, y0), min(h, y1)) for x in range(max(0, x0), min(w, x1))]
        if vals:
            out["hero"] = stats(vals)
    return out


def shot_light(video: Path, t: float, rep: dict) -> dict:
    """Проверка плана: виден ли герой (лицо в кадре в пробах сцены) и читается ли (яркость/контраст его области)."""
    xy = rep.get("face_xy")
    hero = bool(xy and 0.0 <= xy[0] <= 1.0 and 0.0 <= xy[1] <= 1.0 and rep.get("face_in", 0) > 0)
    box = None
    if hero:
        # голова и плечи: от макушки до груди, не залезая в полосу субтитров (0.70+)
        box = (max(0.0, xy[0] - 0.18), max(0.0, xy[1] - 0.12), min(1.0, xy[0] + 0.18), min(0.68, xy[1] + 0.16))
    st = luma_stats(video, t, box)
    frame = st.get("frame") or (0.0, 0.0)
    region = st.get("hero") or frame
    dark = frame[0] < FRAME_DARK or region[0] < DARK_MEAN or region[1] < DARK_SPREAD and region[0] < DARK_MEAN * 1.6
    return {"hero": hero, "frame": frame, "region": region, "dark": bool(dark)}


def phrase_sheet(video: Path, shots: list[dict], report: list[dict], sheet: Path, cols: int = 7,
                 raw: Path | None = None) -> tuple[str, str, list[dict]]:
    """Лист «фраза → что в кадре»: кадр середины каждого плана, место, план, названные вещи (видно / НЕТ), герой в
    кадре и свет по его области (яркость/контраст; ТЕМНО — брак) + фраза. ``raw`` — рендер без титров (для замера)."""
    import subprocess
    import tempfile
    import textwrap

    by_n = {r["n"]: r for r in report}
    tmp = Path(tempfile.mkdtemp(prefix="phrase_sheet_"))
    tiles, ok, total = [], 0, 0
    checks: list[dict] = []
    try:
        for s in shots:
            r = by_n.get(s["n"], {})
            seen = [x for x in r.get("show") or [] if not str(x["item"]).startswith("место")]
            vis = [x["visible"] for x in seen if x["visible"] is not None]
            ok, total = ok + sum(1 for v in vis if v), total + len(vis)
            marks = ", ".join(f"{x['item']} {'видно' if x['visible'] else 'НЕТ'}" for x in seen) or "—"
            t_mid = (float(s["t0"]) + float(s["t1"])) / 2
            lit = shot_light(raw if raw and raw.is_file() else video, t_mid, r)
            checks.append({"n": s["n"], "kind": r.get("kind"), **lit,
                           "missing": [x["item"] for x in seen if x["visible"] is False]})
            cap = f"{s['n']}. {PLACE_RU.get(r.get('location') or '', '?')} · {r.get('kind', s.get('camera'))}: {marks}"
            light = (f"герой {'да' if lit['hero'] else 'НЕТ'} · свет {lit['region'][0]:.0f}/{lit['region'][1]:.0f}"
                     + (" ТЕМНО" if lit["dark"] else ""))
            head = textwrap.wrap(cap, 36)[:2] + [light]
            lines = head + textwrap.wrap("«" + str(s.get("text") or "") + "»", 34)[:3]
            vf = "scale=270:-2,pad=270:610:0:0:0x111111"
            for i, line in enumerate(lines):   # строка — свой drawtext: перевод строки в textfile рисуется квадратиком
                cf = tmp / f"c{s['n']}_{i}.txt"
                cf.write_bytes(line.encode("utf-8"))
                esc = cf.as_posix().replace(":", "\\:")
                bad = line is light and (lit["dark"] or not lit["hero"])
                color = "0xff4040" if bad else ("0xffd23f" if i < len(head) else "white")
                vf += (",drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':"
                       f"textfile='{esc}':expansion=none:x=6:y={484 + i * 19}:fontsize=13:fontcolor={color}")
            img = tmp / f"t{s['n']:03d}.jpg"
            t = (float(s["t0"]) + float(s["t1"])) / 2
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                            "-vf", vf, str(img)], check=True)
            tiles.append(img)
        pad = (-len(tiles)) % cols
        labels = [f"[{i}:v]" for i in range(len(tiles))]
        graph = "".join(f"color=0x111111:s=270x610:d=1[p{k}];" for k in range(pad))
        labels += [f"[p{k}]" for k in range(pad)]
        rows = len(labels) // cols
        graph += "".join("".join(labels[r * cols:(r + 1) * cols]) + f"hstack={cols}[r{r}];" for r in range(rows))
        graph += "".join(f"[r{r}]" for r in range(rows)) + f"vstack={rows}[out]" if rows > 1 else ""
        args_ = ["ffmpeg", "-v", "error", "-y"]
        for t_ in tiles:
            args_ += ["-i", str(t_)]
        out_lab = "[out]" if rows > 1 else "[r0]"
        if rows == 1:
            graph = graph.rstrip(";")
        sheet.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(args_ + ["-filter_complex", graph, "-map", out_lab, "-frames:v", "1", "-q:v", "3", str(sheet)],
                       check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(sheet), f"{ok}/{total}", checks


def _printer():
    last = [0.0]

    def cb(p: float, note: str) -> None:
        if time.time() - last[0] > 30 or p >= 1.0:
            last[0] = time.time()
            print(f"  рендер {p * 100:5.1f}% {note}", flush=True)
    return cb


if __name__ == "__main__":
    sys.exit(main())
