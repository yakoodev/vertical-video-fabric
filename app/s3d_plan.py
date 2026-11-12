"""План 3D-серии (проект вида ``series3d``): схема, проверка, правки и черновик по правилам.

План — JSON, его пишет агент (``series3d_plan_from_script``), правит владелец в интерфейсе или агент
(``series3d_update_plan``); сервис хранит каждую версию (``app/s3d_store.py``) и рендерит заданием
(``app/s3d_render.py``)::

    {"version": 1, "engine": "vrm_desk", "title": "Лиса из двух картинок",
     "cast": ["nika", "kira"],              # персонажи библиотеки: первый — в кадре, остальные — на мониторе эфира
     "location": "stream_room",             # локация библиотеки
     "narrator": "narrator",                # кто читает реплики без who: голос за кадром или персонаж
     "presets": {"look": "anime_soft", "light": "monitor_glow"},   # стиль, свет, настроение (пресеты движка)
     "props": ["cats"], "lead": 0.6, "tail": 1.8, "music": {"track_id": 3} | null,
     "captions": {"y": 0.8, "highlight": {"слово": "#ffd400"}}, "pronounce": {"Live2D": "лайв-ту-ди"},
     "screen": {…экран монитора эфира: title, uptime, camBg, chat…},
     "lines": [{"id": "l1", "who": "narrator", "text": "Реплика (субтитры)", "say": "как читать (TTS)",
                "pause_after": 0.4,
                "shots": [{"camera": "face", "move": "slow_push", "at": "слово", "strength": 0.6, "roll": 0}],
                "performance": [{"who": "nika", "preset": "happy", "at": "слово", "dt": 0.1, "dur": 2}],
                "events": [{"kind": "title", "text": "…", "dur": 3},
                           {"kind": "comment", "user": "kotletka_99", "text": "лиса топ", "card": true, "at": "лиса"},
                           {"kind": "card", "card": "alert", "title": "Донат", "amount": 200, "dur": 3},
                           {"kind": "sfx", "sound": "pop", "at": "end"}, {"kind": "viewers", "value": 7},
                           {"kind": "screen", "mode": "stream"}, {"kind": "avatar", "who": "kira", "act": "mirror"},
                           {"kind": "vrm", "do": {"nod": 2}}, {"kind": "vtubika", "do": {"decor": "retro"}}],
                "transition": {"kind": "dip_black", "duration": 0.4}}],
     "source": {"kind": "vtubika_plan", "file": "fox.json"}}

``at`` — слово реплики (начало первого слова, которое так начинается, без регистра и «ё»), ``end`` — конец реплики;
нет — начало реплики. Камера (``camera``) — ракурс движка, ``move`` — пресет движения камеры (каталог 3D-пресетов),
игра (``performance``) — пресеты эмоций и жестов, переход (``transition``) — пресет перехода в начале реплики.

Движки (``ENGINES``): ``vrm_desk`` — VRM-героиня за столом в нарисованной комнате стримера (рендер в сервисе);
``scene3d`` — комната-пресет с роботом-помощником или героем из примитивов (план проверяется, рендер — следующий шаг).
"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any

from app import presets3d

VERSION = 1
ENGINES = {"vrm_desk": "VRM-героиня за столом в нарисованной комнате стримера",
           "scene3d": "комната-пресет: робот-помощник или герой из примитивов (рендер — следующий шаг)"}
RENDERABLE = ("vrm_desk",)
ENGINE_LOCATION = {"vrm_desk": "plate", "scene3d": "room"}
ENGINE_CHARACTERS = {"vrm_desk": ("vrm",), "scene3d": ("deskbot", "voxel")}

# Ракурсы VRM за столом (app/vtubika_vrm.py CAMS): с героиней и вставки без неё во весь кадр.
VRM_FRAMINGS = {
    "wide": "общий план комнаты (со спины)", "face": "крупно лицо", "face_left": "крупно лицо, с другой стороны",
    "face_close": "очень крупно лицо", "smile": "крупно, чуть сверху", "two": "средний план: героиня и эфир над ней",
    "insert": "средний план под вставку телефона", "typing": "средний план, руки на клавиатуре",
    "ots": "через плечо на монитор", "monitor": "экран эфира во весь кадр", "monitor_close": "экран эфира крупно",
    "chat": "чат эфира во весь кадр", "tablet": "планшет с эскизом", "jar": "банка-копилка",
}
VRM_INSERTS = ("monitor", "monitor_close", "chat", "tablet", "jar")
SCENE3D_FRAMINGS = {"face": "лицо героя", "chest": "по грудь", "full": "во весь рост", "wide": "общий план комнаты"}
FRAMINGS = {"vrm_desk": VRM_FRAMINGS, "scene3d": SCENE3D_FRAMINGS}
# Движения камеры из каталога пресетов, которые честно работают на длинном объективе VRM за столом (фон нарисован).
VRM_MOVES = ("static", "slow_push", "pull_out", "punch_in", "crash_zoom", "handheld", "dutch_tilt", "dolly_zoom")

EVENT_KINDS = {
    "title": "надпись крупно (заголовок, подпись)", "comment": "комментарий в чате эфира, card — ещё и карточкой",
    "card": "карточка поверх кадра: chat (чат), screen (эфир), alert (донат), phone (телефон), hud (копилка)",
    "sfx": "звук из паков (имя или sfx:пак/файл)", "viewers": "счётчик зрителей", "screen": "что на мониторе",
    "avatar": "что делает аватар на мониторе", "vrm": "движение VRM как есть: nod, bow, look, arms, cat…",
    "vtubika": "событие истории Витубики как есть (do фразы плана Витубики)",
}
CARD_KINDS = ("chat", "screen", "alert", "phone", "hud")
SCREEN_MODES = ("stream", "draw", "shop", "rig")
AVATAR_ACTS = ("mirror", "idle", "png", "dance", "mug", "bow", "showcase")
MAX_LINES = 80
MAX_SEC = 120.0
CPS = 14.0  # символов речи в секунду — оценка длины до озвучки


class PlanError(ValueError):
    """План с ошибками: ``problems`` — по строке на ошибку (по-русски, с подсказкой)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _norm(word: Any) -> str:
    return re.sub(r"[^\w]", "", str(word or "").lower().replace("ё", "е"))


def tokens(text: str) -> list[str]:
    return [w for w in str(text or "").split() if _norm(w)]


def anchor_ok(text: str, at: Any) -> bool:
    if at in (None, "", "start", "end"):
        return True
    key = _norm(at)
    return bool(key) and any(_norm(w).startswith(key) for w in tokens(text))


def _num(v: Any, lo: float, hi: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= float(v) <= hi


def _hint(value: Any, options: Any) -> str:
    near = difflib.get_close_matches(str(value), list(options), n=3, cutoff=0.5)
    return f" (может, {', '.join(near)}?)" if near else f" (есть: {', '.join(list(options)[:24])})"


def perf_presets() -> list[str]:
    return [*presets3d.names("emotion"), *presets3d.names("gesture")]


def estimate_sec(plan: dict) -> float:
    lines = plan.get("lines") or []
    speech = sum(len(str(ln.get("say") or ln.get("text") or "")) for ln in lines) / CPS
    pauses = sum(float(ln.get("pause_after") or 0.3) for ln in lines)
    return round(speech + pauses + float(plan.get("lead") or 0) + float(plan.get("tail") or 0), 1)


def _shot(shot: Any, where: str, engine: str, text: str, problems: list[str]) -> dict:
    if not isinstance(shot, dict):
        problems.append(f"{where}: объект {{camera, move, at}}")
        return {}
    framings = FRAMINGS[engine]
    cam = str(shot.get("camera") or shot.get("cam") or "").strip()
    if cam not in framings:
        problems.append(f"{where}: ракурс «{cam}»{_hint(cam, framings)}")
    out: dict[str, Any] = {"camera": cam}
    move = shot.get("move")
    if move not in (None, ""):
        allowed = VRM_MOVES if engine == "vrm_desk" else presets3d.names("camera")
        if move not in allowed:
            problems.append(f"{where}: движение камеры «{move}»{_hint(move, allowed)}")
        out["move"] = str(move)
        params = {k: shot[k] for k in shot if k not in ("camera", "cam", "move", "at", "roll", "strength", "extra")}
        try:
            presets3d.validate_presets({"camera": [{"move": move, **params}]} if move in allowed else {})
        except presets3d.PresetError as exc:
            problems += [f"{where}: {p.split(': ', 1)[-1]}" for p in exc.problems]
        out.update({k: v for k, v in params.items() if isinstance(v, (int, float, str)) and not isinstance(v, bool)})
    for key, lo, hi in (("strength", 0, 3), ("roll", -40, 40)):
        if shot.get(key) is not None:
            if not _num(shot[key], lo, hi):
                problems.append(f"{where}: {key} — число от {lo} до {hi}")
            else:
                out[key] = round(float(shot[key]), 3)
    if shot.get("at") not in (None, ""):
        if not anchor_ok(text, shot["at"]):
            problems.append(f"{where}: нет слова «{shot['at']}» в реплике")
        out["at"] = str(shot["at"])
    if isinstance(shot.get("extra"), dict) and shot["extra"]:
        # Поля кадра истории Витубики как есть (импорт): sketch, zoom, fov, dof, shift.
        out["extra"] = {k: v for k, v in shot["extra"].items() if k in ("sketch", "zoom", "fov", "dof", "shift")}
    return out


def _perf(cue: Any, where: str, text: str, cast: list[str], problems: list[str]) -> dict:
    if not isinstance(cue, dict):
        problems.append(f"{where}: объект {{who, preset, at}}")
        return {}
    name = str(cue.get("preset") or "")
    names = perf_presets()
    if name not in names:
        problems.append(f"{where}: нет эмоции/жеста «{name}»{_hint(name, names)}")
    who = str(cue.get("who") or (cast[0] if cast else ""))
    if who not in cast:
        problems.append(f"{where}: «{who}» не в составе серии ({', '.join(cast) or 'состав пуст'})")
    out: dict[str, Any] = {"who": who, "preset": name}
    if cue.get("at") not in (None, ""):
        if not anchor_ok(text, cue["at"]):
            problems.append(f"{where}: нет слова «{cue['at']}» в реплике")
        out["at"] = str(cue["at"])
    for key, lo, hi in (("dt", -2, 5), ("dur", 0.3, 15), ("intensity", 0, 2)):
        if cue.get(key) is not None:
            if not _num(cue[key], lo, hi):
                problems.append(f"{where}: {key} — число от {lo} до {hi}")
            else:
                out[key] = round(float(cue[key]), 3)
    return out


def _event(ev: Any, where: str, text: str, cast: list[str], problems: list[str]) -> dict:
    from app import vtubika as U

    if not isinstance(ev, dict):
        problems.append(f"{where}: объект {{kind, …}}")
        return {}
    kind = str(ev.get("kind") or "")
    if kind not in EVENT_KINDS:
        problems.append(f"{where}: вид события «{kind}»{_hint(kind, EVENT_KINDS)}")
        return {}
    out = copy.deepcopy(ev)
    if ev.get("at") not in (None, "") and not anchor_ok(text, ev["at"]):
        problems.append(f"{where}: нет слова «{ev['at']}» в реплике")
    for key, lo, hi in (("dt", -2, 5), ("dur", 0.2, 30)):
        if ev.get(key) is not None and not _num(ev[key], lo, hi):
            problems.append(f"{where}: {key} — число от {lo} до {hi}")
    need = {"title": ("text",), "comment": ("user", "text"), "sfx": ("sound",), "viewers": ("value",),
            "screen": ("mode",), "card": ("card",), "vrm": ("do",), "vtubika": ("do",)}.get(kind, ())
    for key in need:
        if ev.get(key) in (None, ""):
            problems.append(f"{where}: у события {kind} нужно поле {key}")
    if kind == "title" and len(str(ev.get("text") or "")) > 80:
        problems.append(f"{where}: заголовок длиннее 80 знаков")
    if kind == "sfx" and ev.get("sound") and not (str(ev["sound"]) in U.SFX or str(ev["sound"]).startswith("sfx:")):
        problems.append(f"{where}: звук «{ev['sound']}» — имя{_hint(ev['sound'], U.SFX)} или ссылка sfx:пак/файл")
    if kind == "card" and ev.get("card") not in CARD_KINDS:
        problems.append(f"{where}: карточка — одна из {', '.join(CARD_KINDS)}")
    if kind == "screen" and ev.get("mode") and ev["mode"] not in SCREEN_MODES:
        problems.append(f"{where}: экран — один из {', '.join(SCREEN_MODES)}")
    if kind == "avatar":
        if ev.get("act") not in AVATAR_ACTS:
            problems.append(f"{where}: аватар делает одно из {', '.join(AVATAR_ACTS)}")
        if ev.get("who") and ev["who"] not in cast[1:]:
            problems.append(f"{where}: аватар «{ev['who']}» — из состава, кроме главной героини")
    if kind == "viewers" and not _num(ev.get("value"), 0, 10_000_000):
        problems.append(f"{where}: value — число зрителей")
    if kind in ("vrm", "vtubika") and not isinstance(ev.get("do"), dict):
        problems.append(f"{where}: do — объект события")
    return out


def validate_plan(plan: Any, lib: dict, settings: dict | None = None) -> tuple[dict, list[str]]:
    """Проверить и привести план: (план с id реплик и умолчаниями, предупреждения). Ошибки — PlanError."""
    if not isinstance(plan, dict):
        raise PlanError(["план — JSON-объект"])
    settings = settings or {}
    problems: list[str] = []
    warnings: list[str] = []
    out: dict[str, Any] = {"version": VERSION}
    engine = str(plan.get("engine") or settings.get("engine") or "vrm_desk")
    if engine not in ENGINES:
        raise PlanError([f"engine: одно из {', '.join(ENGINES)}"])
    out["engine"] = engine
    out["title"] = " ".join(str(plan.get("title") or "").split())[:120]
    chars = lib.get("characters") or {}
    cast = [str(c) for c in plan.get("cast") or [] if str(c)]
    if not cast:
        problems.append("cast: нужен хотя бы один персонаж библиотеки")
    if len(set(cast)) != len(cast):
        problems.append("cast: персонаж повторяется")
    for i, slug in enumerate(cast):
        ch = chars.get(slug)
        if not ch:
            problems.append(f"cast: нет персонажа «{slug}» в библиотеке{_hint(slug, chars)}")
        elif ch.get("type") not in ENGINE_CHARACTERS[engine]:
            problems.append(f"cast: «{slug}» ({ch.get('type')}) не подходит движку {engine} "
                            f"(нужен {' или '.join(ENGINE_CHARACTERS[engine])})")
    out["cast"] = cast
    locs = lib.get("locations") or {}
    loc = str(plan.get("location") or "")
    if loc not in locs:
        problems.append(f"location: нет локации «{loc}» в библиотеке{_hint(loc, locs)}")
    elif locs[loc].get("type") != ENGINE_LOCATION[engine]:
        problems.append(f"location: «{loc}» — {locs[loc].get('type')}, движку {engine} нужна {ENGINE_LOCATION[engine]}")
    out["location"] = loc
    narrator = str(plan.get("narrator") or "narrator")
    if narrator != "narrator" and narrator not in cast:
        problems.append("narrator: narrator (голос за кадром) или персонаж из cast")
    out["narrator"] = narrator
    block = {**(settings.get("presets") or {}), **(plan.get("presets") or {})}
    block = {k: v for k, v in block.items() if k in ("look", "light", "mood", "fx")}
    try:
        presets3d.validate_presets(block)
    except presets3d.PresetError as exc:
        problems += [f"presets: {p}" for p in exc.problems]
    if engine == "vrm_desk":
        if block.get("fx"):
            warnings.append("presets.fx: эффекты атмосферы в комнате стримера пока не применяются")
        look = block.get("look")
        if look and (look if isinstance(look, str) else look.get("name")) != "anime_soft":
            warnings.append("presets.look: VRM-персонажи рисуются в мягком аниме (anime_soft); другой стиль — для комнат-пресетов")
    out["presets"] = block
    props = [str(p) for p in plan.get("props") or []]
    for p in props:
        if p not in (lib.get("props") or {}):
            problems.append(f"props: нет вещи «{p}» в библиотеке")
    out["props"] = props
    for key, lo, hi, default in (("lead", 0, 5, settings.get("lead", 0.6)), ("tail", 0, 6, settings.get("tail", 1.8))):
        value = plan.get(key, default)
        if not _num(value, lo, hi):
            problems.append(f"{key}: число секунд от {lo} до {hi}")
            value = default
        out[key] = round(float(value), 3)
    caps = dict(plan.get("captions") or {})
    y = caps.get("y", (settings.get("captions") or {}).get("y", 0.8))
    if not _num(y, 0.1, 0.92):
        problems.append("captions.y: доля высоты кадра 0.1–0.92")
        y = 0.8
    hl = {str(k): str(v) for k, v in dict(caps.get("highlight") or {}).items()}
    for word, col in hl.items():
        if not re.match(r"^#[0-9a-fA-F]{6}$", col):
            problems.append(f"captions.highlight.{word}: цвет #rrggbb")
    out["captions"] = {"y": float(y), "highlight": hl}
    out["pronounce"] = {str(k): str(v) for k, v in dict(plan.get("pronounce") or {}).items()}
    music = plan.get("music", settings.get("music"))
    out["music"] = {k: music[k] for k in ("track_id", "ref", "under_db", "file") if isinstance(music, dict) and music.get(k) is not None} or None
    out["screen"] = copy.deepcopy(plan.get("screen")) if isinstance(plan.get("screen"), dict) else {}
    lines_in = plan.get("lines")
    if not isinstance(lines_in, list) or not lines_in:
        problems.append("lines: нужна хотя бы одна реплика")
        lines_in = []
    if len(lines_in) > MAX_LINES:
        problems.append(f"lines: не больше {MAX_LINES} реплик")
    lines: list[dict] = []
    seen: set[str] = set()
    for n, raw in enumerate(lines_in[:MAX_LINES]):
        where = f"реплика {n + 1}"
        if not isinstance(raw, dict):
            problems.append(f"{where}: объект {{who, text, shots, …}}")
            continue
        lid = re.sub(r"[^\w-]", "", str(raw.get("id") or ""))[:24] or f"l{n + 1}"
        while lid in seen:
            lid = f"{lid}_{n + 1}"
        seen.add(lid)
        text = " ".join(str(raw.get("text") or "").split())
        if not text or not tokens(text):
            problems.append(f"{where}: пустой текст")
        if len(text) > 400:
            problems.append(f"{where}: длиннее 400 знаков — раздели на две реплики")
        who = str(raw.get("who") or narrator)
        if who != "narrator" and who not in cast:
            problems.append(f"{where}: кто говорит «{who}» — narrator или персонаж из cast")
        line: dict[str, Any] = {"id": lid, "who": who, "text": text}
        if raw.get("say"):
            line["say"] = " ".join(str(raw["say"]).split())[:600]
        pause = raw.get("pause_after", 0.3)
        if not _num(pause, 0, 3):
            problems.append(f"{where}: pause_after — 0…3 с")
            pause = 0.3
        line["pause_after"] = round(float(pause), 3)
        line["shots"] = [_shot(s, f"{where}, кадр {i + 1}", engine, text, problems) for i, s in enumerate(raw.get("shots") or [])]
        line["performance"] = [_perf(c, f"{where}, игра {i + 1}", text, cast, problems)
                               for i, c in enumerate(raw.get("performance") or [])]
        line["events"] = [_event(e, f"{where}, событие {i + 1}", text, cast, problems) for i, e in enumerate(raw.get("events") or [])]
        tr = raw.get("transition")
        if tr:
            name = tr if isinstance(tr, str) else (tr or {}).get("kind")
            if not presets3d.get("transition", str(name)):
                problems.append(f"{where}: переход «{name}»{_hint(name, presets3d.names('transition'))}")
            dur = (tr.get("duration") if isinstance(tr, dict) else None) or 0.4
            if not _num(dur, 0.1, 1.5):
                problems.append(f"{where}: длина перехода 0.1–1.5 с")
            line["transition"] = {"kind": str(name), "duration": round(float(dur), 3) if _num(dur, 0.1, 1.5) else 0.4}
        if raw.get("show"):
            line["show"] = [str(x)[:200] for x in raw["show"]][:6]
        if raw.get("vtubika"):  # исходная фраза плана Витубики (импорт): do/vrm как есть
            line["vtubika"] = copy.deepcopy(raw["vtubika"])
        lines.append(line)
    if lines and not lines[0]["shots"]:
        lines[0]["shots"] = [{"camera": "wide", "move": "slow_push", "strength": 0.5}]
        warnings.append("у первой реплики не было кадра — поставлен общий план с медленным наездом")
    out["lines"] = lines
    if plan.get("source"):
        out["source"] = copy.deepcopy(plan["source"])
    est = estimate_sec(out)
    if est > MAX_SEC:
        problems.append(f"серия ≈ {est:.0f} с — длиннее {MAX_SEC:.0f} с: раздели на части")
    if problems:
        raise PlanError(list(dict.fromkeys(problems)))
    if engine not in RENDERABLE:
        warnings.append(f"движок {engine}: план проверен, рендер в сервисе — следующий шаг (сейчас рисуется только vrm_desk)")
    return out, warnings


# --------------------------------------------------------------------------- правки плана

TOP_FIELDS = ("title", "cast", "location", "narrator", "presets", "props", "lead", "tail", "music", "captions",
              "pronounce", "screen", "engine")


def apply_changes(plan: dict, changes: list[dict]) -> dict:
    """Правки плана по шагам (агент и UI): set — поле серии; line — поля реплики; insert — новая реплика после
    after (нет — в конец); delete; move — переставить реплику после after (пусто — в начало)."""
    out = copy.deepcopy(plan)
    lines = out.setdefault("lines", [])

    def idx(lid: Any) -> int:
        for i, ln in enumerate(lines):
            if ln.get("id") == lid:
                return i
        raise PlanError([f"нет реплики «{lid}» (есть: {', '.join(str(ln.get('id')) for ln in lines)})"])

    for n, ch in enumerate(changes or []):
        if not isinstance(ch, dict):
            raise PlanError([f"правка {n + 1}: объект {{op, …}}"])
        op = ch.get("op")
        if op == "set":
            if ch.get("field") not in TOP_FIELDS:
                raise PlanError([f"правка {n + 1}: поле серии — одно из {', '.join(TOP_FIELDS)}"])
            out[ch["field"]] = copy.deepcopy(ch.get("value"))
        elif op == "line":
            i = idx(ch.get("id"))
            fields = {k: v for k, v in dict(ch.get("set") or {}).items() if k != "id"}
            lines[i] = {**lines[i], **copy.deepcopy(fields)}
        elif op == "insert":
            pos = idx(ch["after"]) + 1 if ch.get("after") else len(lines)
            new = copy.deepcopy(ch.get("line") or {})
            if new.get("id") in {ln.get("id") for ln in lines}:
                new.pop("id")  # такой id уже есть — дадим новый
            if not new.get("id"):
                k = len(lines) + 1
                while f"l{k}" in {ln.get("id") for ln in lines}:
                    k += 1
                new["id"] = f"l{k}"
            lines.insert(pos, new)
        elif op == "delete":
            lines.pop(idx(ch.get("id")))
        elif op == "move":
            item = lines.pop(idx(ch.get("id")))
            lines.insert(idx(ch["after"]) + 1 if ch.get("after") else 0, item)
        else:
            raise PlanError([f"правка {n + 1}: op — set, line, insert, delete или move"])
    return out


# --------------------------------------------------------------------------- черновик по правилам (без Gemini)

_SENT = re.compile(r"(?<=[.!?…])\s+")
_DIALOG = re.compile(r"^\s*([А-ЯЁA-Z][\wЁё -]{0,30}?)\s*[:—–-]\s+(.+)$")
# Камера по кругу: соседние реплики — разные ракурсы, первая — общий план.
CAMERA_CYCLE = [("face", "slow_push"), ("two", "static"), ("face_left", "slow_push"), ("ots", "handheld"),
                ("face_close", "punch_in"), ("smile", "static")]
_KEYWORDS = [
    (re.compile(r"устал|сонн|зева", re.I), "tired"), (re.compile(r"бесит|злюсь|злая|злой|ненавиж", re.I), "angry"),
    (re.compile(r"обид", re.I), "offended"), (re.compile(r"шок|не может быть|что\?!|офиг", re.I), "shocked"),
    (re.compile(r"грусть|грустн|жаль|плак", re.I), "sad"), (re.compile(r"паник|помогите|ужас", re.I), "panic"),
    (re.compile(r"привет|пока\b|до встречи", re.I), "wave"), (re.compile(r"^\s*нет\b", re.I), "head_shake"),
    (re.compile(r"^\s*да\b|конечно", re.I), "nod"), (re.compile(r"думаю|интересно|хм+\b", re.I), "thinking"),
    (re.compile(r"пиш|печата|набира", re.I), "typing"), (re.compile(r"вздох|эх\b|ох\b", re.I), "sigh"),
]


def split_script(script: str) -> list[tuple[str, str]]:
    """Сценарий → [(кто по тексту или "", реплика)]: строки «Имя: текст» — реплики персонажа, остальное — по фразам."""
    out: list[tuple[str, str]] = []
    for raw in str(script or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _DIALOG.match(line)
        who, body = (m.group(1).strip(), m.group(2).strip()) if m else ("", line)
        for part in _SENT.split(body):
            part = part.strip()
            if not tokens(part):
                continue
            if out and out[-1][0] == who and len(out[-1][1]) < 24 and len(part) < 60:
                out[-1] = (who, f"{out[-1][1]} {part}")  # «Рот закрыт.» — коротко: с соседней фразой одним кадром
            else:
                out.append((who, part))
    return out


def _who(name: str, lib: dict, cast: list[str], narrator: str) -> str:
    if not name:
        return narrator
    key = _norm(name)
    for slug in cast:
        ch = (lib.get("characters") or {}).get(slug) or {}
        if key in (_norm(slug), _norm(ch.get("name"))):
            return slug
    return narrator


def draft_from_script(script: str, lib: dict, settings: dict, *, title: str = "", cast: list[str] | None = None,
                      location: str | None = None, narrator: str = "narrator") -> dict:
    """Черновик плана без Gemini: реплика на фразу, ракурсы по кругу (первый — общий план), эмоции по словам."""
    chars = lib.get("characters") or {}
    engine = settings.get("engine") or "vrm_desk"
    if not cast:
        cast = [s for s, c in chars.items() if c.get("type") in ENGINE_CHARACTERS[engine]][:1]
    if not location:
        location = next((s for s, loc in (lib.get("locations") or {}).items() if loc.get("type") == ENGINE_LOCATION[engine]), "")
    parts = split_script(script)
    lines = []
    for n, (name, text) in enumerate(parts):
        who = _who(name, lib, cast, narrator)
        if n == 0:
            cam, move = ("wide", "slow_push")
        else:
            cam, move = CAMERA_CYCLE[(n - 1) % len(CAMERA_CYCLE)]
            if text.rstrip().endswith("!") and n % 2:
                cam, move = "face_close", "punch_in"
        if engine != "vrm_desk":
            cam = {"wide": "wide", "two": "chest", "ots": "full"}.get(cam, "face")
        line: dict[str, Any] = {"id": f"l{n + 1}", "who": who, "text": text,
                                "shots": [{"camera": cam, "move": move, "strength": 0.5}],
                                "pause_after": 0.5 if text.endswith(("?", "!", "…")) else 0.3}
        perf = next((p for rx, p in _KEYWORDS if rx.search(text)), None)
        if perf is None and text.rstrip().endswith("!"):
            perf = "happy"
        if perf and cast:
            line["performance"] = [{"who": who if who in cast else cast[0], "preset": perf}]
        lines.append(line)
    if lines and title:
        lines[0]["events"] = [{"kind": "title", "text": title[:80], "dur": 2.6}]
    return {"version": VERSION, "engine": engine, "title": title or (parts[0][1][:60] if parts else ""), "cast": cast,
            "location": location, "narrator": narrator, "presets": dict(settings.get("presets") or {}), "lines": lines}


def cheatsheet() -> str:
    """Шпаргалка схемы плана для агента (по-русски, коротко)."""
    return "\n".join([
        "План 3D-серии: {engine, title, cast: [slug персонажей; первый — в кадре], location, narrator, presets {look, light, mood},"
        " lines: [{id, who, text, say?, pause_after?, shots: [{camera, move?, at?, strength?, roll?}],"
        " performance: [{who, preset, at?, dt?, dur?}], events: [{kind, …}], transition?: {kind, duration}}]}.",
        "Ракурсы vrm_desk: " + ", ".join(f"{k} — {v}" for k, v in VRM_FRAMINGS.items()) + ".",
        "Движения камеры: " + ", ".join(VRM_MOVES) + " (параметры — list_3d_presets category=camera).",
        "Игра: " + ", ".join(perf_presets()) + ".",
        "События: " + "; ".join(f"{k} — {v}" for k, v in EVENT_KINDS.items()) + ".",
        "Переходы: " + ", ".join(presets3d.names("transition")) + ". at — слово реплики, end — её конец.",
    ])
