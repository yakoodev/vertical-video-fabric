# -*- coding: utf-8 -*-
"""Clip library from source films: scan (cached), shots, sub-clips of long shots, normalized scores.

sources.json format:
  [{"film": "spring", "path": "D:/.../spring.mp4", "exclude": [[0, 12.5], [440, 9999]], "credit": "...",
    "acts": [[0, 30, "life"], [30, 52, "bond"]], "box": [0, 138, 1920, 804]}]
`exclude` drops title cards, credits and logos (seconds of the source). `acts` (optional) tags source ranges with
a story act; a clip takes the act of its middle (the plan's story map then restricts each song part to its acts)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import shots


def letterbox(path: str | Path, samples: int = 12) -> tuple[int, int, int, int]:
    """Content box (x, y, w, h) of a letterboxed/pillarboxed film, from a few frames (rows/cols that are black in
    all samples are bars)."""
    from . import media
    info = media.probe(path)
    w, h = info["w"], info["h"]
    acc_r = np.zeros(h, np.float32)
    acc_c = np.zeros(w, np.float32)
    n = 0
    for k in range(samples):
        t = info["duration"] * (0.1 + 0.8 * k / max(1, samples - 1))
        fr = next(iter(media.frames(path, w, h, t0=t, dur=0.1, gray=True)), None)
        if fr is None:
            continue
        acc_r = np.maximum(acc_r, fr.max(1).astype(np.float32))
        acc_c = np.maximum(acc_c, fr.max(0).astype(np.float32))
        n += 1
    rows = np.where(acc_r > 28)[0]
    cols = np.where(acc_c > 28)[0]
    if not n or rows.size == 0 or cols.size == 0:
        return 0, 0, w, h
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    # even numbers, and ignore tiny trims
    y0 = y0 if y0 > 4 else 0
    x0 = x0 if x0 > 4 else 0
    y1 = y1 if h - y1 > 4 else h
    x1 = x1 if w - x1 > 4 else w
    return x0 // 2 * 2, y0 // 2 * 2, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2


def _dir(F: dict, a: int, b: int) -> tuple[float, float]:
    fps = float(F["fps"])
    a, b = max(0, a), min(len(F["tx"]), b)
    if b <= a:
        return 0.0, 0.0
    # camera pan plus a share of the residual motion centroid drift
    tx = float(F["tx"][a:b].mean()) * fps
    ty = float(F["ty"][a:b].mean()) * fps
    return round(tx, 4), round(ty, 4)


def act_at(acts: list | None, t: float) -> str | None:
    """Story act of source time t from [[t0, t1, act], ...] (first match), None if untagged."""
    for a, b, name in acts or []:
        if a <= t < b:
            return name
    return None


def allowed_parts(a: int, b: int, exclude: list, fps: float, pad: int = 2) -> list[tuple[int, int]]:
    """Frame ranges of [a, b) outside the excluded source ranges (seconds), each kept `pad` frames clear."""
    parts = [(a, b)]
    for ea, eb in exclude:
        fa, fb = int(ea * fps) - pad, int(np.ceil(eb * fps)) + pad
        nxt = []
        for x, y in parts:
            if fb <= x or fa >= y:
                nxt.append((x, y))
                continue
            if fa > x:
                nxt.append((x, fa))
            if fb < y:
                nxt.append((fb, y))
        parts = nxt
    return parts


def film_clips(film: str, path: str | Path, F: dict, exclude: list | None = None, max_len: float = 3.0,
               min_len: float = 0.6, acts: list | None = None) -> list[dict]:
    """Shots of one film → clips (long shots are split into windows of <= max_len around motion peaks)."""
    fps = float(F["fps"])
    cuts = shots.detect_cuts(F["hist_d"], fps, min_shot_s=0.4)
    sh = shots.shots_from(F, cuts, src=str(path))
    ex = exclude or []
    out = []
    for s in sh:
        if s["dur"] < min_len:
            continue
        L = int(max_len * fps)
        windows = []
        for a, b in allowed_parts(s["a"], s["b"], ex, fps):      # a shot that touches an excluded range is trimmed
            n = b - a
            if n < min_len * fps:
                continue
            windows += [(a, b)] if n <= L else [(a + k, min(b, a + k + L))
                                                for k in range(0, n - int(min_len * fps), int(L * 0.8))]
        for wi, (wa, wb) in enumerate(windows):
            if (wb - wa) / fps < min_len:
                continue
            f = shots.shot_features(F, wa, wb)
            sc = shots.interest(f)
            k = int(0.3 * fps)
            out.append({
                "id": f"{film}:{s['a']}:{wi}", "film": film, "src": str(path),
                "t_in": round(wa / fps, 4), "t_out": round(wb / fps, 4), "dur": round((wb - wa) / fps, 3),
                "t_peak": round(f["peak"] / fps, 4), "motion": f["motion"], "peak_val": f["peak_val"],
                "camera": f["camera"], "luma": f["luma"], "contrast": f["contrast"], "sat": f["sat"],
                "cx": f["cx"], "score": sc,
                # share of near-identical consecutive frames (holds, static shots) — reads as a freeze when slowed
                "static_frac": round(float((F["pix_d"][wa + 1:wb] < 0.7).mean()) if wb - wa > 2 else 1.0, 3),
                "dir_in": _dir(F, wa + 1, wa + 1 + k), "dir_out": _dir(F, wb - k, wb),
                "act": act_at(acts, (wa + wb) / 2 / fps),
            })
    return out


def best_window(profile: np.ndarray, frac: float, prior: float = 0.15) -> float:
    """Centre (0..1) of the window of relative width frac with the largest profile sum (+ a mild centre prior)."""
    n = len(profile)
    k = max(1, min(n, int(round(frac * n))))
    if k >= n:
        return 0.5
    cs = np.concatenate([[0.0], np.cumsum(profile, dtype=np.float64)])
    sums = cs[k:] - cs[:-k]
    centres = (np.arange(len(sums)) + k / 2) / n
    sums = sums / (sums.max() + 1e-9) - prior * np.abs(centres - 0.5) * 2
    return float(centres[int(np.argmax(sums))])


def focus_map(img: np.ndarray) -> np.ndarray:
    """Where the in-focus detail is (Laplacian energy, blurred) — CG films keep the subject sharp and the
    background soft, so this finds characters better than a centre crop."""
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(g, (3, 3), 0), cv2.CV_32F))
    return cv2.GaussianBlur(lap, (0, 0), 6)


def skin_mask(img: np.ndarray) -> np.ndarray:
    """Skin-tone pixels (HSV box + YCrCb box) — a cheap «a character is here» cue for CG films."""
    import cv2
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    ycc = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    m1 = cv2.inRange(hsv, (0, 35, 70), (22, 190, 255))
    m2 = cv2.inRange(ycc, (60, 138, 85), (255, 175, 128))
    return cv2.erode((m1 & m2), np.ones((3, 3), np.uint8)) > 0


VENDOR = Path(__file__).resolve().parents[2] / "vendor"
ANIMALS = set(range(14, 24))   # COCO bird … giraffe
_det: dict = {}


def detect_subjects(img: np.ndarray) -> dict:
    """People/animals (YOLOX-nano via app.object_detect) and faces (YuNet), both vendored ONNX models.

    Returns {"char": 0..1 presence, "faces": n, "x": subject centre 0..1 or None}."""
    import os
    import cv2
    os.environ.setdefault("YOLOX_MODEL", str(VENDOR / "yolox_nano.onnx"))
    from app import object_detect as od
    h, w = img.shape[:2]
    if "yn" not in _det and (VENDOR / "yunet.onnx").exists():
        _det["yn"] = cv2.FaceDetectorYN.create(str(VENDOR / "yunet.onnx"), "", (w, h), 0.6, 0.3, 50)
    faces = []
    if "yn" in _det:
        _det["yn"].setInputSize((w, h))
        _, f = _det["yn"].detect(img)
        faces = [] if f is None else [(float(r[0] + r[2] / 2) / w, float(r[2] * r[3]) / (w * h), float(r[-1])) for r in f]
    best, presence = None, 0.0
    for o in od.detect_objects(img, 0.3):
        if o["cls"] != 0 and o["cls"] not in ANIMALS:
            continue
        area = o["w"] * o["h"]
        v = o["score"] * min(1.0, (area / 0.06) ** 0.5)
        presence = max(presence, v)
        rank = v * (1.3 if o["cls"] == 0 else 1.0)
        if best is None or rank > best[0]:
            best = (rank, o["x"] + o["w"] / 2)
    if faces:
        presence = max(presence, min(1.0, 0.6 + 0.4 * max(sc for _, _, sc in faces)))
        fx = max(faces, key=lambda r: r[1] * r[2])[0]
        x = fx
    else:
        x = best[1] if best else None
    return {"char": round(float(presence), 3), "faces": len(faces), "x": x}


def focus_pass(clips: list[dict], boxes: dict, aspects=(("cx_916", 9 / 16), ("cx_169", 16 / 9))) -> list[dict]:
    """Per clip: sharpness (p90 Laplacian energy of the content) and the best crop centre per output aspect,
    from frames at 30 % / peak / 70 %."""
    from . import media
    for c in clips:
        if "sharp" in c and "char" in c and "thumb" in c:
            continue
        x, y, w, h = boxes.get(c["src"], (0, 0, 0, 0))
        info_w, info_h = (w, h) if w else (media.probe(c["src"])["w"], media.probe(c["src"])["h"])
        tw = 480
        th = max(16, int(round(tw * info_h / max(1, info_w) / 2)) * 2)
        vf = f"crop={w}:{h}:{x}:{y}" if w else ""
        maps, skins, dets = [], [], []
        import cv2
        for t in (c["t_in"] + 0.3 * c["dur"], c["t_peak"], c["t_in"] + 0.7 * c["dur"]):
            fr = next(iter(media.frames(c["src"], 640, max(16, int(round(640 * info_h / max(1, info_w) / 2)) * 2),
                                        t0=max(0.0, t), dur=0.1, vf_pre=vf)), None)
            if fr is not None:
                dets.append(detect_subjects(fr))
                if len(dets) == 1 or t == c["t_peak"]:      # tiny gray thumb for duplicate detection
                    c["thumb"] = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (32, 18),
                                            interpolation=cv2.INTER_AREA).ravel().tolist()
                fr = cv2.resize(fr, (tw, th), interpolation=cv2.INTER_AREA)
                maps.append(focus_map(fr))
                skins.append(skin_mask(fr))
        if not maps:
            c["sharp"], c["skin"], c["char"], c["faces"] = 0.0, 0.0, 0.0, 0.0
            c["cx_916"], c["cx_169"] = c.get("cx", 0.5), 0.5
            continue
        c["char"] = round(float(np.mean([d["char"] for d in dets])), 3) if dets else 0.0
        c["faces"] = round(float(np.mean([d["faces"] for d in dets])), 2) if dets else 0.0
        xs = [d["x"] for d in dets if d["x"] is not None]
        m = np.mean(maps, 0)
        sk = np.mean(skins, 0)
        c["sharp"] = round(float(np.percentile(m, 95)), 2)
        c["skin"] = round(float(sk.mean()), 4)
        # crop centre: in-focus detail, with skin (faces, hands) weighted up
        m = m / (m.max() + 1e-9) + 1.5 * sk
        prof = m.sum(0)
        prof = np.maximum(prof - np.percentile(prof, 20), 0)
        for key, asp in aspects:
            frac = min(1.0, (info_h * asp) / max(1, info_w))
            if xs:      # a detected character / face wins over the focus profile
                c[key] = round(float(np.clip(np.median(xs), frac / 2, 1 - frac / 2)), 3)
            else:
                c[key] = round(best_window(prof, frac), 3)
    return clips


def normalize(clips: list[dict]) -> list[dict]:
    """energy = percentile of (motion + 0.5 camera); impact = percentile of the motion peak; score rescaled 0..1."""
    if not clips:
        return clips
    mot = np.array([c["motion"] + 0.5 * c["camera"] for c in clips])
    pk = np.array([c["peak_val"] for c in clips])
    sc = np.array([c.get("raw_score", c["score"]) for c in clips])
    sh = np.array([c.get("sharp", 0.0) for c in clips])
    r_s = sh.argsort().argsort() / max(1, len(clips) - 1)
    r_m = mot.argsort().argsort() / max(1, len(clips) - 1)
    r_p = pk.argsort().argsort() / max(1, len(clips) - 1)
    s_lo, s_hi = np.percentile(sc, [2, 98])
    for c, e, p, s, q in zip(clips, r_m, r_p, sc, r_s):
        c["raw_score"] = float(s)
        c["energy"] = round(float(e), 3)
        c["impact"] = round(float(p), 3)
        v = 0.7 * float(np.clip((s - s_lo) / max(1e-6, s_hi - s_lo), 0, 1)) + 0.3 * float(q)
        if c["luma"] < 40:          # dark shots read as mud on phones even after the grade
            v *= 0.55 + 0.45 * max(0.0, c["luma"] - 20) / 20
        if q < 0.12:                # out-of-focus / smeared
            v *= 0.6
        v += 0.25 * c.get("char", 0.0)                    # a character (person / animal / face) is visible
        v = min(1.0, v)
        c["sharp_rank"] = round(float(q), 3)
        c["score"] = round(v, 3)
    return clips


def build(sources: list[dict], work: str | Path, progress: bool = True) -> list[dict]:
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    old = {}
    if (work / "clips.json").exists():
        old = {c["id"]: c for c in json.loads((work / "clips.json").read_text(encoding="utf-8"))}
    # "box": [x, y, w, h] fixes the content box by hand (e.g. a trailer whose subtitles sit in the letterbox bar —
    # the auto-detector would count the text rows as picture)
    boxes = {s["path"]: tuple(s["box"]) if s.get("box") else letterbox(s["path"]) for s in sources}
    clips = []
    for src in sources:
        cache = work / f"{src['film']}.scan.npz"
        if cache.exists():
            F = dict(np.load(cache))
        else:
            if progress:
                print(f"scan {src['film']} …", flush=True)
            F = shots.scan_video(src["path"], progress=progress)
            np.savez_compressed(cache, **F)
        cl = film_clips(src["film"], src["path"], F, src.get("exclude"), acts=src.get("acts"))
        for c in cl:
            o = old.get(c["id"])
            if o and "sharp" in o and "char" in o and "thumb" in o:
                c.update({k: o[k] for k in ("sharp", "skin", "char", "faces", "cx_916", "cx_169", "thumb")})
        focus_pass(cl, boxes)
        if progress:
            print(f"  {src['film']}: {len(cl)} clips", flush=True)
        clips += cl
    clips = normalize(clips)
    dup_groups(clips)
    (work / "clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
    (work / "boxes.json").write_text(json.dumps({k: list(v) for k, v in boxes.items()}), encoding="utf-8")
    return clips


def dup_groups(clips: list[dict], thresh: float = 0.9) -> list[dict]:
    """Mark near-identical clips (the same shot cut into several trailers, or a repeated shot) with a shared
    "dup" id: normalized correlation of the 32x18 gray thumbs >= thresh. The assembler uses one per group."""
    idx = [i for i, c in enumerate(clips) if c.get("thumb")]
    if not idx:
        return clips
    import cv2
    T = []
    for i in idx:
        g = cv2.GaussianBlur(np.asarray(clips[i]["thumb"], np.float32).reshape(18, 32), (3, 3), 0).ravel()
        g = g - g.mean()
        T.append(g / (np.linalg.norm(g) + 1e-6) if g.std() > 4 else np.zeros_like(g))   # flat frames never match
    T = np.array(T)
    C = T @ T.T
    parent = list(range(len(idx)))

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in zip(*np.where(np.triu(C, 1) >= thresh)):
        parent[root(a)] = root(b)
    for k, i in enumerate(idx):
        clips[i]["dup"] = clips[idx[root(k)]]["id"]
    return clips


def apply_overrides(clips: list[dict], ov: dict) -> list[dict]:
    """Hand curation after looking at the review sheets:
    {"ban": [clip ids], "ban_films": [films], "boost": {clip id: +score}, "act": {clip id: act},
     "cx_916": {clip id: crop centre 0..1}, "cx_169": {...}, "face": [clip ids with a readable face]}.
    A `face` clip counts as a character on screen (anime faces are not found by the photo-face detector)."""
    ban = set(ov.get("ban", []))
    ban_films = set(ov.get("ban_films", []))
    boost = ov.get("boost", {})
    acts = ov.get("act", {})
    face = set(ov.get("face", []))
    out = []
    for c in clips:
        if c["id"] in ban or c.get("film") in ban_films:
            continue
        upd = {}
        if c["id"] in boost:
            upd.update(score=round(min(1.3, c["score"] + float(boost[c["id"]])), 3), boosted=True)
        if c["id"] in acts:
            upd["act"] = acts[c["id"]]
        if c["id"] in face:
            upd.update(face=True, char=max(c.get("char", 0.0), 0.9))
        for key in ("cx_916", "cx_169"):
            if c["id"] in ov.get(key, {}):
                upd[key] = float(ov[key][c["id"]])
        out.append(dict(c, **upd) if upd else c)
    return out
