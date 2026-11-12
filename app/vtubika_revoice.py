"""Переозвучка одной фразы поверх готовой озвучки «Витубики»: платный голос заново читает только её, остальное —
прошлый дубль (и та же подача, и те же деньги не тратятся второй раз).

Когда: поменялось, как читать фразу (``say`` фразы, словарь произношения ``app.vtubika.PRONOUNCE``) или в плане у
фразы ``"revoice": true`` (прочитана неверно — ударение, оговорка). Фразы, у которых текст для TTS отличается
только знаком ударения (U+0301), без ``revoice`` остаются из прошлого дубля: там слово уже прочитано верно.

Как: прошлый полный дубль того же голоса (``narration_<key>.json`` с полем ``sentences``) → фразы на его времени
(Whisper, слова сценария) → разрез в тишине до и после фразы (``app.vtubika_audio.quiet_point``) → новая фраза без
своих краевых тишин, той же громкости и, если длина отличается не больше чем на 10 %, той же длины (``atempo``) — время
остальных фраз и всего ролика не меняется; паузы до и после — прежние, края — с наплывом. Стыки проверяются: разрез
не в тишине — ошибка, а не «заикание».

План может запретить: ``"voice": {"patch": false}`` — тогда, как раньше, вся озвучка заново.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

REVOICE_V = 1
STRESS = "́"
MAX_SHARE = 0.25          # переозвучивать не больше четверти фраз (иначе — дубль целиком)
TEMPO_FIT = 0.10          # подогнать длину новой фразы под старую, если разница не больше 10 %
MARGIN_IN, MARGIN_OUT = 0.02, 0.04
FADE_SEC = 0.01


class RevoiceError(RuntimeError):
    pass


def voice_key(text: str, voice: dict) -> str:
    """Ключ озвучки — как у ``tools/vtubika_story.narrate``."""
    return hashlib.sha1(json.dumps([text, voice], ensure_ascii=False).encode()).hexdigest()[:12]


def _plain(text: str) -> str:
    return str(text).replace(STRESS, "")


def needs(plan: dict, base: list[str], new: list[str]) -> list[int] | None:
    """Какие фразы переозвучить поверх дубля ``base`` (номера с 0); None — дубль не годится (другое число фраз)."""
    if len(base) != len(new):
        return None
    out = []
    for i, (a, b) in enumerate(zip(base, new)):
        flag = bool(plan["sentences"][i].get("revoice"))
        if flag or (a != b and _plain(a) != _plain(b)):
            out.append(i)
    return out


def find_base(plan: dict, work: Path, voice: dict, new: list[str]) -> tuple[Path, dict, list[int]] | None:
    """Полный дубль того же голоса в ``work`` с наименьшим числом фраз на переозвучку (не больше ``MAX_SHARE``)."""
    if (plan.get("voice") or {}).get("patch") is False:
        return None
    best = None
    for meta_path in sorted(work.glob("narration_*.json")):
        if meta_path.name.startswith("narration_paced_"):
            continue
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("patched_from") or meta.get("voice_spec") != voice:
            continue
        raw = meta_path.with_suffix(".wav")
        todo = needs(plan, list(meta.get("sentences") or []), new)
        if todo is None or not raw.is_file():
            continue
        if len(todo) > max(2, int(len(new) * MAX_SHARE)):
            continue
        if best is None or len(todo) < len(best[2]):
            best = (raw, meta, todo)
    return best


def patch_key(base_raw: Path, todo: list[int], new: list[str], voice: dict) -> str:
    data = [base_raw.stem, [[i, new[i]] for i in todo], voice, REVOICE_V]
    return hashlib.sha1(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:12]


def _speech_bounds(pcm: Any, a: int, b: int, level: float, sr: int) -> tuple[int, int]:
    """Первый и последний сэмпл речи на [a, b) по энергии (окна 5 мс выше тишины)."""
    from app import vtubika_audio as AUD

    db = AUD.frame_db(pcm[a:b], AUD.FRAME, sr)
    loud = [i for i, v in enumerate(db) if v > level + AUD.SILENT_DB]
    if not loud:
        raise RevoiceError("в куске фразы нет речи")
    n = int(AUD.FRAME * sr)
    return a + loud[0] * n, a + (loud[-1] + 1) * n


def _atempo(seg: Any, ratio: float, sr: int, tmp: Path) -> Any:
    from app import series3d_voice as VF

    src, dst = tmp.with_suffix(".in.wav"), tmp.with_suffix(".out.wav")
    VF._write_wav(src, seg)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter:a", f"atempo={ratio:.4f}", "-ar", str(sr),
                        str(dst)], check=True, capture_output=True, timeout=60)
        return VF.decode(dst)
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)


def splice(pcm: Any, spans: list[tuple[float, float]], lines: dict[int, Any], *, sr: int, work: Path) -> tuple[Any, list[dict]]:
    """Заменить фразы ``lines`` (номер → звук новой фразы) в ``pcm``; ``spans`` — время речи каждой фразы (с).
    → (новый звук, отчёт по фразам)."""
    import numpy as np

    from app import vtubika_audio as AUD

    level = AUD.speech_level(AUD.frame_db(pcm, AUD.FRAME, sr))
    ramp = np.linspace(0.0, 1.0, int(FADE_SEC * sr), dtype=np.float32)
    out, prev, info = [], 0, []
    for i in sorted(lines):
        s0, s1 = spans[i]
        a = 0.0 if i == 0 else AUD.quiet_point(pcm, max(spans[i - 1][1], s0 - 0.6), s0 + 0.12, sr=sr, level=level)
        b = len(pcm) / sr if i == len(spans) - 1 else AUD.quiet_point(pcm, s1 - 0.12, min(spans[i + 1][0], s1 + 0.6),
                                                                       sr=sr, level=level)
        joins = AUD.join_levels(pcm, [t for t in (a, b) if 0 < t < len(pcm) / sr], sr=sr)
        if not all(j["ok"] for j in joins):
            raise RevoiceError(f"фраза {i + 1}: нет тишины для стыка ({joins})")
        ia, ib = int(a * sr), int(b * sr)
        on, off = _speech_bounds(pcm, ia, ib, level, sr)
        line = np.asarray(lines[i], dtype=np.float32)
        l_level = AUD.speech_level(AUD.frame_db(line, AUD.FRAME, sr))
        lo, lf = _speech_bounds(line, 0, len(line), l_level, sr)
        seg = line[max(0, lo - int(MARGIN_IN * sr)): min(len(line), lf + int(MARGIN_OUT * sr))].copy()
        old_len = (off - on) / sr + MARGIN_IN + MARGIN_OUT
        ratio = len(seg) / sr / old_len
        if abs(ratio - 1.0) > 0.005 and abs(ratio - 1.0) <= TEMPO_FIT:
            seg = _atempo(seg, ratio, sr, work / f"revoice_tmp_{i}")
        # Громкость — как у прежнего прочтения фразы (по громкой речи), не больше ±6 дБ.
        gain_db = max(-6.0, min(6.0, AUD.speech_level(AUD.frame_db(pcm[on:off], AUD.FRAME, sr))
                                - AUD.speech_level(AUD.frame_db(seg, AUD.FRAME, sr))))
        seg = seg * np.float32(10 ** (gain_db / 20))
        seg[: len(ramp)] *= ramp
        seg[-len(ramp):] *= ramp[::-1]
        pre = max(0, on - int(MARGIN_IN * sr) - ia)
        post = max(0, ib - (off + int(MARGIN_OUT * sr)))
        head = np.asarray(pcm[prev:ia], dtype=np.float32).copy()
        if len(head) > len(ramp) and ia < len(pcm):
            head[-len(ramp):] *= ramp[::-1]
        out += [head, np.zeros(pre, dtype=np.float32), seg, np.zeros(post, dtype=np.float32)]
        prev = ib
        info.append({"n": i + 1, "cut": [round(a, 3), round(b, 3)], "old_sec": round(old_len, 3),
                     "new_sec": round(len(seg) / sr, 3), "tempo": round(ratio, 3) if abs(ratio - 1) <= TEMPO_FIT else 1.0,
                     "gain_db": round(gain_db, 1), "joins": joins})
    tail = np.asarray(pcm[prev:], dtype=np.float32).copy()
    if prev and len(tail) > len(ramp):
        tail[: len(ramp)] *= ramp
    out.append(tail)
    return np.concatenate(out), info


def sentence_spans(plan: dict, heard: list[dict]) -> list[tuple[float, float]]:
    """Время речи каждой фразы дубля по словам Whisper (слова сценария, термины словаря слиты обратно)."""
    from app import vtubika_plan as P
    from app import vtubika_say as SAY
    from app.explainer_gen_words import script_words

    words = script_words(P.caption_text(plan), SAY.heard_as_script(heard, SAY.pronounce_map(plan)))
    return [(float(t[0]["start"]), float(t[-1]["end"])) for t in P.token_times(plan, words)]


def revoice(plan: dict, work: Path, voice: dict, new: list[str], base: tuple[Path, dict, list[int]],
            heard_of: Callable[[Path], list[dict]], synth: Callable[[str, Path], float]) -> tuple[Path, dict]:
    """Дубль с переозвученными фразами → (путь .wav, meta). ``heard_of(raw)`` — слова Whisper дубля (из кэша или
    заново), ``synth(text, out_mp3)`` — платный синтез фразы, возвращает цену (₽). Кэш — по дублю и текстам фраз."""
    from app import series3d_voice as VF

    raw, meta, todo = base
    key = patch_key(raw, todo, new, voice)
    out = work / f"narration_{key}.wav"
    meta_path = out.with_suffix(".json")
    if out.is_file() and meta_path.is_file():
        return out, json.loads(meta_path.read_text("utf-8"))
    sentences = list(meta["sentences"])
    if not todo:
        info = {**meta, "patched_from": raw.stem.removeprefix("narration_"), "revoiced": [], "cost_rub": 0.0}
        return raw, info
    lines, cost, chars = {}, 0.0, 0
    for i in todo:
        line = work / f"revoice_{voice_key(new[i], voice)}.mp3"
        line_meta = line.with_suffix(".json")
        if not (line.is_file() and line_meta.is_file()):
            price = synth(new[i], line)
            line_meta.write_text(json.dumps({"text": new[i], "voice": voice, "cost_rub": price}, ensure_ascii=False), "utf-8")
        else:
            price = float(json.loads(line_meta.read_text("utf-8")).get("cost_rub") or 0.0)
        cost += price
        chars += len(new[i])
        lines[i] = VF.decode(line)
        sentences[i] = new[i]
    pcm = VF.decode(raw)
    spans = sentence_spans(plan, heard_of(raw))
    patched, report = splice(pcm, spans, lines, sr=VF.SR, work=work)
    VF._write_wav(out, patched)
    info = {"provider": meta.get("provider"), "voice": meta.get("voice"), "model": meta.get("model"),
            "voice_spec": voice, "sentences": sentences, "patched_from": raw.stem.removeprefix("narration_"),
            "revoiced": [i + 1 for i in todo], "chars": chars, "cost_rub": round(cost, 4), "splice": report,
            "base_cost_rub": meta.get("cost_rub")}
    meta_path.write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    return out, info
