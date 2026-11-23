"""Звук 3D-серии от автора сценария: фон места, звуки событий по умолчанию и их замена, свои звуки фраз.

Ч.5–6 «Лифта» собирали руками: лестница звучала своим гулом, а не гулом кабины; на «пришло сообщение» телефон
вибрировал; фонарик щёлкал, пока мигал, и темнота падала глухим ударом. Сценарий теперь говорит это сам:

* ``script.ambience`` — фон места списком ``[{bed | ref, from, at, to, to_at, under_db, fade_in, fade_out, label,
  volume}]``: ``bed`` — имя из ``BEDS`` (петли пака tension), ``ref`` — любая петля пака (``sfx:пак/файл.ogg``);
  ``from``/``to`` — номер фразы (с 0), ``at``/``to_at`` — секунды от начала её плана; без ``from`` — с начала серии,
  без ``to`` — до конца. Пустой список — тишина без гула кабины. Нет поля — фон лифта как раньше (``sfx_for``).
* ``script.event_sfx`` — замена звука события по его виду (``msg_in``, ``vibrate``, ``flick_click``, ``dark_hit``,
  ``blackout``, ``doors_open``…): ``false`` — без звука, ``"sfx:пак/файл.ogg"`` или ``{ref, volume}`` — своя запись.
* ``sentence.sounds`` — свои звуки фразы ``[{ref, at, volume, label}]`` или событие ``[{event, at}]``
  (``SOUND_EVENTS``: вибрация, сообщение, стук…), ``at`` — секунды от начала плана фразы.
* По умолчанию: фонарик автора (``flicker``) щёлкает на каждом «погас» и гаснет ударом; на «пришло сообщение»
  телефон ещё и вибрирует (звук + дрожь в руке).

Только настоящие записи паков (память sfx-and-overlays: синтетики нет), громкость относительно голоса ставит холст
(``stories_3d.sfx_layers``). Модуль грузит и Blender (через ``series3d_blender``): на верхнем уровне — только
стандартная библиотека.
"""
from __future__ import annotations

import re
from typing import Any

REF_RX = re.compile(r"^sfx:[a-z0-9][a-z0-9_-]{0,40}/[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.(ogg|wav|mp3)$")
# Фон-петли пака tension: имя → (запись, на сколько дБ тише голоса, подпись)
BEDS = {
    "cabin": ("sfx:tension/cabin-hum.ogg", 26.0, "гул кабины"),
    "shaft": ("sfx:tension/shaft-rumble.ogg", 24.0, "гул шахты"),
    "stairwell": ("sfx:tension/bed-space-drone.ogg", 20.0, "гул лестницы"),
    "deep": ("sfx:tension/bed-ominous-deep.ogg", 18.0, "низкий гул"),
    "terror": ("sfx:tension/bed-terror.ogg", 20.0, "тревожный гул"),
    "creepy": ("sfx:tension/bed-scary-loop.ogg", 20.0, "жуткий фон"),
    "strings": ("sfx:tension/bed-dark-strings.ogg", 20.0, "тёмные струнные"),
    "gloom": ("sfx:tension/bed-risset-gloom.ogg", 20.0, "бесконечное падение"),
}
# События, которые фраза может поставить сама (``sounds: [{event, at}]``): исполнение их видит (дрожь телефона…)
SOUND_EVENTS = ("vibrate", "msg_in", "knock", "jolt", "sting", "flash", "scrape")
# Фонарик мигает: щелчок на каждом «погас», в конце — темнота глухим ударом (ч.6)
FLICKER_CLICK = ("sfx:kenney-interface/glitch_002.ogg", 0.22, "фонарик мигает")
FLICKER_DARK = ("sfx:freesound/impact-low.ogg", 0.55, "темнота")
FLICKER_AT, FLICKER_DARK_AT = 0.5, 2.0       # как у исполнения (series3d_blender_perform), если автор не сказал


def sentence_starts(shots: list[dict]) -> dict[int, float]:
    """Начало первого плана каждой фразы, с."""
    out: dict[int, float] = {}
    for s in shots:
        for i in s.get("sentences") or []:
            out.setdefault(int(i), float(s["t0"]))
    return out


def _flicker_events(shots: list[dict]) -> list[dict]:
    from app import series3d_stairs as ST

    out: list[dict] = []
    for s in shots:
        fl = s.get("flicker")
        if not isinstance(fl, dict) or s.get("by") != "author":
            continue
        t0 = float(s["t0"])
        t_dark = t0 + float(fl.get("dark", FLICKER_DARK_AT))
        for a, _b, v in ST.flicker_spans(t0 + float(fl.get("at", FLICKER_AT)), t_dark)[:-1]:
            if v == 0.0:
                out.append({"kind": "flick_click", "t": round(a, 3), "sfx": FLICKER_CLICK[0], "volume": FLICKER_CLICK[1],
                            "label": FLICKER_CLICK[2]})
        out.append({"kind": "dark_hit", "t": round(t_dark, 3), "sfx": FLICKER_DARK[0], "volume": FLICKER_DARK[1],
                    "label": FLICKER_DARK[2]})
    return out


def author_events(sentences: list[dict], shots: list[dict], events: list[dict]) -> list[dict]:
    """События задания + звуки автора: свои звуки фраз, щелчки мигающего фонарика, вибрация на сообщении.
    Ничего из этого нет — список тот же (у старых серий задание прежнее)."""
    starts = sentence_starts(shots)
    extra: list[dict] = []
    for i, sent in enumerate(sentences):
        if i not in starts:
            continue
        for snd in sent.get("sounds") or []:
            t = round(starts[i] + float(snd.get("at") or 0.0), 3)
            if snd.get("event"):
                extra.append({"kind": str(snd["event"]), "t": t, "sentence": i, "role": ""})
            elif snd.get("ref"):
                extra.append({"kind": "sound", "t": t, "sfx": str(snd["ref"]), "volume": float(snd.get("volume", 0.6)),
                              "label": str(snd.get("label") or "звук"), "sentence": i})
    extra += _flicker_events(shots)
    vib = [float(e["t"]) for e in events + extra if e["kind"] == "vibrate"]
    for e in events + extra:
        # пришло сообщение — телефон и вибрирует (если автор уже не поставил вибрацию рядом)
        if e["kind"] == "msg_in" and float(e["t"]) >= 0 and not any(-0.5 <= v - float(e["t"]) <= 1.5 for v in vib):
            extra.append({"kind": "vibrate", "t": round(float(e["t"]), 3)})
    if not extra:
        return events
    return sorted(list(events) + extra, key=lambda e: float(e["t"]))


def author_ambience(spec: Any, shots: list[dict], total: float) -> list[dict] | None:
    """``script.ambience`` → фон задания ``[{ref, t, end, under_db, fade_in, fade_out, label}]`` (читает ``sfx_for``).
    Нет поля — ``None`` (фон лифта по событиям, как раньше); пустой список — тишина."""
    if spec is None:
        return None
    starts = sentence_starts(shots)
    out: list[dict] = []
    for item in spec if isinstance(spec, list) else []:
        bed = BEDS.get(str(item.get("bed") or ""))
        ref = item.get("ref") or (bed[0] if bed else None)
        if not ref:
            continue
        t0 = 0.0 if item.get("from") is None else starts.get(int(item["from"]))
        t1 = float(total) if item.get("to") is None else starts.get(int(item["to"]))
        if t0 is None or t1 is None:
            continue
        t0 += float(item.get("at") or 0.0)
        t1 += float(item.get("to_at") or 0.0) if item.get("to") is not None else 0.0
        row = {"ref": str(ref), "t": round(max(0.0, t0), 3), "end": round(min(float(total), t1), 3),
               "under_db": float(item.get("under_db", bed[1] if bed else 20.0)),
               "fade_in": float(item.get("fade_in", 0.6)), "fade_out": float(item.get("fade_out", 0.6)),
               "label": str(item.get("label") or (bed[2] if bed else "фон"))}
        if item.get("volume") is not None:
            row["volume"] = float(item["volume"])
        out.append(row)
    return out


def override(job: dict, kind: str, ref_vol: tuple[str, float]) -> tuple[str, float] | None:
    """Звук события с заменой автора (``job.event_sfx``): ``None`` — автор выключил этот звук."""
    ov = (job.get("event_sfx") or {}).get(kind)
    if ov is False:
        return None
    if isinstance(ov, dict) and ov.get("ref"):
        return str(ov["ref"]), float(ov.get("volume", ref_vol[1]))
    return ref_vol
