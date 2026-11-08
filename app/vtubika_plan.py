"""План истории «Витубики» → сцена движка: кадры и события привязаны к словам сценария (время — из озвучки).

План — JSON (``tools/vtubika_plans/<история>.json``)::

    {"id": "fox", "title": "…", "hero": "alina", "look": "cozy", "voice": {…}, "music": {…},
     "screen": {… постоянное содержимое монитора: uptime, title, camBg …},
     "highlight": {"слово": "#цвет"},            # ключевые слова субтитров цветом
     "sentences": [{"text": "фраза сценария (субтитры)", "say": "как читать TTS (необязательно)",
                    "show": ["что должно быть видно"],
                    "shots": [{"cam": "face", "at": "слово", "push": 0.2, "roll": -6}],
                    "do": [{"at": "слово", "dt": 0.1, …событие…}]}]}

``at`` — первое слово фразы, начинающееся так (без регистра и «ё»), ``"end"`` — конец фразы; нет ``at`` — начало
фразы. События (``do``):

* герой: ``act`` (процедурный клип, voxelclips.ts; одноразовый — поверх текущего), ``face``, ``pose`` (+``dur``);
* монитор: ``screen`` (режим stream/draw/shop/rig), ``avatar`` (fox/wolf/silhouette), ``mouth`` (closed/open/talk),
  ``chat`` ({user, text, color, big}), ``alert`` ({title, amount, note, dur}), ``viewers`` (+``label``),
  ``declined``, ``fix``, ``draw`` (+``until``/``dur``: окно рисования — монитор и планшет), ``wolf_blink``,
  ``wag`` (сила хвоста), ``wolf_laugh`` (+``dur``), ``squint`` (bool);
* карточки: ``hud`` (копилка, ₽), ``viewers_hud``, ``phone`` (экран телефона-карточки, +``until``/``dur``),
  ``paid`` (кнопка «Оплачено» на карточке товара), ``tag``;
* вещи и звук: ``coin`` (монета падает в банку), ``sfx`` (имя из ``app.vtubika.SFX``).

Звуки к событиям добавляются сами (монета, чат, донат, ошибка, оплата, сообщение, перо).
"""

from __future__ import annotations

import copy
import difflib
import json
import re
from pathlib import Path
from typing import Any

from app import voxel3d as V
from app import vtubika as U

FPS = 30
SHOT_MIN = 0.7
AUTO_SFX = {"coin": "coin", "alert": "donate", "declined": "error", "paid": "success", "chat": "pop", "draw": "pen"}
CHAT_COLORS = ["#7ee0ff", "#ffd35c", "#ff8ad8", "#9dff7e", "#c9a0ff", "#ffb07e"]


class PlanError(ValueError):
    """План истории неверен (нет фраз, неизвестный ракурс, слово-якорь не найдено)."""


def _norm(word: Any) -> str:
    return re.sub(r"[^\w]", "", str(word or "").lower().replace("ё", "е"))


def load_plan(path: str | Path) -> dict:
    plan = json.loads(Path(path).read_text("utf-8"))
    validate_plan(plan)
    return plan


def validate_plan(plan: dict) -> None:
    sents = plan.get("sentences") or []
    if not sents:
        raise PlanError("в плане нет фраз")
    if plan.get("hero", "alina") not in U.HEROES:
        raise PlanError(f"герой: один из {', '.join(U.HEROES)}")
    if plan.get("look", "cozy") not in U.LOOKS:
        raise PlanError(f"образ: один из {', '.join(U.LOOKS)}")
    if not (sents[0].get("shots") or []):
        raise PlanError("у первой фразы нет кадра")
    for n, s in enumerate(sents):
        if not str(s.get("text") or "").strip():
            raise PlanError(f"фраза {n + 1}: пустой текст")
        for shot in s.get("shots") or []:
            if shot.get("cam") not in U.CAMERAS:
                raise PlanError(f"фраза {n + 1}: ракурс «{shot.get('cam')}» — один из {', '.join(U.CAMERAS)}")
        for ev in s.get("do") or []:
            if "act" in ev and ev["act"] not in V.VOXEL_CLIPS:
                raise PlanError(f"фраза {n + 1}: нет клипа «{ev['act']}»")
            if "face" in ev and ev["face"] not in V.VOXEL_FACES:
                raise PlanError(f"фраза {n + 1}: нет выражения «{ev['face']}»")
            if "sfx" in ev and ev["sfx"] not in U.SFX:
                raise PlanError(f"фраза {n + 1}: нет звука «{ev['sfx']}»")


def narration_text(plan: dict) -> str:
    """Текст для TTS: ``say`` фразы (как читать), иначе её текст."""
    return " ".join(str(s.get("say") or s["text"]).strip() for s in plan["sentences"])


def caption_text(plan: dict) -> str:
    return " ".join(str(s["text"]).strip() for s in plan["sentences"])


def tokens(text: str) -> list[str]:
    return [w for w in str(text or "").split() if _norm(w)]


def token_times(plan: dict, words: list[dict]) -> list[list[dict]]:
    """Время каждого слова каждой фразы (``[[{word, start, end}]]``): слова сценария сопоставляются со словами
    субтитров (``script_words``), несопоставленные — между соседями."""
    sent_tokens = [tokens(s["text"]) for s in plan["sentences"]]
    flat = [w for toks in sent_tokens for w in toks]
    keys = [_norm(w) for w in flat]
    heard = [_norm(w["word"]) for w in words]
    times: list[list[float] | None] = [None] * len(flat)
    sm = difflib.SequenceMatcher(None, keys, heard, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                times[i1 + k] = [float(words[j1 + k]["start"]), float(words[j1 + k]["end"])]
        elif tag == "replace" and j2 > j1:
            a, b = float(words[j1]["start"]), float(words[j2 - 1]["end"])
            for k in range(i1, i2):
                f0, f1 = (k - i1) / (i2 - i1), (k - i1 + 1) / (i2 - i1)
                times[k] = [a + (b - a) * f0, a + (b - a) * f1]
    last = 0.0
    for k in range(len(times)):
        if times[k] is None:
            nxt = next((times[j][0] for j in range(k + 1, len(times)) if times[j] is not None), last + 0.3)
            times[k] = [last, max(last + 0.05, min(nxt, last + 0.3))]
        last = times[k][1]
    out, pos = [], 0
    for toks in sent_tokens:
        out.append([{"word": w, "start": round(times[pos + i][0], 3), "end": round(times[pos + i][1], 3)}
                    for i, w in enumerate(toks)])
        pos += len(toks)
    return out


def anchor(stimes: list[dict], at: Any, n: int) -> float:
    """Время якоря ``at`` во фразе (начало слова; ``end`` — конец фразы)."""
    if not stimes:
        raise PlanError(f"фраза {n + 1}: нет слов")
    if at in (None, "", "start"):
        return stimes[0]["start"]
    if at == "end":
        return stimes[-1]["end"]
    key = _norm(at)
    for w in stimes:
        if _norm(w["word"]).startswith(key):
            return w["start"]
    raise PlanError(f"фраза {n + 1}: нет слова «{at}» ({' '.join(w['word'] for w in stimes)})")


def build(plan: dict, words: list[dict], total: float, *, look: str | None = None, fps: int = FPS) -> dict:
    """План + слова озвучки → ``{spec, sfx, captions, shots, sentences}``: сцена движка на ``total`` секунд."""
    validate_plan(plan)
    times = token_times(plan, words)
    look = look or plan.get("look") or "cozy"
    hero_cfg = U.HEROES[plan.get("hero") or "alina"]
    screen: dict[str, Any] = {"modes": [], "avatars": [], "mouth": [], "chat": [], "alerts": [], "viewers": [],
                              "wolf": {"blinks": [], "wag": [], "laugh": [], "squint": []},
                              **copy.deepcopy(plan.get("screen") or {})}  # копия: события дописываются в списки
    acts: list[dict] = []
    faces: list[dict] = []
    poses: list[dict] = []
    hud: list[tuple[float, int]] = []
    vhud: list[tuple[float, int]] = []
    phones: list[dict] = []
    drops: list[float] = []
    draw_win: list[float] | None = None
    sfx: list[dict] = []
    tags: list[dict] = []
    shots: list[dict] = []
    nick_color: dict[str, str] = {m["user"]: m.get("color") or CHAT_COLORS[0] for m in screen["chat"]}

    def add_sfx(name: str, t: float) -> None:
        ref, vol = U.SFX[name]
        sfx.append({"t": round(max(0.0, t), 3), "ref": ref, "volume": vol, "name": name})

    for n, s in enumerate(plan["sentences"]):
        st = times[n]
        t_end = st[-1]["end"]
        for shot in s.get("shots") or []:
            shots.append({**shot, "t0": round(anchor(st, shot.get("at"), n), 3), "n": n})
        for ev in s.get("do") or []:
            t = round(anchor(st, ev.get("at"), n) + float(ev.get("dt") or 0), 3)
            until = (anchor(st, ev["until"], n) if ev.get("until") else
                     t + float(ev["dur"]) if ev.get("dur") else None)
            if "act" in ev:
                acts.append({"clip": ev["act"], "t": t, "fade": float(ev.get("fade") or 0.35)})
            if "face" in ev:
                faces.append({"t": t, "face": ev["face"]})
            if "pose" in ev:
                poses.append({"start": t, "end": round(until or t + 1.5, 3), "fade": 0.3, **ev["pose"]})
            if "screen" in ev:
                screen["modes"].append({"t": t, "mode": ev["screen"]})
            if "avatar" in ev:
                screen["avatars"].append({"t": t, "kind": ev["avatar"]})
            if "mouth" in ev:
                screen["mouth"].append({"t": t, "m": ev["mouth"]})
            if "chat" in ev:
                msg = dict(ev["chat"])
                msg.setdefault("color", nick_color.setdefault(msg["user"], CHAT_COLORS[len(nick_color) % len(CHAT_COLORS)]))
                screen["chat"].append({"t": t, **msg})
            if "alert" in ev:
                screen["alerts"].append({"t": t, "dur": 1.6, **ev["alert"]})
            if "viewers" in ev:
                v = int(ev["viewers"])
                screen["viewers"].append({"t": t, "value": v, "label": ev.get("label") or f"{v}"})
            if ev.get("declined"):
                screen["declined"] = t
            if ev.get("fix"):
                screen["fixAt"] = t
            if "draw" in ev:
                draw_win = [t, round(until or t + 2.0, 3)]
            if ev.get("wolf_blink"):
                screen["wolf"]["blinks"].append(t)
            if "wag" in ev:
                screen["wolf"]["wag"].append({"t": t, "amp": float(ev["wag"])})
            if "wolf_laugh" in ev:
                screen["wolf"]["laugh"].append([t, round(until or t + float(ev["wolf_laugh"] or 1.5), 3)])
            if "squint" in ev:
                screen["wolf"]["squint"].append({"t": t, "on": bool(ev["squint"])})
            if "hud" in ev:
                hud.append((t, int(ev["hud"])))
            if "viewers_hud" in ev:
                vhud.append((t, int(ev["viewers_hud"])))
            if "phone" in ev:
                ph = copy.deepcopy(ev["phone"])
                if ph.get("screen") == "messenger":
                    ph["message"] = {**(ph.get("message") or {}), "at": round(t + 0.35, 3)}
                phones.append({"t0": t, "t1": round(until or t + 3.0, 3), **ph})
            if ev.get("paid") and phones:
                phones[-1].setdefault("store", {})["paidAt"] = t
            if ev.get("coin"):
                drops.append(t)
            if "tag" in ev:
                tags.append({"kind": "tag", "text": ev["tag"], "start": t, "end": round(until or total, 3)})
            for key, name in AUTO_SFX.items():
                if key in ev and ev[key] not in (False, None):
                    add_sfx(name, t + (0.3 if key == "coin" else 0.0))
            if "phone" in ev and ev["phone"].get("screen") == "messenger":
                add_sfx("notify", t + 0.3)
            if "sfx" in ev:
                add_sfx(ev["sfx"], t)
        if n == len(plan["sentences"]) - 1:
            shots_end = t_end
    shots.sort(key=lambda x: x["t0"])
    # Кадры: от своего якоря до следующего (последний — до конца ролика); слишком короткие сливаются.
    merged: list[dict] = []
    for sh in shots:
        if merged and sh["t0"] - merged[-1]["t0"] < SHOT_MIN:
            merged[-1] = {**sh, "t0": merged[-1]["t0"]}
            continue
        merged.append(sh)
    merged[0]["t0"] = 0.0
    for i, sh in enumerate(merged):
        sh["t1"] = merged[i + 1]["t0"] if i + 1 < len(merged) else round(total, 3)

    room = U.story_room(look, screen=screen)
    objs = {o["id"]: o for o in room["objects"]}
    objs["jar"]["params"]["drops"] = drops
    if draw_win:
        objs["tablet"]["params"]["draw"] = draw_win
        screen["drawWin"] = draw_win
        # Стилус: лежит на планшете, на время рисования — в правой руке.
        room["objects"].append({"id": "stylus", "primitive": "stylus", "params": {"length": 0.14},
                                "position": [U.TABLET[0] + 0.12, room["desk_top"] + 0.012, U.TABLET[1]],
                                "attach": [{"to": "hero:hand_r", "start": draw_win[0], "end": draw_win[1] + 0.3,
                                            "fadeIn": 0.25, "fadeOut": 0.25, "offset": [0, -0.06, 0.02]}]})
    objs["monitor_screen"]["params"].update(screen)
    # Действия героини: петля — до следующей петли (+ наплыв); одноразовое — поверх, потом снова прошлая петля.
    actions: list[dict] = []
    acts.sort(key=lambda a: a["t"])
    loops = [a for a in acts if a["clip"].endswith("_Loop")]
    if not loops or loops[0]["t"] > 0:
        loops.insert(0, {"clip": "Sit_Loop", "t": 0.0, "fade": 0.3})
    for i, a in enumerate(loops):
        end = loops[i + 1]["t"] + loops[i + 1]["fade"] if i + 1 < len(loops) else total + 0.5
        actions.append({"clip": a["clip"], "start": round(a["t"], 3), "end": round(end, 3), "fade": a["fade"],
                        "offset": round((i * 0.37) % 1.0, 2)})
    for a in acts:
        if a["clip"].endswith("_Loop"):
            continue
        dur = V.VOXEL_CLIPS[a["clip"]]
        actions.append({"clip": a["clip"], "start": a["t"], "end": round(a["t"] + dur, 3), "fade": 0.2})
        prev = next((x for x in reversed(loops) if x["t"] <= a["t"]), loops[0])
        actions.append({"clip": prev["clip"], "start": round(a["t"] + dur - 0.35, 3),
                        "end": round(min(total + 0.5, next((x["t"] + x["fade"] for x in loops if x["t"] > a["t"]), total + 0.5)), 3),
                        "fade": 0.35})
    actions.sort(key=lambda a: a["start"])
    look_hero = hero_cfg["look"]
    hero = V.voxel_hero("hero", shape=hero_cfg["shape"], look=look_hero, position=[U.HERO_X, 0, U.HERO_Z], yaw=180.0,
                        seat=room["seat"], actions=actions, poses=poses, faces=faces or [{"t": 0, "face": "neutral"}])

    def act_at(t: float) -> str:
        cur = "Sit_Loop"
        for a in loops:
            if a["t"] <= t:
                cur = a["clip"]
        return cur

    cam_keys: list[dict] = []
    for i, sh in enumerate(merged):
        k0, k1 = U.camera_preset(sh["cam"], room, act=act_at((sh["t0"] + sh["t1"]) / 2), push=float(sh.get("push", 0.18)),
                                 roll=float(sh.get("roll", 0.0)), fov=sh.get("fov"), dof=sh.get("dof"),
                                 shift=sh.get("shift"))
        a = {"t": sh["t0"], **k0}
        if i:
            a["cut"] = True
        cam_keys += [a, {"t": sh["t1"], **k1, "ease": "inOutSine"}]
    overlays: list[dict] = [*tags]
    if hud:
        overlays.append(V.hud_overlay(hud, label="КОПИЛКА", prefix="", suffix=" ₽", group=True, chip_sec=2.6,
                                      start=hud[0][0]))
    if vhud:
        vh = V.hud_overlay(vhud, label="ЗРИТЕЛЕЙ", prefix="", chip_sec=2.2, top=60, start=vhud[0][0])
        vh["left"] = 40.0  # слева сверху: справа — копилка со своими чипами
        overlays.append(vh)
    for ph in phones:
        t0, t1 = ph.pop("t0"), ph.pop("t1")
        kind = ph.pop("screen", "notifications")
        overlays.append(V.phone_overlay(t0, t1, screen=kind, y=ph.pop("y", 900), scale=ph.pop("scale", 0.95), **ph))
    spec = {"version": 1, "width": 1080, "height": 1920, "fps": int(fps), "duration": round(total, 3),
            "environment": room["environment"], "room": room["room"], "lights": room["lights"], "objects": room["objects"],
            "characters": [hero], "overlays": overlays,
            "camera": {"fov": 40, "keys": cam_keys, "clamp": False, "fill": room["fill"]},
            "quality": {"shadows": True, "shadowSize": 1024, "antialias": True}}
    sent_out = [{"n": n + 1, "text": s["text"], "t0": times[n][0]["start"], "t1": times[n][-1]["end"],
                 "show": s.get("show") or []} for n, s in enumerate(plan["sentences"])]
    for so in sent_out:
        so["shots"] = [sh["cam"] for sh in merged if sh["t0"] < so["t1"] and sh["t1"] > so["t0"]]
    return {"spec": spec, "sfx": sorted(sfx, key=lambda x: x["t"]), "shots": merged, "sentences": sent_out,
            "speech_end": shots_end, "look": look}


def caption_words(words: list[dict], highlight: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Слова субтитров (comp.words) и ключевые слова цветом (highlightWords) по плану."""
    keys = {_norm(k): v for k, v in (highlight or {}).items()}
    comp = [{"t0": w["start"], "t1": w["end"], "w": w["word"]} for w in words]
    marks = [{"i": i, "color": keys[_norm(w["word"])]} for i, w in enumerate(words) if _norm(w["word"]) in keys]
    return comp, marks


def checklist_md(plan: dict, built: dict, frames: dict[int, str] | None = None) -> str:
    """«Фраза → что в кадре»: каждая фраза, её время, ракурсы и что должно быть видно (кадр для сверки)."""
    lines = [f"# {plan.get('title') or plan.get('id')}: фраза → что в кадре", "",
             f"Образ: {built['look']}. Время — секунды готового ролика.", ""]
    for s in built["sentences"]:
        lines.append(f"## {s['n']}. {s['text']}")
        lines.append(f"- время: {s['t0']:.2f}–{s['t1']:.2f} с; ракурсы: {', '.join(s['shots'])}")
        for item in s["show"]:
            lines.append(f"- [ ] {item}")
        if frames and s["n"] in frames:
            lines.append(f"- кадр: {frames[s['n']]}")
        lines.append("")
    return "\n".join(lines)
