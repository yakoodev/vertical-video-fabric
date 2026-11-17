"""Мини-гайды «Витубики»: графика объяснялки поверх ведущей (VRM) — слои моушн-компоновщика на словах озвучки.

Формат (план ``tools/vtubika_plans/guide_*.json``, рендер — тот же ``tools/vtubika_story.py --engine vrm``): ведущая
говорит в камеру, а поверх кадра по словам появляются плоские карточки бренда — заголовок типа, строки с значками
(«что нужно»), пример аватара, настоящие скриншоты сайта в телефоне, ведущая в кружке, пока кадр занят графикой.
Всё рисует движок ``frontend/src/motion`` (слои ``ui``, ``image``, ``video``, ``shape``, ``text``) на этапе субтитров
(``vtubika_story.burn_captions``); 3D-сцена об этом не знает.

План::

    "guide": {"accent": "#ff8a4c", "plate": "#2b2f36",          # палитра бренда (слой ui, тема brand)
              "badge": {"text": "МИНИ-ГАЙД · VTUBIKA", "from": 1.7}},  # чип серии сверху до конца ролика
    "sentences": [{"text": "…", "gfx": [
        {"kind": "scrim", "until": "end"},                                   # притушить кадр под графикой
        {"kind": "pip", "until": "end"},                                     # ведущая в кружке (её же кадр)
        {"kind": "title", "chip": "1 из 3", "text": "PNGTuber", "color": "#8edfd1", "y": 0.1},
        {"kind": "rows", "at": "Камера", "items": [{"icon": "mic", "text": "Только микрофон", "at": "микрофона"}]},
        {"kind": "image", "src": "D:/…/catalog.png", "device": "phone", "w": 420, "x": 0.5, "y": 0.45},
        {"kind": "seq", "pattern": "D:/…/f_{:03d}.png", "count": 48, "fps": 12, "w": 420},
        {"kind": "pngtuber", "idle": "…/idle.png", "talk": "…/talk.png", "blink": "…/blink.png", "w": 520},
        {"kind": "chip", "text": "молчу", "at": "молчишь"},
        {"kind": "text", "text": "vtubika.store/guides", "size": 64},
        {"kind": "layer", "layer": {…любой слой моушна…}}]}]

Время пункта — как у событий ``do``: ``at`` (слово фразы, ``end``; нет — начало фразы), ``dt``; конец — ``until``
(слово / ``end`` / ``next`` — начало следующей фразы) + ``dt_end``, или ``dur``, или ``span: k`` — конец фразы n+k;
по умолчанию — до начала следующей фразы; ``until_s: k`` — слово ``until`` берётся из фразы n+k. У строк ``rows`` и чипов внутри — свои ``at`` (слово той же фразы).
Пути картинок — абсолютные; в ``assets.json`` компоновщика они попадают под ключами ``g…``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app import vtubika_plan as P

W, H = 1080, 1920
ACCENT = "#ff8a4c"  # бренд vtubika.store (assets/brand/vtubika): оранжевый, персик, мята, сирень, графит
PLATE = "#2b2f36"
SCRIM = "#12141a"
INK = "#0b0812"
# Слои по дорожкам: подложка ниже карточек, кружок ведущей — над ними, субтитры (9) и крючок (10) — сверху.
TRACK = {"scrim": 2, "panel": 3, "media": 4, "ui": 5, "chip": 6, "pip": 7, "text": 6, "badge": 8}
PIP_ASSET = "1"  # тот же ключ, что у перетекающих переходов (vtubika_story.FLOW_ASSET): кадр самой сцены


class GuideError(ValueError):
    """Пункт графики гайда неверен."""


def _times(plan: dict, words: list[dict]) -> list[list[dict]]:
    return P.token_times(plan, words)


def _window(item: dict, n: int, times: list[list[dict]], total: float) -> tuple[float, float] | None:
    st = times[n]
    t0 = P.anchor(st, item.get("at"), n) + float(item.get("dt") or 0)
    if "dur" in item:
        t1 = t0 + float(item["dur"])
    elif "span" in item:
        k = min(len(times) - 1, n + int(item["span"]))
        t1 = times[k][-1]["end"] + float(item.get("dt_end") or 0)
    else:
        until = item.get("until", "next")
        if until == "next":
            t1 = (times[n + 1][0]["start"] if n + 1 < len(times) else total) + float(item.get("dt_end") or 0)
        else:
            # until_s: k — слово ``until`` фразы n+k (подложка на несколько фраз до слова в последней).
            k = min(len(times) - 1, n + int(item.get("until_s", 0)))
            t1 = P.anchor(times[k], until, k) + float(item.get("dt_end") or 0)
    t0, t1 = max(0.0, t0), min(total, t1)
    if t1 <= t0 + 0.02:
        return None  # вне ролика (кадр-превью vtubika_stills — короткий отрезок) или пустое окно
    return round(t0, 3), round(t1, 3)


def _local(item_at: Any, n: int, times: list[list[dict]], t0: float, dt: float = 0.0) -> float:
    """Секунда слоя (от его начала), когда звучит слово ``item_at`` фразы n."""
    return round(max(0.0, P.anchor(times[n], item_at, n) + dt - t0), 3)


def _row_at(it: dict, n: int, times: list[list[dict]], t0: float) -> float:
    """Время строки/чипа внутри слоя: ``at`` — слово фразы n (или фразы n + ``s``: строка следующей фразы)."""
    k = min(len(times) - 1, n + int(it.get("s", 0)))
    return _local(it["at"], k, times, t0, float(it.get("dt", -0.1)))


ASSET_BASE = 80001  # id картинок гайда в assets.json: компоновщик берёт id файла числом (app.motion.asset_key)


def _asset(path: str | Path, assets: dict[str, str]) -> int:
    p = Path(path)
    if not p.is_file():
        raise GuideError(f"нет файла {path}")
    full = str(p.resolve())
    for k, v in assets.items():
        if v == full:
            return int(k)
    key = ASSET_BASE + len(assets)
    assets[str(key)] = full
    return key


def seq_apng(pattern: str, count: int, fps: float, first: int = 1) -> Path:
    """Кадры PNG с альфой → одна APNG по кругу (кэш рядом с кадрами): слой image рисует её кадрами с альфой
    (app/anim_frames.py) — один слой вместо слоя на кадр (у компоновщика предел 200 слоёв)."""
    import subprocess

    src = Path(str(pattern).format(first))
    key = hashlib.sha1(json.dumps([pattern, count, fps, first, src.stat().st_mtime]).encode()).hexdigest()[:10]
    out = src.parent / f"_anim_{key}.apng"
    if not out.is_file():
        ff_pattern = str(pattern).replace("{:03d}", "%03d")
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", f"{fps:g}", "-start_number", str(first), "-i", ff_pattern,
                               "-frames:v", str(count), "-plays", "0", "-f", "apng", str(out)],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if proc.returncode != 0 or not out.is_file():
            raise GuideError(f"APNG из {pattern}: {proc.stderr.strip()[-300:]}")
    return out


def _io(item: dict, default_in: str = "pop", default_out: str = "fade") -> dict:
    return {"in": {"type": item.get("in", default_in), "duration": float(item.get("in_dur", 0.32))},
            "out": {"type": item.get("out", default_out), "duration": float(item.get("out_dur", 0.22))}}


def _ui(lid: str, t0: float, t1: float, item: dict, props: dict, accent: str, plate: str, track: int) -> dict:
    return {"id": lid, "type": "ui", "start": t0, "end": t1, "x": float(item.get("x", 0.5)), "y": float(item.get("y", 0.3)),
            "track": track, "scale": float(item.get("scale", 1.0)),
            "props": {"theme": "brand", "accent": item.get("color") or accent, "plate": plate, **props},
            "shadow": {"color": "#000000", "blur": 40, "y": 14, "opacity": 0.45}, **_io(item)}


def _talk_windows(words: list[dict], t0: float, t1: float, gap: float = 0.16) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        if b <= t0 or a >= t1:
            continue
        a, b = max(a, t0), min(b, t1)
        if out and a - out[-1][1] < gap:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _pngtuber(lid: str, t0: float, t1: float, item: dict, words: list[dict], assets: dict[str, str],
              voice_from: float | None = None) -> list[dict]:
    """Картинка PNGtuber: говорит (talk) на словах озвучки внутри окна, молчит (idle) между ними, изредка моргает.
    На речи — подпрыгивает (как veadotube/PNGTuber+). ``voice_from`` — до этой секунды молчит, даже если голос звучит
    («молчишь — одна картинка, говоришь — другая»)."""
    idle, talk = _asset(item["idle"], assets), _asset(item["talk"], assets)
    blink = _asset(item["blink"], assets) if item.get("blink") else None
    w = float(item.get("w", 520))
    x, y = float(item.get("x", 0.5)), float(item.get("y", 0.42))
    base = {"type": "image", "x": x, "track": TRACK["media"], "shadow": {"color": "#000000", "blur": 30, "y": 12, "opacity": 0.35}}
    talks = _talk_windows(words, max(t0, voice_from or t0), t1)
    out: list[dict] = []
    cur = t0
    k = 0

    def idle_span(a: float, b: float) -> None:
        nonlocal k
        if b - a < 0.02:
            return
        # Моргание: раз в ~2.6 с молчания, 0.14 с.
        s = a
        while blink and b - s > 1.4:
            bt = s + 1.1 + (k % 3) * 0.5
            if bt + 0.14 > b - 0.2:
                break
            out.append({**base, "id": f"{lid}i{k}", "start": round(s, 3), "end": round(bt, 3), "y": y, "props": {"asset_id": idle, "w": w}})
            out.append({**base, "id": f"{lid}b{k}", "start": round(bt, 3), "end": round(bt + 0.14, 3), "y": y, "props": {"asset_id": blink, "w": w}})
            s = bt + 0.14
            k += 1
        out.append({**base, "id": f"{lid}i{k}", "start": round(s, 3), "end": round(b, 3), "y": y, "props": {"asset_id": idle, "w": w}})
        k += 1

    hop = float(item.get("hop", 0.012))
    for a, b in talks:
        idle_span(cur, a)
        d = b - a
        out.append({**base, "id": f"{lid}t{k}", "start": a, "end": b, "y": y, "props": {"asset_id": talk, "w": w},
                    "keys": {"y": [{"t": 0, "v": y}, {"t": round(min(0.09, d / 2), 3), "v": round(y - hop, 4), "ease": "outQuad"},
                                   {"t": round(min(0.2, d), 3), "v": y, "ease": "inQuad"}]}})
        k += 1
        cur = b
    idle_span(cur, t1)
    # Вход/выход всей картинки: первый и последний кусок — с наплывом.
    if out:
        out[0]["in"] = {"type": item.get("in", "pop"), "duration": 0.3}
        out[-1]["out"] = {"type": "fade", "duration": 0.2}
    return out


def _seq(lid: str, t0: float, t1: float, item: dict, assets: dict[str, str]) -> dict:
    """Последовательность PNG с альфой (поворот 3D-модели, анимированный ассет) — APNG по кругу одним слоем."""
    apng = seq_apng(str(item["pattern"]), int(item["count"]), float(item.get("fps", 12)), int(item.get("from", 1)))
    lay = {"id": lid, "type": "image", "start": t0, "end": t1, "x": float(item.get("x", 0.5)), "y": float(item.get("y", 0.42)),
           "track": TRACK["media"], "props": {"asset_id": _asset(apng, assets), "w": float(item.get("w", 480))}, **_io(item)}
    if item.get("shadow", True):
        lay["shadow"] = {"color": "#000000", "blur": 30, "y": 12, "opacity": 0.35}
    return lay


def _image(lid: str, t0: float, t1: float, item: dict, assets: dict[str, str], accent: str) -> dict:
    props: dict[str, Any] = {"asset_id": _asset(item["src"], assets), "w": float(item.get("w", 600))}
    for k in ("radius", "crop", "device", "deviceColor", "statusBar", "island", "flipX"):
        if k in item:
            props[k] = item[k]
    if item.get("border"):
        b = item["border"]
        props["border"] = b if isinstance(b, dict) else {"color": item.get("color") or accent, "width": 6}
    lay = {"id": lid, "type": "image", "start": t0, "end": t1, "x": float(item.get("x", 0.5)), "y": float(item.get("y", 0.42)),
           "track": TRACK["media"], "props": props, "shadow": {"color": "#000000", "blur": 40, "y": 16, "opacity": 0.5}, **_io(item)}
    if item.get("keys"):
        lay["keys"] = item["keys"]
    if item.get("scroll"):
        # Скролл экрана телефона: окно crop едет вниз по странице (доли высоты картинки).
        c = props.get("crop") or {"x": 0, "y": 0, "w": 1, "h": 1}
        a, b = item["scroll"]
        lay["keys"] = {**lay.get("keys", {}), "cropY": [{"t": float(item.get("scroll_t0", 0.6)), "v": float(a)},
                                                       {"t": round(t1 - t0 - 0.3, 3), "v": float(b), "ease": "inOutCubic"}]}
        props["crop"] = {**c, "y": float(a)}
    return lay


def _pip(lid: str, t0: float, t1: float, item: dict, accent: str) -> list[dict]:
    """Ведущая в кружке: её же кадр (видео сцены, ключ PIP_ASSET), вырезан вокруг лица, рамка акцентом."""
    # Окно вокруг лица на крупном плане face (ведущая смотрит в объектив, лицо правее центра): квадрат 605 px.
    crop = item.get("crop") or {"x": 0.34, "y": 0.29, "w": 0.56, "h": 0.315}
    size = float(item.get("w", 280))
    x, y = float(item.get("x", 0.82)), float(item.get("y", 0.635))
    ring = {"id": f"{lid}r", "type": "shape", "start": t0, "end": t1, "x": x, "y": y, "track": TRACK["pip"],
            "props": {"kind": "ellipse", "w": size + 20, "h": size + 20, "fill": item.get("color") or accent},
            "shadow": {"color": "#000000", "blur": 36, "y": 12, "opacity": 0.5}, **_io(item)}
    vid = {"id": lid, "type": "video", "start": t0, "end": t1, "x": x, "y": y, "track": TRACK["pip"],
           "props": {"asset_id": PIP_ASSET, "from": t0, "w": size, "radius": size / 2, "crop": crop}, **_io(item)}
    return [ring, vid]


def _text(lid: str, t0: float, t1: float, item: dict) -> dict:
    props = {"text": item["text"], "size": int(item.get("size", 64)), "weight": int(item.get("weight", 900)),
             "color": item.get("color", "#ffffff"), "maxWidth": int(item.get("maxWidth", 980)),
             "font": item.get("font", "Montserrat")}
    if item.get("stroke", True):
        props["stroke"] = {"color": INK, "width": int(item.get("stroke_w", 8))}
    return {"id": lid, "type": "text", "start": t0, "end": t1, "x": float(item.get("x", 0.5)), "y": float(item.get("y", 0.5)),
            "track": TRACK["text"], "props": props, **_io(item)}


def layers(plan: dict, words: list[dict], total: float) -> tuple[list[dict], dict[str, str]]:
    """Пункты ``gfx`` всех фраз → (слои моушна, картинки для assets.json). ``words`` — слова сценария на времени
    озвучки (как у субтитров)."""
    from app.motion_normalize import normalize_layers

    cfg = plan.get("guide") or {}
    accent, plate = cfg.get("accent", ACCENT), cfg.get("plate", PLATE)
    times = _times(plan, words)
    out: list[dict] = []
    assets: dict[str, str] = {}
    badge = cfg.get("badge")
    b0, b1 = (float(badge.get("from", 0.0)), min(total, float(badge.get("to", total)))) if badge else (0.0, 0.0)
    if badge and b1 > b0 + 0.02:
        out.append({"id": "gbadge", "type": "ui", "start": b0, "end": b1, "x": float(badge.get("x", 0.5)), "y": float(badge.get("y", 0.045)),
                    "track": TRACK["badge"], "props": {"component": "chip", "variant": badge.get("variant", "solid"), "text": badge["text"],
                                                        "icon": badge.get("icon", "sparkles"), "theme": "brand", "accent": accent, "plate": plate,
                                                        "size": int(badge.get("size", 30))},
                    "in": {"type": "slideDown", "duration": 0.35}, "out": {"type": "fade", "duration": 0.2}})
    for n, s in enumerate(plan["sentences"]):
        for j, item in enumerate(s.get("gfx") or []):
            kind = item.get("kind")
            lid = f"g{n}_{j}"
            win = _window(item, n, times, total)
            if win is None:
                continue
            t0, t1 = win
            if kind == "scrim":
                # Кадр под графикой — размыт и притушен (fx backdrop): ведущая и комната живут фоном, плашки читаются.
                out.append({"id": lid, "type": "fx", "start": t0, "end": t1, "x": 0.5, "y": 0.5, "track": TRACK["scrim"],
                            "props": {"kind": "backdrop", "intensity": float(item.get("opacity", 0.66)),
                                      "size": float(item.get("blur", 26)), "color": item.get("color", SCRIM)},
                            "in": {"type": "fade", "duration": float(item.get("in_dur", 0.3))},
                            "out": {"type": "fade", "duration": float(item.get("out_dur", 0.3))}})
            elif kind == "panel":
                out.append({"id": lid, "type": "shape", "start": t0, "end": t1, "x": float(item.get("x", 0.5)), "y": float(item.get("y", 0.4)),
                            "track": TRACK["panel"], "opacity": float(item.get("opacity", 1.0)),
                            "props": {"kind": "rect", "w": float(item.get("w", 960)), "h": float(item.get("h", 900)),
                                      "radius": float(item.get("radius", 44)), "fill": item.get("fill", "#1d2027"),
                                      "stroke": {"color": item.get("color") or "#3a3f4a", "width": float(item.get("stroke_w", 3))}},
                            "shadow": {"color": "#000000", "blur": 50, "y": 20, "opacity": 0.5}, **_io(item)})
            elif kind == "pip":
                out += _pip(lid, t0, t1, item, accent)
            elif kind == "title":
                props = {"component": "title", "variant": item.get("variant", "plain"), "label": item.get("chip"),
                         "title": item["text"], "text": item.get("sub"), "size": int(item.get("size", 96)), "w": float(item.get("w", 960))}
                out.append(_ui(lid, t0, t1, {"y": 0.12, **item}, {k: v for k, v in props.items() if v is not None}, accent, plate, TRACK["ui"]))
            elif kind == "rows":
                rows = []
                for it in item["items"]:
                    row = {k: v for k, v in it.items() if k not in ("at", "dt", "s")}
                    if "at" in it:
                        row["at"] = _row_at(it, n, times, t0)
                    rows.append(row)
                props = {"component": "checklist", "variant": item.get("variant", "card"), "reveal": item.get("reveal", "appear"),
                         "label": item.get("label"), "title": item.get("title"), "items": rows, "w": float(item.get("w", 720)),
                         "stagger": float(item.get("stagger", 0.5)), "delay": float(item.get("delay", 0.3)), "icon": item.get("icon")}
                # Кегли плоской темы рассчитаны на кадр, где карточка — часть экрана; в гайде карточка — главное: крупнее.
                out.append(_ui(lid, t0, t1, {"y": 0.42, "scale": 1.32, **item}, {k: v for k, v in props.items() if v is not None},
                               accent, plate, TRACK["ui"]))
            elif kind == "ui":
                props = dict(item["props"])
                for it in props.get("items") or []:
                    if isinstance(it.get("at"), str):
                        it["at"] = _row_at(it, n, times, t0)
                        it.pop("dt", None)
                        it.pop("s", None)
                out.append(_ui(lid, t0, t1, item, props, accent, plate, int(item.get("track", TRACK["ui"]))))
            elif kind == "chip":
                props = {"component": "chip", "variant": item.get("variant", "solid"), "text": item["text"], "icon": item.get("icon"),
                         "size": int(item.get("size", 40))}
                out.append(_ui(lid, t0, t1, item, {k: v for k, v in props.items() if v is not None}, accent, plate, TRACK["chip"]))
            elif kind == "image":
                out.append(_image(lid, t0, t1, item, assets, accent))
            elif kind == "seq":
                out.append(_seq(lid, t0, t1, item, assets))
            elif kind == "pngtuber":
                vf = (P.anchor(times[n], item["voice_from"], n) + float(item.get("voice_dt", 0))) if item.get("voice_from") else None
                out += _pngtuber(lid, t0, t1, item, words, assets, vf)
            elif kind == "text":
                out.append(_text(lid, t0, t1, item))
            elif kind == "layer":
                out.append({**item["layer"], "id": lid, "start": t0, "end": t1})
            else:
                raise GuideError(f"фраза {n + 1}: неизвестный пункт графики «{kind}»")
    return (normalize_layers(out) if out else []), assets


def video_layers(gfx: list[dict]) -> list[dict]:
    """Видео-слои графики (кружок ведущей) — им нужны нарезанные кадры сцены (``prepare_video_frames``)."""
    return [g for g in gfx if g.get("type") == "video"]
