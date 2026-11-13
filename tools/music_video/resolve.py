# -*- coding: utf-8 -*-
"""Шаг prep: план (plan.json, человеческие секунды и ссылки на слова) → scene.json для Blender (кадры, доли экрана)
+ все текстуры (фоны из картинок, вырезанные персонажи, буквы, плашки, вспышки, рамки).

Ссылки на время в плане:
  12.5        — секунда песни;
  "w:155"     — атака слова №155 из analysis.json (вокальный стем, не Whisper);
  "w:155.1"   — атака второго слога этого слова;
  "b:24"      — доля №24 сетки; "b:24+2" — через две доли.
Границы сцен прилипают к ближайшей доле (склейки по сетке)."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import cv2
import numpy as np

from styles import STYLES, TexStore

VOWELS = set("аеёиоуыэюяaeiouyАЕЁИОУЫЭЮЯAEIOUY")


class Clock:
    def __init__(self, an: dict, fps: int, fix: dict | None = None):
        self.an, self.fps = an, fps
        self.t0 = an["start"]
        self.beats = an["beats"]
        self.words = an["words"]
        self.fix = {int(k): v for k, v in (fix or {}).items()}

    def t(self, ref) -> float:
        if isinstance(ref, (int, float)):
            return float(ref)
        s = str(ref).strip()
        m = re.fullmatch(r"w:(\d+)(?:\.(\d+))?([+-]\d*\.?\d+)?", s)
        if m:
            w = self.words[int(m.group(1))]
            fx = self.fix.get(int(m.group(1)), {})
            syl = self.syl_times(int(m.group(1)))
            if m.group(2):
                k = int(m.group(2))
                base = syl[k] if k < len(syl) else syl[-1] + 0.12 * (k - len(syl) + 1)
            elif "at" in fx:
                base = fx["at"]
            else:
                base = w["on"]
            return base + float(m.group(3) or 0)
        m = re.fullmatch(r"b:(\d+)([+-]\d+)?", s)
        if m:
            i = int(m.group(1)) + int(m.group(2) or 0)
            per = self.an["period"]
            return self.beats[0] + i * per if i >= len(self.beats) else self.beats[i]
        return float(s)

    def f(self, ref) -> int:
        return int(round((self.t(ref) - self.t0) * self.fps))

    def snap_beat(self, ref) -> float:
        t = self.t(ref)
        if t <= self.t0 + 0.05:
            return self.t0
        if t >= self.an["end"] - 0.05:
            return self.an["end"]
        return min(self.beats, key=lambda b: abs(b - t))

    def word_text(self, i: int) -> str:
        fx = self.fix.get(i, {})
        return fx.get("text", self.words[i]["w"]) if isinstance(fx, dict) else fx

    def syl_times(self, i: int) -> list[float]:
        """Атаки слогов слова; в lyrics_fix можно задать {"at": t, "syl": [t1, t2, …]} — сверено по спектрограмме."""
        fx = self.fix.get(i, {})
        if "syl" in fx:
            return list(fx["syl"])
        if "at" in fx:
            return [fx["at"]] + [t for t in self.words[i]["syl"][1:] if t > fx["at"] + 0.06]
        return list(self.words[i]["syl"])


# ---------------- картинки → текстуры ----------------
def key_alpha(bgr: np.ndarray, key: str) -> tuple[np.ndarray, np.ndarray]:
    b, g, r = bgr[..., 0], bgr[..., 1], bgr[..., 2]
    if key == "magenta":
        d = np.minimum(r, b) - g
        lo, hi = 0.18, 0.40
    else:
        d = g - np.maximum(r, b)
        lo, hi = 0.10, 0.30
    a = 1 - np.clip((d - lo) / (hi - lo), 0, 1)
    # фон — только то, что связано с краем кадра (внутренние пятна цвета ключа не трогаем)
    bgm = (a < 0.5).astype(np.uint8)
    n, lab = cv2.connectedComponents(bgm)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    outer = np.isin(lab, list(edge))
    a = np.where(outer | (a >= 0.5), a, 1.0)
    a = cv2.erode(a, np.ones((3, 3), np.uint8))
    a = cv2.GaussianBlur(a, (0, 0), 0.8)
    out = bgr.copy()
    if key == "magenta":
        out[..., 2] = np.minimum(r, g * 1.0 + np.maximum(0, r - b) + 0.35)
        out[..., 0] = np.minimum(b, g * 1.0 + np.maximum(0, b - r) + 0.35)
    else:
        out[..., 1] = np.minimum(g, np.maximum(r, b) * 1.02 + 0.02)
    return out, a


def prep_images(plan: dict, work: Path, store: TexStore) -> dict:
    """plate_<id>: апскейл ×2 с резкостью; cut_<id>[_k]: вырезка по ключу, кадрирование, компоненты."""
    out = {}
    for iid, spec in plan.get("images", {}).items():
        src = work / "img" / f"{iid}.jpg"
        if not src.exists():
            print("  ! нет картинки", src)
            continue
        im = cv2.imread(str(src)).astype(np.float32) / 255
        if spec.get("kind", "plate") == "plate":
            nm = f"plate_{iid}"
            if not store.has(nm):
                h, w = im.shape[:2]
                big = cv2.resize(im, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
                bl = cv2.GaussianBlur(big, (0, 0), 1.4)
                big = np.clip(big + (big - bl) * 0.4, 0, 1)
                store.put(nm, (big[..., ::-1] * 255 + 0.5).astype(np.uint8))
            out[iid] = nm
            continue
        rgb, a = key_alpha(im, spec.get("key", "green"))
        comps = [a]
        if spec.get("split"):
            nlab, lab, st, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
            big = [i for i in range(1, nlab) if st[i, cv2.CC_STAT_AREA] > 0.004 * a.size]
            order = sorted(big, key=lambda i: st[i, cv2.CC_STAT_LEFT]) if spec["split"] == "x" else \
                sorted(big, key=lambda i: -st[i, cv2.CC_STAT_AREA])
            comps = []
            for i in order:
                m = cv2.dilate((lab == i).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(np.float32)
                comps.append(a * m)
        else:
            nlab, lab, st, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
            keep = np.zeros_like(a)
            for i in range(1, nlab):
                if st[i, cv2.CC_STAT_AREA] > 0.0015 * a.size:
                    keep[lab == i] = 1
            comps = [a * cv2.dilate(keep, np.ones((7, 7), np.uint8))]
        for k, ca in enumerate(comps):
            nm = f"cut_{iid}" + (f"_{k}" if spec.get("split") else "")
            if not store.has(nm):
                ys, xs = np.where(ca > 0.05)
                m = 12
                y0, y1 = max(0, ys.min() - m), min(ca.shape[0], ys.max() + m)
                x0, x1 = max(0, xs.min() - m), min(ca.shape[1], xs.max() + m)
                c = rgb[y0:y1, x0:x1]
                c = cv2.resize(c, (c.shape[1] * 2, c.shape[0] * 2), interpolation=cv2.INTER_LANCZOS4)
                cl = cv2.resize(ca[y0:y1, x0:x1], (c.shape[1], c.shape[0]), interpolation=cv2.INTER_LINEAR)
                bl = cv2.GaussianBlur(c, (0, 0), 1.2)
                c = np.clip(c + (c - bl) * 0.35, 0, 1)
                store.put(nm, (np.dstack([c[..., ::-1], cl]) * 255 + 0.5).astype(np.uint8),
                          box=[int(x0), int(y0), int(x1), int(y1)])
            out[iid + (f"#{k}" if spec.get("split") else "")] = nm
    return out


def tex_ref(name: str, imgs: dict) -> str:
    """'bear' → cut_bear / plate_bear; 'hunter#1' → cut_hunter_1; уже готовое имя текстуры — как есть."""
    if name in imgs:
        return imgs[name]
    raise KeyError(f"нет картинки {name!r} (есть: {', '.join(sorted(imgs))})")


# ---------------- раскладка ----------------
def syl_groups(token: str, n: int) -> list[str]:
    """Буквы слова → n групп по слогам (граница после гласной)."""
    if n <= 1:
        return [token]
    groups, cur = [], ""
    for ch in token:
        cur += ch
        if ch in VOWELS and len(groups) < n - 1:
            groups.append(cur); cur = ""
    if cur:
        if groups and not any(c in VOWELS for c in cur):
            groups[-1] += cur
        else:
            groups.append(cur)
    return groups


def word_item(w: dict, clk: Clock, style, fps: int, f0: int, f1: int, seed: int) -> dict:
    """Большое слово: буквы, у каждой свой кадр «удара» (атака слога − 0…1 кадр + разбег по буквам)."""
    rng = np.random.default_rng(seed)
    lines = w["text"].split("\n")
    refs = w.get("at", [])
    if not isinstance(refs, list):
        refs = [refs]
    tokens = [tok for ln in lines for tok in ln.split(" ") if tok]
    while len(refs) < len(tokens):
        refs.append(None)
    fill, fill2 = w.get("fill", "yellow"), w.get("fill2", "orange")
    letters, li = [], 0
    rows = []
    tk = 0
    prev_land = None
    for ln in lines:
        row = []
        for tok in [t for t in ln.split(" ") if t]:
            ref = refs[tk]
            m = re.fullmatch(r"w:(\d+)", str(ref)) if ref is not None else None
            if ref is None:
                times = [None]
            elif m and w.get("syllables", True):
                st = clk.syl_times(int(m.group(1)))
                times = [clk.t(ref)] + st[1:]
            else:
                times = [clk.t(ref)]
            groups = syl_groups(tok, len(times))
            times = times[:len(groups)]
            for gi, grp in enumerate(groups):
                t = times[gi] if gi < len(times) else None
                for k, ch in enumerate(grp):
                    if t is None:
                        land = (prev_land + 1) if prev_land is not None else f0 + 2
                    else:
                        land = int(round((t - clk.t0) * fps)) + 1 + k   # видно за 1–2 кадра до атаки, удар на +1
                    prev_land = land
                    tex = style.letter(ch, fill, fill2, seed + li) if ch.strip() else None
                    row.append({"ch": ch, "tex": tex, "land": land})
                    li += 1
            row.append({"ch": " ", "tex": None, "land": prev_land})
            tk += 1
        rows.append(row[:-1])
    # ширина: буквы в пикселях текстуры, межбуквенный нахлёст, пробел = 0.35 высоты
    H_SCR, W_SCR = 1920, 1080
    ov = style.letter_overlap
    sizes = []
    for row in rows:
        tot = 0.0
        for c in row:
            if c["tex"]:
                tw, th = style.s.size(c["tex"])
                c["w"], c["h"] = tw, th
                tot += tw * ov
            else:
                c["w"], c["h"] = 140, 380
                tot += 140
        sizes.append(tot)
    width = min(w.get("w", 0.84), 0.86)       # шире — край слова уходит за кадр при наклоне и встряске
    max_letter_h = w.get("max_h", 0.13)
    px2scr = min(width * W_SCR / max(sizes), max_letter_h * H_SCR / 420)   # экранных пикселей на пиксель текстуры
    x0c, yc = w.get("x", 0.5), w.get("y", 0.3)
    line_h = 420 * px2scr * w.get("leading", 0.92) / H_SCR
    out_letters = []
    tilt_word = w.get("tilt", float(rng.uniform(-5, 5)))
    for ri, row in enumerate(rows):
        x = -sizes[ri] / 2
        y = yc + (ri - (len(rows) - 1) / 2) * line_h
        for c in row:
            adv = c["w"] * ov if c["tex"] else c["w"]
            if c["tex"]:
                cx = x + c["w"] / 2
                # наклон всей строки: сдвиг по y пропорционально x
                sx = x0c + cx * px2scr / W_SCR
                sy = y - math.tan(math.radians(tilt_word)) * cx * px2scr / H_SCR + rng.uniform(-0.004, 0.004)
                out_letters.append({"tex": c["tex"], "x": round(sx, 4), "y": round(sy, 4),
                                    "h": round(c["h"] * px2scr * rng.uniform(0.94, 1.06) / H_SCR, 4),
                                    "rot": round(tilt_word + float(rng.uniform(-style.letter_tilt, style.letter_tilt)), 2),
                                    "land": max(f0, min(f1 - 1, c["land"]))})
            x += adv
    out = clk.f(w["out"]) if w.get("out") is not None else None
    return {"type": "word", "letters": out_letters, "depth": w.get("depth", 6.0), "out": out,
            "lock": w.get("lock", True), "exit": w.get("exit", "pop")}


def clear_hero_overlaps(items: list[dict]) -> None:
    """Большие слова никогда не наезжают друг на друга: если следующее слово попадает в ту же полосу экрана,
    предыдущее успевает улететь до первого кадра появления следующего."""
    ws = [it for it in items if it["type"] == "word" and it["letters"]]
    ws.sort(key=lambda it: min(L["land"] for L in it["letters"]))

    def band(it):
        return (min(L["y"] - L["h"] / 2 for L in it["letters"]), max(L["y"] + L["h"] / 2 for L in it["letters"]))
    for i, a in enumerate(ws):
        a0, a1 = band(a)
        for b in ws[i + 1:]:
            b0, b1 = band(b)
            if b0 > a1 or b1 < a0:
                continue
            appear = min(L["land"] for L in b["letters"]) - 3
            n = len(a["letters"])
            gone = (a["out"] + n // 2 + 6) if a["out"] is not None else 10 ** 9
            if gone >= appear:
                a["out"] = max(max(L["land"] for L in a["letters"]) + 2, appear - (n // 2 + 6) - 1)
            break


def captions(spec, clk: Clock, style, fps: int, f0: int, f1: int, censor: list[str], seed: int) -> list[dict]:
    """Мелкие слова текста на плашках, каждое появляется на своей атаке; строки по ширине, не больше max_rows."""
    if isinstance(spec, dict) and "from" in spec:
        a, b = int(str(spec["from"]).split(":")[1]), int(str(spec["to"]).split(":")[1])
        skip = set(spec.get("skip", []))
        seq = [(clk.word_text(i), f"w:{i}") for i in range(a, b + 1) if i not in skip]
        opts = spec
    else:
        seq = [(c["text"], c["at"]) for c in spec["words"]]
        opts = spec
    y0 = opts.get("y", 0.12)
    ch_h = opts.get("h", 0.040)
    max_rows = opts.get("rows", 2)
    margin = opts.get("margin", 0.07)
    items, row, x, cur = [], 0, margin, []
    rng = np.random.default_rng(seed)
    for k, (text, ref) in enumerate(seq):
        low = text.lower()
        cens = any(low.startswith(c) for c in censor)
        shown = "БИП" if cens else (text.upper() if opts.get("upper", True) else text)
        tex = style.chip(shown, cens, seed + k)
        tw, th = style.s.size(tex)
        wscr = ch_h * 1920 * tw / th / 1080
        if x + wscr > 1 - margin and cur:
            row += 1; x = margin
        if row >= max_rows:
            on = clk.f(ref)
            for it in cur:
                it["off"] = on
            cur, row, x = [], 0, margin
        on = max(f0, clk.f(ref))
        it = {"type": "chip", "tex": tex, "x": round(x + wscr / 2, 4), "y": round(y0 + row * ch_h * 1.25, 4),
              "h": ch_h, "on": on, "off": f1, "rot": round(float(rng.uniform(-3, 3)), 2), "censor": cens}
        x += wscr + 0.012
        items.append(it); cur.append(it)
    end = clk.f(opts["until"]) if opts.get("until") is not None else None
    if end is not None:
        for it in cur:
            it["off"] = min(it["off"], end)
    return items


def resolve(plan: dict, an: dict, out: Path) -> dict:
    work = out / "work"
    fps = plan.get("fps", 30)
    store = TexStore(work / "tex")
    style = STYLES[plan.get("style", "comic")](store, plan.get("fonts"))
    clk = Clock(an, fps, plan.get("lyrics_fix"))
    imgs = prep_images(plan, work, store)
    censor = [c.lower() for c in plan.get("censor", [])]
    nf = int(round((an["end"] - an["start"]) * fps))
    scenes = plan["scenes"]
    # границы сцен на долях
    bounds = []
    for i, sc in enumerate(scenes):
        a = clk.snap_beat(sc["from"]) if i else an["start"]
        bounds.append(a)
    bounds.append(an["end"])
    res = {"fps": fps, "nf": nf, "W": plan.get("size", [1080, 1920])[0], "H": plan.get("size", [1080, 1920])[1],
           "style": style.name, "material": style.material, "tex_dir": str(store.root),
           "beats": [clk.f(b) for b in an["beats"] if an["start"] <= b < an["end"]],
           "kicks": [clk.f(b) for b in an["downbeats"] if an["start"] <= b < an["end"]],
           "shadow": style.shadow(), "scenes": []}
    for i, sc in enumerate(scenes):
        f0 = int(round((bounds[i] - an["start"]) * fps))
        f1 = int(round((bounds[i + 1] - an["start"]) * fps)) if i + 1 < len(scenes) else nf
        seed = 101 + i * 17
        if "page" in sc:                                  # страница комикса: полёт камеры по панелям
            from pages import resolve_page
            res["scenes"].append(resolve_page(sc, i, f0, f1, clk, style, imgs, store, fps, censor, seed))
            continue
        items = []
        bg = sc.get("bg")
        if isinstance(bg, str):
            items.append({"type": "plate", "tex": tex_ref(bg, imgs), "depth": sc.get("bg_depth", 30), "over": sc.get("over", 1.55),
                          "shift": sc.get("bg_shift", [0, 0]), "spin": 0})
        elif isinstance(bg, dict):
            items.append({"type": "plate", "tex": style.background(bg, seed), "depth": 30, "over": 1.6,
                          "shift": [0, 0], "spin": bg.get("spin", 6)})
        for p in sc.get("props", []):
            tex = tex_ref(p["img"], imgs)
            args = {}
            for k, v in p.get("args", {}).items():           # "end_at": "w:166" → "end": кадр
                if k.endswith("_at"):
                    args[k[:-3]] = clk.f(v)
                else:
                    args[k] = v
            p = {**p, "args": args}
            items.append({"type": "sprite", "tex": tex, "x": p.get("x", 0.5), "y": p.get("y", 0.86), "h": p.get("h", 0.45),
                          "depth": p.get("depth", 9.0), "in": clk.f(p["in"]) if p.get("in") is not None else f0,
                          "out": clk.f(p["out"]) if p.get("out") is not None else None,
                          "anim": p.get("anim", "pop"), "args": p.get("args", {}), "flip": p.get("flip", False),
                          "shadow": p.get("shadow", True), "beat": p.get("beat", True)})
        g = sc.get("ground")
        if g:
            items.append({"type": "ground", "tex": style.ground(g, seed), "y": g.get("y", 0.88), "depth": g.get("depth", 5.0),
                          "h": g.get("h", 0.3)})
        for k, bspec in enumerate(sc.get("bushes", [])):
            items.append({"type": "bush", "tex": style.bush(bspec, seed + k), "x": bspec.get("x", 0.1), "y": bspec.get("y", 1.0),
                          "w": bspec.get("w", 0.6), "depth": bspec.get("depth", 3.2)})
        for k, w in enumerate(sc.get("words", [])):
            items.append(word_item(w, clk, style, fps, f0, f1, seed * 7 + k))
        clear_hero_overlaps(items)
        cap = sc.get("captions")
        if cap:
            for cs in (cap if isinstance(cap, list) else [cap]):
                items += captions(cs, clk, style, fps, f0, f1, censor, seed + 900)
        for k, s in enumerate(sc.get("sfx", [])):
            kind = s.get("kind", "sfx")
            tex = style.burst(s["text"], kind, seed + 31 * k)
            on = clk.f(s["at"]) - (1 if kind != "censor" else 0)
            items.append({"type": "burst", "tex": tex, "x": s.get("x", 0.5), "y": s.get("y", 0.5), "h": s.get("h", 0.17),
                          "on": max(f0, on), "off": min(f1, on + int(round(s.get("dur", 0.8) * fps))),
                          "rot": s.get("rot", float(np.random.default_rng(seed + k).uniform(-12, 12))),
                          "depth": s.get("depth", 4.0), "shake": s.get("shake", 1.0)})
        # вспышки не наслаиваются: новая, задевающая старую, гасит её на своём первом кадре
        bs = sorted((it for it in items if it["type"] == "burst"), key=lambda it: it["on"])
        for a_i, a in enumerate(bs):
            for b in bs[a_i + 1:]:
                if b["on"] < a["off"] and abs(a["x"] - b["x"]) < 0.42 and abs(a["y"] - b["y"]) < (a["h"] + b["h"]) * 0.6:
                    a["off"] = b["on"]
        for k, pn in enumerate(sc.get("panels", [])):
            src = tex_ref(pn["img"], imgs)
            on = clk.f(pn["at"])
            items.append({"type": "inset", "tex": style.panel(src), "x": pn.get("x", 0.5), "y": pn.get("y", 0.5),
                          "w": pn.get("w", 0.82), "on": on - 6, "land": on, "off": min(f1, on + int(round(pn.get("dur", 1.6) * fps))),
                          "rot": pn.get("rot", -4), "dir": pn.get("dir", 1)})
        for k, bar in enumerate(sc.get("bars", [])):
            on = clk.f(bar["at"])
            items.append({"type": "chip", "tex": style.bar(bar["text"]), "x": bar.get("x", 0.5), "y": bar.get("y", 0.5),
                          "h": bar.get("h", 0.07), "on": on, "off": clk.f(bar["to"]) if bar.get("to") else f1,
                          "rot": bar.get("rot", -6), "censor": True})
        cam = dict(sc.get("camera", {"move": "push"}))
        cam.setdefault("seed", seed)
        cam["punch"] = sorted({it["letters"][0]["land"] for it in items if it["type"] == "word" and it["letters"]} |
                              {it["on"] for it in items if it["type"] == "burst"})
        tr = sc.get("transition", "cut")
        res["scenes"].append({"id": sc.get("id", f"s{i}"), "f0": f0, "f1": f1, "cam": cam, "items": items,
                              "transition": tr})
    # переходы между страницами: перелистывание, клякса туши, растровое растворение
    from pages import page_transitions
    page_transitions(res["scenes"], store, style)
    # переход «panel»: следующая сцена в рамке комикса влетает к склейке
    for i, sc in enumerate(res["scenes"][:-1]):
        if sc["transition"] in ("panel", "burst"):
            nxt = res["scenes"][i + 1]
            plate = next((it for it in nxt["items"] if it["type"] == "plate"), None)
            if sc["transition"] == "panel" and plate:
                sc["items"].append({"type": "panel_in", "tex": style.panel(plate["tex"]), "start": sc["f1"] - 9,
                                    "cut": sc["f1"], "dir": 1 if i % 2 == 0 else -1})
            else:
                bt = style.flash(7 + i)
                sc["items"].append({"type": "flash_out", "tex": bt, "start": sc["f1"] - 5, "cut": sc["f1"]})
                nxt["items"].append({"type": "flash_in", "tex": bt, "cut": nxt["f0"], "end": nxt["f0"] + 5})
    store.save()
    json.dump(res, open(work / "scene.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_items = sum(len(s["items"]) for s in res["scenes"])
    print(f"  scene.json: {len(res['scenes'])} сцен, {n_items} элементов, {nf} кадров; текстур {len(store.meta)}")
    return res
