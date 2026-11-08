"""Пресет способа «AI-аниме 2D»: чтение и проверка JSON, смета картинок, игра персонажа и камера по ключам.

Пресет (пример — tools/anime2d_presets/tema_yawn.json) описывает всё, что нужно для ролика:

* ``generation`` — модель, цены за картинку по разрешению, контейнер, стиль и список картинок
  (``id``, ``file``, ``role``, ``res``, ``ar``, ``refs``, ``prompt``; у правок — ``edit_of``;
  забракованные попытки — ``rejected`` с причиной);
* ``scene`` — как из картинок собрать слои (фон, персонаж и его варианты лица, аватар, экран, частицы);
* ``acting`` — дыхание, покачивание, моргания, смены лица по отрезкам, свет монитора;
* ``shots`` — кадры (``character`` / ``screen``) с камерой и надписями поверх;
* ``captions``, ``music``, ``patches`` (правки картинок кодом, например закрыть чужой логотип).

Числа в ключах — ``[[t, значение], ...]`` (плавно между соседями) или просто число.
Модуль не делает платных вызовов: генерация — tools/anime2d_polza.py, запуск — tools/anime2d_run.py.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from tools import anime2d as A

ROLES = {"sheet", "background", "character", "variant", "avatar", "prop"}


class PresetError(ValueError):
    """Пресет с ошибкой — понятное сообщение, что поправить."""


# ---------------------------------------------------------------- чтение и проверка


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text("utf-8"))
    validate(data)
    return data


def images(preset: dict) -> dict[str, dict]:
    return {im["id"]: im for im in preset["generation"]["images"]}


def validate(p: dict) -> None:
    """Обязательные поля, ссылки между картинками и кадрами, цены на все разрешения."""
    for key in ("id", "output", "generation", "scene", "acting", "shots"):
        if key not in p:
            raise PresetError(f"в пресете нет «{key}»")
    gen = p["generation"]
    for key in ("model", "price_rub", "images"):
        if key not in gen:
            raise PresetError(f"generation: нет «{key}»")
    seen: set[str] = set()
    for im in gen["images"]:
        iid = im.get("id")
        if not iid or iid in seen:
            raise PresetError(f"картинка без id или id повторяется: {iid!r}")
        for key in ("file", "res", "ar", "prompt"):
            if not im.get(key):
                raise PresetError(f"картинка {iid}: нет «{key}»")
        if im.get("role", "prop") not in ROLES:
            raise PresetError(f"картинка {iid}: роль {im.get('role')!r}, допустимо {sorted(ROLES)}")
        if im["res"] not in gen["price_rub"]:
            raise PresetError(f"картинка {iid}: нет цены для {im['res']} в generation.price_rub")
        for ref in im.get("refs", []) + ([im["edit_of"]] if im.get("edit_of") else []):
            if ref not in seen:
                raise PresetError(f"картинка {iid}: референс {ref!r} должен быть описан раньше")
        seen.add(iid)
    sc = p["scene"]
    used = [sc.get("background", {}).get("image"), sc.get("character", {}).get("image"),
            sc.get("avatar", {}).get("image"), sc.get("screen_plate", {}).get("image")]
    for part in ("character", "avatar"):
        used += [v["image"] for v in sc.get(part, {}).get("variants", [])]
    used += [pt["image"] for pt in p.get("patches", [])]
    for iid in used:
        if iid and iid not in seen:
            raise PresetError(f"сцена ссылается на неизвестную картинку {iid!r}")
    dur = p["output"]["duration"]
    last = 0.0
    for i, s in enumerate(p["shots"]):
        if s.get("kind") not in ("character", "screen"):
            raise PresetError(f"кадр {i}: kind должен быть character или screen")
        if abs(s["t0"] - last) > 1e-6 or s["t1"] <= s["t0"]:
            raise PresetError(f"кадр {i}: отрезки должны идти подряд без дыр ({s['t0']}–{s['t1']})")
        last = s["t1"]
    if abs(last - dur) > 1e-6:
        raise PresetError(f"кадры кончаются на {last} с, а длительность {dur} с")
    names = {v["name"] for v in sc.get("character", {}).get("variants", [])}
    for seg in p["acting"].get("segments", []):
        for face in seg.get("faces", []):
            if face[0] not in names:
                raise PresetError(f"acting: вариант лица {face[0]!r} не описан в scene.character.variants")


# ---------------------------------------------------------------- смета


def plan(preset: dict, src: Path, only: list[str] | None = None, force: bool = False) -> list[dict]:
    """Какие картинки надо сгенерировать (нет файла в ``src`` или ``force``) и почём: [{image, rub}]."""
    prices = preset["generation"]["price_rub"]
    out = []
    for im in preset["generation"]["images"]:
        if only and im["id"] not in only:
            continue
        if not force and (src / im["file"]).is_file():
            continue
        out.append({"image": im, "rub": float(prices[im["res"]])})
    return out


def spent_before(preset: dict) -> float:
    """Сколько стоили картинки пресета вместе с забракованными попытками (для справки в смете)."""
    prices = preset["generation"]["price_rub"]
    total = 0.0
    for im in preset["generation"]["images"]:
        total += float(prices[im["res"]])
        total += sum(float(r.get("rub", prices[im["res"]])) for r in im.get("rejected", []))
    return total


def prompt_text(preset: dict, im: dict) -> str:
    """Промпт картинки; ``{style}`` заменяется на generation.style (у старых пресетов стиль вписан прямо)."""
    return im["prompt"].replace("{style}", preset["generation"].get("style", ""))


# ---------------------------------------------------------------- время


def value(t: float, spec: Any) -> float:
    """Число или ключи ``[[t, v], ...]``."""
    if isinstance(spec, (int, float)):
        return spec
    return A.keys(t, spec)


def step(t: float, t0: float, t1: float) -> float:
    """1 на [t0, t1), иначе 0 — смена рисунка кадром, как в аниме (без наплыва)."""
    return 1.0 if t0 <= t < t1 else 0.0


def shot_at(preset: dict, t: float) -> dict:
    for s in preset["shots"]:
        if s["t0"] <= t < s["t1"]:
            return s
    return preset["shots"][-1]


class Acting:
    """Состояние персонажа в момент t: голова, дыхание, веса вариантов лица, свет монитора, челюсть."""

    def __init__(self, preset: dict) -> None:
        self.a = preset["acting"]
        ch = preset["scene"]["character"]
        self.names = [v["name"] for v in ch["variants"]]
        self.blink = ch.get("blink_variant", "blink")

    def segment(self, t: float) -> dict:
        for seg in self.a.get("segments", []):
            if t < seg["until"]:
                return seg
        return {}

    def state(self, t: float) -> dict:
        a = self.a
        br = a["breath"]
        breath = br["amp"] * math.sin(2 * math.pi * t / br["period"])
        sw = a["sway"]
        rot = sw["rot"][0] * A.noise1(t * sw["rot"][1], sw["rot"][2])
        dx = sw["dx"][0] * A.noise1(t * sw["dx"][1], sw["dx"][2])
        dy = 0.0
        w = {n: 0.0 for n in self.names}
        seg = self.segment(t)
        for name, t0, t1 in seg.get("faces", []):
            w[name] = max(w[name], step(t, t0, t1))
        if "rot" in seg:
            rot += value(t, seg["rot"])
        tr = seg.get("tremble")
        if tr:
            rot += tr["amp"] * math.sin(t * tr["freq"]) * w[tr["variant"]]
        if "dy" in seg:
            dy += value(t, seg["dy"])
        if "breath" in seg:
            breath += value(t, seg["breath"])
        if self.blink in w:
            w[self.blink] = max(w[self.blink], 1.0 if A.blink_weight(t, a.get("blinks", [])) >= 0.5 else 0.0)
        f = a["flicker"]
        flick = 1.0 + f["amp"] * math.sin(t * f["f1"]) * math.sin(t * f["f2"]) \
            + f["noise"] * A.noise1(t * f["noise_speed"], f["noise_seed"])
        tint = np.float32([0.0, 0.0, 0.0])
        for item in a.get("tint", []):
            if "env" in item:
                tint += np.float32(item["rgb"]) * A.env(t, *item["env"])
            else:
                wb = item.get("wobble", [1.0, 0.0, 0.0])
                tint += np.float32(item["rgb"]) * A.smooth((t - item["from"]) / item["ramp"]) \
                    * (wb[0] + wb[1] * math.sin(t * wb[2]))
        out = {"rot": rot, "dx": dx, "dy": dy, "breath": breath, "w": w, "gain": flick, "tint": tint}
        if "jaw" in a:
            j = a["jaw"]
            out["jaw"] = value(t, j["keys"]) + j["tremble"][0] * math.sin(t * j["tremble"][1])
        return out


def camera(preset: dict, shot: dict, t: float) -> tuple[float, float, float]:
    """(cx, cy, z) кадра: база по ключам + «ручная» дрожь камеры."""
    c = shot["camera"]
    if "noise" in c:
        nx, ny = c["noise"]["x"], c["noise"]["y"]
        cx = value(t, c["x"]) + nx[0] * A.noise1(t * nx[1], nx[2])
        cy = value(t, c["y"]) + ny[0] * A.noise1(t * ny[1], ny[2])
    else:
        cn = preset["camera_noise"]
        n1, n2 = A.noise1(t * cn["x"][0], cn["x"][1]), A.noise1(t * cn["y"][0], cn["y"][1])
        cx = value(t, c["x"]) + c["shake"][0] * n1
        cy = value(t, c["y"]) + c["shake"][1] * n2
    z = value(t, c["z"])
    if "x_add" in c:
        cx += value(t, c["x_add"])
    return cx, cy, z


def caption_words(preset: dict) -> list[dict]:
    return [{"word": w, "start": s, "end": e} for w, s, e in preset.get("captions", {}).get("words", [])]
