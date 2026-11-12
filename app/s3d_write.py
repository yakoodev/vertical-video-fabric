"""План 3D-серии из сценария или темы: Gemini (бесплатно, ключ владельца) раскладывает текст на реплики, кадры, игру
и события по каталогу движка; без Gemini — черновик по правилам (``app.s3d_plan.draft_from_script``).

Ответ Gemini проверяется той же ``validate_plan``: неизвестный ракурс, пресет или слово-якорь не ломают план —
такое поле выкидывается с заметкой (``sanitize``), чтобы владелец получил рабочий черновик, а не ошибку.
"""

from __future__ import annotations

from typing import Any

from app import presets3d
from app import s3d_plan as SP
from app import s3d_store as D

RULES = """\
Ты режиссёр вертикальных 3D-сериалов (Shorts/TikTok/Reels, 15–60 с) на нашем движке: VRM-героиня сидит за
столом в комнате стримерши, на мониторе эфир и чат; камера — готовые ракурсы и движения, игра — готовые эмоции и
жесты, на экране — заголовки, комментарии чата, карточки. Пиши ПО-РУССКИ, разговорно, без канцелярита.
Если script пуст — сначала придумай сценарий на тему topic (примерно duration_sec секунд, ~14 символов в секунду).
Разложи сценарий на реплики (lines): одна реплика — одна фраза до ~15 слов; who — slug персонажа из cast (говорит
в кадре) или "narrator" (голос за кадром). Текст реплик — ДОСЛОВНО из сценария (если он дан), только порезанный.
На каждую реплику 1–2 кадра (shots): camera — только из framings, move — только из moves, at — СЛОВО ЭТОЙ реплики,
с которого начинается кадр (первое — без at). Соседние кадры — разные ракурсы; первый кадр серии — wide.
Вставки без героини (monitor, chat, tablet, jar) — когда о них говорят. strength 0.3–1 (мягче на крупных).
performance — 0–2 пресета на реплику (preset из emotion/gesture, at — слово). events — по делу: title (крупная надпись
2–3 с в начале), comment (сообщение в чате: user латиницей, text коротко, card: true — ещё и карточкой поверх),
sfx (sound из sounds). transition — редко, на смене темы (kind из transitions).
Верни ТОЛЬКО JSON по схеме.
"""


def schema() -> dict:
    s = {"type": "string"}
    n = {"type": "number"}
    return {"type": "object", "properties": {
        "title": s, "script": s,
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "who": s, "text": s, "pause_after": n,
            "shots": {"type": "array", "items": {"type": "object", "properties": {
                "camera": s, "move": s, "at": s, "strength": n}, "required": ["camera"]}},
            "performance": {"type": "array", "items": {"type": "object", "properties": {
                "who": s, "preset": s, "at": s, "dur": n}, "required": ["preset"]}},
            "events": {"type": "array", "items": {"type": "object", "properties": {
                "kind": s, "text": s, "user": s, "sound": s, "at": s, "dur": n, "card": {"type": "boolean"}},
                "required": ["kind"]}},
            "transition": {"type": "object", "properties": {"kind": s, "duration": n}}},
            "required": ["who", "text", "shots"]}}},
        "required": ["title", "lines"]}


def context(lib: dict, cast: list[str], location: str, script: str, topic: str, duration: float, tone: str) -> dict:
    from app import vtubika as U

    chars = lib.get("characters") or {}
    return {"script": script, "topic": topic, "duration_sec": duration, "tone": tone,
            "cast": [{"slug": s, "name": (chars.get(s) or {}).get("name"), "persona": (chars.get(s) or {}).get("persona")}
                     for s in cast],
            "location": location, "framings": SP.VRM_FRAMINGS, "moves": list(SP.VRM_MOVES),
            "emotion": presets3d.names("emotion"), "gesture": presets3d.names("gesture"),
            "transitions": presets3d.names("transition"), "sounds": list(U.SFX), "event_kinds": ["title", "comment", "sfx"]}


def sanitize(raw: dict, cast: list[str]) -> tuple[dict, list[str]]:
    """Ответ Gemini → план: выкинуть то, чего нет в каталоге, и слова-якоря не из реплики (с заметками)."""
    notes: list[str] = []
    perf = set(SP.perf_presets())
    lines = []
    for n, ln in enumerate(raw.get("lines") or []):
        text = " ".join(str(ln.get("text") or "").split())
        if not SP.tokens(text):
            continue
        who = ln.get("who") if ln.get("who") in [*cast, "narrator"] else "narrator"
        shots = []
        for sh in ln.get("shots") or []:
            cam = sh.get("camera") if sh.get("camera") in SP.VRM_FRAMINGS else "face"
            item = {"camera": cam}
            if sh.get("move") in SP.VRM_MOVES:
                item["move"] = sh["move"]
            if sh.get("at") and SP.anchor_ok(text, sh["at"]) and shots:
                item["at"] = sh["at"]
            if isinstance(sh.get("strength"), (int, float)) and 0 <= sh["strength"] <= 3:
                item["strength"] = sh["strength"]
            if shots and "at" not in item:
                continue  # второй кадр без слова начала — лишний
            shots.append(item)
        cues = []
        for c in ln.get("performance") or []:
            if c.get("preset") not in perf:
                notes.append(f"реплика {n + 1}: пресета «{c.get('preset')}» нет — выкинут")
                continue
            cue = {"who": c.get("who") if c.get("who") in cast else cast[0], "preset": c["preset"]}
            if c.get("at") and SP.anchor_ok(text, c["at"]):
                cue["at"] = c["at"]
            if isinstance(c.get("dur"), (int, float)) and 0.3 <= c["dur"] <= 15:
                cue["dur"] = c["dur"]
            cues.append(cue)
        events = []
        for e in ln.get("events") or []:
            kind = e.get("kind")
            ev = {k: e[k] for k in ("kind", "text", "user", "sound", "dur", "card") if e.get(k) not in (None, "")}
            if e.get("at") and SP.anchor_ok(text, e["at"]):
                ev["at"] = e["at"]
            probe: list[str] = []
            SP._event(ev, "", text, cast, probe)  # noqa: SLF001 - та же проверка события, что у плана
            if kind not in ("title", "comment", "sfx") or probe:
                notes.append(f"реплика {n + 1}: событие {kind} не по схеме — выкинуто")
                continue
            events.append(ev)
        line = {"who": who, "text": text, "shots": shots, "performance": cues, "events": events}
        if isinstance(ln.get("pause_after"), (int, float)) and 0 <= ln["pause_after"] <= 3:
            line["pause_after"] = ln["pause_after"]
        tr = ln.get("transition") or {}
        if presets3d.get("transition", str(tr.get("kind") or "")):
            line["transition"] = {"kind": tr["kind"], "duration": tr.get("duration") if isinstance(tr.get("duration"), (int, float))
                                  and 0.1 <= tr["duration"] <= 1.5 else 0.4}
        lines.append(line)
    return {"title": str(raw.get("title") or "")[:120], "lines": lines}, notes


def gemini_client() -> Any:
    """Клиент Gemini или None. Тесты подменяют."""
    from app.explainer_gen import gemini_client as client

    return client()


def ask(client: Any, ctx: dict) -> dict:
    from app.explainer_gen import _ask

    return _ask(client, RULES, ctx, schema(), temperature=0.7)


def plan_from_script(store: Any, project_id: int, *, script: str = "", topic: str = "", title: str = "",
                     cast: list[str] | None = None, location: str | None = None, narrator: str = "narrator",
                     duration: float = 30, tone: str = "", client: Any = None) -> dict:
    """План (проверенный) + откуда он: {plan, warnings, source: gemini|rules, notes}."""
    lib, cfg = D.library(store, project_id), D.get_settings(store, project_id)
    if not (script or "").strip() and not (topic or "").strip():
        raise SP.PlanError(["нужен сценарий (script) или тема (topic)"])
    draft = SP.draft_from_script(script or topic, lib, cfg, title=title, cast=cast, location=location, narrator=narrator)
    cast, location = draft["cast"], draft["location"]
    notes: list[str] = []
    source = "rules"
    client = client if client is not None else gemini_client()
    if client is not None:
        try:
            raw = ask(client, context(lib, cast, location, script, topic, duration, tone))
            got, notes = sanitize(raw, cast)
            if got["lines"]:
                draft = {**draft, "title": title or got["title"] or draft["title"], "lines": got["lines"]}
                source = "gemini"
        except Exception as exc:  # noqa: BLE001 - Gemini недоступен: черновик по правилам
            notes.append(f"Gemini не ответил ({type(exc).__name__}) — план по правилам")
    elif not (script or "").strip():
        raise SP.PlanError(["Gemini недоступен, а по одной теме план по правилам не написать — пришлите текст сценария"])
    else:
        notes.append("Gemini не подключён — план по правилам (реплика на фразу, ракурсы по кругу)")
    plan, warnings = SP.validate_plan(draft, lib, cfg)
    return {"plan": plan, "warnings": warnings, "source": source, "notes": notes}
