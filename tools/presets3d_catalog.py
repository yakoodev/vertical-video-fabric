"""Каталог 3D-пресетов картинками: по кадру на пресет → лист категории ``catalog_<категория>.jpg``.

    python tools/presets3d_catalog.py --category look [--only clay,noir] [--out D:/vvf-offload/tmp/presets3d]
    python tools/presets3d_catalog.py --category all
    python tools/presets3d_catalog.py --sheets-only          # только пересобрать листы из готовых кадров

Каждый пресет — одна сцена-образец (``reference_spec``: та же опорная сцена категории, меняется только пресет)
→ ``node/scene3d/render.mjs --preview`` (GPU). Камера, переходы и игра персонажа — полоской из 3 кадров (начало,
середина, конец). Перед каждым рендером — проверка свободной памяти (меньше ~6 ГБ — ждём) и общий замок
``<out>/.render.lock``: на машине рендерят и другие агенты, браузер с WebGL — по одному.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app import presets3d  # noqa: E402

OUT = Path(os.environ.get("PRESETS3D_OUT", "D:/vvf-offload/tmp/presets3d"))
RENDER = ROOT / "node" / "scene3d" / "render.mjs"
PACKS = os.environ.get("MOTION_PACKS_DIR", "D:/vvf-offload/packs3d")
FONT = ROOT / "vendor" / "fonts" / "GolosText-Bold.ttf"
FONT2 = ROOT / "vendor" / "fonts" / "Inter-Regular.ttf"
W, H = 540, 960
MIN_FREE_KB = 6_000_000


def free_kb() -> int:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-c", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return int(out or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 10**9


def wait_memory(log=print) -> None:
    for _ in range(40):
        kb = free_kb()
        if kb >= MIN_FREE_KB:
            return
        log(f"свободно {kb // 1024} МБ < {MIN_FREE_KB // 1024} МБ — жду 150 с")
        time.sleep(150)


class RenderLock:
    """Замок «один WebGL-браузер за раз» (файл с PID; старше 20 мин — брошенный)."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 1200:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                time.sleep(2)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def run_render(args: list[str], out_dir: Path, timeout: float = 600) -> dict:
    wait_memory()
    with RenderLock(OUT / ".render.lock"):
        proc = subprocess.run(["node", str(RENDER), *args, "--gl", "gpu", "--packs", PACKS], capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[-800:])
    line = (proc.stdout or "").strip().splitlines()[-1]
    return json.loads(line)


# ---------- опорные сцены ----------

HERO = {"id": "hero", "style": "voxel", "voxel": {"shape": "soft", "top": "#7c6cf0", "accent": "#ffd166", "hair": "#3b2a35",
                                                "pants": "#2f3446", "headphones": "#22242c", "glow": "#9a8cff"}, "at": "hero"}


def _props_showcase() -> list[dict]:
    return [
        {"id": "ball", "primitive": "sphere", "position": [0.62, 0, 0.55], "params": {"size": [0.34], "color": "#ef6f6c", "roughness": 0.35}},
        {"id": "cube", "primitive": "rounded_box", "position": [-0.66, 0, 0.45], "rotation": [0, 25, 0],
         "params": {"size": [0.42, 0.42, 0.42], "color": "#4c9be8", "radius": 0.05}},
        {"id": "cone", "primitive": "cone", "position": [-0.25, 0, 0.95], "params": {"size": [0.22, 0.3], "color": "#ffc857", "segments": 24}},
    ]


def base_spec(duration: float = 4.0) -> dict:
    return {"width": W, "height": H, "fps": 30, "duration": duration, "characters": [json.loads(json.dumps(HERO))]}


def _in_room(s: dict, room: str, *, look: str, light: str | None) -> dict:
    """Опорная сцена «герой в окружении room» с общим планом из suggest.camera окружения."""
    sug = (presets3d.get("room", room) or {}).get("suggest") or {}
    cam = {"move": "static", "target": "hero:chest", "distance": 4.2, "height": 0.6, "yaw": 25, **(sug.get("camera") or {})}
    s["presets"] = {"room": room, "look": look, "camera": [cam], **({"light": light} if light else {})}
    return s


def reference_spec(category: str, name: str) -> tuple[dict, list[float]]:
    """Сцена-образец пресета и моменты кадров (1 кадр — пресет-«картинка», 3 — движение)."""
    s = base_spec()
    look_room = {"room": "studio_cyclorama"}
    if category == "look":
        # Один и тот же уютный стол и тот же кадр — меняется только стиль (свет — родной для стиля).
        light = {"neon_cyber": "monitor_glow", "vhs_found": "monitor_glow", "noir": "moonlight", "psx": "moonlight"}.get(name, "golden_hour")
        s["presets"] = {"look": name, "light": light, "room": "cozy_desk",
                        "camera": [{"move": "static", "target": "hero:chest", "distance": 3.7, "height": 0.55, "yaw": -14, "fov": 40}]}
        return s, [1.5]
    if category == "light":
        # Схема в «своём» окружении (улица — лес, ночь города — крыша, экран — RGB-комната…) и родным общим планом.
        room = {"golden_hour": "forest_clearing", "overcast_soft": "forest_clearing", "noon_sun": "forest_clearing",
                "campfire": "forest_clearing", "moonlight": "city_rooftop", "night_neon": "city_rooftop", "studio_3pt": "studio_cyclorama",
                "stage_spot": "void_stage", "horror_lamp": "kitchen_night", "monitor_glow": "gamer_rgb", "sunrise_window": "cozy_desk",
                "underwater": "forest_clearing"}.get(name, "studio_cyclorama")
        return _in_room(s, room, look="anime_soft", light=name), [1.5]
    if category == "camera":
        s["objects"] = _props_showcase()
        mv: dict = {"move": name, "t0": 0, "t1": 4, "target": "hero"}
        if name == "over_shoulder":
            s["characters"].append({**json.loads(json.dumps(HERO)), "id": "friend", "at": None, "position": [0.25, 0, 2.5], "yaw": 180,
                                    "voxel": {**HERO["voxel"], "top": "#ef8354", "hair": "#c98b4b"}})
            mv = {"move": name, "t0": 0, "t1": 4, "who": "hero", "target": "friend"}
        if name == "whip_pan":
            mv["to"] = [1.8, 1.0, 0.4]
        s["presets"] = {"look": "anime_soft", "light": "studio_3pt", **look_room, "camera": [mv]}
        return s, [0.05, 2.0, 3.95]
    if category == "fx":
        room, light = {"rain": ("city_rooftop", "night_neon"), "snow": ("city_rooftop", "moonlight"), "dust_motes": ("cozy_desk", "sunrise_window"),
                       "god_rays": ("cozy_desk", "sunrise_window"), "fireflies": ("forest_clearing", "campfire"), "lens_flare": ("forest_clearing", "golden_hour"),
                       "light_leak": ("forest_clearing", "golden_hour"), "fog_layers": ("forest_clearing", "moonlight"), "bloom": ("gamer_rgb", "monitor_glow"),
                       "depth_of_field": ("cozy_desk", "golden_hour"), "film_grain": ("kitchen_night", "moonlight"),
                       "chromatic": ("gamer_rgb", "night_neon"), "vignette": ("train_station", "moonlight")}.get(name, ("studio_cyclorama", "studio_3pt"))
        s = _in_room(s, room, look="anime_soft", light=light)
        s["presets"]["fx"] = [name]
        if name in ("lens_flare", "god_rays"):
            # Источник в кадре: камера смотрит против света.
            s["presets"]["camera"] = [{"move": "static", "target": "hero:chest", "distance": 3.6, "height": 0.1, "yaw": 55 if name == "lens_flare" else -70, "fov": 42}]
        return s, [2.0]
    if category == "mood":
        room = {"night_rain": "city_rooftop", "foggy_morning": "forest_clearing", "snowy_evening": "city_rooftop", "dusty_attic": "cozy_desk",
                "horror_flicker": "kitchen_night", "dreamy_pastel": "forest_clearing", "summer_noon": "forest_clearing", "cyber_night": "city_rooftop",
                "cozy_evening": "cozy_desk", "campfire_tales": "forest_clearing"}.get(name, "studio_cyclorama")
        s = _in_room(s, room, look="anime_soft", light=None)
        s["presets"]["mood"] = name
        return s, [2.0]
    if category == "room":
        meta = presets3d.get("room", name) or {}
        sug = meta.get("suggest") or {}
        # Общий план окружения: камера из suggest.camera окружения ({move, target, distance, height, yaw, fov}).
        cam = {"move": "static", "target": "hero:chest", "distance": 4.2, "height": 0.6, "yaw": 25, **(sug.get("camera") or {})}
        s["presets"] = {"room": name, "look": sug.get("look", "anime_soft"), "light": sug.get("light", "studio_3pt"), "camera": [cam]}
        if sug.get("fx"):
            s["presets"]["fx"] = sug["fx"]
        if sug.get("mood"):
            s["presets"]["mood"] = sug["mood"]
        return s, [1.5]
    if category in ("emotion", "gesture"):
        meta = presets3d.get(category, name) or {}
        dur = float(meta.get("duration") or 2.0)
        s["duration"] = dur + 1.0
        s["presets"] = {"look": "anime_soft", "light": "studio_3pt", **look_room, "performance": [{"who": "hero", "preset": name, "t": 0.4}],
                        "camera": [{"move": "static", "target": "hero:chest", "distance": 2.6, "height": 0.15, "yaw": -18}]}
        return s, [0.4 + dur * 0.25, 0.4 + dur * 0.5, 0.4 + dur * 0.8]
    if category == "transition":
        s["objects"] = _props_showcase()
        s["presets"] = {"look": "anime_soft", "light": "studio_3pt", **look_room,
                        "camera": [{"move": "static", "t0": 0, "t1": 2, "target": "hero:chest", "distance": 3.4, "yaw": 20},
                                   {"move": "static", "t0": 2, "t1": 4, "target": "hero", "distance": 1.6, "yaw": -30}],
                        "transitions": [{"kind": name, "t": 2.0}]}
        meta = presets3d.get("transition", name) or {}
        d = float(meta.get("duration") or 0.5) or 0.2
        return s, [2.0 - d * 0.3, 2.0, 2.0 + d * 0.3]
    raise ValueError(category)


# ---------- рендер и листы ----------

def render_preset(category: str, name: str, log=print) -> list[Path]:
    spec, times = reference_spec(category, name)
    sdir = OUT / "specs" / category
    sdir.mkdir(parents=True, exist_ok=True)
    spath = sdir / f"{name}.json"
    spath.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    fdir = OUT / "frames" / category / name
    fdir.mkdir(parents=True, exist_ok=True)
    res = run_render(["--spec", str(spath), "--preview", ",".join(f"{t:.3f}" for t in times), "--preview-dir", str(fdir)], fdir)
    files = [Path(p["file"]) for p in res.get("previews", [])]
    still = OUT / "stills" / category / f"{name}.png"
    still.parent.mkdir(parents=True, exist_ok=True)
    if len(files) == 1:
        Image.open(files[0]).save(still)
    else:
        ims = [Image.open(f) for f in files]
        strip = Image.new("RGB", (W * len(ims) // 2 + 8 * (len(ims) - 1), H // 2), "#101014")
        for i, im in enumerate(ims):
            strip.paste(im.resize((W // 2, H // 2), Image.LANCZOS), (i * (W // 2 + 8), 0))
        strip.save(still)
    warn = [w for w in res.get("warnings") or [] if "camera_" not in w]
    log(f"{category}/{name}: {still}" + (f"  ⚠ {warn}" if warn else ""))
    return files


def build_sheet(category: str) -> Path:
    items = [p for p in presets3d.catalog()[category]["presets"]]
    tiles = []
    for p in items:
        f = OUT / "stills" / category / f"{p['name']}.png"
        if f.exists():
            tiles.append((p, Image.open(f).convert("RGB")))
    if not tiles:
        raise SystemExit(f"нет кадров категории {category}")
    tw = 300 if tiles[0][1].width <= W else 3 * 150 + 16
    th = int(tiles[0][1].height * tw / tiles[0][1].width)
    cols = 6 if tw == 300 else 3
    label_h = 64
    rows = (len(tiles) + cols - 1) // cols
    pad = 14
    title_h = 90
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, title_h + rows * (th + label_h + pad) + pad), "#15151b")
    d = ImageDraw.Draw(sheet)
    f_title = ImageFont.truetype(str(FONT), 40)
    f_name = ImageFont.truetype(str(FONT), 19)
    f_id = ImageFont.truetype(str(FONT2), 15)
    d.text((pad, 22), f"3D-пресеты · {presets3d.catalog()[category]['title']} ({category})", font=f_title, fill="#f2f0ea")
    for i, (p, im) in enumerate(tiles):
        x = pad + (i % cols) * (tw + pad)
        y = title_h + (i // cols) * (th + label_h + pad)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
        d.text((x, y + th + 6), p.get("title") or p["name"], font=f_name, fill="#f2f0ea")
        d.text((x, y + th + 32), p["name"] + ("  · слот" if p.get("status") == "slot" else ""), font=f_id, fill="#9ea3b5")
    out = OUT / f"catalog_{category}.jpg"
    sheet.save(out, quality=88)
    return out


def main(argv: list[str] | None = None) -> int:
    global OUT
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--exec" in argv:
        # Любая команда под замком рендера: --exec [--] cmd args… (всё после --exec — команда).
        cmd = argv[argv.index("--exec") + 1:]
        cmd = cmd[1:] if cmd[:1] == ["--"] else cmd
        wait_memory()
        with RenderLock(OUT / ".render.lock"):
            return subprocess.run(cmd, cwd=str(ROOT)).returncode
    ap =argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--category", default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--sheets-only", action="store_true")
    a = ap.parse_args(argv)
    OUT = Path(a.out)
    cats = presets3d.CATEGORIES if a.category == "all" else tuple(a.category.split(","))
    only = {x for x in a.only.split(",") if x}
    for cat in cats:
        names = [n for n in presets3d.names(cat) if not only or n in only]
        if not a.sheets_only:
            for n in names:
                try:
                    render_preset(cat, n)
                except Exception as exc:  # noqa: BLE001 — один плохой пресет не роняет каталог
                    print(f"{cat}/{n}: ОШИБКА {exc}", file=sys.stderr)
        if presets3d.names(cat):
            print("лист:", build_sheet(cat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
