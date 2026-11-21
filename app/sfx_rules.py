"""Звук по меткам, а не вручную (findings.md п. 53–55): только записи пака freesound (настоящие, CC0).

* склейка / переход → whoosh, пик на склейке (начало звука за 50 мс до неё);
* крик / громко (``audio_map.label_spans``) → «бум» (drama-boom), а на смехе/коротком громком — «динь» со
  вспышкой (``fx flash_frame``);
* неловкая пауза после реплики дольше 1,5 с → «awkward» (в паке нет сверчков; ближайшая запись — record-scratch).

Плотность ограничена: не больше ``MAX_PER_MIN`` звуков в минуту (скользящее окно 60 с) и не ближе
``MIN_GAP`` друг к другу; при споре важнее крик, потом склейка, потом пауза. ``check_sfx`` — замер: у каждой
склейки звук в пределах ±50 мс (или он снят ради плотности и это сказано), звуков в минуту ≤ N, все файлы из пака.
"""

from __future__ import annotations

from typing import Iterable

PACK = "freesound"
SOUNDS = {  # предпочтения по порядку: первая запись, что есть в паке
    "transition": ("whoosh-1.ogg", "whoosh-2.ogg", "whoosh-3.ogg"),
    "shout": ("drama-boom.ogg", "impact-low.ogg"),
    "ding": ("ding.ogg", "triangle.ogg"),
    "awkward": ("crickets.ogg", "record-scratch.ogg", "record-scratch-2.ogg"),
}
VOLUME = {"transition": 0.5, "shout": 0.7, "ding": 0.55, "awkward": 0.6}
PRIORITY = {"shout": 0, "ding": 0, "transition": 1, "awkward": 2}
MAX_PER_MIN = 6
MIN_GAP = 1.2
SAME_KIND_GAP = 4.0  # один и тот же звук не чаще раза в 4 с (пилот GTA 6 v5: четыре «бума» за 4 с на крике)
AWKWARD_PAUSE = 1.5
SHOUT_MIN_SEC = 0.4
CUT_LEAD = 0.05
CUT_TOLERANCE = 0.05
FLASH_SEC = 0.25


def pack_files() -> set[str]:
    """Файлы пака freesound на диске (vendor/packs/freesound)."""
    from app.motion_assets import pack_item_info

    out = set()
    for kind in SOUNDS.values():
        for name in kind:
            try:
                if pack_item_info(f"sfx:{PACK}/{name}"):
                    out.add(name)
            except Exception:  # noqa: BLE001 - нет пака — нет звука, синтетику не подставляем
                continue
    return out


def pick(kind: str, available: Iterable[str]) -> str | None:
    have = set(available)
    return next((f"sfx:{PACK}/{n}" for n in SOUNDS[kind] if n in have), None)


def sfx_events(cuts: Iterable[float], spans: Iterable[tuple[float, float, str]], words: list[tuple[float, float]],
               duration: float) -> list[dict]:
    """События для звука в шкале ролика: склейки, крик/громко/смех, неловкие паузы после реплик."""
    events = [{"t": round(float(t), 3), "kind": "transition"} for t in cuts if 0 < float(t) < duration]
    for a, b, lab in spans or []:
        if float(b) - float(a) < SHOUT_MIN_SEC:
            continue
        if lab == "крик" or (lab == "громко" and float(b) - float(a) >= 1.0):
            events.append({"t": round(float(a), 3), "kind": "shout"})
        elif lab in ("смех?", "громко"):
            events.append({"t": round(float(a), 3), "kind": "ding"})
    ws = sorted(words or [])
    for (_a0, a1), (b0, _b1) in zip(ws, ws[1:]):
        if b0 - a1 > AWKWARD_PAUSE:
            quiet = not any(lab in ("крик", "громко", "смех?") and float(s0) < b0 and float(s1) > a1
                            for s0, s1, lab in spans or [])
            if quiet:  # громкая пауза — это реакция, не неловкость
                events.append({"t": round(a1 + 0.4, 3), "kind": "awkward"})
    return sorted(events, key=lambda e: (e["t"], PRIORITY[e["kind"]]))


def limit_density(events: list[dict], max_per_min: int = MAX_PER_MIN, min_gap: float = MIN_GAP) -> tuple[list[dict], list[dict]]:
    """Отбор по важности с ограничением плотности: (оставленные, снятые)."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for ev in sorted(events, key=lambda e: (PRIORITY[e["kind"]], e["t"])):
        t = ev["t"]
        near = any(abs(t - k["t"]) < min_gap or (k["kind"] == ev["kind"] and ev["kind"] != "transition"
                                                  and abs(t - k["t"]) < SAME_KIND_GAP) for k in kept)
        if near or _would_exceed(kept, t, max_per_min):
            dropped.append(ev)
        else:
            kept.append(ev)
    return sorted(kept, key=lambda e: e["t"]), sorted(dropped, key=lambda e: e["t"])


def _would_exceed(kept: list[dict], t: float, max_per_min: int) -> bool:
    times = sorted([k["t"] for k in kept] + [t])
    j = 0
    for i, x in enumerate(times):
        while x - times[j] >= 60.0:
            j += 1
        if i - j + 1 > max_per_min:
            return True
    return False


def sfx_layers(events: list[dict], available: Iterable[str], duration: float, prefix: str = "lsfx_") -> list[dict]:
    """Слои ``audio`` (и вспышка ``fx flash_frame`` у «динь») по событиям; кинды без записи в паке пропускаются."""
    have = set(available)
    out: list[dict] = []
    for n, ev in enumerate(events):
        ref = pick(ev["kind"], have)
        if not ref:
            continue
        t = float(ev["t"])
        start = max(0.0, t - CUT_LEAD) if ev["kind"] == "transition" else t
        end = min(duration, start + 1.2)
        if end - start < 0.1:
            continue
        out.append({"id": f"{prefix}{n:02d}", "type": "audio", "name": f"Звук: {ev['kind']}", "start": round(start, 3),
                    "end": round(end, 3), "props": {"asset_id": ref, "volume": VOLUME[ev["kind"]], "role": "sfx"}})
        if ev["kind"] == "ding":
            out.append({"id": f"{prefix}{n:02d}f", "type": "fx", "name": "Вспышка", "start": round(t, 3),
                        "end": round(min(duration, t + FLASH_SEC), 3), "track": 9,
                        "out": {"type": "fade", "duration": FLASH_SEC * 0.8},
                        "props": {"kind": "flash_frame", "intensity": 0.5, "color": "#ffffff"}})
    return out


def place_sfx(cuts: Iterable[float], spans: Iterable[tuple[float, float, str]], words: list[tuple[float, float]],
              duration: float, available: Iterable[str] | None = None, max_per_min: int = MAX_PER_MIN) -> dict:
    """Всё вместе: события → отбор по плотности → слои. ``available`` — файлы пака (нет — с диска)."""
    have = set(pack_files() if available is None else available)
    events = sfx_events(cuts, spans, words, duration)
    kept, dropped = limit_density(events, max_per_min)
    return {"layers": sfx_layers(kept, have, duration), "kept": kept, "dropped": dropped}


def check_sfx(layers: list[dict], cuts: Iterable[float], duration: float, dropped: Iterable[dict] = (),
              max_per_min: int = MAX_PER_MIN, available: Iterable[str] | None = None) -> dict:
    """Замер: склейка без звука ±50 мс (если не снята ради плотности), звуков в минуту, звук не из пака."""
    have = None if available is None else set(available)
    audio = [layer for layer in layers if layer.get("type") == "audio"]
    peaks = []
    problems = []
    for layer in audio:
        ref = str((layer.get("props") or {}).get("asset_id") or "")
        if not ref.startswith(f"sfx:{PACK}/") or (have is not None and ref.split("/", 1)[1] not in have):
            problems.append(f"{layer.get('id')}: {ref} не из пака {PACK}")
        is_cut = "whoosh" in ref
        peaks.append(float(layer["start"]) + (CUT_LEAD if is_cut else 0.0))
    dropped_t = {round(float(e["t"]), 3) for e in dropped or [] if e.get("kind") == "transition"}
    missing = [t for t in cuts if 0 < float(t) < duration and round(float(t), 3) not in dropped_t
               and not any(abs(p - float(t)) <= CUT_TOLERANCE + 1e-6 for p in peaks)]
    if missing:
        problems.append(f"склейки без звука ±50 мс: {[round(float(t), 2) for t in missing]}")
    starts = sorted(float(layer["start"]) for layer in audio)
    worst = 0
    j = 0
    for i, x in enumerate(starts):
        while x - starts[j] >= 60.0:
            j += 1
        worst = max(worst, i - j + 1)
    if worst > max_per_min:
        problems.append(f"{worst} звуков за минуту > {max_per_min}")
    return {"ok": not problems, "problems": problems, "sounds": len(audio), "max_per_min": worst,
            "uncovered_cuts": len(missing)}
