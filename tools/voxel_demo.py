"""Тест воксельного стиля: ~10 с без голоса, 4 кадра, свой three.js-движок (node/scene3d), без платного.

Стример уснул в эфире (крупно, на мониторе чат и «3 зрителя») → телефон на столе: сыплются уведомления, счётчик
бежит до 99 → проснулся, глаза круглые → на мониторе алерт «НОВЫЙ ДОНАТ $5», бюджет $175 → $180 с чипом «+5».
Комната, свет и карточки — общие (``app/voxel3d.py``), герой — в одной из форм (``--shapes block,soft``).

Конвейер: сцена движка → mp4 (``app.scene3d.render_scene``) → субтитры слоем ``captions`` моушн-компоновщика тем же
стилем, что у «Историй» (``app.stories_3d._captions``), ключевые слова цветом → звуки — настоящие записи пака
freesound (``sfx:``) → лист кадров.

    python tools/voxel_demo.py OUT_DIR [--shapes block,soft] [--gl gpu|swiftshader] [--preview 1,3.5,6,8.5]
        [--no-captions]

Пишет ``style_a.mp4``/``sheet_a.jpg`` (block) и ``style_b.mp4``/``sheet_b.jpg`` (soft); печатает время каждого шага.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voxel3d import (  # noqa: E402
    hud_overlay, streamer_room, tag_overlay, voxel_hero,
)

DURATION = 10.0
FPS = 30
CUTS = (0.0, 2.6, 5.0, 7.4, DURATION)
LETTER = {"block": "a", "soft": "b"}
RED, YELLOW, GREEN = "#ff3b4e", "#ffd400", "#2ee86f"

# Субтитры-заглушки (голоса нет): слово, начало, конец, цвет ключевого слова.
WORDS = [
    ("ТЫ", 0.25, 0.5, None), ("УСНУЛ", 0.5, 1.0, RED), ("ПРЯМО", 1.05, 1.4, None), ("В", 1.4, 1.55, None),
    ("ЭФИРЕ.", 1.55, 2.3, None),
    ("А", 2.8, 2.95, None), ("ТАМ", 2.95, 3.3, None), ("99", 3.35, 3.85, YELLOW), ("УВЕДОМЛЕНИЙ.", 3.85, 4.7, None),
    ("СТОП.", 5.35, 5.9, None), ("ЭТО", 6.05, 6.35, None), ("МНЕ?", 6.35, 7.1, RED),
    ("ПЕРВЫЙ", 7.75, 8.15, None), ("ДОНАТ", 8.15, 8.7, GREEN), ("ПЯТЬ", 8.8, 9.15, None),
    ("БАКСОВ", 9.15, 9.8, YELLOW),
]

# Звуки — записи пака freesound (CC0), громкость здесь.
SFX = [
    ("sfx:freesound/whoosh-2.ogg", 2.55, 0.35), ("sfx:freesound/ding.ogg", 2.75, 0.55),
    ("sfx:freesound/pop.ogg", 3.1, 0.45), ("sfx:freesound/pop.ogg", 3.45, 0.45), ("sfx:freesound/pop.ogg", 3.8, 0.45),
    ("sfx:freesound/pop.ogg", 4.15, 0.45), ("sfx:freesound/whoosh-3.ogg", 4.95, 0.35),
    ("sfx:freesound/record-scratch.ogg", 5.25, 0.5), ("sfx:freesound/whoosh-1.ogg", 7.35, 0.35),
    ("sfx:freesound/cha-ching.ogg", 7.8, 0.7), ("sfx:freesound/cash.ogg", 8.25, 0.45),
]

NOTIFS = [
    (0.0, "Т", "Твой стрим смотрят", "3 зрителя", "#8b3dff"),
    (2.75, "+", "+40 подписчиков", "за последний час", "#8b3dff"),
    (3.05, "@", "Вас упомянули", "@sonya.k: он реально спит", "#ff4fd8"),
    (3.3, "+", "+1 подписчик", "max_play подписался", "#8b3dff"),
    (3.55, "▶", "Новый клип с тобой", "«уснул в прямом эфире»", "#ff3b4e"),
    (3.8, "@", "Вас упомянули", "@dimon228: легенда", "#ff4fd8"),
    (4.05, "+", "+120 подписчиков", "рекомендации", "#8b3dff"),
    (4.3, "♥", "1 245 лайков", "на клипе", "#ff3b4e"),
]

CHAT = [
    {"t": 0.0, "user": "max_play", "color": "#7ee0ff", "text": "он спит?))"},
    {"t": 0.0, "user": "dimon228", "color": "#ffd35c", "text": "ау, ты тут"},
    {"t": 0.0, "user": "sonya.k", "color": "#ff8ad8", "text": "zzz"},
    {"t": 1.2, "user": "max_play", "color": "#7ee0ff", "text": "клипаю это"},
    {"t": 2.0, "user": "nik_off", "color": "#9dff7e", "text": "сколько спал?"},
    {"t": 6.2, "user": "dimon228", "color": "#ffd35c", "text": "ПРОСНУЛСЯ"},
    {"t": 8.9, "user": "sonya.k", "color": "#ff8ad8", "text": "на кофе ☕"},
    {"t": 9.4, "user": "max_play", "color": "#7ee0ff", "text": "ахахах"},
]


def shots() -> list[dict]:
    """Ключи камеры по кадрам: крупно, медленные наезды, голландский угол, глубина резкости."""
    k = [
        # 1. Спит: камера у стола справа, низко; голова на руках крупно, за ней — монитор с чатом. Наезд.
        ({"position": [1.02, 1.46, -0.66], "target": [-0.04, 0.92, -1.32], "fov": 47, "roll": -7, "dof": 0.6},
         {"position": [0.86, 1.38, -0.76], "target": [-0.04, 0.92, -1.32], "fov": 44, "roll": -5, "dof": 0.6}),
        # 2. Телефон на столе сверху, голландский угол, наезд.
        ({"position": [0.68, 0.96, -0.76], "target": [0.5, 0.6, -1.0], "fov": 40, "roll": 14, "dof": 1.2},
         {"position": [0.64, 0.92, -0.81], "target": [0.5, 0.6, -1.0], "fov": 38, "roll": 10, "dof": 1.2}),
        # 3. Проснулся: лицо крупно со стороны монитора.
        ({"position": [0.64, 1.34, -1.54], "target": [0.0, 1.08, -0.5], "fov": 52, "roll": 0, "dof": 0.8},
         {"position": [0.5, 1.31, -1.46], "target": [0.0, 1.12, -0.5], "fov": 47, "roll": -4, "dof": 0.8}),
        # 4. Монитор через плечо: алерт доната.
        ({"position": [0.46, 1.42, -0.36], "target": [0.0, 0.95, -1.43], "fov": 42, "roll": 4, "dof": 0.8},
         {"position": [0.3, 1.22, -0.85], "target": [0.0, 0.95, -1.43], "fov": 38, "roll": 6, "dof": 0.8}),
    ]
    keys: list[dict] = []
    for n, (a, b) in enumerate(k):
        t0, t1 = CUTS[n], CUTS[n + 1]
        k0 = {"t": t0, **a}
        if n:
            k0["cut"] = True
        keys += [k0, {"t": t1, **b, "ease": "inOutSine"}]
    return keys


def build_spec(shape: str) -> dict:
    """Сцена движка на 10 с: комната стримера, герой формы ``shape``, экраны, HUD."""
    loc = streamer_room(
        chat=CHAT,
        viewers=[{"t": 0, "value": 3, "label": "3 зрителя"}, {"t": 8.9, "value": 4, "label": "4 зрителя"}],
        alerts=[{"t": 7.8, "dur": 2.4, "title": "НОВЫЙ ДОНАТ", "amount": "$5", "note": "на кофе, чтобы не уснул"}],
        phone={"notifications": [{"t": t, "app": a, "title": ti, "text": tx, "color": c} for t, a, ti, tx, c in NOTIFS],
               "counter": [{"t0": 0.0, "t1": 0.01, "from": 3, "to": 3}, {"t0": 2.7, "t1": 4.8, "from": 3, "to": 99}]},
    )
    x, z, yaw = loc["spots"]["chair"]
    hero = voxel_hero(
        "hero", shape=shape, position=[x, 0, z], yaw=yaw, seat=loc["seat"],
        actions=[{"clip": "Sleep_Desk_Loop", "start": 0.0, "end": 5.4, "fade": 0.2},
                 {"clip": "Startle", "start": 5.2, "end": 6.4, "fade": 0.18},
                 {"clip": "Sit_Loop", "start": 6.0, "end": DURATION + 0.5, "fade": 0.45}],
        # Проснулся — смотрит в камеру (у монитора), потом снова в экран.
        poses=[{"start": 5.6, "end": 7.4, "fade": 0.3, "rotate": {"Head": [-4, -14, 0], "neck_01": [0, -6, 0]}}],
        faces=[{"t": 0, "face": "sleep"}, {"t": 5.3, "face": "wide"}, {"t": 7.4, "face": "happy"}],
    )
    return {"version": 1, "width": 1080, "height": 1920, "fps": FPS, "duration": DURATION,
            "environment": loc["environment"], "room": loc["room"], "lights": loc["lights"], "objects": loc["objects"],
            "characters": [hero],
            "overlays": [tag_overlay("ЧАСТЬ 1"), hud_overlay([(0.0, 175), (8.25, 180)])],
            "camera": {"fov": 40, "keys": shots(), "clamp": False,
                       "fill": {"color": "#c9b4ff", "intensity": 0.35, "distance": 2.5}},
            "quality": {"shadows": True, "shadowSize": 1024, "antialias": True}}


def caption_layers() -> tuple[list[dict], list[dict]]:
    """Слой субтитров стилем «Историй» (app.stories_3d._captions) и слова для comp.words; ключевые — цветом."""
    from app.stories_3d import _captions
    from app.stories_library import DEFAULT_CAPTIONS

    props = _captions({"captions": dict(DEFAULT_CAPTIONS)})
    words = [{"t0": a, "t1": b, "w": w} for w, a, b, _ in WORDS]
    props["highlightWords"] = [{"i": i, "color": c} for i, (_, _, _, c) in enumerate(WORDS) if c]
    layer = {"id": "caps", "type": "captions", "start": 0.0, "end": DURATION, "x": 0.5,
             "y": float(DEFAULT_CAPTIONS["y"]), "props": props}
    return [layer], words


def burn_captions(video: Path, out: Path, work: Path) -> None:
    from app.motion_render_comp import build_compositor_args, write_comp

    layers, words = caption_layers()
    comp = write_comp(work / "comp.json", width=1080, height=1920, fps=FPS, duration=DURATION, layers=layers,
                      camera=[], words=words)
    (work / "assets.json").write_text("{}", "utf-8")
    args = build_compositor_args(comp, work / "assets.json", input_path=video, output_path=out)
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"субтитры: {proc.stderr.strip()[-600:]}")


def mix_sfx(video: Path, out: Path) -> list[str]:
    """Звуки пака поверх ролика (тем же сведением, что у audio-слоёв клипа)."""
    from app.motion import asset_key
    from app.motion_assets import resolve_pack_ref
    from app.motion_render import build_audio_layers_args

    layers, assets, notes = [], {}, []
    for n, (ref, t, vol) in enumerate(SFX):
        path = resolve_pack_ref(ref)
        if path is None:
            notes.append(f"нет звука {ref}")
            continue
        assets[asset_key(ref)] = str(path)
        layers.append({"id": f"sfx{n}", "type": "audio", "start": t, "end": min(DURATION, t + 2.5),
                       "props": {"asset_id": ref, "volume": vol, "mode": "effect"}})
    args = build_audio_layers_args(video, out, layers, assets, DURATION, clip_has_audio=False, speechless=True,
                                   notes=notes)
    if not args:
        out.write_bytes(video.read_bytes())
        return notes + ["звуков нет"]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"звуки: {proc.stderr.strip()[-400:]}")
    return notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="воксельный тест: 4 кадра, ~10 с")
    ap.add_argument("out_dir")
    ap.add_argument("--shapes", default="block,soft")
    ap.add_argument("--gl", default=os.environ.get("SCENE3D_GL", "gpu"), choices=["gpu", "swiftshader"])
    ap.add_argument("--preview", help="только кадры PNG: секунды через запятую")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--spec-only", action="store_true", help="только записать сцены JSON")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    from app.scene3d import contact_sheet, preview_frames, render_scene

    summary: dict = {}
    for shape in [s.strip() for s in args.shapes.split(",") if s.strip()]:
        letter = LETTER[shape]
        spec = build_spec(shape)
        (out_dir / f"scene_{letter}.json").write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
        if args.spec_only:
            continue
        if args.preview:
            times = [float(x) for x in args.preview.split(",")]
            res = preview_frames(spec, times, out_dir / f"preview_{letter}", gl=args.gl, check_assets=False)
            summary[shape] = [{"t": r["t"], "file": r["file"], "luma": r["luma"]} for r in res]
            continue
        started = time.time()
        raw = out_dir / f"style_{letter}.3d.mp4"
        info = render_scene(spec, raw, workers=1, gl=args.gl, check_assets=False)
        t3d = time.time() - started
        final = out_dir / f"style_{letter}.mp4"
        with tempfile.TemporaryDirectory(prefix="voxel-", dir=out_dir) as tmp:
            work = Path(tmp)
            capped = work / "caps.mp4"
            t1 = time.time()
            if args.no_captions:
                capped.write_bytes(raw.read_bytes())
            else:
                burn_captions(raw, capped, work)
            tcap = time.time() - t1
            notes = mix_sfx(capped, final)
        raw.unlink(missing_ok=True)
        sheet = contact_sheet(final, out_dir / f"sheet_{letter}.jpg", count=8, cols=4, width=360)
        summary[shape] = {"video": str(final), "sheet": str(sheet), "render3d_sec": round(t3d, 1),
                          "captions_sec": round(tcap, 1), "total_sec": round(time.time() - started, 1),
                          "frames": info.get("frames"), "luma_min": info.get("luma_min"),
                          "warnings": info.get("warnings"), "notes": notes}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
