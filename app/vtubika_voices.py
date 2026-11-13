"""Рот по СВОЕМУ голосу у историй «Витубики» с несколькими голосами (``voices_extra`` с полем ``who``).

Раньше рот героини (node/vrm, ``timeline.visemes``) считался по голосу рассказчика в окнах «говорит в эфир», а
реплики других голосов (``voices_extra``) звучали поверх паузы — героиня молчала на своих же словах, а модель
напарницы на мониторе открывала рот общим ритмом. Теперь:

* ``who: "hero"`` — реплики героини: своя дорожка (реплики на своих местах + рассказчик только в окнах, где
  героиня говорит его голосом — ``timeline.speech``), слова — Whisper по каждой реплике (текст — сценария),
  гласные и формы ARKit — тем же путём, что у серии 7 (``lipsync.attach_timeline``). Вне её окон рот закрыт: голос
  рассказчика ей рот не двигает.
* ``who: "<id модели на мониторе>"`` — реплики модели в эфире (``spec.avatar.visemes[id]``, node/vrm/src/avatar.ts):
  гласные по её голосу и словам; без дорожки — прежний ритм слогов окна ``av_talk``.

Модель самой героини на эфире (действие track/mirror) повторяет её лицо — рот у неё тот же.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

Words = list[dict]


def line_words(ln: dict, work: Path, transcribe: Callable[[Path], Words]) -> Words:
    """Слова реплики на времени ролика: Whisper по файлу реплики (кэш рядом) → слова текста реплики (сценарий)."""
    from app.explainer_gen_words import script_words

    path = Path(ln["path"])
    cache = path.with_suffix(".words.json")
    if cache.is_file():
        heard = json.loads(cache.read_text("utf-8"))
    else:
        heard = transcribe(path)
        cache.write_text(json.dumps(heard, ensure_ascii=False), "utf-8")
    text = str(ln.get("text") or "")
    words = script_words(text, heard) if text and heard else heard
    t0 = float(ln["t"])
    return [{**w, "start": round(float(w["start"]) + t0, 3), "end": round(float(w["end"]) + t0, 3)} for w in words]


def speaker_track(lines: list[dict], total: float, out: Path, base: tuple[Any, list[tuple[float, float]], float] | None = None) -> Path:
    """Дорожка одного говорящего на шкале ролика: его реплики на своих местах (и, если дан ``base`` — (голос
    рассказчика, окна, lead), — рассказчик только внутри окон). WAV ``series3d_voice.SR``."""
    import numpy as np

    from app import series3d_voice as VF

    n = int(round((total + 0.5) * VF.SR))
    pcm = np.zeros(n, dtype=np.float32)
    if base is not None:
        nar, windows, lead = base
        src = VF.decode(nar)
        for a, b in windows:
            i0, i1 = max(0, int((a - 0.05) * VF.SR)), min(n, int((b + 0.08) * VF.SR))
            j0 = i0 - int(lead * VF.SR)
            if j0 < 0 or i1 <= i0:
                continue
            seg = src[j0:j0 + (i1 - i0)]
            pcm[i0:i0 + len(seg)] += seg
    for ln in lines:
        src = VF.decode(ln["path"])
        i0 = int(round(float(ln["t"]) * VF.SR))
        seg = src[: max(0, n - i0)]
        pcm[i0:i0 + len(seg)] += seg
    VF._write_wav(out, pcm)
    return out


def windows_of(lines: list[dict], words: Words) -> list[tuple[float, float]]:
    """Окна речи по репликам: от первого до последнего слова каждой (без тишины по краям файла)."""
    out = []
    for ln in lines:
        mine = [w for w in words if float(ln["t"]) - 0.01 <= w["start"] <= float(ln["t"]) + float(ln["dur"]) + 0.01]
        if mine:
            out.append((round(mine[0]["start"], 3), round(mine[-1]["end"], 3)))
        else:
            out.append((round(float(ln["t"]), 3), round(float(ln["t"]) + float(ln["dur"]), 3)))
    return out


def attach(plan: dict, spec: dict, lines: list[dict], narration: Path, words: Words, total: float, lead: float,
           work: Path, transcribe: Callable[[Path], Words]) -> dict:
    """Рот героини и моделей на мониторе по их голосам (см. модуль). Возвращает отчёт: окна и доля гласных."""
    from app import lipsync

    tl = spec["timeline"]
    arkit = bool((plan.get("vrm") or {}).get("arkit"))
    highlight = list((plan.get("highlight") or {}).keys())
    report: dict[str, Any] = {}
    hero = [ln for ln in lines if ln.get("who") == "hero"]
    if not hero:
        # Как раньше (серия 7): рот по голосу рассказчика в окнах «говорит в эфир».
        lipsync.attach_timeline(tl, narration, words, total, offset=lead, highlight=highlight, arkit=arkit)
        return report
    talk = [tuple(w) for w in tl.get("speech") or []]
    nar_words = [w for w in words if any(a - 0.05 <= w["start"] <= b + 0.05 for a, b in talk)]
    hw = [w for ln in hero for w in line_words(ln, work, transcribe)]
    hero_win = windows_of(hero, hw)
    win = sorted([*talk, *hero_win])
    track = speaker_track(hero, total, work / "voice_hero.wav", (narration, talk, lead) if talk else None)
    all_words = sorted([*nar_words, *hw], key=lambda w: w["start"])
    tl["speech"] = [list(w) for w in win]
    ok = lipsync.attach_timeline(tl, track, all_words, total, windows=win, offset=0.0, highlight=highlight, arkit=arkit)
    report["hero"] = {"windows": win, "visemes": bool(ok), "words": len(all_words)}
    av = spec.get("avatar")
    ids = {m["id"] for m in (av or {}).get("models") or []}
    for who in sorted({str(ln.get("who")) for ln in lines} & ids):
        mine = [ln for ln in lines if ln.get("who") == who]
        ws = [w for ln in mine for w in line_words(ln, work, transcribe)]
        w_win = windows_of(mine, ws)
        vis = lipsync.from_track(speaker_track(mine, total, work / f"voice_{who}.wav"), ws, total, windows=w_win)
        if vis:
            av.setdefault("visemes", {})[who] = vis
            # Окна речи модели (улыбка в речи) — по её настоящим словам, а не по оценке из плана.
            av["talk"] = [w for w in av.get("talk") or [] if w.get("who") != who] + \
                [{"who": who, "t0": a, "t1": b, "face": "smile"} for a, b in w_win]
        report[who] = {"windows": w_win, "visemes": bool(vis), "words": len(ws)}
    return report
