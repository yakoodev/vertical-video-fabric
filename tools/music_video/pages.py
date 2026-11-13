# -*- coding: utf-8 -*-
"""Сцена-страница комикса: план страницы → текстура страницы + кадры камеры по панелям + персонажи, пузыри, звуки.

В плане сцена со страницей выглядит так (координаты внутри панели — доли её ширины/высоты):

    {"id": "toilet", "from": 14.52, "transition": "pageturn|ink|halftone|cut",
     "page": {"width": 2000, "rows": [["toilet", "urinal"], ["approach"], ...]},
     "panels": {"toilet": {"img": "toilet", "focus": [0.5, 0.6]}, "boom": {"burst": {"bg": "yellow"}, "aspect": 1.2},
                "stamp": {"stamp": "ЧИСТАЯ\\nПРАВДА", "aspect": 1.2}, "end": {"tbc": true, "aspect": 0.8}},
     "shots": [{"at": 14.52, "frame": ["toilet"]}, {"at": "w:22", "frame": ["urinal"], "move": "pan|smash|cut|whip"},
               {"at": 20.0, "frame": ["toilet", "urinal"]}, {"at": 21.0, "frame": "page"}],
     "acts": [{"img": "narr", "panel": "boom", "x": 0.5, "y": 0.98, "h": 0.9, "in": 2.1, "anim": "pop",
               "rig": [{"src": [540, 420], "r": 200, "pivot": [440, 400], "rot": 7, "mode": "beat"}]}],
     "texts": [{"panel": "urinal", "from": "w:22", "to": "w:29", "kind": "caption", "x": 0.5, "y": 0.12, "w": 0.85},
               {"panel": "approach", "text": "ПРИВЕТ, Я ВЕГАН!", "at": ["w:37", "w:38", "w:39"], "kind": "speech",
                "x": 0.7, "y": 0.2, "tail": [0.7, 1.4]}],
     "sfx": [{"panel": "door", "text": "ХРЯСЬ!", "at": "w:75", "x": 0.5, "y": 0.4, "h": 0.35}],
     "words": [ … большие слова поверх кадра, как в обычной сцене … ]}"""
from __future__ import annotations

import hashlib
import json

import cv2
import numpy as np

import kit_comic as kc
import kit_page as kpg

S_M = 0.005          # метров на пиксель страницы
SCREEN_AR = 9 / 16


def _h(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def _load_rgb(store, tex: str) -> np.ndarray:
    im = cv2.imread(str(store.root / f"{tex}.png"), cv2.IMREAD_UNCHANGED)
    rgb = im[..., [2, 1, 0]]
    if im.shape[2] == 4:                              # вырезка: подложить лучи, чтобы панель не была пустой
        a = im[..., 3:4].astype(np.float32) / 255
        bg = kc.speed_burst(im.shape[0], im.shape[1], np.random.default_rng(3), "cream", "purple")[..., :3]
        rgb = (rgb * a + bg * (1 - a)).astype(np.uint8)
    return np.ascontiguousarray(rgb)


def build_page(sc: dict, imgs: dict, store, seed: int) -> tuple[str, dict, int, int]:
    """Текстура страницы (кэш по содержимому) и раскладка панелей."""
    pg = sc["page"]
    rows_spec = pg["rows"]
    pspec = sc.get("panels", {})
    key = _h(json.dumps(pg, ensure_ascii=False, sort_keys=True), json.dumps(pspec, ensure_ascii=False, sort_keys=True), seed)
    nm = f"page_{sc['id']}_{key}"
    if store.has(nm):
        m = store.meta[nm]
        return nm, m["layout"], m["w"], m["h"]
    rng = np.random.default_rng(seed)
    rows = []
    for row in rows_spec:
        r = []
        for pid in row:
            spec = pspec.get(pid, {"img": pid})
            if "img" in spec:
                rgb = _load_rgb(store, imgs[spec["img"]])
                if spec.get("trim"):                       # срезать нарисованную нейросетью рамку по краям
                    t = spec["trim"]; hh, ww = rgb.shape[:2]
                    rgb = np.ascontiguousarray(rgb[int(hh * t):int(hh * (1 - t)), int(ww * t):int(ww * (1 - t))])
                asp = spec.get("aspect", rgb.shape[1] / rgb.shape[0])
            else:
                asp = spec.get("aspect", 1.0)
                w = 1400; h = int(w / asp)
                if "stamp" in spec:
                    st = spec.get("colors", {})
                    rgb = kpg.stamp_panel(spec["stamp"], w, h, rng, st.get("bg", "yellow"), st.get("line", "orange"),
                                          st.get("fill", "red"), st.get("fill2", "orange"))
                elif spec.get("tbc"):
                    rgb = kpg.tbc_card(w, h, rng)
                else:
                    b = spec.get("burst", {})
                    rgb = kc.speed_burst(h, w, rng, b.get("bg", "cream"), b.get("line", "purple"),
                                         center=tuple(b.get("center", (0.5, 0.5))))[..., :3]
            r.append({"id": pid, "rgb": np.ascontiguousarray(rgb), "aspect": asp, "focus": tuple(spec.get("focus", (0.5, 0.5)))})
        rows.append(r)
    rgba, layout = kpg.page(pg.get("width", 2000), rows, rng, gutter=pg.get("gutter", 34))
    store.put(nm, rgba, layout=layout)
    return nm, layout, rgba.shape[1], rgba.shape[0]


def _union(layout: dict, ids, pad: float = 26) -> list[float]:
    rs = [layout[i]["rect"] for i in ids]
    return [min(r[0] for r in rs) - pad, min(r[1] for r in rs) - pad, max(r[2] for r in rs) + pad, max(r[3] for r in rs) + pad]


def visible_rect(rect, page_w, page_h) -> list[float]:
    """Что реально видно в кадре 9:16, когда камера кадрирует rect (он вписывается целиком)."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    vh = max(h, w / SCREEN_AR) * 1.04
    vw = vh * SCREEN_AR
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return [cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2]


def _rig(rig: list[dict], tex: str, store, clk, fps: int) -> list[dict]:
    """Координаты ригa из пикселей исходной картинки → доли текстуры вырезки (u слева, v сверху)."""
    m = store.meta[tex]
    x0, y0, x1, y1 = m.get("box", [0, 0, m["w"] / 2, m["h"] / 2])     # фон-лист — апскейл ×2 исходника
    bw, bh = x1 - x0, y1 - y0
    out = []
    for r in rig:
        q = dict(r)
        if "src" in r:
            q["c"] = [(r["src"][0] - x0) / bw, (r["src"][1] - y0) / bh]
            q["r"] = r.get("r", 150) / bh
        if "pivot" in r and "src" in r:
            q["pivot"] = [(r["pivot"][0] - x0) / bw, (r["pivot"][1] - y0) / bh]
        for k in ("from", "to"):
            if r.get(k) is not None:
                q[k] = clk.f(r[k])
        q.pop("src", None)
        out.append(q)
    return out


def resolve_page(sc: dict, idx: int, f0: int, f1: int, clk, style, imgs: dict, store, fps: int, censor: list, seed: int) -> dict:
    from resolve import word_item            # общий раскладчик больших слов
    tex, layout, pw, ph = build_page(sc, imgs, store, seed)
    # кадры камеры
    shots = []
    for k, sh in enumerate(sc["shots"]):
        fr = sh["frame"]
        if isinstance(fr, dict):                      # часть панели: {"panel": id, "sub": [u0, v0, u1, v1]}
            r0 = layout[fr["panel"]]["rect"]; u0, v0, u1, v1 = fr["sub"]
            rect = [r0[0] + u0 * (r0[2] - r0[0]), r0[1] + v0 * (r0[3] - r0[1]), r0[0] + u1 * (r0[2] - r0[0]), r0[1] + v1 * (r0[3] - r0[1])]
            fr = [fr["panel"]]
        else:
            rect = [0, 0, pw, ph] if fr == "page" else _union(layout, fr if isinstance(fr, list) else [fr], sh.get("pad", 26))
        f = f0 if k == 0 else int(round((clk.snap_beat(sh["at"]) - clk.t0) * fps))
        mv = sh.get("move", "cut" if k == 0 else "pan")
        shots.append({"f": max(f0, min(f1 - 1, f)), "rect": rect, "move": mv,
                      "dur": sh.get("dur", {"pan": 9, "smash": 7, "whip": 5, "cut": 0}.get(mv, 9)),
                      "ids": fr if isinstance(fr, list) else ([fr] if fr != "page" else list(layout))})
    shots.sort(key=lambda s: s["f"])

    def ref_rect(panel: str, f: int) -> list[float]:
        cand = [s for s in shots if panel in s["ids"]]
        if not cand:
            return shots[0]["rect"]
        before = [s for s in cand if s["f"] <= f + 3]
        return (before[-1] if before else cand[0])["rect"]

    def prect(panel):
        return layout[panel]["rect"]

    items = []
    # персонажи-вырезки в панелях
    for a in sc.get("acts", []):
        t = imgs[a["img"]]
        r = prect(a["panel"])
        fin = clk.f(a["in"]) if a.get("in") is not None else f0
        args = {}
        for k, v in a.get("args", {}).items():
            args[k[:-3] if k.endswith("_at") else k] = clk.f(v) if k.endswith("_at") else v
        items.append({"type": "act", "tex": t, "px": [r[0] + a.get("x", 0.5) * (r[2] - r[0]), r[1] + a.get("y", 0.95) * (r[3] - r[1])],
                      "h_px": a.get("h", 0.8) * (r[3] - r[1]), "z": a.get("z", 0.3), "in": fin,
                      "out": clk.f(a["out"]) if a.get("out") is not None else None, "anim": a.get("anim", "pop"),
                      "args": args, "flip": a.get("flip", False), "beat": a.get("beat", True), "shadow": a.get("shadow", True),
                      "rig": _rig(a.get("rig", []), t, store, clk, fps), "ref": ref_rect(a["panel"], fin), "panel": a["panel"]})
    # пузыри и рамки рассказчика: слова появляются на своих атаках
    def first_ref(tx):
        if "from" in tx:
            return clk.t(tx["from"])
        at = tx.get("at", [None])
        at = at[0] if isinstance(at, list) else at
        return clk.t(at) if at is not None else clk.t0 + f0 / fps
    texts = sorted(sc.get("texts", []), key=first_ref)
    for n, tx in enumerate(texts):
        if "from" in tx:
            a_i, b_i = int(str(tx["from"]).split(":")[1]), int(str(tx["to"]).split(":")[1])
            seq = [(clk.word_text(i), f"w:{i}") for i in range(a_i, b_i + 1) if i not in set(tx.get("skip", []))]
        else:
            toks = tx["text"].split(" ")
            ats = tx.get("at", [None])
            ats = ats if isinstance(ats, list) else [ats]
            seq = []
            for k, tok in enumerate(toks):
                seq.append((tok, ats[k] if k < len(ats) and ats[k] is not None else None))
            last = None
            for k in range(len(seq)):                  # без времени — следом за предыдущим словом
                if seq[k][1] is None:
                    seq[k] = (seq[k][0], (clk.t(last) + 0.12) if last is not None else clk.t0 + f0 / fps)
                last = seq[k][1]
        r = prect(tx["panel"])
        pw_, ph_ = r[2] - r[0], r[3] - r[1]
        first_on = clk.f(seq[0][1])
        rr = ref_rect(tx["panel"], first_on)
        size = tx.get("size", 0.042) * (visible_rect(rr, pw, ph)[3] - visible_rect(rr, pw, ph)[1])   # высота строки в px страницы
        cap = 64
        k_scale = size / (cap * 1.3)
        maxw = tx.get("w", 0.8) * pw_ / k_scale
        tiles = []
        for word, ref in seq:
            low = word.lower()
            cens = any(low.startswith(c) for c in censor)
            shown = "БИП" if cens else (word.upper() if tx.get("upper", True) else word)
            nm = f"T_{_h(shown, tx.get('kind', 'speech'))}"
            if not store.has(nm):
                store.put(nm, kpg.text_tile(shown, cap, "Rubik-Bold.ttf", "red" if cens else "black"))
            tw, th = store.size(nm)
            tiles.append([nm, tw, th, clk.f(ref)])
        lines, cur, cw = [], [], 0.0
        space = cap * 0.35
        for tl in tiles:
            if cur and cw + space + tl[1] > maxw:
                lines.append((cur, cw)); cur, cw = [], 0.0
            cw += (space if cur else 0) + tl[1]; cur.append(tl)
        if cur:
            lines.append((cur, cw))
        bw = int(max(w for _, w in lines)); bh = int(len(lines) * cap * 1.3)
        kind = tx.get("kind", "speech")
        bnm = f"BB_{_h(kind, bw, bh, tuple(tx.get('tail', (0.3, 1.35))), n)}"
        if store.has(bnm):
            bx, by = store.meta[bnm]["text_at"]
        else:
            bimg, (bx, by) = kpg.bubble(bw, bh, kind, tuple(tx.get("tail", (0.3, 1.35))), seed + n)
            store.put(bnm, bimg, text_at=[bx, by])
        BW, BH = store.size(bnm)
        cx, cy = r[0] + tx.get("x", 0.5) * pw_, r[1] + tx.get("y", 0.15) * ph_
        # пузырь целиком внутри страницы
        tl_x, tl_y = cx - BW * k_scale / 2, cy - BH * k_scale / 2
        nxt_on = None
        for tx2 in texts[n + 1:]:
            if tx2["panel"] == tx["panel"] or tx2.get("replace", False):
                nxt_on = int(round((first_ref(tx2) - clk.t0) * fps)) - 2
                break
        last_on = max(t[3] for t in tiles)
        off = min(f1, clk.f(tx["until"]) if tx.get("until") is not None else last_on + int(fps * tx.get("hold", 1.3)))
        if nxt_on is not None:
            off = min(off, nxt_on)
        on = max(f0, first_on - 2)
        items.append({"type": "bubble", "tex": bnm, "px": [cx, cy], "h_px": BH * k_scale, "on": on, "off": max(on + 6, off),
                      "z": 0.45 + 0.004 * n, "ref": rr, "kind": kind})
        y = by
        for ln, lw in lines:
            x = bx + (bw - lw) / 2
            for nm, tw, th, fon in ln:
                px = tl_x + (x + tw / 2) * k_scale
                py = tl_y + (y + th / 2) * k_scale
                items.append({"type": "btext", "tex": nm, "px": [px, py], "h_px": th * k_scale, "on": max(on, fon - 1),
                              "off": max(on + 6, off), "z": 0.452 + 0.004 * n, "ref": rr})
                x += tw + space
            y += cap * 1.3
    # звукоподражания, нарисованные в панели
    for k, s in enumerate(sc.get("sfx", [])):
        kind = s.get("kind", "sfx")
        t = style.burst(s["text"], kind, seed + 31 * k)
        r = prect(s["panel"])
        on = clk.f(s["at"]) - 1
        items.append({"type": "pburst", "tex": t, "px": [r[0] + s.get("x", 0.5) * (r[2] - r[0]), r[1] + s.get("y", 0.4) * (r[3] - r[1])],
                      "h_px": s.get("h", 0.3) * (r[3] - r[1]), "on": max(f0, on),
                      "off": min(f1, on + int(round(s.get("dur", 1.0) * fps))), "rot": s.get("rot", float(np.random.default_rng(seed + k).uniform(-12, 12))),
                      "z": s.get("z", 0.4), "ref": ref_rect(s["panel"], on)})
    for k, w in enumerate(sc.get("words", [])):
        items.append(word_item(w, clk, style, fps, f0, f1, seed * 7 + k))
    cam = dict(sc.get("camera", {}))
    cam.setdefault("seed", seed)
    cam["punch"] = sorted({it["letters"][0]["land"] for it in items if it["type"] == "word" and it["letters"]} |
                          {it["on"] for it in items if it["type"] == "pburst"})
    desk = store.get("desk", lambda: kc.speed_burst(1800, 1800, np.random.default_rng(9), "dpurple", "purple", n=70))
    return {"id": sc.get("id", f"s{idx}"), "kind": "page", "f0": f0, "f1": f1, "cam": cam,
            "page": {"tex": tex, "w": pw, "h": ph, "m_per_px": S_M, "desk": desk}, "shots": shots, "items": items,
            "transition": sc.get("transition", "cut")}


def page_transitions(scenes: list[dict], store, style) -> None:
    for i, sc in enumerate(scenes[:-1]):
        tr, nxt = sc.get("transition"), scenes[i + 1]
        if tr == "pageturn" and sc.get("kind") == "page":
            last = sc["shots"][-1]["rect"]
            vr = visible_rect(last, sc["page"]["w"], sc["page"]["h"])
            nm = f"turn_{sc['id']}_{_h(vr, sc['page']['tex'])}"
            if not store.has(nm):
                pg = cv2.imread(str(store.root / f"{sc['page']['tex']}.png"))
                H, W = pg.shape[:2]
                x0, y0, x1, y1 = [int(round(v)) for v in vr]
                pad = cv2.copyMakeBorder(pg, max(0, -y0), max(0, y1 - H), max(0, -x0), max(0, x1 - W), cv2.BORDER_CONSTANT, value=(40, 30, 46))
                crop = pad[y0 + max(0, -y0):y1 + max(0, -y0), x0 + max(0, -x0):x1 + max(0, -x0)]
                crop = cv2.resize(crop, (900, 1600), interpolation=cv2.INTER_AREA)
                rgb = crop[..., ::-1]
                # тень у корешка, как у бумажной страницы
                g = np.linspace(0.75, 1.0, 120)[None, :, None]
                rgb = rgb.astype(np.float32); rgb[:, :120] *= g
                store.put(nm, np.clip(rgb, 0, 255).astype(np.uint8))
            nxt["items"].append({"type": "pageturn_in", "tex": nm, "start": nxt["f0"], "end": nxt["f0"] + 13})
        elif tr == "ink":
            nm = store.get(f"splat_{i}", lambda: kpg.ink_splat(1400, np.random.default_rng(40 + i)))
            sc["items"].append({"type": "ink_out", "tex": nm, "start": sc["f1"] - 6, "cut": sc["f1"]})
            nxt["items"].append({"type": "ink_in", "tex": nm, "cut": nxt["f0"], "end": nxt["f0"] + 8})
        elif tr == "halftone":
            nm = store.get("dots_field", lambda: kpg.dots_field(900, 1600, 64))
            sc["items"].append({"type": "dots_out", "tex": nm, "start": sc["f1"] - 9, "cut": sc["f1"]})
            nxt["items"].append({"type": "dots_in", "tex": nm, "cut": nxt["f0"], "end": nxt["f0"] + 9})
