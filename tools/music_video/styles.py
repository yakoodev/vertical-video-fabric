# -*- coding: utf-8 -*-
"""Стили клипа: как выглядят буквы, плашки подписей, вспышки, фоны, полоса земли, рамки переходов.

Стиль отдаёт картинки (RGBA uint8, порядок RGB); TexStore кэширует их PNG-файлами в OUT/work/tex и знает размеры.
Новый стиль = новый класс с теми же методами + запись в STYLES."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

import kit_comic as kc
import kit_paper as kp


class TexStore:
    """Текстуры по имени: создаёт один раз, помнит размеры (meta.json) для раскладки и для Blender."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.meta_path = root / "meta.json"
        self.meta = json.load(open(self.meta_path, encoding="utf-8")) if self.meta_path.exists() else {}

    def has(self, name: str) -> bool:
        return name in self.meta and (self.root / f"{name}.png").exists()

    def put(self, name: str, rgba: np.ndarray, **extra) -> str:
        if rgba.shape[2] == 3:
            rgba = np.dstack([rgba, np.full(rgba.shape[:2], 255, np.uint8)])
        cv2.imwrite(str(self.root / f"{name}.png"), rgba[..., [2, 1, 0, 3]])
        self.meta[name] = {"w": int(rgba.shape[1]), "h": int(rgba.shape[0]), **extra}
        return name

    def get(self, name: str, make) -> str:
        if not self.has(name):
            self.put(name, make())
        return name

    def size(self, name: str) -> tuple[int, int]:
        m = self.meta[name]
        return m["w"], m["h"]

    def save(self) -> None:
        json.dump(self.meta, open(self.meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _h(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


class ComicStyle:
    name = "comic"
    material = "flat"            # неосвещённые картинки (эмиссия), как печать комикса
    hero_font = "Rubik-Black.ttf"
    chip_font = "Rubik-Bold.ttf"
    letter_overlap = 0.86        # доля ширины буквы до следующей (контуры заходят друг на друга)
    letter_tilt = 7.0            # разброс наклона букв, градусы

    def __init__(self, store: TexStore, fonts: dict | None = None):
        self.s = store
        if fonts:
            self.hero_font = fonts.get("hero", self.hero_font)
            self.chip_font = fonts.get("chip", self.chip_font)

    def letter(self, ch: str, fill="yellow", fill2="orange", seed: int = 0) -> str:
        nm = f"L_{_h(self.hero_font, ch, fill, fill2)}"
        return self.s.get(nm, lambda: kc.letter(ch, np.random.default_rng(seed), 380, self.hero_font, fill, fill2))

    def chip(self, text: str, censored: bool = False, seed: int = 0) -> str:
        nm = f"C_{_h(self.chip_font, text, censored)}"
        if censored:
            return self.s.get(nm, lambda: kc.chip(text, np.random.default_rng(seed), 70, "RubikMonoOne-Regular.ttf",
                                                   bg="red", ink="white"))
        return self.s.get(nm, lambda: kc.chip(text, np.random.default_rng(seed), 70, self.chip_font))

    def burst(self, text: str, kind: str = "sfx", seed: int = 0) -> str:
        nm = f"B_{_h(text, kind, seed)}"
        rng = np.random.default_rng(seed)
        if kind == "censor":
            return self.s.get(nm, lambda: kc.burst(text, rng, 210, self.hero_font, fill="black", fill2="dpurple",
                                                   ring="red", ink_fill="white", ink_fill2="pink"))
        if kind == "cool":
            return self.s.get(nm, lambda: kc.burst(text, rng, 210, self.hero_font, fill="lime", fill2="yellow",
                                                   ring="purple", ink_fill="white", ink_fill2="cream"))
        return self.s.get(nm, lambda: kc.burst(text, rng, 210, self.hero_font))

    def bar(self, text: str) -> str:
        return self.s.get(f"bar_{_h(text)}", lambda: kc.censor_bar(text, np.random.default_rng(3)))

    def background(self, spec: dict, seed: int = 0) -> str:
        bg, line = spec.get("bg", "cream"), spec.get("line", "black")
        nm = f"burst_{bg}_{line}_{seed}"
        return self.s.get(nm, lambda: kc.speed_burst(2400, 1500, np.random.default_rng(seed), bg, line,
                                                     center=tuple(spec.get("center", (0.5, 0.45)))))

    def flash(self, seed: int = 7) -> str:
        return self.s.get(f"flash_{seed}", lambda: kc.flash_star(1600, np.random.default_rng(seed)))

    def ground(self, spec: dict, seed: int = 0) -> str:
        body, stripe = spec.get("body", "purple"), spec.get("stripe", "lime")
        return self.s.get(f"ground_{body}_{stripe}_{seed}",
                          lambda: kc.ground_strip(2600, 900, np.random.default_rng(seed), body, stripe))

    def bush(self, spec: dict, seed: int = 0) -> str:
        body, rim = spec.get("body", "dpurple"), spec.get("rim", "purple")
        return self.s.get(f"bush_{body}_{rim}_{seed}", lambda: kc.bush(900, 620, np.random.default_rng(seed), body, rim))

    def panel(self, src: str) -> str:
        nm = f"panel_{src}"
        if self.s.has(nm):
            return nm
        im = cv2.imread(str(self.s.root / f"{src}.png"), cv2.IMREAD_UNCHANGED)
        rgb = im[..., [2, 1, 0]]
        h, w = rgb.shape[:2]
        k = 1100 / max(h, w)
        rgb = cv2.resize(rgb, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        return self.s.put(nm, kc.panel(rgb))

    def shadow(self) -> str:
        return self.s.get("shadow", kc.shadow_sprite)


class PaperStyle(ComicStyle):
    """Пресет song15: буквы-вырезки на рваной бумаге, плашки печатной машинки, рваные листы."""
    name = "paper"
    material = "paper"
    hero_font = "PTSerif-Bold.ttf"
    letter_overlap = 0.78
    letter_tilt = 7.0
    RANSOM = ["PTSerif-Bold.ttf", "Oswald-Bold.ttf", "DelaGothicOne-Regular.ttf", "AlumniSans-Black.ttf",
              "RobotoCondensed-Black.ttf", "PTSerif-Bold.ttf", "Montserrat-Black.ttf", "PTSerif-Bold.ttf"]

    def letter(self, ch: str, fill="white", fill2=None, seed: int = 0) -> str:
        style = fill if fill in ("white", "grey", "black", "red") else "white"
        fnt = self.RANSOM[seed % len(self.RANSOM)]
        nm = f"L_{_h('ransom', ch, style, fnt, seed % 5)}"
        return self.s.get(nm, lambda: kp.ransom_letter(ch, np.random.default_rng(seed), 360, style, fnt))

    def chip(self, text: str, censored: bool = False, seed: int = 0) -> str:
        nm = f"C_{_h('paper', text, censored, seed % 3)}"
        return self.s.get(nm, lambda: kp.caption_word(text, np.random.default_rng(seed), 64))

    def burst(self, text: str, kind: str = "sfx", seed: int = 0) -> str:
        rng = np.random.default_rng(seed)

        def make():
            tiles = [kp.ransom_letter(ch, rng, 260, "red" if (kind == "censor" or i == 0) else "white",
                                      self.RANSOM[i % len(self.RANSOM)]) for i, ch in enumerate(text) if ch.strip()]
            H = max(t.shape[0] for t in tiles)
            W = int(sum(t.shape[1] * 0.8 for t in tiles) + tiles[-1].shape[1] * 0.2)
            out = np.zeros((H, W, 4), np.uint8)
            x = 0
            for t in tiles:
                y = (H - t.shape[0]) // 2
                a = t[..., 3:4].astype(np.float32) / 255
                sub = out[y:y + t.shape[0], x:x + t.shape[1]]
                sub[..., :3] = (sub[..., :3] * (1 - a) + t[..., :3] * a).astype(np.uint8)
                sub[..., 3] = np.maximum(sub[..., 3], t[..., 3])
                x += int(t.shape[1] * 0.8)
            return out
        return self.s.get(f"B_{_h('paper', text, kind, seed)}", make)

    def background(self, spec: dict, seed: int = 0) -> str:
        rng = np.random.default_rng(seed)
        return self.s.get(f"paperbg_{seed}", lambda: (np.dstack([kp.paper_texture(1600, 1000, rng, base=(30, 28, 27), dark=True),
                                                                 np.ones((1600, 1000))]) * 255).astype(np.uint8))

    def ground(self, spec: dict, seed: int = 0) -> str:
        rng = np.random.default_rng(seed)

        def make():
            m, rim = kp.torn_sheet_alpha(700, 2400, rng, margin=0.03)
            pap = kp.paper_texture(700, 2400, rng, base=(32, 30, 29), dark=True)
            a = np.clip(m + rim, 0, 1)
            c = (pap * m[..., None] + 0.85 * rim[..., None]) / np.maximum(a[..., None], 1e-4)
            return (np.dstack([np.clip(c, 0, 1), a]) * 255).astype(np.uint8)
        return self.s.get(f"pground_{seed}", make)

    def bush(self, spec: dict, seed: int = 0) -> str:
        return self.ground(spec, seed + 100)

    def flash(self, seed: int = 7) -> str:
        return self.ground({}, seed + 200)

    def panel(self, src: str) -> str:
        nm = f"panel_{src}"
        if self.s.has(nm):
            return nm
        im = cv2.imread(str(self.s.root / f"{src}.png"), cv2.IMREAD_UNCHANGED)
        rgb = im[..., [2, 1, 0]].astype(np.float32) / 255
        h, w = rgb.shape[:2]
        k = 1100 / max(h, w)
        rgb = cv2.resize(rgb, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        rng = np.random.default_rng(5)
        m, rim = kp.torn_sheet_alpha(rgb.shape[0], rgb.shape[1], rng, margin=0.02)
        a = np.clip(m + rim, 0, 1)
        c = (rgb * m[..., None] + np.array([0.9, 0.92, 0.93]) * rim[..., None]) / np.maximum(a[..., None], 1e-4)
        return self.s.put(nm, (np.dstack([np.clip(c, 0, 1), a]) * 255).astype(np.uint8))

    def shadow(self) -> str:
        def make():
            m = np.zeros((300, 1000), np.float32)
            cv2.ellipse(m, (500, 150), (430, 75), 0, 0, 360, 1.0, -1)
            m = cv2.GaussianBlur(m, (0, 0), 38); m = m / m.max() * 0.9
            return np.dstack([np.zeros((300, 1000, 3), np.uint8), (m * 255).astype(np.uint8)])
        return self.s.get("shadow", make)


STYLES = {"comic": ComicStyle, "paper": PaperStyle}
