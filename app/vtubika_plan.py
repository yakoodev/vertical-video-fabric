"""План истории «Витубики» → сцена движка: кадры и события привязаны к словам сценария (время — из озвучки).

План — JSON (``tools/vtubika_plans/<история>.json``)::

    {"id": "fox", "title": "…", "hero": "alina", "look": "cozy", "voice": {…}, "music": {…},
     "screen": {… постоянное содержимое монитора: uptime, title, camBg …},
     "highlight": {"слово": "#цвет"},            # ключевые слова субтитров цветом
     "sentences": [{"text": "фраза сценария (субтитры)", "say": "как читать TTS (необязательно)",
                    "revoice": true,     # переозвучить только эту фразу поверх прошлой озвучки (app/vtubika_revoice.py)
                    "show": ["что должно быть видно"],
                    "shots": [{"cam": "face", "at": "слово", "push": 0.2, "roll": -6}],
                    "do": [{"at": "слово", "dt": 0.1, …событие…}]}]}

``at`` — первое слово фразы, начинающееся так (без регистра и «ё»), ``"end"`` — конец фразы; нет ``at`` — начало
фразы. События (``do``):

* герой: ``act`` (процедурный клип, voxelclips.ts; одноразовый — поверх текущего), ``face``, ``pose`` (+``dur``);
* монитор: ``decor`` (оформление эфира: retro/studio/studio_ember/plain — ``DECORS``), ``screen`` (режим stream/draw/shop/rig), ``avatar`` (fox/wolf/silhouette), ``mouth`` (closed/open/talk),
  ``chat`` ({user, text, color, big}), ``alert`` ({title, amount, note, dur}), ``viewers`` (+``label``),
  ``declined``, ``fix``, ``draw`` (+``until``/``dur``: окно рисования — монитор и планшет), ``wolf_blink``,
  ``wag`` (сила хвоста), ``wolf_laugh`` (+``dur``), ``squint`` (bool);
* карточки: ``hud`` (копилка, ₽), ``viewers_hud``, ``phone`` (экран телефона-карточки, +``until``/``dur``),
  ``paid`` (кнопка «Оплачено» на карточке товара), ``tag``;
* вещи и звук: ``coin`` (монета падает в банку), ``fork`` (вилка в руке, +``until``/``dur``), ``sfx`` (имя из
  ``app.vtubika.SFX``);
* «Без лица»: ``title`` (подпись эфира), ``avatar`` hoodie/vtuber/nocam, ``guy_nod``, ``guy_yawn`` (сек),
  ``guy_talk`` (сек), ``vt_blink``, ``shop_page`` (0/1/2 — зарубежные магазины), ``decline`` (ещё одна «Оплата
  отклонена» каскадом), ``screencard`` ({x, y, w} + ``until``/``dur``: карточка эфира поверх кадра; у VRM-историй с монитором на столе —
  только с ``"float": true``, иначе эфир показывает сам монитор), ``alertcard``
  ({title, amount, from, note, y, scale} + ``dur``: алерт доната крупно);
* каст и локации (``app/vtubika_cast.py``, ``app/vtubika_sets.py``): ``"sets": ["warehouse"]``, ``"cast": {id:
  {hero, at}}``, события с ``"who": id`` — ``act``, ``face``, ``place``, ``walk`` (+``dur``), ``carry``. План может
  перекрасить комнату (``"room"``: палитра) и выбрать вещи (``"props"``: story1, noodles, webcam), подпись HUD
  (``"hud_label"``).

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
from app import vtubika_cast as C
from app import vtubika_sets as S

FPS = 30
SHOT_MIN = 0.7
AUTO_SFX = {"coin": "coin", "alert": "donate", "declined": "error", "decline": "error", "paid": "success",
            "chat": "pop", "draw": "pen"}
# Оформление окна эфира (событие ``decor``, node/scene3d/src/ui_deco.ts): plain — обычный; retro — чёрный фон, рамка
# вебки в огне, «тот самый шрифт»; studio — музыкальная комната и попкорн; studio_ember — то же и огонёк в углу.
DECORS = ("plain", "retro", "studio", "studio_ember")
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
    if plan.get("presets3d") is not None:
        # 3D-пресеты поверх истории: стиль картинки, свет, настроение, эффекты, переходы (комнату и камеру ведёт история).
        from app.presets3d import PresetError, validate_presets

        try:
            validate_presets(plan["presets3d"])
        except PresetError as exc:
            raise PlanError("presets3d: " + "; ".join(exc.problems)) from exc
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
            faces_p = ((plan.get("screen") or {}).get("product") or {}).get("faces") or {}
            if "product_face" in ev and ev["product_face"] not in faces_p:
                raise PlanError(f"фраза {n + 1}: нет выражения товара «{ev['product_face']}» в screen.product.faces")
            if "decor" in ev and ev["decor"] not in DECORS:
                raise PlanError(f"фраза {n + 1}: оформление эфира «{ev['decor']}» — одно из {', '.join(DECORS)}")


def narration_sentences(plan: dict) -> list[str]:
    """Текст для TTS по фразам: ``say`` фразы (как читать), иначе её текст; термины — по словарю произношения
    (``app.vtubika_say``: «Live2D» → «лайв-два-дэ»; субтитры этого не видят)."""
    from app import vtubika_say as SAY

    mapping = SAY.pronounce_map(plan)
    return [SAY.apply(str(s.get("say") or s["text"]).strip(), mapping) for s in plan["sentences"]]


def narration_text(plan: dict) -> str:
    """Весь текст для TTS (фразы через пробел, ``narration_sentences``)."""
    return " ".join(narration_sentences(plan))


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
                              "guy": {"blinks": [], "nods": [], "yawns": [], "talk": []}, "vtuber": {"blinks": []},
                              "titles": [], "shops": [], "declines": [],
                              **copy.deepcopy(plan.get("screen") or {})}  # копия: события дописываются в списки
    props = {"story1": True, "noodles": False, "webcam": False, **(plan.get("props") or {})}
    wh = S.warehouse() if "warehouse" in (plan.get("sets") or []) else None
    cast = C.Cast(plan, wh["spots"] if wh else {})
    acts: list[dict] = []
    faces: list[dict] = []
    poses: list[dict] = []
    hud: list[tuple[float, int]] = []
    vhud: list[tuple[float, int]] = []
    phones: list[dict] = []
    drops: list[float] = []
    draw_win: list[float] | None = None
    fork_win: list[list[float]] = []
    sfx: list[dict] = []
    tags: list[dict] = []
    shots: list[dict] = []
    chatcards: list[dict] = []
    screencards: list[dict] = []
    alertcards: list[dict] = []
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
            until = (anchor(st, ev["until"], n) + float(ev.get("dt_end") or 0) if ev.get("until") else
                     t + float(ev["dur"]) if ev.get("dur") else None)
            if "span" in ev:  # до конца фразы через span фраз (+ пауза-«бит» после неё)
                k = min(len(times) - 1, n + int(ev["span"]))
                until = times[k][-1]["end"] + float(ev.get("dt_end") or 0)
            who = ev.get("who") or "hero"
            if who != "hero":
                if not cast.has(who):
                    raise PlanError(f"фраза {n + 1}: нет персонажа «{who}» в касте")
                cast.event(who, ev, t, until)
            else:
                if "act" in ev:
                    acts.append({"clip": ev["act"], "t": t, "fade": float(ev.get("fade") or 0.35)})
                if "face" in ev:
                    # "talk": true — героиня говорит в эфир с этим лицом (рот по голосу, эмоция под речью; VRM).
                    faces.append({"t": t, "face": ev["face"], **({"talk": True} if ev.get("talk") else {})})
            if "pose" in ev:
                poses.append({"start": t, "end": round(until or t + 1.5, 3), "fade": 0.3, **ev["pose"]})
            if "screen" in ev:
                screen["modes"].append({"t": t, "mode": ev["screen"]})
            if "avatar" in ev:
                screen["avatars"].append({"t": t, "kind": ev["avatar"]})
            if "title" in ev:
                screen["titles"].append({"t": t, "title": ev["title"]})
            if "decor" in ev:
                screen.setdefault("decor", []).append({"t": t, "kind": ev["decor"]})
            if "product_face" in ev:
                # Купленная модель в эфире (аватар product): выражение — картинка со страницы товара.
                screen.setdefault("product", {"faces": {}}).setdefault("keys", []).append({"t": t, "face": ev["product_face"]})
            if "mouth" in ev:
                screen["mouth"].append({"t": t, "m": ev["mouth"]})
            if "chat" in ev:
                msg = dict(ev["chat"])
                msg.setdefault("color", nick_color.setdefault(msg["user"], CHAT_COLORS[len(nick_color) % len(CHAT_COLORS)]))
                screen["chat"].append({"t": t, **msg})
            if "alert" in ev:
                screen["alerts"].append({"t": t, "dur": 1.6, **ev["alert"]})
            if "alertcard" in ev:
                alertcards.append({"kind": "alert", "t": t, "dur": round((until or t + 3.0) - t, 3), **ev["alertcard"]})
            if "viewers" in ev:
                v = int(ev["viewers"])
                screen["viewers"].append({"t": t, "value": v, "label": ev.get("label") or f"{v}"})
            if ev.get("declined"):
                screen["declined"] = t
            if ev.get("decline"):
                screen["declines"].append(t)
            if "shop_page" in ev:
                screen["shops"].append({"t": t, "page": int(ev["shop_page"])})
            if ev.get("fix"):
                screen["fixAt"] = t
            if "draw" in ev:
                draw_win = [t, round(until or t + 2.0, 3)]
            if ev.get("wolf_blink"):
                screen["wolf"]["blinks"].append(t)
            if ev.get("vt_blink"):
                screen["vtuber"]["blinks"].append(t)
            if ev.get("guy_nod"):
                screen["guy"]["nods"].append(t)
            if "guy_yawn" in ev:
                screen["guy"]["yawns"].append([t, round(t + float(ev["guy_yawn"]), 3)])
            if "guy_talk" in ev:
                screen["guy"]["talk"].append([t, round(t + float(ev["guy_talk"]), 3)])
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
            if "app_frame" in ev and phones:
                # Экран «app» (настоящая запись приложения): с t — этот кадр; tap — касание прошлого кадра перед сменой
                # (точка в долях картинки), звук касания.
                app = phones[-1].setdefault("app", {"frames": []})
                if ev.get("tap") and app["frames"]:
                    app["frames"][-1].update({"tap": [float(v) for v in ev["tap"]], "tapT": round(t - 0.12, 3)})
                    add_sfx("click", t - 0.12)
                app["frames"].append({"t": t, "url": str(ev["app_frame"])})
            if "dm" in ev and phones:
                # Экран «dm» (личка): сообщение появляется в t; входящее — со звуком.
                phones[-1].setdefault("dm", {"title": "", "msgs": []})["msgs"].append({"t": t, **ev["dm"]})
                add_sfx("pop", t)
            if "scene" in ev:
                # Сцена окна эфира (фон, слои поверх аватара, плашка): времена слоёв t0 и ключей — от события.
                sc = copy.deepcopy(ev["scene"])
                for layer in sc.get("front") or []:
                    if "t0" in layer:
                        layer["t0"] = round(t + float(layer["t0"]), 3)
                    for k in layer.get("keys") or []:
                        k["t"] = round(t + float(k["t"]), 3)
                if isinstance(sc.get("plate"), dict) and "t0" in sc["plate"]:
                    sc["plate"]["t0"] = round(t + float(sc["plate"]["t0"]), 3)
                screen.setdefault("scenes", []).append({"t": t, **sc})
            if ev.get("site_step") and phones:
                # Шаг вставки «сайт на телефоне» (app/vtubika_site.py): product — открыть товар, tap — кнопка,
                # delivered — «Модель пришла».
                phones[-1].setdefault("steps", []).append({"t": t, "step": str(ev["site_step"])})
                if ev["site_step"] in ("product", "tap"):
                    add_sfx("click", t)
                elif ev["site_step"] == "delivered":
                    add_sfx("notify", t)
            if ev.get("coin"):
                drops.append(t)
            if ev.get("fork"):
                fork_win.append([t, round(until or t + 3.0, 3)])
            if ev.get("chatcard"):
                chatcards.append({"start": t, "end": round(until or total, 3)})
            if "screencard" in ev:
                screencards.append({"start": t, "end": round(until or total, 3), **(ev["screencard"] or {})})
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
    if shots:
        shots[0]["t0"] = 0.0  # первый кадр — с начала ролика (вступление), иначе короткий первый слился бы со вторым
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

    room = U.story_room(look, palette=plan.get("room"), screen=screen)
    if not props["story1"]:
        S.strip_story1_props(room)
    room["objects"] += S.room_extras(room, noodles=bool(props["noodles"]), webcam=bool(props["webcam"]))
    if wh:
        S.merge(room, wh)
    objs = {o["id"]: o for o in room["objects"]}
    if "jar" in objs:
        objs["jar"]["params"]["drops"] = drops
    if draw_win and "tablet" in objs:
        objs["tablet"]["params"]["draw"] = draw_win
        screen["drawWin"] = draw_win
        # Стилус: лежит на планшете, на время рисования — в правой руке.
        room["objects"].append({"id": "stylus", "primitive": "stylus", "params": {"length": 0.14},
                                "position": [U.TABLET[0] + 0.12, room["desk_top"] + 0.012, U.TABLET[1]],
                                "attach": [{"to": "hero:hand_r", "start": draw_win[0], "end": draw_win[1] + 0.3,
                                            "fadeIn": 0.25, "fadeOut": 0.25, "offset": [0, -0.06, 0.02]}]})
    if fork_win and "fork" in objs:
        objs["fork"]["attach"] = [{"to": "hero:hand_r", "start": a, "end": b, "fadeIn": 0.25, "fadeOut": 0.25,
                                   "offset": [0.0, -0.07, 0.05]} for a, b in fork_win]
    if wh:
        cast.carry_attach(wh["carry"], room["objects"])
    objs["monitor_screen"]["params"].update(screen)
    # Действия героя: петля — до следующей петли (+ наплыв); одноразовое — поверх, потом снова прошлая петля.
    actions, loops = C.actions_for(acts, total, "Sit_Loop")
    hero = V.voxel_hero("hero", shape=hero_cfg["shape"], look=hero_cfg["look"], position=[U.HERO_X, 0, U.HERO_Z], yaw=180.0,
                        seat=room["seat"], actions=actions, poses=poses, faces=C.from_start(faces))

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
        overlays.append(V.hud_overlay(hud, label=plan.get("hud_label") or "КОПИЛКА", prefix="", suffix=" ₽", group=True,
                                      chip_sec=2.6, start=hud[0][0]))
    if vhud:
        vh = V.hud_overlay(vhud, label="ЗРИТЕЛЕЙ", prefix="", chip_sec=2.2, top=60, start=vhud[0][0])
        vh["left"] = 40.0  # слева сверху: справа — копилка со своими чипами
        overlays.append(vh)
    for i, ph in enumerate(phones):
        if ph.pop("to_next", False) and i + 1 < len(phones):
            ph["t1"] = phones[i + 1]["t0"]  # склейка: следующий телефон сменяет этот без зазора
            # Телефон остаётся на месте — меняется экран: первый не уезжает, второй не въезжает.
            ph["cutOut"] = True
            phones[i + 1]["cutIn"] = True
    for ph in phones:
        t0, t1 = ph.pop("t0"), ph.pop("t1")
        kind = ph.pop("screen", "notifications")
        overlays.append(V.phone_overlay(t0, t1, screen=kind, y=ph.pop("y", 900), scale=ph.pop("scale", 0.95), **ph))
    for cc in chatcards:
        # Карточка чата: те же сообщения и зрители, что на мониторе чата (рисует только пришедшие к t).
        overlays.append({"kind": "chat", **cc, "x": 290, "y": 470, "w": 480, "h": 560, "chat": screen["chat"],
                         "viewers": screen["viewers"]})
    for sc in screencards:
        # Карточка эфира: то же, что на мониторе (аватар повторяет за героем в кадре одновременно).
        overlays.append({"kind": "screen", **{k: v for k, v in screen.items()}, "noChat": True, **sc})
    overlays += alertcards
    environment = room["environment"]
    phase_list = C.phases(merged, environment, room["lights"], wh)
    if phase_list:
        environment = {**environment, "phases": phase_list}
    characters = [hero, *cast.characters(total)]
    spec = {"version": 1, "width": 1080, "height": 1920, "fps": int(fps), "duration": round(total, 3),
            "environment": environment, "room": room["room"], "lights": room["lights"], "objects": room["objects"],
            "characters": characters, "overlays": overlays,
            "camera": {"fov": 40, "keys": cam_keys, "clamp": False, "fill": room["fill"]},
            "quality": {"shadows": True, "shadowSize": 1024, "antialias": True}}
    if plan.get("presets3d"):
        spec["presets"] = plan["presets3d"]
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
