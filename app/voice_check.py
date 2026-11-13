"""Анализатор озвучки клипа (``check_voiceover``, группа ``voice`` у ``lint_clip``, отчёт QC рендера).

Эксперимент 12.11: агенты сдали три рекламы с битой озвучкой и не заметили — нечем было заметить. Что ловим:

* **услышано ≠ написано** — Whisper каждого слоя-голоса против текста озвучки (описание файла: add_voiceover и
  tts_preview пишут «Текст озвучки: «…»»): лишние слова в начале (зачитан style), нет хвоста фразы (обрезан слоем
  или текстом), доля ошибок по словам (WER) на фразу;
* **обрезанные слои** — ``end`` слоя раньше конца файла;
* **паузы** — тишина между фразами дольше 1.2 с (кроме заданной ``pause_after``; внутри одного файла — 2 с: там
  паузы ставит сам синтез) и голос, не покрывающий клип
  (последние 30 % — молчание);
* **громкость** — голос во время речи не меньше чем на 10–12 дБ громче музыки (стемы сведения, app/audio_mix_model.py);
  голос, учтённый как эффект или музыка (нет ``role: voice`` / не ``mode: track``);
* **субтитры** — слова субтитров, которых нет в голосе, и сдвиг страницы больше 0.4 с.

Каждая находка — что не так, где (``t`` — шкала clip) и точное исправление: ``fix`` текстом и ``fix_call``
``{tool, args}``. Ничего не меняет.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from typing import Any
from uuid import uuid4

from app import voice_asr as A
from app import voice_layers as VL

GAP_SEC = 1.2  # тишина внутри голоса длиннее — «дыра» (если её не заказали pause_after)
GAP_TOLERANCE_SEC = 0.35  # заказанная пауза: столько сверху ещё не дыра
GAP_IN_FILE_SEC = 2.0  # пауза внутри одного файла озвучки (её ставит сам синтез) — дыра, только если длиннее
TAIL_SHARE = 0.3  # последние 30 % клипа без голоса — голос не покрывает ролик
TAIL_MIN_SEC = 4.0  # …но финал с логотипом 3 с без голоса — нормально
HEAD_LATE_SEC = 3.0
LEAD_EXTRA_MIN = 2  # столько лишних слов в начале — зачитанное указание
WER_WARN = 0.4
WER_INFO = 0.25
VOICE_OVER_MUSIC_MIN_DB = 10.0
VOICE_OVER_MUSIC_GOOD_DB = 12.0
SPEAKING_BELOW_LU = 10.0
SYNC_SEC = 0.4
UNHEARD_SHARE = 0.15
PHRASE_GAP_SEC = 0.6
MAX_LIST = 6
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_SEC = 600.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finding(severity: str, code: str, message: str, fix: str = "", call: dict | None = None,
             t: float | None = None, layer: str | None = None, **extra: Any) -> dict:
    out: dict[str, Any] = {"check": "voice", "severity": severity, "code": code, "message": message}
    if fix:
        out["fix"] = fix
    if call:
        out["fix_call"] = call
    if t is not None:
        out["t"] = round(max(0.0, float(t)), 2)
    if layer:
        out["layer"] = layer
    out.update({k: v for k, v in extra.items() if v not in (None, [], {}, "")})
    return out


def _quote(words: list[str], limit: int = 8) -> str:
    text = " ".join(words[:limit])
    return text + ("…" if len(words) > limit else "")


def _pause_after(row: dict | None) -> float | None:
    match = re.search(r"пауза после ([\d.]+) с", str((row or {}).get("description") or ""))
    return float(match.group(1)) if match else None


def voice_entries(store: Any, layers: list[dict], duration: float) -> list[dict]:
    """Слои-голоса клипа: роль voice или файл озвучки (TTS, проба голоса) — с файлом, длиной и текстом."""
    out = []
    for layer in layers:
        if layer.get("type") != "audio" or layer.get("hidden"):
            continue
        props = layer.get("props") or {}
        row = VL.asset_row(store, props.get("asset_id"))
        role = VL.layer_role(layer, duration)
        if role != "voice" and not VL.looks_like_voice_asset(row):
            continue
        path = VL.asset_path(store, props.get("asset_id"))
        text, full = VL.intended_text(row)
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        out.append({"layer": layer, "id": str(layer.get("id")), "row": row, "path": path, "role": role,
                    "mode": props.get("mode") or "effect", "start": start, "end": end,
                    "offset": max(0.0, _num(props.get("offset"))), "file_sec": VL.asset_seconds(store, props.get("asset_id")),
                    "text": text, "text_full": full, "pause_after": _pause_after(row)})
    return sorted(out, key=lambda e: e["start"])


def _digest(store: Any, plan: dict, rs: dict, asr: bool, levels: bool) -> str:
    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    files = []
    for layer in rs.get("layers") or []:
        if layer.get("type") == "audio":
            path = VL.asset_path(store, (layer.get("props") or {}).get("asset_id"))
            if path is not None:
                try:
                    files.append(f"{path}|{path.stat().st_size}|{int(path.stat().st_mtime)}")
                except OSError:
                    pass
    raw = json.dumps([rs, payload.get("words"), plan.get("segments"), files, asr, levels], sort_keys=True, default=str)
    return f"{plan['id']}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def check_voiceover(store: Any, clip_plan_id: int, *, asr: bool = True, levels: bool = True) -> dict:
    """Проверить озвучку клипа. ``asr`` — слушать слои Whisper-ом (False — только по плану: обрезка, роль);
    ``levels`` — мерить голос против музыки по стемам. Ответ: ok, verdict, lines, findings, levels, coverage."""
    from app.agent_tools import plan_clock
    from app.canvas import is_canvas
    from app.clip_settings import normalize_render_settings

    plan = store.get_clip_plan(int(clip_plan_id))
    rs = normalize_render_settings(plan.get("render_settings") or {})
    key = _digest(store, plan, rs, asr, levels)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < _CACHE_SEC:
            return json.loads(json.dumps(hit[1]))
    clock = plan_clock(store, plan)
    duration = float(clock.clip_duration)
    try:
        canvas = is_canvas(store.get_source(int(plan["source_id"])))
    except KeyError:
        canvas = False
    layers = [l for l in rs.get("layers") or [] if not l.get("hidden")]
    entries = voice_entries(store, layers, duration)
    findings: list[dict] = []
    out: dict[str, Any] = {"ok": True, "clean": True, "clip_id": int(clip_plan_id), "canvas": canvas, "duration": round(duration, 2),
                           "voice_layers": len(entries), "time": "clip"}
    if not entries:
        out.update(verdict="голоса в клипе нет (слоёв audio с role voice или озвучкой нет)", lines=[], findings=[])
        if canvas and any(l.get("type") == "audio" for l in layers):
            out["note"] = "звуки есть, голоса нет: озвучка — add_voiceover(lines)"
        return _remember(key, out)

    findings += _plan_findings(store, entries, layers, duration)
    lines: list[dict] = []
    heard_clip: list[dict] = []
    for e in entries:
        line = {"layer": e["id"], "start": round(e["start"], 2), "end": round(e["end"], 2),
                "file_sec": round(e["file_sec"], 2) if e["file_sec"] else None,
                "role": e["role"], "text": e["text"][:200] + ("…" if len(e["text"]) > 200 else "")}
        if asr and e["path"] is not None:
            try:
                words = A.transcribe(e["path"])
            except Exception as exc:  # noqa: BLE001 - нет Whisper — проверка без слуха
                line["asr_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                words = None
            if words is not None:
                findings += _line_findings(e, words, line, heard_clip)
        lines.append(line)
    out["lines"] = lines
    if asr and heard_clip:
        heard_clip.sort(key=lambda w: w["start"])
        findings += _gap_findings(entries, heard_clip, duration, out)
        findings += _subtitle_findings(store, plan, clock, heard_clip, canvas, rs, out)
    if levels:
        try:
            findings += _level_findings(store, int(clip_plan_id), out)
        except Exception as exc:  # noqa: BLE001 - нет ffmpeg/файлов — без замера
            out["levels_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    order = {"warn": 0, "info": 1}
    findings.sort(key=lambda f: (order.get(f["severity"], 2), f.get("t", 0.0)))
    out["findings"] = findings
    warns = [f for f in findings if f["severity"] == "warn"]
    out["clean"] = not warns  # ok — инструмент отработал; clean — озвучка без ошибок
    out["counts"] = {"warn": len(warns), "info": len(findings) - len(warns)}
    out["verdict"] = verdict_text(findings)
    return _remember(key, out)


def verdict_text(findings: list[dict]) -> str:
    warns = [f for f in findings if f["severity"] == "warn"]
    if not warns:
        infos = len(findings)
        return "озвучка в порядке" + (f" (подсказок {infos})" if infos else "")
    parts = [w["message"] for w in warns[:3]]
    more = f" и ещё {len(warns) - 3}" if len(warns) > 3 else ""
    return "озвучка с ошибками: " + "; ".join(parts) + more


def _remember(key: str, out: dict) -> dict:
    with _CACHE_LOCK:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = (time.time(), json.loads(json.dumps(out, default=str)))
    return out


def _fix_layer(layer_id: str, patch: dict) -> dict:
    return {"tool": "update_layer", "args": {"id": layer_id, "patch": patch}}


def _plan_findings(store: Any, entries: list[dict], layers: list[dict], duration: float) -> list[dict]:
    """Без слуха: обрезанные слои, голос учтён как эффект/музыка, голос за концом клипа."""
    out = []
    cut = {c["id"]: c for c in VL.clipped(store, layers, duration)}
    for e in entries:
        c = cut.get(e["id"])
        e["clipped"] = bool(c)
        if c:
            patch = {"end": c["full_end"]}
            out.append(_finding("warn", "clipped", f"голос {e['id']} обрезан: {c['length']:.1f} из {c['file_sec']:.1f} с",
                                f"update_layer(id: \"{e['id']}\", patch: {{end: {c['full_end']:g}}}) — конец по "
                                "длине файла", _fix_layer(e["id"], patch), t=e["end"], layer=e["id"]))
        if e["role"] != "voice" or e["mode"] != "track":
            what = {"sfx": "эффект", "music": "музыка", "ambience": "фон", "video": "звук видео"}.get(e["role"], e["role"])
            how = f"учтён как {what}" if e["role"] != "voice" else "в режиме эффекта"
            out.append(_finding("warn", "role", f"голос {e['id']} {how}: сведение прижмёт его под другие звуки",
                                f"update_layer(id: \"{e['id']}\", patch: {{props: {{role: \"voice\", mode: \"track\"}}}})",
                                _fix_layer(e["id"], {"props": {"role": "voice", "mode": "track"}}), t=e["start"],
                                layer=e["id"]))
        if duration and e["start"] >= duration - 0.05:
            out.append(_finding("warn", "outside", f"голос {e['id']} начинается на {e['start']:.1f} с — после конца "
                                f"клипа ({duration:.1f} с), его не слышно",
                                "сдвинь слой (update_layer start) или удлини холст (trim_piece end_sec)",
                                t=e["start"], layer=e["id"]))
        elif duration and e["end"] > duration + 0.3 and e["file_sec"]:
            out.append(_finding("warn", "past_end", f"голос {e['id']} идёт до {e['end']:.1f} с, а клип {duration:.1f} с — "
                                "хвост фразы не прозвучит",
                                f"удлини клип до {e['end'] + 1:.1f} с (холст — trim_piece(index: 0, end_sec: …)) или "
                                "сократи фразу", t=duration, layer=e["id"]))
    return out


def _line_findings(e: dict, words: list[dict], line: dict, heard_clip: list[dict]) -> list[dict]:
    """Слух одного слоя: что прозвучало в окне слоя против текста озвучки."""
    out = []
    length = e["end"] - e["start"]
    lo, hi = e["offset"], e["offset"] + length
    played = [w for w in words if float(w["start"]) < hi - 0.05 and float(w["end"]) > lo]
    line["heard"] = " ".join(str(w["word"]).strip() for w in played)[:300]
    for w in played:
        a = e["start"] + float(w["start"]) - lo
        heard_clip.append({"word": w["word"], "start": round(a, 3),
                           "end": round(e["start"] + float(w["end"]) - lo, 3), "layer": e["id"]})
    if played:
        line["speech"] = [round(e["start"] + float(played[0]["start"]) - lo, 2),
                          round(e["start"] + float(played[-1]["end"]) - lo, 2)]
    if not words:
        out.append(_finding("warn", "silent", f"в голосе {e['id']} речь не распознана — тишина или не речь",
                            "переозвучь фразу: add_voiceover(lines, replace: true)", t=e["start"], layer=e["id"]))
        return out
    text = e["text"]
    if not text:
        line["text_check"] = "текст озвучки неизвестен (файл не из add_voiceover/tts_preview) — сверено только время"
        return out
    lead = A.lead_extra(text, played)
    if len(lead) >= LEAD_EXTRA_MIN:
        out.append(_finding("warn", "lead_extra", f"в начале {e['id']} слышно лишнее: «{_quote(lead)}» — зачитано "
                            "указание к речи (style) или чужой текст",
                            "переозвучь без style: add_voiceover(lines, replace: true) (style сервис больше не "
                            "передаёт) или tts_preview(text) и новый asset_id слою",
                            {"tool": "add_voiceover", "args": {"replace": True}}, t=e["start"], layer=e["id"],
                            heard_lead=" ".join(lead)))
    core = played[len(lead):] if len(lead) >= LEAD_EXTRA_MIN else played
    if e["text_full"]:
        gone = A.missing_tail(text, core)
        if gone:
            in_file = not A.missing_tail(text, words[len(lead):] if len(lead) >= LEAD_EXTRA_MIN else words)
            if in_file and e.get("clipped"):
                pass  # уже сказано находкой clipped (по плану): тот же слой, то же исправление
            elif in_file and e["file_sec"]:
                full_end = round(e["start"] + e["file_sec"] - e["offset"], 3)
                out.append(_finding("warn", "tail_cut", f"конец фразы {e['id']} не звучит: «{_quote(gone)}» — слой "
                                    "кончается раньше голоса",
                                    f"update_layer(id: \"{e['id']}\", patch: {{end: {full_end:g}}})",
                                    _fix_layer(e["id"], {"end": full_end}), t=e["end"], layer=e["id"]))
            else:
                out.append(_finding("warn", "tail_missing", f"в файле {e['id']} нет конца фразы: «{_quote(gone)}» — "
                                    "текст обрезан при синтезе", "переозвучь фразу целиком: add_voiceover(lines, "
                                    "replace: true); длинную — раздели на 2 фразы", t=e["end"], layer=e["id"]))
    wer, missing, extra = A.word_errors(text, core)
    line["wer"] = wer
    if missing:
        line["missing"] = missing[:MAX_LIST]
    if extra:
        line["extra"] = extra[:MAX_LIST]
    if e.get("clipped"):
        # Слой обрезан — недослышанный хвост это и есть обрезка (находка clipped): по полному файлу, без хвоста.
        wer, missing, _extra = A.word_errors(text, words[len(lead):] if len(lead) >= LEAD_EXTRA_MIN else words)
        line["wer_file"] = wer
    if wer >= WER_WARN:
        out.append(_finding("warn", "wer", f"{e['id']}: слышно не то, что написано — ошибок {wer:.0%} (нет «"
                            f"{_quote(missing, 5)}»)", "послушай (listen) и переозвучь: add_voiceover(lines, replace: "
                            "true); имена и бренды — напиши как читаются", t=e["start"], layer=e["id"], wer=wer))
    elif wer >= WER_INFO:
        out.append(_finding("info", "wer", f"{e['id']}: часть слов не расслышана ({wer:.0%}: «{_quote(missing, 5)}») — "
                            "бывает у брендов и чисел; проверь на слух", t=e["start"], layer=e["id"], wer=wer))
    return out


def _gap_findings(entries: list[dict], heard: list[dict], duration: float, out: dict) -> list[dict]:
    res = []
    allowed = {}
    for e in entries:
        if e["pause_after"] is not None:
            allowed[e["id"]] = e["pause_after"]
    gaps = []
    for prev, nxt in zip(heard, heard[1:]):
        gap = float(nxt["start"]) - float(prev["end"])
        # Внутри одного файла паузы ставит сам синтез (абзац, точка) — дырой считаем только заметно длиннее.
        if gap <= (GAP_SEC if prev["layer"] != nxt["layer"] else GAP_IN_FILE_SEC):
            continue
        ordered = allowed.get(prev["layer"])
        if prev["layer"] != nxt["layer"] and ordered is not None and gap <= ordered + GAP_TOLERANCE_SEC:
            continue
        gaps.append((float(prev["end"]), gap, prev["layer"]))
    for start, gap, layer in gaps[:MAX_LIST]:
        res.append(_finding("warn", "gap", f"тишина {gap:.1f} с в голосе на {start:.1f} с",
                            "сдвинь следующую фразу ближе (update_layer start) или пересобери: add_voiceover(lines, "
                            "replace: true) — фразы встанут подряд; паузу нарочно — pause_after у фразы",
                            t=start, layer=layer, gap_sec=round(gap, 2)))
    first, last = float(heard[0]["start"]), float(heard[-1]["end"])
    tail = duration - last
    out["coverage"] = {"speech_start": round(first, 2), "speech_end": round(last, 2), "clip": round(duration, 2),
                       "silent_tail_sec": round(max(0.0, tail), 2), "gaps": len(gaps)}
    if duration and tail > max(TAIL_SHARE * duration, TAIL_MIN_SEC):
        res.append(_finding("warn", "coverage", f"голос кончается на {last:.1f} с из {duration:.1f}: последние "
                            f"{tail:.1f} с ({tail / duration:.0%}) без голоса",
                            f"добавь фразы (add_voiceover) или укороти клип до ~{last + 3:.0f} с (холст — trim_piece "
                            "index 0, end_sec)", t=last))
    if first > HEAD_LATE_SEC:
        res.append(_finding("info", "late_start", f"голос начинается только на {first:.1f} с", "первая фраза раньше — "
                            "start у первой строки add_voiceover", t=0.0))
    return res


def _subtitle_findings(store: Any, plan: dict, clock: Any, heard: list[dict], canvas: bool, rs: dict,
                       out: dict) -> list[dict]:
    from app.speech_heard import align_words, heard_onset

    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    words = []
    for w in payload.get("words") or []:
        try:
            a, b = clock.plain_to_clip(float(w["start"])), clock.plain_to_clip(float(w["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if str(w.get("word") or "").strip():
            words.append({"word": str(w["word"]), "start": round(a, 3), "end": round(b, 3)})
    has_caps = any(l.get("type") == "captions" for l in rs.get("layers") or [] if not l.get("hidden"))
    if not words:
        if canvas:
            return [_finding("info", "no_subtitles", "субтитров нет — в рекламе их читают без звука",
                             "add_voiceover сам ставит субтитры по голосу; или set_subtitle_words")]
        return []
    if canvas and not has_caps and not rs.get("subs_on"):
        out["subtitles_hidden"] = True
    lo, hi = float(heard[0]["start"]) - 0.5, float(heard[-1]["end"]) + 0.5
    cands = words if canvas else [w for w in words if lo <= w["start"] <= hi]
    if not cands:
        return []
    found = align_words(cands, heard)
    solid = [i for i, w in enumerate(cands) if len(A.norm(w["word"])) >= 3]
    unheard = [cands[i]["word"] for i in solid if i not in found]
    res = []
    info = {"words": len(cands), "matched": len(found), "unheard": len(unheard)}
    if solid and len(unheard) / len(solid) > UNHEARD_SHARE:
        first = next(i for i in solid if i not in found)
        res.append(_finding("warn", "sub_unheard", f"в субтитрах {len(unheard)} слов, которых нет в голосе: "
                            f"«{_quote(unheard)}»", "субтитры по голосу: add_voiceover(lines, replace: true) или "
                            "set_subtitle_words по словам check_voiceover.lines[].heard", t=cands[first]["start"]))
    # Сдвиг: фразы субтитров (пауза > 0.6 с) — медиана «слово субтитров − начало услышанного».
    phrases: list[list[int]] = []
    for i, w in enumerate(cands):
        if phrases and w["start"] - cands[phrases[-1][-1]]["end"] <= PHRASE_GAP_SEC:
            phrases[-1].append(i)
        else:
            phrases.append([i])
    bad = []
    for idx in phrases:
        deltas = sorted(cands[i]["start"] - heard_onset(found[i]) for i in idx if i in found)
        if len(deltas) < 2:
            continue
        med = deltas[len(deltas) // 2]
        if abs(med) > SYNC_SEC:
            bad.append((cands[idx[0]]["start"], med, " ".join(cands[i]["word"] for i in idx[:5])))
    info["off_phrases"] = len(bad)
    out["subtitles"] = info
    for t, med, text in bad[:MAX_LIST]:
        side = "раньше" if med < 0 else "позже"
        res.append(_finding("warn", "sub_offset", f"субтитры «{text}…» {side} голоса на {abs(med):.1f} с",
                            "snap_subtitles_to_speech (встанут на распознанный голос) или add_voiceover(replace: "
                            "true)", {"tool": "snap_subtitles_to_speech", "args": {}}, t=t, offset_sec=round(med, 2)))
    return res


def _level_findings(store: Any, clip_plan_id: int, out: dict) -> list[dict]:
    """Голос против музыки во время речи (стемы, как их сведёт рендер)."""
    from app.audio_mix_analyze import FRAME_SEC, _frames
    from app.audio_mix_model import build_model, ebur128, power_mean
    from app.settings import settings

    work = settings.tmp_dir / f"voice-check-{uuid4().hex}"
    try:
        model = build_model(store, clip_plan_id, work)
        stems = model["stems"]
        if "voice" not in stems:
            out["levels"] = {"note": "в сведении нет стема голоса — слои-голоса учтены не как voice"}
            return []
        n = max(1, int(round(float(model["duration"]) / FRAME_SEC)))
        voice = _frames(ebur128(stems["voice"]), n)
        active = [v for v in voice if v > -50]
        if not active:
            return [_finding("warn", "voice_silent", "стем голоса пустой — голос не звучит в сведении",
                             "проверь файлы слоёв (listen) и громкость (volume, gain_db)")]
        level = power_mean(active)
        mask = [i for i, v in enumerate(voice) if v >= level - SPEAKING_BELOW_LU]
        info = {"voice_lufs": round(level, 1)}
        res = []
        if "music" in stems and mask:
            music = _frames(ebur128(stems["music"]), n)
            under = power_mean([music[i] for i in mask])
            diff = power_mean([voice[i] for i in mask]) - under
            info.update(music_under_voice_lufs=round(under, 1), voice_over_music_db=round(diff, 1))
            call = {"tool": "auto_mix", "args": {"music_under_voice_db": -18}}
            if diff < VOICE_OVER_MUSIC_MIN_DB:
                res.append(_finding("warn", "level", f"голос всего на {diff:.1f} дБ громче музыки (нужно ≥ "
                                    f"{VOICE_OVER_MUSIC_GOOD_DB:g})", "auto_mix(music_under_voice_db: -18) — музыка "
                                    "уйдёт под голос", call))
            elif diff < VOICE_OVER_MUSIC_GOOD_DB:
                res.append(_finding("info", "level", f"голос на {diff:.1f} дБ громче музыки — на грани (лучше ≥ "
                                    f"{VOICE_OVER_MUSIC_GOOD_DB:g})", "auto_mix(music_under_voice_db: -18)", call))
        out["levels"] = info
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def lint_findings(report: dict) -> list[dict]:
    """Находки для ``lint_clip`` (проверка ``voice``): те же, без служебных полей."""
    return [{k: v for k, v in f.items() if k != "code"} | {"code": f.get("code")} for f in report.get("findings") or []]


def qc_report(store: Any, clip_plan_id: int) -> dict | None:
    """Для отчёта QC рендера: None — голоса нет; иначе {ok, summary, warnings[«что → как»]}."""
    report = check_voiceover(store, clip_plan_id)
    if not report.get("voice_layers"):
        return None
    warns = [f for f in report.get("findings") or [] if f["severity"] == "warn"]
    return {"ok": not warns, "summary": report["verdict"],
            "warnings": [f["message"] + (f" → {f['fix']}" if f.get("fix") else "") for f in warns[:8]]}


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = ["check_voiceover", "lint_findings", "qc_report", "verdict_text", "voice_entries", "reset_cache"]
