"""VRM-истории «Витубики»: героини (модель, образ, кресло) и всё, чего нет у воксельной сцены, — 3D-аватар на мониторе
эфира, 2D-кот, кивки, поклоны, жесты рук, взгляд. Это события ``"vrm"`` фраз плана (только для ``--engine vrm``)::

    "vrm": {"heroine": "vera", "avatars": ["trial", "vera_oc"]}            # в корне плана
    {"text": "…", "vrm": [{"at": "руку", "arms": "wave", "dur": 2.2},      # в фразе
                          {"at": "она", "av": "mirror", "who": "trial"},
                          {"nod": 2}, {"bow": 1.6}, {"look": "webcam", "dur": 2},
                          {"cat": {"x": 760, "y": 1700}, "until": "end"},
                          {"av_show": ["trial"]}, {"av_cam": "full"}, {"label": {"who": "trial", "text": "бывшая"}},
                          {"badge": "трекинг: лицо и руки", "dur": 3}]}

``at``/``dt``/``until``/``dur`` — как у событий ``do`` (слово-якорь фразы). Аватар: ``av`` — действие модели
(mirror — повторяет за героиней, idle, png — застывшая картинка PNGtuber (только моргает), dance, mug с ``drop`` —
словом, когда роняет кружку, bow, showcase).
"""

from __future__ import annotations

from typing import Any

from app import vtubika_plan as P

TWIST = "VRM1_Constraint_Twist_Sample.vrm"
GIRL = "three-vrm-girl.vrm"

# Героини: модель, образ (look.ts), кресло (низ спинки, у ниже ростом — ниже), вещи на голове.
HEROINES: dict[str, dict[str, Any]] = {
    "alina": {"model": TWIST, "chair_y": 0.79, "headphones": True,
              "look": {"outline": 0.0034, "shade": "#a28ad6", "toony": 0.97, "shift": -0.05, "rim": "#ff8fd0", "rimPower": 4,
                       "tint": {"Tops": "#8e86c8"}}},
    # Вера, художница 20–25: тёмно-каштановые волосы, горчичная кофта в пятнах краски, кремовые наушники.
    "vera": {"model": GIRL, "chair_y": 0.775,
             "headphones": {"kind": "headphones", "color": "#2c2436", "accent": "#8a4a2a", "ring": 0.02, "scale": 1.06, "lift": 0.035},
             "look": {"outline": 0.0034, "shade": "#a690c8", "toony": 0.97, "shift": -0.05, "rim": "#ffb08a", "rimPower": 4,
                      "skin": "^(face|body)", "skinShade": "#e0a0a0",
                      "repaint": {"Hair": ["#140c10", "#7a4a3a"], "Tops": ["#4a3010", "#e8b44a"]},
                      "stains": {"Tops": {"colors": ["#3f8cff", "#ff5c8a", "#2ec27e", "#ffffff", "#7a4dff"], "n": 46, "seed": 11,
                                          "size": 0.028}}}},
    # Ника, стримерка-PNGtuber: медовые светлые волосы, бирюзовая толстовка, белые наушники с бирюзовым кольцом.
    # Модель — та же, что у Веры: руки Twist-модели при печати/взмахе проходят сквозь корпус (проверка рук), у этой — чисто.
    "nika": {"model": GIRL, "chair_y": 0.775,
             "headphones": {"kind": "headphones", "color": "#f4f1ff", "accent": "#35d0c0", "ring": 0.02, "scale": 1.06, "lift": 0.035},
             "look": {"outline": 0.0034, "shade": "#9aa0d0", "toony": 0.97, "shift": -0.05, "rim": "#8ff0e0", "rimPower": 4,
                      "skin": "^(face|body)", "skinShade": "#e8a8a0", "tint": {"Hair": "#ffffff"},
                      "repaint": {"Hair": ["#c4963c", "#fff2c0"], "Tops": ["#0e5a5a", "#6fe0d0"], "EyeIris": ["#0a3a3a", "#6adfc8"]}}},
}

# Модели на мониторе: «пробная» (готовая 3D-модель: светлые мятные волосы, тёмно-синяя спортивная кофта, ушки) и
# свой персонаж Веры (сиреневые волосы, белый верх, звезда-заколка, фиолетовые глаза).
AVATARS: dict[str, dict[str, Any]] = {
    "trial": {"model": TWIST,
              "look": {"outline": 0.004, "shade": "#8fa0d8", "toony": 0.97, "rim": "#9af0ff", "rimPower": 4,
                       "repaint": {"HAIR": ["#2a5a64", "#e6fff8"], "Tops": ["#0c1028", "#3c4c94"], "Bottoms": ["#141428", "#4a4a70"]}},
              "accessories": [{"kind": "ears", "color": "#e8fbf6", "accent": "#7ad8ff", "scale": 1.0}]},
    "vera_oc": {"model": GIRL,
                "look": {"outline": 0.004, "shade": "#b08ad8", "toony": 0.97, "rim": "#ff9ad6", "rimPower": 4, "skin": "^(face|body)",
                         "repaint": {"Hair": ["#9a5ab8", "#fff0fc"], "Tops": ["#7a6aa0", "#fbf6ff"], "EyeIris": ["#2a0a40", "#d08aff"],
                                     "Bottoms": ["#2a1a4a", "#8a6ac8"]}},
                "accessories": [{"kind": "star", "color": "#ffd36a", "side": 1}]},
    # Картинка Ники (сначала неподвижная PNGtuber, потом оживлённая модель): чёрно-синие волосы,
    # красный верх, красные глаза и чёрные ушки.
    "kira": {"model": GIRL,
             "look": {"outline": 0.004, "shade": "#9a8ad0", "toony": 0.97, "rim": "#ff8a9a", "rimPower": 4, "skin": "^(face|body)",
                      "tint": {"Hair": "#ffffff"},
                      "repaint": {"Hair": ["#101840", "#6a9cff"], "Tops": ["#5a0f1e", "#ff7a8a"], "EyeIris": ["#4a0a1a", "#ff6a7a"],
                                  "Bottoms": ["#1a1a30", "#5a5a90"]}},
             "accessories": [{"kind": "ears", "color": "#14122a", "accent": "#ff6a7a", "scale": 1.0}]},
}

# Камеры сцены аватара (модель стоит в начале координат, рост ≈ 1.55 м): весь рост, по колено, по грудь, двое.
AV_CAMS: dict[str, dict] = {
    "full": {"pos": [0, 0.95, 3.7], "target": [0, 0.84, 0], "fov": 30},
    "mid": {"pos": [0, 1.2, 2.7], "target": [0, 1.08, 0], "fov": 30},
    "close": {"pos": [0, 1.36, 1.7], "target": [0, 1.3, 0], "fov": 30},
    "two": {"pos": [0, 1.02, 4.3], "target": [0, 0.92, 0], "fov": 30},
}
LOOKS = {"webcam": [0.0, 1.5, 1.1], "monitor": [0, 1.43, 1.15], "chat": [-0.5, 1.36, 0.9], "down": [0.05, 1.08, 0.4]}


def heroine(plan: dict) -> dict[str, Any]:
    name = ((plan.get("vrm") or {}).get("heroine")) or "alina"
    if name not in HEROINES:
        raise P.PlanError(f"героиня VRM: одна из {', '.join(HEROINES)}")
    return HEROINES[name]


def _windows(plan: dict, words: list[dict], total: float) -> list[tuple[int, dict, float, float | None]]:
    """События ``vrm`` всех фраз со временем: (номер фразы, событие, t, until)."""
    times = P.token_times(plan, words)
    out = []
    for n, s in enumerate(plan["sentences"]):
        st = times[n]
        for ev in s.get("vrm") or []:
            t = round(P.anchor(st, ev.get("at"), n) + float(ev.get("dt") or 0), 3)
            until = (P.anchor(st, ev["until"], n) + float(ev.get("dt_end") or 0) if ev.get("until") else
                     t + float(ev["dur"]) if ev.get("dur") else None)
            if "span" in ev:
                k = min(len(times) - 1, n + int(ev["span"]))
                until = times[k][-1]["end"] + float(ev.get("dt_end") or 0)
            if "drop" in ev:
                ev = {**ev, "drop_t": round(P.anchor(st, ev["drop"], n) + float(ev.get("drop_dt") or 0), 3)}
            out.append((n, ev, t, None if until is None else round(min(until, total), 3)))
    return out


def stage(plan: dict, words: list[dict], total: float) -> dict[str, Any]:
    """События ``vrm`` → части спеки: avatar (сцена аватара), cats, nods, bows, окна рук и взгляда."""
    cfg = plan.get("vrm") or {}
    av_ids = list(cfg.get("avatars") or [])
    for a in av_ids:
        if a not in AVATARS:
            raise P.PlanError(f"аватар VRM: один из {', '.join(AVATARS)}")
    acts: list[dict] = []
    show: list[dict] = []
    cams: list[dict] = []
    labels: list[dict] = []
    badge: list[dict] = []
    cats: list[dict] = []
    nods: list[dict] = []
    bows: list[dict] = []
    arms: list[tuple[float, float, str, str]] = []
    looks: list[tuple[float, float, list[float]]] = []
    for _n, ev, t, until in _windows(plan, words, total):
        if "av" in ev:
            a = {"t": t, "who": ev.get("who") or (av_ids[0] if av_ids else "trial"), "act": ev["av"]}
            if "drop_t" in ev:
                a["drop"] = ev["drop_t"]
            acts.append(a)
        if "av_show" in ev:
            show.append({"t": t, "who": list(ev["av_show"])})
        if "av_cam" in ev:
            cams.append({"t": t, **AV_CAMS[ev["av_cam"]]})
        if "label" in ev:
            labels.append({"t": t, **({"t1": until} if until else {}), **ev["label"]})
        if "badge" in ev:
            badge.append({"t0": t, "t1": until or t + 3.0, "text": ev["badge"]})
        if "cat" in ev:
            cats.append({"t0": t, "t1": until or total, **ev["cat"]})
        if "nod" in ev:
            nods.append({"t": t, "n": int(ev["nod"]), "amp": float(ev.get("amp", 10))})
        if "bow" in ev:
            bows.append({"t": t, "dur": float(ev["bow"]), "deg": float(ev.get("deg", 16))})
        if "arms" in ev:
            left, right = ("idle", ev["arms"]) if ev["arms"] in ("wave", "mouth", "chin") else (ev["arms"], ev["arms"])
            arms.append((t, until or t + 2.0, left, right))
        if "look" in ev:
            looks.append((t, until or t + 2.0, LOOKS[ev["look"]] if isinstance(ev["look"], str) else ev["look"]))
    out: dict[str, Any] = {"cats": cats, "nods": nods, "bows": bows, "arms": arms, "looks": looks}
    if av_ids:
        models = []
        for i, a in enumerate(av_ids):
            m = {"id": a, **AVATARS[a]}
            models.append(m)
        out["avatar"] = {"models": models, "acts": sorted(acts, key=lambda k: k["t"]), "show": sorted(show, key=lambda k: k["t"]),
                         "cams": sorted(cams, key=lambda k: k["t"]) or [{"t": 0, **AV_CAMS["mid"]}], "labels": labels,
                         "badge": badge, "positions": cfg.get("positions") or {}}
        for m in models:
            if m["id"] in out["avatar"]["positions"]:
                m["x"] = float(out["avatar"]["positions"][m["id"]])
    return out


def merge_arms(base: list[dict], windows: list[tuple[float, float, str, str]], dur: float = 0.8) -> list[dict]:
    """Ключи рук по действиям + окна жестов (машет, ладонь ко рту): в окне — жест, после — снова по действиям."""
    if not windows:
        return base

    def base_at(t: float) -> tuple[str, str]:
        cur = (base[0]["left"], base[0]["right"])
        for k in base:
            if k["t"] <= t:
                cur = (k["left"], k["right"])
        return cur

    def at(t: float) -> tuple[str, str]:
        for a, b, left, right in windows:
            if a <= t < b:
                return left, right
        return base_at(t)

    cuts = sorted({k["t"] for k in base} | {w[0] for w in windows} | {w[1] for w in windows})
    out = [{**base[0]}]
    for t in cuts:
        if t <= out[0]["t"]:
            continue
        lr = at(t + 1e-4)
        if (out[-1]["left"], out[-1]["right"]) != lr:
            out.append({"t": round(t, 3), "dur": dur, "left": lr[0], "right": lr[1]})
    return out


def merge_look(base: list[dict], windows: list[tuple[float, float, list[float]]]) -> list[dict]:
    """Взгляд: ключи по действиям и чату + окна «смотрит туда» (после окна — куда смотрела бы)."""
    if not windows:
        return base

    def base_at(t: float) -> list[float]:
        cur = base[0]["target"]
        for k in base:
            if k["t"] <= t:
                cur = k["target"]
        return cur

    out = [k for k in base if not any(a <= k["t"] < b for a, b, _ in windows)]
    for a, b, tgt in windows:
        out.append({"t": round(a, 3), "target": list(tgt), "head": 0.8})
        out.append({"t": round(b, 3), "target": base_at(b), "head": 0.7})
    return sorted(out, key=lambda k: k["t"])
