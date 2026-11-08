"""Сцена по пресету: картинки → слои → кадр. Два вида кадров:

* ``character`` — фон в расфокусе (+ мерцание гирлянды), персонаж с вариантами лица, дыханием и головой,
  свет монитора на нём, край стола спереди, пыль и боке, надписи поверх (``hud``, ``stream_card``);
* ``screen`` — картинка стола с монитором, на экране интерфейс эфира кодом (аватар + чат + зрители).

Все числа берутся из пресета (tools/anime2d_preset.py), движок — tools/anime2d.py, интерфейс —
tools/anime2d_ui.py. ``patches`` пресета правят картинки кодом при загрузке (например, закрыть чужой
бренд на стакане своей этикеткой) — без новых платных вызовов.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from tools import anime2d as A
from tools import anime2d_preset as P
from tools import anime2d_ui as U


def _rig(spec: dict, files: dict[str, str]) -> A.FaceRig:
    base = A.key_green(A.load_rgb(files[spec["image"]]))
    rig = A.FaceRig(base, box=tuple(spec["box"]))
    for v in spec["variants"]:
        (cx, cy), (ax, ay), feather = v["ellipse"]
        rig.add(v["name"], A.key_green(A.load_rgb(files[v["image"]])),
                A.ellipse_mask(base.shape[:2], (cx, cy), (ax, ay), feather))
    return rig


def apply_label(rgb: np.ndarray, patch: dict) -> np.ndarray:
    """Своя этикетка вместо чужой надписи на картинке (например, бренда на стакане):
    буквы в прямоугольнике ``rect`` стираются заливкой по соседней бумаге (cv2.inpaint — сохраняет
    её градиент и тени), сверху — свой текст и плашка кодом. Текст рисуется в 4× и уменьшается,
    слегка размывается (``soften``), чтобы по резкости не отличаться от рисунка."""
    x0, y0, x1, y1 = patch["rect"]
    pad = 8
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(rgb.shape[1], x1 + pad), min(rgb.shape[0], y1 + pad)
    crop = (np.clip(rgb[Y0:Y1, X0:X1], 0, 1) * 255 + 0.5).astype(np.uint8)
    px = rgb[y0:y1, x0:x1]
    lum = px @ np.float32([0.3, 0.59, 0.11])
    paper = np.median(lum[lum >= np.median(lum)])
    ink = (lum < paper * 0.9) | (px[..., 0] - px[..., 2] > 0.04)
    mask = np.zeros(crop.shape[:2], np.uint8)
    mask[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = ink.astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2)
    clean = cv2.inpaint(crop, mask, 6, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    k = 4
    w, h = (x1 - x0) * k, (y1 - y0) * k
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = tuple(patch.get("color", [180, 60, 50])) + (255,)
    fnt = patch.get("font", "Rubik-Black.ttf")
    cy = h * (0.42 if patch.get("sub") else 0.5)
    d.text((w / 2, cy), patch["text"], font=U.font(fnt, patch["size"] * k), fill=col, anchor="mm")
    if patch.get("sub"):
        f = U.font(fnt, patch["sub_size"] * k)
        tw = f.getlength(patch["sub"])
        py = h * 0.78
        d.rounded_rectangle([w / 2 - tw / 2 - 2 * k, py - patch["sub_size"] * k * 0.7,
                             w / 2 + tw / 2 + 2 * k, py + patch["sub_size"] * k * 0.7], radius=3 * k, fill=col)
        d.text((w / 2, py), patch["sub"], font=f, fill=(0, 0, 0, 0), anchor="mm")
        # «вырезанный» текст плашки: прозрачные буквы — сквозь них видна бумага
        hole = Image.new("L", (w, h), 0)
        ImageDraw.Draw(hole).text((w / 2, py), patch["sub"], font=f, fill=255, anchor="mm")
        img.putalpha(Image.fromarray(np.minimum(np.asarray(img.getchannel("A")),
                                                255 - np.asarray(hole)).astype(np.uint8)))
    img = img.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    if patch.get("soften"):
        img = img.filter(ImageFilter.GaussianBlur(patch["soften"]))
    lab = np.asarray(img, np.float32) / 255.0
    a = lab[..., 3:4]
    reg = clean[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0]
    reg[:] = reg * (1 - a) + lab[..., :3] * a
    out = rgb.copy()
    out[Y0:Y1, X0:X1] = clean
    return out


class Scene:
    def __init__(self, preset: dict, src: Path, patches: bool = True) -> None:
        self.p = preset
        o = preset["output"]
        self.W, self.H, self.fps = o["width"], o["height"], o["fps"]
        sc = preset["scene"]
        self.world = sc["world"]
        self.home = (self.world[0] // 2, self.world[1] // 2)
        self.base = self.W / self.world[0]
        files = {iid: str(src / im["file"]) for iid, im in P.images(preset).items()}
        loaded: dict[str, np.ndarray] = {}

        def rgb(iid: str) -> np.ndarray:
            if iid not in loaded:
                img = A.load_rgb(files[iid])
                for pt in preset.get("patches", []) if patches else []:
                    if pt["image"] == iid and pt.get("type") == "label":
                        img = apply_label(img, pt)
                loaded[iid] = img
            return loaded[iid]

        self.acting = P.Acting(preset)
        bg = sc["background"]
        size = (int(self.world[0] * bg["scale"]), int(self.world[1] * bg["scale"]))
        room = cv2.resize(rgb(bg["image"]), size, interpolation=cv2.INTER_AREA)
        self.room = A.blur(room, bg["blur"])
        tw = bg.get("twinkle")
        self.lab = None
        if tw:
            self.lab, self.nbulbs, core = A.bulb_labels(room, reach=tw["reach"])
            glow = None
            for sigma, gain in tw["glow"]:
                g = A.blur(core, sigma) * gain
                glow = g if glow is None else glow + g
            self.glow = glow[..., None] * np.float32(tw["color"])
        ch = sc["character"]
        self.rig = _rig(ch, files)
        shape = self.rig.base.shape[:2]
        b = ch["body"]
        self.body = A.Body(shape, pivot=tuple(b["pivot"]), neck_y=b["neck_y"], neck_soft=b["neck_soft"],
                           floor_y=b["floor_y"])
        lt = ch["light"]
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float32)
        self.light = (np.exp(-(((xx - lt["center"][0]) / lt["sigma"][0]) ** 2
                               + ((yy - lt["center"][1]) / lt["sigma"][1]) ** 2)) * lt["amp"]
                      + lt["floor"]).astype(np.float32)
        self.arig = _rig(sc["avatar"], files) if "avatar" in sc else None
        sp = sc.get("screen_plate")
        if sp:
            desk = rgb(sp["image"])
            dof = sp["dof"]
            dy = np.mgrid[0:desk.shape[0], 0:desk.shape[1]][0].astype(np.float32)
            far = np.clip((dy - dof["near_y"]) / dof["near_ramp"], 0, 1) \
                + np.clip((dof["far_y"] - dy) / dof["far_ramp"], 0, 1) * dof["far_amount"]
            self.desk = desk * (1 - far[..., None]) + A.blur(desk, dof["blur"]) * far[..., None]
        fg = sc.get("foreground", {}).get("desk_edge")
        self.fg_desk = desk_edge(*fg["size"], fg["top"]) if fg else None
        du = sc["dust"]
        self.dust = A.Dust(self.W, self.H, n=du["n"], seed=du["seed"])
        W, H = self.W, self.H
        vg = sc["post"]["vignette"]
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W * vg[0])) ** 2 + ((yy - H / 2) / (H * vg[1])) ** 2)
        self.vignette = (1 - np.clip(r - vg[2], 0, 1) * vg[3])[..., None].astype(np.float32)
        gr = sc["post"]["grain"]
        rng = np.random.default_rng(gr["seed"])
        self.grain = [(rng.standard_normal((H // 2, W // 2)).astype(np.float32) * gr["amount"])
                      for _ in range(gr["frames"])]

    # ------------------------------------------------------------ кадр

    def render(self, t: float) -> np.ndarray:
        shot = P.shot_at(self.p, t)
        frame = self.render_screen(shot, t) if shot["kind"] == "screen" else self.render_character(shot, t)
        frame *= self.vignette
        g = self.grain[int(t * self.fps) % len(self.grain)]
        frame += cv2.resize(g, (self.W, self.H), interpolation=cv2.INTER_NEAREST)[..., None]
        return np.clip(frame, 0, 1)

    def avatar_frame(self, t: float, w: int, h: int, rot: float, lag: dict, zoom: float = 1.0) -> np.ndarray:
        """Аватар в окне w×h: следит за головой персонажа (с задержкой трекинга), моргает и зевает с ним."""
        av = self.p["scene"]["avatar"]
        fr = av["frame"]
        weights = {v["name"]: lag["w"].get(v["name"], 0.0) for v in av["variants"]}
        jaw = {}
        jv = self.p["scene"]["character"].get("jaw", {}).get("variant")
        if jv:
            jaw = {jv: (av["jaw_pivot_y"], lag.get("jaw", 1.0))}
        rgba = self.arig.compose(weights, jaw)
        s = h / fr["height_px"] * zoom
        bob = fr["bob"][0] * math.sin(t * fr["bob"][1])
        fo = fr["follow"]
        m = A.affine(s, fr["center"][0] - fo[0] * lag["dx"], fr["center"][1] + fo[1] * lag["dy"] - bob, w, h,
                     rot * fo[2] + fr["sway"][0] * math.sin(t * fr["sway"][1]))
        return A.warp_rgba(rgba, m, (w, h))

    def render_character(self, shot: dict, t: float) -> np.ndarray:
        W, H = self.W, self.H
        sc = self.p["scene"]
        bg, ch, du = sc["background"], sc["character"], sc["dust"]
        cx, cy, z = P.camera(self.p, shot, t)
        st = self.acting.state(t)
        cam = A.Camera(W, H, self.base, self.home)
        mb = cam.matrix(cx, cy, z, bg["parallax"], layer_scale=bg["scale"])
        frame = A.warp(self.room, mb, (W, H))
        if self.lab is not None:
            tw = A.twinkle_layer(self.lab, self.nbulbs, t, self.glow)
            frame += A.warp(tw, mb, (W, H)) * bg["twinkle"]["gain"]
        frame *= bg["light_gain"][0] + bg["light_gain"][1] * st["gain"]
        m = cam.matrix(cx, cy, z, 1.0)
        jaw = {}
        if "jaw" in ch and "jaw" in st:
            jaw = {ch["jaw"]["variant"]: (ch["jaw"]["pivot_y"], st["jaw"])}
        src = self.rig.compose(st["w"], jaw)
        mx, my = self.body.maps(m, (W, H), st["rot"], st["dx"], st["dy"], st["breath"],
                                st["breath"] * ch["body"]["shoulders"])
        layer = A.remap_rgba(src, mx, my)
        lm = cv2.remap(self.light, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        layer[..., :3] = layer[..., :3] * st["gain"] + st["tint"][None, None, :] * (lm * layer[..., 3])[..., None]
        hx, hy = self.home
        shift = (-(cx - hx) * du["shift"], -(cy - hy) * du["shift"])
        self.dust.draw(frame, t, shift, amount=0.0, bokeh_amount=0.9)  # боке гирлянды — за персонажем
        A.over(frame, layer)
        if self.fg_desk is not None:
            fg = sc["foreground"]["desk_edge"]
            mf = cam.matrix(cx, cy, z, fg["parallax"])
            A.over(frame, A.warp_rgba(self.fg_desk, place(mf, *fg["place"]), (W, H)))
        fs = du["front_shift"]
        self.dust.draw(frame, t, (shift[0] * fs, shift[1] * fs), amount=1.0, bokeh_amount=0.0)
        for ov in shot.get("overlays", []):
            if ov["type"] == "stream_card":
                self._card(frame, ov, t)
            elif ov["type"] == "hud":
                A.over_at(frame, U.hud(ov["label"], ov["value"], ov["size"]), *ov["at"])
        return frame

    def _card(self, frame: np.ndarray, ov: dict, t: float) -> None:
        k = A.clamp01((t - ov["appear"]) / ov["pop"])
        if k <= 0:
            return
        card, slot = U.stream_card(ov["size"][0], ov["size"][1], t, ov["viewers"], ov["title"])
        sw, sh = slot[2] - slot[0], slot[3] - slot[1]
        lag = self.acting.state(max(0.0, t - ov["lag"]))
        av = self.avatar_frame(t, sw, sh, lag["rot"], lag)
        A.over(card[slot[1]:slot[3], slot[0]:slot[2]], av)
        s = A.ease_out_back(k, 1.8)
        if s < 0.999:
            nh, nw = max(2, int(card.shape[0] * s)), max(2, int(card.shape[1] * s))
            card = cv2.resize(card, (nw, nh), interpolation=cv2.INTER_LINEAR)
        cx, cy = ov["center"]
        A.over_at(frame, card, int(cx - card.shape[1] / 2), int(cy - card.shape[0] / 2), min(1.0, k * 2.5))

    def render_screen(self, shot: dict, t: float) -> np.ndarray:
        W, H = self.W, self.H
        sp = self.p["scene"]["screen_plate"]
        ui = shot["ui"]
        k = 1 / sp["scale"]
        sx0, sy0, sx1, sy1 = (int(v * k) for v in sp["screen"])
        cx, cy, z = P.camera(self.p, shot, t)
        cam = A.Camera(W, H, self.base, self.home)
        frame = A.warp(self.desk, cam.matrix(cx, cy, z, 1.0, layer_scale=sp["scale"]), (W, H))
        vw = ui["viewers"]
        viewers = U.viewers_value(t, vw["t0"], vw["t1"], vw["from"], vw["to"])
        plus = (ui["plus"]["t"], ui["plus"]["value"]) if ui.get("plus") else None
        msgs = [U.Msg(m["t"], m["user"], tuple(m["color"]), m["text"], m.get("big", False)) for m in ui["chat"]]
        img, av = U.screen(t, sx1 - sx0, sy1 - sy0, msgs, viewers, plus, ui["title"])
        if self.arig is not None and ui.get("avatar"):
            ua = ui["avatar"]
            lag = self.acting.state(t)
            names = [v["name"] for v in self.p["scene"]["avatar"]["variants"]]
            lag["w"] = {n: 0.0 for n in names}
            blink = self.p["scene"]["character"].get("blink_variant", "blink")
            if blink in lag["w"]:
                lag["w"][blink] = A.blink_weight(t, ua.get("blinks", []))
            lag["dx"], lag["dy"] = 0.0, 0.0
            aw, ah = av[2] - av[0], av[3] - av[1]
            A.over(img[av[1]:av[3], av[0]:av[2]],
                   self.avatar_frame(t, aw, ah, ua["rot_amp"] * math.sin(t * ua["rot_freq"]), lag, zoom=ua["zoom"]))
        mu = A.warp_rgba(img, place(cam.matrix(cx, cy, z, 1.0), 1.0, sx0, sy0), (W, H))
        small = cv2.resize(mu[..., :3], (W // 8, H // 8), interpolation=cv2.INTER_AREA)
        glow = cv2.resize(A.blur(small, 6), (W, H), interpolation=cv2.INTER_LINEAR)
        gl = ui.get("glow", [0.55, 0.1, 9])
        frame += glow * (gl[0] + gl[1] * math.sin(t * gl[2]))
        A.over(frame, mu)
        d = ui.get("dust", [0.8, 0.5])
        self.dust.draw(frame, t, (0, 0), amount=d[0], bokeh_amount=d[1])
        return frame


def place(m: np.ndarray, k: float, wx: float, wy: float) -> np.ndarray:
    """Матрица спрайта: (масштаб k, левый верх в точке мира wx, wy) ∘ камера."""
    out = m.copy()
    out[:, :2] = m[:, :2] * k
    out[:, 2] = m[:, :2] @ np.float32([wx, wy]) + m[:, 2]
    return out


def desk_edge(w: int, h: int, top: int) -> np.ndarray:
    """Край стола у камеры: тёмная столешница, по верху — размытая радужная полоса света клавиатуры."""
    img = np.zeros((h, w, 4), np.float32)
    y = np.arange(h, dtype=np.float32)[:, None]
    a = np.clip((y - top) / 6, 0, 1)
    col = np.float32([0.035, 0.04, 0.07]) + np.clip(1 - (y - top) / 60, 0, 1)[..., None] * np.float32([0.10, 0.20, 0.28])
    img[..., :3] = col * a[..., None]
    img[..., 3] = a
    hue = np.linspace(0, 179, w, dtype=np.float32)
    rainbow = cv2.cvtColor(np.dstack([hue, np.full_like(hue, 200), np.full_like(hue, 255)]).astype(np.uint8),
                           cv2.COLOR_HSV2RGB)[0].astype(np.float32) / 255.0
    band = np.exp(-((y - top - 14) / 7) ** 2) * 0.9
    img[..., :3] += band[..., None] * rainbow[None, :, :] * a[..., None]
    return A.blur(img, 5.0)
