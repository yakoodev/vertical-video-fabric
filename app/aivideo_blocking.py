"""ИИ-видео v3: блокинг планов в нашем 3D-движке сцен (слой ``scene``, docs/motion/scene-engine.md).

Зачем: картинковая модель рисует ключевые кадры «по описанию» — позы, места и ракурс плывут, пылесос
встаёт на дыбы, печенье двоится. Блокинг даёт ей КОМПОЗИЦИЮ: подробный макет сцены — комната локации
со всеми её вещами, манекены персонажей в позе, реквизит в руках и на местах, снятые камерой плана, на
старте и на конце плана. Скриншот блокинга идёт первым референсом кадра (только позиции, позы и
ракурс — цвета и формы макета модель не рисует), листы персонажей и плита локации — следом.

* **Комната** — одна раскладка на локацию (``app/aivideo_layout.py``): все вещи из описания (и с плиты —
  Gemini-зрением, один раз, кэш), узнаваемые пропорции и цвета, одна и та же во всех планах.
* **spec** плана (сантиметры; x вправо, y вверх, z на камеру; пол y=0, комната по центру):

  .. code-block:: json

     {"start": {"actors": [{"id": "mira", "pos": [-40, 20], "facing": "camera", "pose": "standing",
                            "arms": "holding", "on": ""}],
                "props": [{"id": "mug", "name": "кружка", "holder": "mira"},
                          {"id": "sock", "name": "носок", "on": "rug"}]},
      "end": {"actors": [...], "props": [...]},
      "objects": [], "room": {}, "camera": {"pos": [...], "target": [...], "fov": 50}}

  ``objects``/``room`` пусто — комната локации (так и надо); своя мебель — только если агент её задал.
  ``camera`` можно не писать — она ставится по плану (``camera.shot``/``angle``/``lens``). ``facing`` —
  градусы (0 — в камеру, 90 — вправо кадра, −90 — влево, 180 — спиной) или camera|left|right|away.
  ``pose`` — standing | walking | sitting | crouching | kneeling | lying; ``arms`` — down | up | forward |
  holding | wave; ``on`` — id вещи комнаты, на которой персонаж сидит/лежит (кровать, стул…).
  Реквизит: ``holder`` — в руке персонажа, ``on`` — на вещи комнаты, иначе ``pos`` [x, y, z].
* **Манекены** в реальном росте: человек — голова, туловище, руки и ноги в позе (рост из карты
  личности, по умолчанию 165 см), робот-диск — низкий диск ~33 см с экраном-глазами и антенной,
  животное — тело, голова, лапы, хвост; у каждого персонажа свой нейтральный цвет — промпт кадра
  говорит, какой цвет кто.
* **Камера** по плану: крупность → сколько видно по высоте, объектив → угол поля, ракурс → высота;
  смотрит на центр масс персонажей, все персонажи влезают; никогда не внутри вещи или стены.
* **Самоконтроль кадра** (``frame_check``): лучи из камеры по сетке кадра — каждый персонаж в кадре и
  не закрыт, персонажи занимают разумную долю кадра, никакая вещь перед ними не закрывает > 40 % кадра.
  Не прошёл ни один вариант камеры — блокинг в кадр НЕ идёт (промпт без него, пометка в params).
* **Кто пишет** места персонажей: Gemini — раскладку всей серии одним запросом (бесплатно; комната
  дана, места из state/end_state планов); нет ключа — ``auto_spec`` по state (left/center/right,
  foreground/background, поза, «у кровати», «на тумбочке»). Агент и владелец правят spec
  (``set_blocking``) или кладут свою JSON-сцену движка поверх (``scenes.start/end``).
* **Рендер** — 3D-движком сцен (``app/aivideo_blocking3d.py``: комната из моделей Kenney, персонажи
  со скелетом в позах из клипов UAL, цвета из карточек, нейтральный матовый стиль), 9:16, 0 ₽ —
  ``prompt`` = ``blocking3d:<хэш>``. Нет браузера/паков, своя сцена агента или
  ``AIV_BLOCKING_ENGINE=2d`` — компоновщиком (2D-холст, ``blocking:<хэш сцены>``). Версии
  ``blocking_start`` / ``blocking_end`` плана.

Где что лежит. Здесь — служба блокинга: ``view``, ``current``, ``set_blocking``, ``write_blocking``, ``render`` и ``cast_info`` (его подменяют
тесты, поэтому он и его читатели остаются в этом файле). Вынесено: описание сцены ``app/aivideo_blocking_spec.py``, детали тел и
предметов ``app/aivideo_blocking_parts.py``, камера и проверка кадра ``app/aivideo_blocking_camera.py``, сборка сцены
``app/aivideo_blocking_scene.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import logging

from app import aivideo as V
from app import aivideo_layout as L

from app.aivideo_blocking_spec import (  # noqa: F401
    KINDS, POSES, ARMS, ACTOR_COLORS, COLOR_NAMES, ROOM_DEFAULT, LIMIT, _num, _color, _norm, _DISC_WORDS,
    _QUAD_WORDS, _SMALL_WORDS, stand_in, body_size, cast_info, _FACING_WORDS, _NOTHING, facing_deg, pose_of,
    arms_of, holding_text, _position, _ON_WORDS, _HOLD_WORDS, _STRETCH, PROP_KINDS, prop_kind, _who, _moment,
    _in_front, _clean_props, clean_spec, _rot_y,
)
from app.aivideo_blocking_parts import (  # noqa: F401
    _mat, _mm, _mv, _r, _LEGS, _ARMS, _human_rig, _human_nodes, _hand_local, _disc_nodes, _quad_nodes, _support_top,
    _actor_parts, _prop_parts,
)
from app.aivideo_blocking_camera import (  # noqa: F401
    FRAME_HEIGHT, MUST_SEE, CUT, HEAD_ROOM, MIN_FRAME, CROWD_MAX, SIZES, MIN_SHARE, OCCLUDE_MAX, HIDDEN_MAX,
    FOCUS_SEEN_MIN, GRID, lens_fov, _basis, _box_corners, _must_see, _place_camera, _inside, frame_rays, _hit_box,
    _hit_walls, head_room, check_frame, _camera_notes, _wider, choose_camera, _dolly,
)
from app.aivideo_blocking_scene import (  # noqa: F401
    _settle_actor, _step_aside, build, _face_partners, compile_scene, prop_place, _prop_nodes,
)

_log = logging.getLogger(__name__)


BLOCKING_W, BLOCKING_H = 1080, 1920
RENDER_WIDTH = 540


def legend(store: Any, project_id: int, shot: dict) -> str:
    """Для промпта кадра: какой манекен — кто («slate-blue mannequin = Мира; sand flat disc = Бип»).
    Кадр блокинга нарисован 3D-движком — персонажи там в своих цветах («mint hair, lavender top = Мира»)."""
    ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "blocking_start")
    if ref and str(ref.get("prompt") or "").startswith("blocking3d:"):
        from app import aivideo_blocking3d as B3

        return B3.legend(cast_info(store, project_id, shot), _characters(store, project_id, shot))
    what = {"human": "mannequin", "disc": "flat disc", "quadruped": "animal stand-in", "small": "ball stand-in"}
    return "; ".join(f"the {c['color_name']} {what.get(c['stand_in'], 'stand-in')} = {c['name']}"
                     for c in cast_info(store, project_id, shot))


def auto_spec(store: Any, project_id: int, shot: dict, nxt: dict | None, layout: dict | None = None,
              prev: list[dict] | None = None) -> dict:
    """Раскладка без Gemini: места/позы/взгляд/руки из state (старт) и конца плана, у вещей комнаты.

    Связность: план, продолжающий предыдущий (``prev`` — планы серии до этого), начинает с тех же мест,
    где тот закончил; кто в конце «там же» (то же место в state) — стоит там же; кто ставит предмет
    на вещь (в начале в руке, в конце на тумбочке) — стоит у этой вещи."""
    cast = shot.get("cast") or []
    if layout is None:
        layout = shot_layout(store, project_id, shot)
    infos = {c["id"]: c for c in cast_info(store, project_id, shot)}
    action = f"{shot.get('action') or ''} {shot.get('motion_prompt') or ''}"
    start = _moment(shot.get("state") or {}, cast, infos, layout, action)
    end = _moment(V.end_state_of(shot, nxt), cast, infos, layout, action, end=True)
    objects = {o["id"]: o for o in layout.get("objects") or []}
    # Предмет из руки на вещь — персонаж у этой вещи и в начале, и в конце.
    for p in end["props"]:
        support = objects.get(str(p.get("on") or ""))
        held = next((q for q in start["props"] if q.get("holder") and prop_kind(q.get("name"))[0] ==
                     prop_kind(p.get("name"))[0]), None)
        if support is None or held is None:
            continue
        for moment in (start, end):
            for a in moment["actors"]:
                if a["id"] == held["holder"] and not a.get("on"):
                    a["pos"] = _in_front(support, 35.0)
                    a["_anchored"] = True
    # Продолжение прошлого плана: начало — там, где он закончился.
    if prev and shot.get("continues_from_prev") and prev[-1].get("location") == shot.get("location"):
        before = auto_spec(store, project_id, prev[-1], shot, layout, prev[:-1])
        last = {a["id"]: a for a in before["end"]["actors"]}
        for a in start["actors"]:
            if a["id"] in last and not a.get("_anchored"):
                a["pos"] = list(last[a["id"]]["pos"])
                if last[a["id"]].get("on"):
                    a["on"] = last[a["id"]]["on"]
    # Кто в конце на том же месте по state — стоит там же, где в начале.
    st_pos = {c["id"]: str(c.get("position") or "") for c in (shot.get("state") or {}).get("characters") or []}
    en_pos = {c["id"]: str(c.get("position") or "") for c in V.end_state_of(shot, nxt).get("characters") or []}
    first = {a["id"]: a for a in start["actors"]}
    for a in end["actors"]:
        if not a.get("_anchored") and a["id"] in first and st_pos.get(a["id"]) == en_pos.get(a["id"]):
            a["pos"] = list(first[a["id"]]["pos"])
            if first[a["id"]].get("on"):
                a["on"] = first[a["id"]]["on"]
    for moment in (start, end):
        for a in moment["actors"]:
            a.pop("_anchored", None)
    return {"room": {}, "objects": [], "start": start, "end": end}


def scene_key(scene: dict) -> str:
    return hashlib.sha1(json.dumps(scene, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def state_key(shot: dict, nxt: dict | None) -> str:
    """По чему писалась раскладка: правка этого меняет авто-/Gemini-раскладку (своя — остаётся)."""
    data = [shot.get("cast"), shot.get("camera"), shot.get("location"), shot.get("action"), shot.get("state"),
            V.end_state_of(shot, nxt)]
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Хранение, Gemini, рендер
# --------------------------------------------------------------------------- #


def _neighbors(store: Any, shot: dict) -> tuple[list[dict], int]:
    shots = V.list_shots(store, int(shot["script_id"]))
    ids = [int(s["id"]) for s in shots]
    return shots, ids.index(int(shot["id"]))


def _project_of(store: Any, shot: dict) -> int:
    return int(V.get_script(store, int(shot["script_id"]), with_shots=False)["project_id"])


def shot_layout(store: Any, project_id: int, shot: dict, vision: bool = False, client: Any = None) -> dict:
    """Комната плана — раскладка его локации (одна на все планы локации)."""
    loc = V.location_by_slug(store, project_id, shot.get("location") or "") if shot.get("location") else None
    return L.location_layout(store, project_id, loc, vision=vision, client=client)


def _save(store: Any, shot_id: int, data: dict) -> None:
    store.db.execute("UPDATE aiv_shots SET blocking_json = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), int(shot_id)))


def current(store: Any, shot: dict) -> dict:
    """Действующая раскладка плана: сохранённая (если не устарела по state) или авто по state."""
    shots, n = _neighbors(store, shot)
    nxt = shots[n + 1] if n + 1 < len(shots) else None
    data = shot.get("blocking") or {}
    key = state_key(shot, nxt)
    if data.get("spec") and (data.get("source") in ("agent", "owner") or data.get("state_key") == key):
        return data
    return {"spec": auto_spec(store, _project_of(store, shot), shot, nxt, prev=shots[:n]), "source": "auto",
            "state_key": key,
            "scenes": {}, "outdated": bool(data.get("spec"))}


def built_of(store: Any, shot: dict) -> dict[str, dict]:
    """Кадры блокинга плана {start, end?}: {scene, check, override}."""
    data = current(store, shot)
    project_id = _project_of(store, shot)
    infos = cast_info(store, project_id, shot)
    layout = shot_layout(store, project_id, shot)
    spec = clean_spec(data["spec"], [c["id"] for c in shot.get("cast") or []])
    out = {}
    for which in ("start", "end") if shot.get("needs_motion") else ("start",):
        override = (data.get("scenes") or {}).get(which)
        if isinstance(override, dict) and override.get("nodes"):
            out[which] = {"scene": override, "override": True,
                          "check": {"ok": True, "problems": [], "notes": ["своя сцена движка — не проверяется"]}}
        else:
            out[which] = {**build(spec, which, shot, infos, layout), "override": False}
    return out


def scenes_of(store: Any, shot: dict) -> dict[str, dict]:
    """Готовые JSON-сцены движка {start, end} (end — только у плана с видео)."""
    return {which: b["scene"] for which, b in built_of(store, shot).items()}


def frame_check(store: Any, shot: dict, which: str) -> dict:
    """Самоконтроль кадра блокинга ``which`` плана: ``{ok, problems, …}`` (кадра нет — ok False)."""
    try:
        built = built_of(store, shot).get(which)
    except Exception as exc:  # noqa: BLE001 - проверка не валит кадр: блокинг просто не используется
        return {"ok": False, "problems": [f"блокинг не собран: {type(exc).__name__}: {str(exc)[:160]}"]}
    if built is None:
        return {"ok": False, "problems": [f"блокинга «{which}» нет"]}
    return built["check"]


def set_blocking(store: Any, shot_id: int, spec: Any = None, scenes: Any = None, source: str = "agent") -> dict:
    """Своя раскладка плана (агент/владелец): spec и/или готовые сцены движка {start?, end?}."""
    from app import motion
    from app import scene_engine as se

    shot = V.get_shot(store, shot_id)
    data = dict(shot.get("blocking") or current(store, shot))
    if spec is not None:
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError as exc:
                raise V.AivError(f"spec — не JSON: {exc}") from exc
        if not isinstance(spec, dict):
            raise V.AivError("spec — объект {start?, end?, objects?, room?, camera?}")
        # Не названное — как было (правка одного старта не стирает конец и мебель).
        merged = {**(data.get("spec") or {}), **spec}
        data["spec"] = clean_spec(merged, [c["id"] for c in shot.get("cast") or []])
    if scenes is not None:
        if not isinstance(scenes, dict):
            raise V.AivError("scenes — {start?: сцена, end?: сцена} (JSON-сцены движка, get_scene_reference)")
        clean = {}
        for which in ("start", "end"):
            if isinstance(scenes.get(which), dict):
                problems: list[str] = []
                clean[which] = se.SceneSpec().norm(scenes[which], motion._Report(problems), f"scenes.{which}")
                if problems:
                    raise V.AivError("; ".join(problems[:5]))
        data["scenes"] = clean
    data["source"] = source
    _save(store, shot_id, data)
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                     "AND kind IN ('blocking_start', 'blocking_end')", (int(shot_id),))
    return view(store, V.get_shot(store, shot_id))


def view(store: Any, shot: dict) -> dict:
    data = current(store, shot)
    project_id = _project_of(store, shot)
    layout = shot_layout(store, project_id, shot)
    try:
        checks = {w: b["check"] for w, b in built_of(store, shot).items()}
    except Exception as exc:  # noqa: BLE001 - просмотр не падает из-за сборки
        checks = {"error": {"ok": False, "problems": [str(exc)[:200]]}}
    return {"shot_id": int(shot["id"]), "idx": shot["idx"], "source": data.get("source"),
            "outdated": bool(data.get("outdated")), "spec": data["spec"], "scenes_override": data.get("scenes") or {},
            "legend": legend(store, project_id, shot), "cast": cast_info(store, project_id, shot),
            "layout": {"source": layout.get("source"), "room": layout.get("room"),
                       "objects": [{k: o.get(k) for k in ("id", "type", "name", "pos", "y", "rot", "size", "on")}
                                   for o in layout.get("objects") or []]},
            "checks": checks,
            "frames": {w: V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{w}")
                       for w in ("start", "end")}}


BLOCKING_PROMPT = """\
Ты постановщик мультсериала и расставляешь персонажей и реквизит планов серии в подробный 3D-макет
(блокинг) для картинковой модели: она перерисует скриншот макета в кадр, сохранив композицию, позы и
места. Единицы — сантиметры. Оси: x вправо (от камеры), y вверх, z к камере; пол y=0; комната по
центру (0,0), задняя стена z = -depth/2. Комнаты локаций УЖЕ расставлены (layouts: вещи с id, типом,
pos [x, z], size [ширина, высота, глубина], y — высота низа, rot) — мебель не двигай и не придумывай,
ставь персонажей относительно неё. Для каждого плана — start (момент начала) и end (момент конца):
actors — каждый персонаж плана: pos [x, z], facing (градусы: 0 — лицом в камеру, 90 — вправо кадра,
-90 — влево, 180 — спиной), pose standing|walking|sitting|crouching|kneeling|lying, arms
down|up|forward|holding|wave (holding — держит предмет правой рукой), on — id вещи комнаты, на которой
сидит/лежит (кровать, стул, диван); props — реквизит плана из state и действия: name, count, и где он:
holder — id персонажа, у которого в руке, или on — id вещи комнаты, на которой стоит/лежит, или
pos [x, y, z]. Состояния бери из state (начало) и end_state (конец) плана; конец плана, который
продолжает следующий, = старт следующего (те же места и позы). Никто не стоит внутри мебели.
Робот-пылесос и другие «предметные» персонажи — всегда на полу. Камеру не пиши — её ставит сервис
по крупности плана. Верни ТОЛЬКО JSON по схеме."""


def _schema() -> dict:
    num = {"type": "number"}
    vec = {"type": "array", "items": num}
    actor = {"type": "object", "properties": {"id": {"type": "string"}, "pos": vec, "facing": num,
                                              "pose": {"type": "string"}, "arms": {"type": "string"},
                                              "on": {"type": "string"}},
             "required": ["id", "pos", "facing", "pose"]}
    prop = {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"},
                                             "count": {"type": "integer"}, "pos": vec, "holder": {"type": "string"},
                                             "on": {"type": "string"}}, "required": ["id"]}
    moment = {"type": "object", "properties": {"actors": {"type": "array", "items": actor},
                                               "props": {"type": "array", "items": prop}}, "required": ["actors"]}
    return {"type": "object", "properties": {
        "shots": {"type": "array", "items": {"type": "object", "properties": {
            "idx": {"type": "integer"}, "start": moment, "end": moment}, "required": ["idx", "start", "end"]}}},
        "required": ["shots"]}


def write_blocking(store: Any, script_id: int, client: Any = None, only: set[int] | None = None) -> dict:
    """Раскладка серии: Gemini одним запросом (бесплатно); нет ключа или ошибка — авто по state.
    ``only`` — id планов, которым писать (остальные не трогаются); своя раскладка (agent) не трогается."""
    from app.settings import settings

    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    shots = script["shots"]
    todo = [s for s in shots if (only is None or int(s["id"]) in only)
            and (s.get("blocking") or {}).get("source") not in ("agent", "owner")]
    if not todo:
        return {"ok": True, "source": "kept", "shots": []}
    data: dict = {}
    source, note = "auto", ""
    if client is not None or settings.gemini_api_key:
        try:
            data = _ask_gemini(store, script, client)
            source = "gemini"
        except Exception as exc:  # noqa: BLE001 - Gemini недоступен: раскладка по state
            note = f"Gemini не ответил ({type(exc).__name__}: {str(exc)[:160]}) — раскладка по state"
    by_idx = {int(s.get("idx") or 0): s for s in data.get("shots") or [] if isinstance(s, dict)}
    written = []
    for shot in todo:
        n = next(i for i, s in enumerate(shots) if s["id"] == shot["id"])
        nxt = shots[n + 1] if n + 1 < len(shots) else None
        auto = auto_spec(store, project_id, shot, nxt, prev=shots[:n])
        got = by_idx.get(int(shot["idx"]))
        if got and source == "gemini":
            raw = {"room": {}, "objects": [], "start": got.get("start") or auto["start"],
                   "end": got.get("end") or auto["end"]}
            spec, src = clean_spec(raw, [c["id"] for c in shot.get("cast") or []]), "gemini"
            for which in ("start", "end"):  # Gemini забыл персонажа или реквизит — ставим по state
                have = {a["id"] for a in spec[which]["actors"]}
                spec[which]["actors"] += [a for a in auto[which]["actors"] if a["id"] not in have]
                if not spec[which]["props"]:
                    spec[which]["props"] = auto[which]["props"]
        else:
            spec, src = auto, "auto"
        _save(store, int(shot["id"]), {"spec": spec, "source": src, "state_key": state_key(shot, nxt), "scenes": {}})
        written.append({"idx": shot["idx"], "source": src})
    return {"ok": True, "source": source, "shots": written, **({"note": note} if note else {})}


def _ask_gemini(store: Any, script: dict, client: Any = None) -> dict:
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings

    project_id = int(script["project_id"])
    shots = script["shots"]
    layouts = []
    for loc in V.list_locations(store, project_id):
        lay = L.location_layout(store, project_id, loc)
        layouts.append({"id": loc["slug"], "name": loc["name"], "room": lay.get("room"),
                        "objects": [{k: o.get(k) for k in ("id", "type", "name", "pos", "y", "rot", "size")}
                                    for o in lay.get("objects") or []]})
    context = {
        "characters": [{"id": c["slug"], "name": c["name"], "stand_in": stand_in(c),
                        "size_cm": body_size(c, stand_in(c)),
                        "identity_card": c["identity_card"], **({"constraints": c["constraints"]}
                                                                  if c.get("constraints") else {})}
                       for c in V.list_characters(store, project_id)],
        "layouts": layouts,
        "shots": [{"idx": s["idx"], "location": s["location"], "camera": s["camera"], "action": s["action"],
                   "cast": [c["id"] for c in s["cast"]], "continues_from_prev": s["continues_from_prev"],
                   "state": s.get("state") or {}, "end_state": V.end_state_of(s, shots[n + 1] if n + 1 < len(shots)
                                                                             else None)}
                  for n, s in enumerate(shots)],
    }
    payload = {"contents": [{"role": "user", "parts": [{"text": BLOCKING_PROMPT},
                                                       {"text": json.dumps(context, ensure_ascii=False)}]}],
               "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json",
                                    "responseSchema": _schema()}}
    response = (client or GeminiClient(timeout=90)).generate_content(settings.gemini_text_model, payload)
    data = json.loads(_extract_response_text(response))
    if not isinstance(data, dict):
        raise V.AivError("Gemini вернул не объект")
    return data


def _characters(store: Any, project_id: int, shot: dict) -> dict[str, dict]:
    out = {}
    for member in shot.get("cast") or []:
        c = V.character_by_slug(store, project_id, member["id"])
        if c:
            out[member["id"]] = c
    return out


def frame_plans(store: Any, shot: dict) -> dict[str, dict]:
    """Чем рисовать кадры блокинга плана: {which: {engine: 3d|2d, marker, scene}}. 3D-движок
    (``app/aivideo_blocking3d``) — по умолчанию, если доступен; своя сцена агента и нет браузера/паков
    (или AIV_BLOCKING_ENGINE=2d) — старый 2D-холст."""
    from app import aivideo_blocking3d as B3

    built = built_of(store, shot)
    use3d = B3.available()
    project_id = _project_of(store, shot)
    infos = chars = None
    out = {}
    for which, b in built.items():
        if use3d and not b.get("override"):
            if infos is None:
                infos, chars = cast_info(store, project_id, shot), _characters(store, project_id, shot)
            spec3 = B3.scene_spec(b, shot, infos, chars, which)
            out[which] = {"engine": "3d", "marker": f"blocking3d:{B3.spec_key(spec3)}", "scene": spec3, "built": b}
        else:
            out[which] = {"engine": "2d", "marker": f"blocking:{scene_key(b['scene'])}", "scene": b["scene"], "built": b}
    return out


def render(store: Any, shot_id: int, which: str, job: dict | None = None) -> dict:
    """Скриншот блокинга (9:16, 0 ₽) → версия ``blocking_<which>`` плана. Тот же кадр уже есть — он."""
    from uuid import uuid4

    from app import scene_engine as se
    from app.montage_assets import assets_dir, probe_asset

    shot = V.get_shot(store, shot_id)
    project_id = _project_of(store, shot)
    plans = frame_plans(store, shot)
    if which not in plans:
        raise V.AivError(f"план {shot['idx']}: блокинга «{which}» нет (конец — только у плана с видео)")
    plan = plans[which]
    marker = plan["marker"]
    kind = f"blocking_{which}"
    have = V.selected_ref(store, project_id, "shot", int(shot_id), kind)
    if have and have.get("prompt") == marker and have.get("asset_id"):
        if have["stale"]:
            store.db.execute("UPDATE aiv_refs SET stale = 0 WHERE id = ?", (int(have["id"]),))
        return V.get_ref(store, int(have["id"]))
    png = None
    if plan["engine"] == "3d":
        from app import aivideo_blocking3d as B3
        from app.scene3d import Scene3dError, SceneSpecError

        try:
            png = B3.render_png(plan["scene"])
        except (Scene3dError, SceneSpecError) as exc:  # 3D не отрисовался — кадр всё равно будет (2D)
            _log.warning("aiv: 3D-блокинг плана %s (%s) не отрисован: %s — 2D-холст", shot["idx"], which, exc)
            marker = f"blocking:{scene_key(plan['built']['scene'])}"
            plan = {**plan, "engine": "2d", "scene": plan["built"]["scene"]}
    if png is None:
        frames, _info = se.render_scene_png(store, plan["scene"], [0.0], w=BLOCKING_W, h=BLOCKING_H, background=None,
                                            width=RENDER_WIDTH)
        png = frames[0]
    dest = assets_dir() / f"{uuid4().hex}-aiv-{kind}.png"
    dest.write_bytes(png)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image", label=f"ИИ-видео: план {shot['idx']} — блокинг {which}"[:120],
        description=f"ИИ-видео: блокинг плана {shot['idx']} ({which}) — 3D-макет композиции"[:500],
        tags="ии-видео", file_path=str(dest), original_filename=dest.name, mime_type="image/png",
        size_bytes=dest.stat().st_size, project_id=int(project_id), **meta)
    return V.add_ref(store, project_id, "shot", int(shot_id), kind, int(asset["id"]), prompt=marker,
                     job_id=(job or {}).get("id"))


def needs_render(store: Any, project_id: int, shot: dict) -> list[str]:
    """Какие кадры блокинга плана надо (пере)рисовать: нет, устарел или сцена другая."""
    out = []
    for which, plan in frame_plans(store, shot).items():
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), f"blocking_{which}")
        if not ref or ref["stale"] or not ref.get("asset_id") or ref.get("prompt") != plan["marker"]:
            out.append(which)
    return out
