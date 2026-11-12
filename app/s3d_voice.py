"""Голос 3D-серии: реплика — свой голос персонажа, деньги — машинерия ИИ-видео (``app/aivideo_gen.py``).

* **Голос реплики** (``voice_for``): персонаж из библиотеки со своим голосом — его голосом; рассказчик —
  голосом проекта (настройки серии), иначе голосом вселенной Витубики (Gemini «Aoede», бесплатно). Черновик
  (``options.dry_run`` задания) — тон вместо речи, 0 ₽.
* **Деньги** — этап ``series3d`` (``register_extension``): по запуску TTS на реплику (цель ``s3d_episode``, вид
  ``s3d_line``, слот — id реплики). Бесплатное (Gemini, тон) идёт сразу; платный голос (Polza ElevenLabs) — одна
  карточка владельцу с суммой, запуск только после «Разрешить», потолок карточки ``SERIES3D_EPISODE_CAP_RUB``.
  Готовая реплика тем же голосом и текстом второй раз не заказывается (версия ``aiv_refs``).
* **Дорожка** (``build_track``): вступление ``lead``, реплики подряд, пауза ``pause_after`` после каждой → один
  файл и точные границы реплик.
* **Слова** (``timed_words``): Whisper (тот же, что у объяснялок) даёт время, текст — из сценария; не распознал —
  слова по длине текста внутри границ реплики (видно в проверках серии).
* **Рот** (``mouth_envelope``): огибающая громкости 30 к/с только на репликах героини в кадре (node/vrm mouthEnv).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.settings import settings

STAGE = "series3d"
TARGET = "s3d_episode"
KIND = "s3d_line"
ENV_FPS = 30


def dry_voice() -> dict:
    return {"provider": "none", "model": "dry-run", "voice": "tone", "style": ""}


def _full_voice(voice: dict) -> dict:
    provider = str(voice.get("provider") or "gemini")
    if provider == "none":
        return dry_voice()
    if provider == "gemini":
        return {"provider": "gemini", "model": voice.get("model") or settings.gemini_tts_model,
                "voice": voice.get("voice") or settings.gemini_tts_voice, "style": voice.get("style") or ""}
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice, "style": ""}


def voice_for(lib: dict, cfg: dict, who: str, dry: bool = False) -> dict:
    """Чем озвучивается реплика ``who`` (slug персонажа или narrator)."""
    from app import vtubika as U

    if dry:
        return dry_voice()
    ch = (lib.get("characters") or {}).get(who) or {}
    if ch.get("voice", {}).get("provider"):
        return _full_voice(ch["voice"])
    if (cfg.get("narrator_voice") or {}).get("provider"):
        return _full_voice(cfg["narrator_voice"])
    return _full_voice(U.NARRATOR)


def line_text(plan: dict, line: dict) -> str:
    """Текст для TTS: ``say`` реплики, иначе текст; термины — по словарю произношения (субтитры этого не видят)."""
    from app import vtubika_say as SAY

    return SAY.apply(str(line.get("say") or line["text"]).strip(), SAY.pronounce_map(plan))


def describe(voice: dict) -> str:
    p = voice.get("provider")
    if p == "none":
        return "тон вместо речи (черновик)"
    if p == "gemini":
        return f"Gemini {voice.get('voice')}, бесплатно"
    return f"Polza {voice.get('voice')}, платно"


def _context(store: Any, episode_id: int) -> tuple[dict, dict, dict, bool]:
    from app import s3d_store as D

    ep = D.get_episode(store, episode_id)
    job = D.active_job(store, episode_id)
    dry = bool(((job or {}).get("options") or {}).get("dry_run"))
    return ep, D.library(store, int(ep["project_id"])), D.get_settings(store, int(ep["project_id"])), dry


def line_ref(store: Any, project_id: int, episode_id: int, line: dict, voice: dict, text: str) -> dict | None:
    """Готовая озвучка реплики тем же голосом и текстом (файл на месте) — иначе None."""
    from app import aivideo as V
    from app.aivideo_gen import _asset_path, _fresh, _line_key

    ref = V.selected_ref(store, int(project_id), TARGET, int(episode_id), KIND, str(line["id"]))
    if not _fresh(ref) or ref["prompt"] != _line_key(voice, text):
        return None
    return ref if _asset_path(store, ref) else None


class _Extension:
    stage = STAGE
    label = "голоса 3D-серии"
    target_types = (TARGET,)
    ref_kinds = {KIND: "реплика 3D-серии"}
    aspects: dict = {}
    card_prefix = "3D-серия"
    cap_hint = "короче реплики или бесплатный голос (Gemini) у части персонажей"

    @property
    def card_cap_rub(self) -> float:
        return float(settings.series3d_episode_cap_rub)

    def project_of(self, store: Any, target_type: str, target_id: int) -> int:
        from app import s3d_store as D

        return int(D.get_episode(store, target_id, full=False)["project_id"])

    def plan(self, store: Any, project_id: int, items: list, regenerate: bool) -> list[dict]:
        from app import aivideo_gen as G
        from app.aivideo_providers import get_tts

        out: list[dict] = []
        for raw in items or []:
            target_type, target_id, _kind, slot = G._parse_item(raw)
            if target_type != TARGET:
                raise ValueError(f"элемент {raw!r}: «{TARGET}:ID[:{KIND}:id реплики]»")
            ep, lib, cfg, dry = _context(store, target_id)
            if int(ep["project_id"]) != int(project_id):
                raise ValueError("серия из другого проекта")
            for line in ep["plan"].get("lines") or []:
                if slot and str(line["id"]) != slot:
                    continue
                voice = voice_for(lib, cfg, line["who"] if line["who"] != "narrator" else ep["plan"].get("narrator", "narrator"), dry)
                text = line_text(ep["plan"], line)
                if not regenerate and line_ref(store, project_id, target_id, line, voice, text) is not None:
                    continue
                tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style") or None)
                price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
                who = line["who"] if line["who"] != "narrator" else "рассказчик"
                out.append({"stage": STAGE, "kind": "tts", "target_type": TARGET, "target_id": int(target_id),
                            "ref_kind": KIND, "slot": str(line["id"]), "group": f"{TARGET}:{int(target_id)}",
                            "label": f"реплика {line['id']} ({who}): «{text[:50]}»", "provider": voice["provider"],
                            "model": voice["model"], "voice": voice["voice"],
                            **({"style": voice["style"]} if voice.get("style") else {}),
                            "text": text, "chars": len(text), "estimate_rub": round(len(text) * price, 2),
                            "price_note": describe(voice), "regenerate": bool(regenerate)})
        return out

    def card_line(self, store: Any, project_id: int, est: dict) -> str:
        from app import s3d_store as D

        items = est["items"]
        paid = [i for i in items if i["provider"] not in ("gemini", "none")]
        voices = sorted({f"{i['provider']} {i['voice']}" for i in paid})
        head = "3D-серия"
        groups = {i.get("group") for i in items if i.get("group")}
        if len(groups) == 1:
            try:
                ep = D.get_episode(store, int(next(iter(groups)).split(":")[1]), full=False)
                head = f"3D-серия «{ep['title']}»"
            except (KeyError, ValueError, IndexError):
                pass
        chars = sum(i["chars"] for i in paid)
        return (f"{head}: платные голоса реплик ({', '.join(voices)}) — {len(paid)} реплик, {chars} симв. ≈ "
                f"{est['total_rub']:.2f} ₽. Не хотите платить: откажите, поставьте персонажу голос Gemini "
                "(бесплатно) и нажмите «Рендер» заново.")


_REGISTERED = False


def register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from app import aivideo_gen as G

    G.register_extension(_Extension())
    _REGISTERED = True


def request(store: Any, episode_id: int, requested_by: str = "agent") -> dict:
    """Озвучить недостающие реплики: бесплатное — сразу, платное — карточка владельцу (ждём «Разрешить»)."""
    from app import aivideo_gen as G
    from app import s3d_store as D

    ep = D.get_episode(store, episode_id, full=False)
    return G.request_stage(store, int(ep["project_id"]), STAGE, None, [f"{TARGET}:{int(episode_id)}"],
                           requested_by=requested_by, note=f"3D-серия «{ep['title']}»")


BAD = ("failed", "blocked_policy", "uncertain", "cancelled")


def readiness(store: Any, episode_id: int, dry: bool = False) -> dict:
    """Готов ли голос всех реплик: {ready, missing, failed, active, held, lines: [{id, path, voice}]}."""
    from app import s3d_store as D
    from app.aivideo_gen import _asset_path

    ep = D.get_episode(store, episode_id)
    pid = int(ep["project_id"])
    lib, cfg = D.library(store, pid), D.get_settings(store, pid)
    out: dict[str, Any] = {"ready": True, "missing": [], "failed": [], "active": [], "held": [], "lines": []}
    narrator = ep["plan"].get("narrator", "narrator")
    for line in ep["plan"].get("lines") or []:
        voice = voice_for(lib, cfg, line["who"] if line["who"] != "narrator" else narrator, dry)
        text = line_text(ep["plan"], line)
        ref = line_ref(store, pid, int(episode_id), line, voice, text)
        if ref is not None:
            out["lines"].append({"id": line["id"], "path": str(_asset_path(store, ref)), "voice": voice,
                                 "asset_id": int(ref["asset_id"])})
            continue
        out["ready"] = False
        out["missing"].append(line["id"])
        row = store.db.query_one("SELECT status, error FROM aiv_gen_jobs WHERE project_id = ? AND target_type = ? AND "
                                 "target_id = ? AND ref_kind = ? AND slot = ? ORDER BY id DESC LIMIT 1",
                                 (pid, TARGET, int(episode_id), KIND, str(line["id"])))
        if row is None:
            continue
        if row["status"] in BAD:
            out["failed"].append(f"{line['id']}: {row['status']} — {(row['error'] or '')[:160]}")
        elif row["status"] == "held":
            out["held"].append(f"{line['id']}: {(row['error'] or 'держится')[:160]}")
        elif row["status"] in ("approved", "submitting", "polling", "recovering", "awaiting"):
            out["active"].append(line["id"])
    return out


# --------------------------------------------------------------------------- дорожка, слова, рот


def build_track(lines: list[dict], paths: dict[str, str], lead: float, out_path: Path) -> list[dict]:
    """Реплики подряд с паузами → один WAV (48 кГц моно). Возвращает границы: [{id, start, end}]."""
    import numpy as np

    from app import series3d_voice as VF

    parts = [np.zeros(int(round(lead * VF.SR)), dtype=np.float32)]
    spans, t = [], float(lead)
    for line in lines:
        pcm = VF.decode(paths[line["id"]])
        dur = len(pcm) / VF.SR
        spans.append({"id": line["id"], "start": round(t, 3), "end": round(t + dur, 3)})
        gap = float(line.get("pause_after") or 0.0)
        parts += [pcm, np.zeros(int(round(gap * VF.SR)), dtype=np.float32)]
        t += dur + gap
    VF._write_wav(out_path, np.concatenate(parts))
    return spans


def transcribe(path: Path) -> list[dict]:
    """Whisper (тот же, что у объяснялок и историй). Тесты подменяют."""
    from app.explainer_gen import transcribe_words

    return transcribe_words(path)


def _proportional(text: str, start: float, end: float) -> list[dict]:
    from app.explainer_gen_words import proportional_words

    span = max(0.3, end - start)
    words = proportional_words(text, span + 0.2)
    return [{**w, "start": round(start + max(0.0, w["start"] - 0.1), 3), "end": round(min(end, start + w["end"] - 0.1), 3)}
            for w in words]


def timed_words(lines: list[dict], spans: list[dict], track: Path, real: bool) -> tuple[list[dict], dict]:
    """Слова сценария со временем + отчёт {source: whisper|length, heard_share}. Слова каждой реплики — в её
    границах: Whisper не утащит слово в паузу соседней."""
    from app.explainer_gen_words import _norm as wnorm
    from app.explainer_gen_words import heard_share, script_words

    text = " ".join(ln["text"] for ln in lines)
    info: dict[str, Any] = {"source": "length", "heard_share": None}
    heard: list[dict] = []
    if real:
        try:
            heard = transcribe(track)
            info["heard_share"] = round(heard_share(text, heard), 3)
        except Exception as exc:  # noqa: BLE001 - нет Whisper: по длине текста, видно в проверках
            info["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    out: list[dict] = []
    if heard and (info["heard_share"] or 0) >= 0.6:
        info["source"] = "whisper"
        words = script_words(text, heard)
        pos = 0
        for line, span in zip(lines, spans):
            n = len([w for w in line["text"].split() if wnorm(w)])
            chunk = words[pos:pos + n]
            pos += n
            if len(chunk) < n:  # не сошлось по числу — эта реплика по длине текста
                chunk = _proportional(line["text"], span["start"], span["end"])
            for w in chunk:
                a = min(max(float(w["start"]), span["start"]), span["end"] - 0.05)
                b = min(max(float(w["end"]), a + 0.05), span["end"])
                out.append({"word": w["word"], "start": round(a, 3), "end": round(b, 3), "line": line["id"]})
        return out, info
    for line, span in zip(lines, spans):
        out += [{**w, "line": line["id"]} for w in _proportional(line["text"], span["start"], span["end"])]
    return out, info


def mouth_envelope(track: Path, windows: list[tuple[float, float]], total: float, fps: int = ENV_FPS) -> list[float]:
    """Громкость голоса по кадрам (0..1, по 95-му процентилю речи) — только внутри окон реплик героини."""
    import numpy as np

    from app import series3d_voice as VF

    pcm = VF.decode(track)
    n = int(math.ceil(total * fps)) + 1
    hop = VF.SR // fps
    rms = np.zeros(n, dtype=np.float32)
    for i in range(n):
        seg = pcm[i * hop:(i + 1) * hop]
        if len(seg):
            rms[i] = float(np.sqrt(np.mean(seg * seg)))
    ref = float(np.percentile(rms[rms > 0], 95)) if (rms > 0).any() else 1.0
    out = []
    for i in range(n):
        t = i / fps
        inside = any(a - 0.05 <= t <= b + 0.05 for a, b in windows)
        out.append(round(min(1.0, float(rms[i]) / max(ref, 1e-6)), 3) if inside else 0.0)
    return out


def speech_windows(words: list[dict], merge: float = 0.25) -> list[tuple[float, float]]:
    spans: list[list[float]] = []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        if spans and a - spans[-1][1] < merge:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def voice_summary(lines: list[dict]) -> list[dict]:
    """Кто чем говорил — для карточки серии."""
    seen: dict[str, dict] = {}
    for ln in lines:
        v = ln.get("voice") or {}
        key = json.dumps([v.get("provider"), v.get("voice")])
        seen.setdefault(key, {"provider": v.get("provider"), "voice": v.get("voice"), "label": describe(v), "lines": 0})
        seen[key]["lines"] += 1
    return list(seen.values())
