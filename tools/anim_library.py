"""Библиотека движений VRM-героя: CC0-паки (Quaternius UAL 1/2, KayKit) + папка «входящие» → клипы node/vrm.

    python tools/anim_library.py --packs D:/vvf-offload/packs3d --out D:/vvf-offload/vtubika/vrm/vendor/anim
        [--inbox D:/vvf-data/runtime/anim_inbox] [--only wave,cheer]

Каталог имён — ``node/vrm/animlib.json`` (имя → пак, клип, русское название, теги, слой full/upper). Каждый клип
переносится на скелет VRM (``tools/anim_retarget.py``) и пишется в ``OUT/<имя>.json``; рядом — ``index.json``
(что есть, по-русски, длительность, источник, лицензия) и ``LICENSES.txt`` (откуда и под какой лицензией каждый пак).

Производные клипы (``from``): клип библиотеки + добавка поворотов (``add``, градусы XYZ в осях модели, как ``bones``
позы node/vrm), колебание (``sway``: частота и амплитуды по костям) и скорость (``speed``) — «грустная» и «злая»
версии покоя без отдельной записи.

Входящие (``--inbox``): владелец кладёт любой гуманоидный FBX/GLB (Mixamo «Without Skin» или с моделью, VRoid, свой
мокап) — клип(ы) переносятся в библиотеку под именем файла (``in_<имя>``), исходник уезжает в ``imported/``. FBX
читается three.js FBXLoader (node/vrm/fbx_dump.mjs) — в нейтральный вид, дальше тот же перенос. Mixamo-файлы в
репозиторий и в раздачу не попадают (условия Adobe): только перенесённый клип в данных сервиса.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import anim_retarget as R  # noqa: E402

CATALOG = ROOT / "node" / "vrm" / "animlib.json"
FBX_DUMP = ROOT / "node" / "vrm" / "fbx_dump.mjs"
INBOX_EXT = {".fbx", ".glb", ".gltf"}
CMU_NOTE = ("CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu): данные бесплатны для любого использования, "
            "включая коммерческое; нельзя перепродавать сами данные.")


def catalog() -> dict:
    return json.loads(CATALOG.read_text("utf-8"))


def euler_q(deg: list[float]) -> np.ndarray:
    """Углы XYZ (градусы) → кватернион, как THREE.Euler(..., "XYZ")."""
    x, y, z = (math.radians(v) / 2 for v in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return np.array([sx * cy * cz + cx * sy * sz, cx * sy * cz - sx * cy * sz,
                     cx * cy * sz + sx * sy * cz, cx * cy * cz - sx * sy * sz])


def derive(base: dict, entry: dict, fps: int = 30) -> dict:
    """Производный клип: скорость, добавка поворотов, колебание (всё — в осях модели, справа, как Rig.euler)."""
    speed = float(entry.get("speed") or 1.0)
    n0 = base["frames"]
    n = max(1, int(round((n0 - 1) / speed)) + 1)
    src_t = np.arange(n) * speed
    out = {**base, "name": entry["name"], "frames": n, "source": f"{base.get('source', '')} → {entry['name']}"}
    loop = bool(entry.get("loop"))

    def resample(arr: np.ndarray, rot: bool) -> np.ndarray:
        if n == n0:
            return arr
        f = np.clip(src_t, 0, n0 - 1)
        i = np.minimum(np.floor(f).astype(int), n0 - 2) if n0 > 1 else np.zeros(n, int)
        k = f - i
        if n0 == 1:
            return np.repeat(arr, n, 0)
        a, b = arr[i], arr[i + 1]
        return R.qslerp(a, b, k) if rot else a + (b - a) * k[:, None]

    bones = {}
    sway = entry.get("sway") or {}
    hz = float(sway.get("hz") or 0)
    dur = (n - 1) / fps
    if loop and hz and dur > 0:
        hz = max(1, round(hz * dur)) / dur  # целое число колебаний за клип — петля без шва
    for b, qs in base["bones"].items():
        q = resample(np.array(qs, float), True)
        add = (entry.get("add") or {}).get(b)
        if add:
            q = R.qmul(q, np.broadcast_to(euler_q(add), q.shape))
        amp = (sway.get("bones") or {}).get(b)
        if amp and hz:
            s = np.sin(2 * math.pi * hz * np.arange(n) / fps)
            q = R.qmul(q, np.stack([euler_q([a * v for a in amp]) for v in s]))
        bones[b] = [[round(float(v), 5) for v in x] for x in R.qnorm(q)]
    for b, add in (entry.get("add") or {}).items():
        if b not in bones:
            bones[b] = [[round(float(v), 5) for v in euler_q(add)]] * n
    out["bones"] = bones
    out["root"] = [[round(float(v), 4) for v in p] for p in resample(np.array(base["root"], float), False)]
    return out


def describe(clip: dict, entry: dict, license_: str, source: str) -> dict:
    return {"name": entry["name"], "ru": entry.get("ru") or entry["name"], "tags": entry.get("tags") or [],
            "layer": entry.get("layer", "full"), "loop": bool(entry.get("loop")), "frames": clip["frames"],
            "duration": round((clip["frames"] - 1) / clip.get("fps", 30), 3), "file": f"anim/{entry['name']}.json"
            if not entry.get("file") else entry["file"], "source": source, "license": license_}


def build(packs: Path, out: Path, only: set[str] | None = None, fps: int = 30, cmu: Path | None = None) -> list[dict]:
    cat = catalog()
    out.mkdir(parents=True, exist_ok=True)
    skels: dict[str, dict] = {}
    built: dict[str, dict] = {}
    index: list[dict] = []
    for e in cat["clips"]:
        if only and e["name"] not in only and not any(x.get("from") == e["name"] for x in cat["clips"] if x["name"] in only):
            continue
        if e.get("pack") == "cmu":
            f = (cmu or out.parent) / e["file"]
            if f.is_file():
                clip = json.loads(f.read_text("utf-8"))
                index.append(describe(clip, e, "CMU mocap (free for all uses)", "CMU Mocap"))
            continue
        if e.get("from"):
            base = built.get(e["from"]) or json.loads((out / f"{e['from']}.json").read_text("utf-8"))
            clip = derive(base, e, fps)
            pk = cat["packs"][next(x for x in cat["clips"] if x["name"] == e["from"])["pack"]]
        else:
            pk = cat["packs"][e["pack"]]
            path = packs / pk["dir"] / pk["file"]
            if e["pack"] not in skels:
                if not path.is_file():
                    print(f"нет пака {path} — {e['name']} пропущен (tools/fetch_packs.py --only {pk['dir']})", file=sys.stderr)
                    continue
                skels[e["pack"]] = R.load_skeleton(path, fps)
            sk = skels[e["pack"]]
            if e["clip"] not in sk["clips"]:
                print(f"в {pk['file']} нет клипа {e['clip']} — {e['name']} пропущен", file=sys.stderr)
                continue
            clip = R.retarget(sk, e["clip"])
            clip["name"] = e["name"]
        clip["loop"] = bool(e.get("loop"))
        built[e["name"]] = clip
        (out / f"{e['name']}.json").write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
        index.append(describe(clip, e, pk["license"], pk["title"]))
        print(f"{e['name']}: {clip['frames']} кадров")
    return index


def fbx_to_neutral(src: Path, fps: int = 30) -> dict:
    proc = subprocess.run(["node", str(FBX_DUMP), str(src), "--fps", str(fps)], capture_output=True, text=True,
                          encoding="utf-8", timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"fbx_dump: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s or "clip"


def import_inbox(inbox: Path, out: Path, fps: int = 30) -> list[dict]:
    """Входящие FBX/GLB → клипы in_<имя>[_<клип>]; исходник — в inbox/imported, ошибка — в inbox/failed/*.txt."""
    inbox.mkdir(parents=True, exist_ok=True)
    done = inbox / "imported"
    failed = inbox / "failed"
    added: list[dict] = []
    for src in sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in INBOX_EXT):
        try:
            skel = fbx_to_neutral(src, fps) if src.suffix.lower() == ".fbx" else R.load_skeleton(src, fps)
            skel["source"] = src.name
            m = R.map_bones([n["name"] for n in skel["nodes"]], [n["parent"] for n in skel["nodes"]])
            clips = [c for c in skel["clips"] if not R.TPOSE_RE.match(c)] or list(skel["clips"])
            if not clips:
                raise ValueError("в файле нет анимаций")
            for c in clips:
                name = "in_" + slug(src.stem) + ("" if len(clips) == 1 else "_" + slug(c))
                clip = R.retarget(skel, c, mapping=m)
                clip["name"] = name
                n = clip["frames"]
                clip["loop"] = bool(re.search(r"loop|idle|walk|run|dance", f"{src.stem} {c}", re.I))
                (out / f"{name}.json").write_text(json.dumps(clip, separators=(",", ":")), "utf-8")
                entry = {"name": name, "ru": f"{src.stem} ({c})" if len(clips) > 1 else src.stem, "tags": ["свой", "входящие"],
                         "layer": "full", "loop": clip["loop"]}
                lic = "Mixamo (Adobe): можно в видео, нельзя раздавать файлы" if "mixamo" in json.dumps(
                    [n["name"] for n in skel["nodes"]]).lower() else "свой файл владельца"
                added.append(describe(clip, entry, lic, f"входящие: {src.name}"))
                print(f"{name}: {n} кадров (из {src.name})")
            done.mkdir(exist_ok=True)
            shutil.move(str(src), str(done / src.name))
        except Exception as exc:  # noqa: BLE001 - один битый файл не валит остальные
            failed.mkdir(exist_ok=True)
            (failed / f"{src.name}.txt").write_text(f"{type(exc).__name__}: {exc}\n", "utf-8")
            print(f"{src.name}: не перенёсся — {exc}", file=sys.stderr)
    return added


def write_index(out: Path, entries: list[dict], *, merge_imported: bool = True) -> Path:
    imported_path = out / "imported.json"
    imported = json.loads(imported_path.read_text("utf-8")) if imported_path.is_file() else []
    fresh_in = [e for e in entries if e["name"].startswith("in_")]
    if fresh_in:
        names = {e["name"] for e in fresh_in}
        imported = [e for e in imported if e["name"] not in names] + fresh_in
        imported_path.write_text(json.dumps(imported, ensure_ascii=False, indent=1), "utf-8")
    base = [e for e in entries if not e["name"].startswith("in_")]
    if not base and (out / "index.json").is_file():
        base = [e for e in json.loads((out / "index.json").read_text("utf-8"))["clips"] if not e["name"].startswith("in_")]
    allc = base + (imported if merge_imported else [])
    (out / "index.json").write_text(json.dumps({"clips": allc}, ensure_ascii=False, indent=1), "utf-8")
    return out / "index.json"


def write_licenses(out: Path) -> Path:
    cat = catalog()
    used: dict[str, list[str]] = {}
    for e in cat["clips"]:
        key = e.get("pack") or next((x.get("pack") for x in cat["clips"] if x["name"] == e.get("from")), "")
        used.setdefault(key, []).append(e["name"])
    lines = ["Библиотека движений VRM-героя (tools/anim_library.py, каталог node/vrm/animlib.json).", ""]
    for key, pk in cat["packs"].items():
        lines += [f"{pk['title']}", f"  автор: {pk['author']}", f"  лицензия: {pk['license']} "
                  "(https://creativecommons.org/publicdomain/zero/1.0/) — можно в коммерческих видео, указывать автора не обязательно",
                  f"  источник: {pk['url']} (файл {pk['dir']}/{pk['file']}, tools/fetch_packs.py)",
                  f"  клипы: {', '.join(used.get(key, []))}", ""]
    lines += ["CMU Mocap (typing — ссылка на cmu/clips/typing.json)", f"  {CMU_NOTE}", "",
              "Входящие (in_*): файлы владельца из папки anim_inbox. Mixamo (Adobe): клипы можно использовать в видео без",
              "  отчислений; нельзя раздавать сами файлы персонажей и анимаций и обучать на них ИИ — поэтому они только здесь,",
              "  в данных сервиса, не в репозитории.", ""]
    path = out / "LICENSES.txt"
    path.write_text("\n".join(lines), "utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="библиотека движений VRM")
    ap.add_argument("--packs", type=Path, default=Path("D:/vvf-offload/packs3d"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--inbox", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--inbox-only", action="store_true")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    if not a.inbox_only:
        entries += build(a.packs, a.out, set(a.only.split(",")) if a.only else None, a.fps)
        write_licenses(a.out)
    if a.inbox:
        entries += import_inbox(a.inbox, a.out, a.fps)
    if a.only and not a.inbox_only and (a.out / "index.json").is_file():
        old = {e["name"]: e for e in json.loads((a.out / "index.json").read_text("utf-8"))["clips"]}
        old.update({e["name"]: e for e in entries})
        entries = list(old.values())
    print(write_index(a.out, entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
