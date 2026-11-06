"""Сведение звука клипа глазами (ушами) агента: громкость по стемам и вердикты (``audio_mix_report``).

Зачем: агент не слышит ролик. Музыка у «совы» (пилот 887) звучала громче голоса, а все замеры
говорили «−14 LUFS, всё хорошо» — громкость ролика в целом была в норме, плохим было соотношение.
Здесь звук клипа раскладывается на стемы — каждый ровно так, как его сведёт рендер:

* ``voice``  — слои-голос (озвучка объяснялки, реплики ИИ-видео: ``props.role`` voice или имя слоя);
* ``source`` — звук исходника по кускам (речь стрима; у холста его нет);
* ``music``  — фоновый трек клипа (громкость, старт, ключи, ручной ducking, sidechain) и слои-музыка;
* ``sfx``    — звуковые эффекты слоёв (вжухи, «динь», удары);
* ``video``  — звук видео-слоёв.

Каждый стем — wav (float, 48 кГц, стерео) на шкале clip; ffmpeg ``ebur128`` даёт momentary-громкость
по 0.1 с, short-term, integrated и true peak. Главная метрика — **голос против музыки только там,
где звучит речь** (кадры, где громкость голоса+исходника выше их уровня минус 12 LU): музыка должна
быть на 12–18 дБ тише речи. Звуки не должны перекрывать слова (громче голоса − 3 дБ под речью).
Итог ролика — −14 LUFS (TikTok/Reels/Shorts) и true peak ≤ −1 dBTP: по готовому рендеру, если он
свежий, иначе прогноз (сумма стемов + та же линейная поправка, что сделает финальный проход).

Модель — приближение: звук исходника читается по кускам (скорость/реверс учтены), без J/L, вырезов
тишины, эффектов отрезка и переходов — для соотношения голос/музыка этого хватает (заметки в ответе).
Громкость слоёв и музыки — те же функции, что у рендера (``motion_render.audio_layer_items``,
``render.music_volume_for``, ``render.music_stem_filter``): модель и рендер не расходятся.

Где что лежит. Здесь — отчёт и автосведение. Вынесено: настройка, дорожки и модель ``app/audio_mix_model.py``, анализ ``app/audio_mix_analyze.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import copy
import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.audio_mix_model import (  # noqa: F401
    ROLES, SILENT, VOICE_WORDS, MUSIC_WORDS, LONG_TRACK_SEC, LONG_TRACK_SHARE, MIX_DB_RANGE, LAYER_DB_RANGE,
    TARGET_RANGE, MAX_LAYER_GAINS, _num, _clamp, normalize_mix, mix_layer_gain_db, layer_role, _FRAME_RE, _I_RE,
    _LRA_RE, _TP_RE, _db, parse_ebur128, ebur128, db2p, p2db, power_mean, power_sum, _run, _out_args, _finish,
    silence_wav, mix_wavs, _atempo, _source_stem, _music_settings, _music_stem, _items_stem, _duck_filter,
    _apply_af, _ITEM_CACHE, _ITEM_LOCK, item_peak_lufs, _clip_duration, build_model,
)
from app.audio_mix_analyze import (  # noqa: F401
    STEMS, STEM_LABELS, FRAME_SEC, TARGET_LUFS, TRUE_PEAK_MAX_DB, LOUDNESS_TOLERANCE_LU, MUSIC_UNDER_MIN_DB,
    MUSIC_UNDER_GOOD_DB, MUSIC_UNDER_TARGET_DB, MUSIC_UNDER_MAX_DB, VIDEO_UNDER_MIN_DB, SFX_MASK_DB,
    SFX_OVER_VOICE_DB, SPEECH_BELOW_LEVEL_LU, SPEECH_FLOOR_LUFS, SPEECH_MERGE_SEC, SPEECH_CORE_LU, SILENCE_LUFS,
    SILENCE_MIN_SEC, MAX_RANGES, LIMITER_HEAVY_DB, _ranges, _frames, speech_mask, _percentile, _r, _spark,
    _timeline, analyze, silence_after_cuts, _source_speechless, FRAME_SHIFT, _frame_of, _worst_ranges, summary,
)

# --------------------------------------------------------------------------- #
# Отчёт для агента
# --------------------------------------------------------------------------- #

_REPORT_CACHE: dict[tuple, dict] = {}
_REPORT_LOCK = threading.Lock()


def _workdir() -> Path:
    from app.settings import settings

    return Path(settings.tmp_dir) / f"mix-{uuid4().hex[:12]}"


def _render_for(store: Any, plan_id: int, render_id: Any) -> tuple[dict | None, Path | None]:
    if render_id not in (None, "", 0):
        clip = store.get_clip(int(render_id))
        if int(clip.get("clip_plan_id") or 0) != int(plan_id):
            raise ValueError(f"рендер {render_id} — не этого клипа")
        path = Path(clip.get("output_path") or "")
        return clip, (path if path.is_file() else None)
    for clip in store.list_clips_for_plan(int(plan_id)):
        if clip.get("status") == "succeeded":
            path = Path(clip.get("output_path") or "")
            return clip, (path if clip.get("output_path") and path.is_file() else None)
    return None, None


def mix_target(mix: dict | None) -> float:
    from app.settings import settings

    value = (mix or {}).get("target_lufs")
    return float(value) if value is not None else float(getattr(settings, "render_loudnorm_target", TARGET_LUFS))


def mix_report(store: Any, plan_id: int, render_id: Any = None, use_render: bool = True,
               overrides: dict | None = None) -> dict:
    """``audio_mix_report``: стемы клипа (как сведёт рендер) + готовый рендер, если он есть и свежий."""
    plan = store.get_clip_plan(int(plan_id))
    clip, path = (None, None)
    stale = None
    if use_render and overrides is None:
        clip, path = _render_for(store, plan_id, render_id)
        if clip is not None and render_id in (None, "", 0) and \
                str(plan.get("updated_at") or "") > str(clip.get("finished_at") or clip.get("created_at") or ""):
            stale = "клип правили после рендера — итог ролика взят из прогноза, рендер старый"
            path = None
    # updated_at — с точностью до секунды: две правки за секунду дали бы старый отчёт.
    import hashlib
    import json

    digest = hashlib.sha1(json.dumps([plan.get("render_settings"), plan.get("segments"), overrides],
                                     sort_keys=True, default=str).encode("utf-8")).hexdigest()
    key = (int(plan_id), digest, int(clip["id"]) if clip and path else 0)
    with _REPORT_LOCK:
        if key in _REPORT_CACHE:
            return copy.deepcopy(_REPORT_CACHE[key])
    work = _workdir()
    try:
        model = build_model(store, plan_id, work, overrides)
        target = mix_target(model["mix"])
        report = analyze(model, target, path)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out = {"ok": True, "clip_id": int(plan_id), **report, "notes": model["notes"],
           "music": model.get("music"), "mix": model["mix"], "canvas": model["canvas"], "time": "clip"}
    if clip is not None and path is not None:
        out["render_id"] = int(clip["id"])
    if stale:
        out["render_stale"] = stale
    with _REPORT_LOCK:
        if len(_REPORT_CACHE) > 64:
            _REPORT_CACHE.clear()
        _REPORT_CACHE[key] = copy.deepcopy(out)
    return out


def lint_findings(report: dict) -> list[dict]:
    """Вердикты отчёта → находки ``lint_clip`` (проверка ``audio_mix``, только предупреждения)."""
    out = []
    for v in report.get("verdicts") or []:
        item = {"check": "audio_mix", "severity": v["severity"], "message": v["message"]}
        if v.get("fix"):
            item["fix"] = v["fix"]
        if v.get("t") is not None:
            item["t"] = float(v["t"])
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Автосведение
# --------------------------------------------------------------------------- #

AUTO_DUCK_DB = -10.0
AUTO_ATTACK_SEC = 0.12
AUTO_RELEASE_SEC = 0.3
AUTO_DUCK_MERGE_SEC = 0.5
# Ducking почти на весь ролик (речь без пауз) — это просто громкость: глубина уходит в громкость трека.
AUTO_DUCK_FULL_SHARE = 0.85
# Звук слоя (пик momentary) — на столько ниже речи там, где говорят; в паузе — чуть громче.
AUTO_SFX_UNDER_VOICE_DB = -6.0
AUTO_SFX_GAP_BONUS_DB = 4.0
# Звук тише цели больше чем на столько — поднимаем (не больше AUTO_SFX_MAX_BOOST_DB).
AUTO_SFX_QUIET_DB = 12.0
AUTO_SFX_MAX_BOOST_DB = 6.0
AUTO_VIDEO_UNDER_VOICE_DB = -12.0


def duck_spans(mask: list[bool], duration: float, depth_db: float, attack: float = AUTO_ATTACK_SEC,
               release: float = AUTO_RELEASE_SEC, limit: int | None = None) -> list[dict]:
    """Отрезки ducking музыки под речью (шкала clip): речь + attack до и release после, близкие
    слиты; больше ``limit`` — сливаются самые близкие."""
    from app.audio_edit import MAX_DUCKS

    limit = limit or MAX_DUCKS
    spans: list[list[float]] = []
    for a, b in _ranges(mask, merge=SPEECH_MERGE_SEC, shift=FRAME_SHIFT):
        a, b = max(0.0, a - attack), min(duration, b + release)
        if spans and a - spans[-1][1] < AUTO_DUCK_MERGE_SEC:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    while len(spans) > limit:
        k = min(range(len(spans) - 1), key=lambda i: spans[i + 1][0] - spans[i][1])
        spans[k][1] = spans[k + 1][1]
        del spans[k + 1]
    return [{"start": round(a, 3), "end": round(b, 3), "gain_db": round(depth_db, 1)} for a, b in spans if b - a >= 0.1]


def plan_auto_mix(model: dict, target_lufs: float = TARGET_LUFS, music_under_voice_db: float = -MUSIC_UNDER_TARGET_DB,
                  duck_db: float = AUTO_DUCK_DB, sfx_under_voice_db: float = AUTO_SFX_UNDER_VOICE_DB,
                  attack: float = AUTO_ATTACK_SEC, release: float = AUTO_RELEASE_SEC) -> dict:
    """Что выставить по замеру «нейтральной» модели (музыка без ducking и sidechain, слои без
    поправок): ``{mix, music_duck, changes, speech_lufs}``."""
    duration = float(model["duration"])
    n = max(1, int(round(duration / FRAME_SEC)))
    stems = model["stems"]
    frames = {kind: _frames(ebur128(path), n) for kind, path in stems.items() if kind in ("voice", "source", "music")}
    kinds = ("voice",) if model.get("speechless") else ("voice", "source")
    dialog_parts = [frames[k] for k in kinds if k in frames]
    dialog = power_sum(*dialog_parts) if dialog_parts else [SILENT] * n
    loud = [v for v in dialog if v > SPEECH_FLOOR_LUFS]
    level = p2db(sum(db2p(v) for v in loud) / len(loud)) if loud else SILENT
    mask = speech_mask(dialog, level) if loud else [False] * n
    speech_frames = [i for i, on in enumerate(mask) if on]
    speech = power_mean([dialog[i] for i in speech_frames]) if speech_frames else SILENT
    mix: dict = {k: v for k, v in (model.get("mix") or {}).items() if k in ("voice_fx", "music_fx", "groups")}
    mix["target_lufs"] = round(_clamp(float(target_lufs), *TARGET_RANGE), 1)
    changes: list[str] = []
    out: dict = {"mix": mix, "music_duck": None, "changes": changes, "speech_lufs": _r(speech)}
    under = -abs(float(music_under_voice_db))
    depth = -abs(float(duck_db))
    music = model.get("music")
    if music and "music" in frames:
        raw = power_mean([frames["music"][i] for i in speech_frames]) if speech_frames else SILENT
        current = float(music["volume_db"]) if music["volume_db"] > SILENT + 1 else 0.0
        if not speech_frames:
            changes.append("речи нет — громкость музыки оставлена, итог выровняет финальный проход")
        elif raw <= SILENT + 1:
            changes.append("музыка под речью не звучит (тишина в треке) — громкость не трогаю")
        else:
            spans = duck_spans(mask, duration, depth, attack, release)
            share = sum(b["end"] - b["start"] for b in spans) / duration if duration else 0.0
            eff_depth = depth if share < AUTO_DUCK_FULL_SHARE else 0.0
            if eff_depth == 0.0:
                spans = []
            want = current + (speech + under) - raw - eff_depth
            fixed = round(_clamp(want, *MIX_DB_RANGE), 1)
            mix["music_db"] = fixed
            mix["sidechain"] = False
            if spans:
                mix["duck_attack"], mix["duck_release"] = round(attack, 3), round(release, 3)
            out["music_duck"] = spans
            changes.append(f"музыка: {current:+.1f} → {fixed:+.1f} дБ к файлу трека — под речью на {-under:g} дБ "
                           "тише голоса" + (f", между фразами — на {-under + eff_depth:g}" if spans else "")
                           + (" (упёрлась в предел громкости)" if fixed != round(want, 1) else ""))
            if spans:
                changes.append(f"ducking музыки под речью: {len(spans)} отрезков по {depth:g} дБ (атака {attack:g} с, "
                               f"отпускание {release:g} с); автоматический sidechain выключен — ducking по фразам")
            else:
                changes.append("речь почти без пауз — ducking не нужен, музыка просто тише; sidechain выключен")
    gains: dict[str, float] = {}
    if speech > SILENT + 1:
        for item in model.get("items") or []:
            if item["role"] not in ("sfx", "video"):
                continue
            peak = item_peak_lufs(item)
            if peak <= SILENT + 1:
                continue
            i0, i1 = max(0, _frame_of(item["start"])), min(n, _frame_of(item["end"]) + 4)
            over = [i for i in range(i0, i1) if mask[i]]
            ref = power_mean([dialog[i] for i in over]) if over else speech
            if item["role"] == "video":
                if not over:
                    continue
                target = ref + AUTO_VIDEO_UNDER_VOICE_DB
            else:
                target = ref + sfx_under_voice_db if over else speech + sfx_under_voice_db + AUTO_SFX_GAP_BONUS_DB
            if peak > target + 0.5:
                gain = target - peak
            elif peak < target - AUTO_SFX_QUIET_DB:
                gain = min(AUTO_SFX_MAX_BOOST_DB, target - AUTO_SFX_QUIET_DB - peak)
            else:
                continue
            gain = round(_clamp(gain, *LAYER_DB_RANGE) * 2) / 2
            if abs(gain) >= 0.5 and item["layer_id"]:
                gains[item["layer_id"]] = gain
    if gains:
        mix["layer_gains"] = gains
        down = sum(1 for g in gains.values() if g < 0)
        changes.append(f"звуки слоёв выровнены под голос: {down} тише, {len(gains) - down} громче (пик под речью "
                       f"на {-sfx_under_voice_db:g} дБ ниже голоса)")
    changes.append(f"итог ролика: {mix['target_lufs']:g} LUFS, true peak ≤ −1.5 dBTP (финальный проход: замер, линейная "
                   "поправка, лимитер по true peak)")
    return out


def auto_mix(store: Any, plan_id: int, target_lufs: float | None = None,
             music_under_voice_db: float = -MUSIC_UNDER_TARGET_DB, duck_db: float = AUTO_DUCK_DB,
             sfx_under_voice_db: float = AUTO_SFX_UNDER_VOICE_DB, apply: bool = True, save: Any = None,
             compare: bool = True) -> dict:
    """Автосведение по замеру: громкость музыки и ducking под речью, звуки слоёв под голос, цель LUFS.

    ``save(render_settings)`` — как записать (инструмент — через историю правок); None — прямо в
    хранилище. ``apply`` false — только прогноз (dry run); ``compare`` false — без замера «до»
    (конвейеры: на один прогон модели меньше). Повторный вызов считает заново от
    «нейтрального» звука: прежние поправки auto_mix не накапливаются."""
    from datetime import datetime, timezone

    from app.audio_edit import normalize_audio
    from app.clip_settings import normalize_render_settings

    plan = store.get_clip_plan(int(plan_id))
    raw_rs = plan.get("render_settings") or {}
    audio = normalize_audio(normalize_render_settings(raw_rs).get("audio"))
    current_mix = normalize_mix(raw_rs.get("mix"))
    target = float(target_lufs) if target_lufs is not None else mix_target(current_mix)
    before = mix_report(store, plan_id, use_render=False) if compare else {}
    neutral_mix = {k: v for k, v in current_mix.items() if k in ("voice_fx", "music_fx", "groups")}
    neutral_mix["sidechain"] = False
    work = _workdir()
    try:
        model = build_model(store, plan_id, work, {"mix": neutral_mix, "audio": {**audio, "music_duck": []}})
        model["mix"] = current_mix
        planned = plan_auto_mix(model, target, music_under_voice_db, duck_db, sfx_under_voice_db)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    new_mix = dict(planned["mix"])
    new_mix["auto"] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "music_under_voice_db": -abs(float(music_under_voice_db)), "duck_db": -abs(float(duck_db)),
                       "sfx_under_voice_db": float(sfx_under_voice_db),
                       **({"speech_lufs": planned["speech_lufs"]} if planned["speech_lufs"] is not None else {})}
    new_mix = normalize_mix(new_mix)
    new_audio = dict(audio)
    if planned["music_duck"] is not None:
        new_audio["music_duck"] = planned["music_duck"]
    new_audio = normalize_audio(new_audio)
    after = mix_report(store, plan_id, use_render=False, overrides={"mix": new_mix, "audio": new_audio})
    got = (after.get("voice_vs_music") or {}).get("diff_db")
    want = abs(float(music_under_voice_db))
    if got is not None and new_mix.get("music_db") is not None and abs(got - want) > 1.0:
        # Рампы ducking и края фраз сдвигают итог на 1–3 дБ — одна поправка по прогнозу.
        new_mix["music_db"] = round(_clamp(float(new_mix["music_db"]) + (got - want), *MIX_DB_RANGE), 1)
        new_mix = normalize_mix(new_mix)
        after = mix_report(store, plan_id, use_render=False, overrides={"mix": new_mix, "audio": new_audio})

    def brief(rep: dict) -> dict:
        pred = rep.get("predicted") or {}
        return {"voice_vs_music_db": (rep.get("voice_vs_music") or {}).get("diff_db"),
                "sfx_masking": len((rep.get("voice_vs_sfx") or {}).get("masking_at") or []),
                "lufs": pred.get("integrated"), "true_peak": pred.get("true_peak"),
                "warnings": [v["message"] for v in rep.get("verdicts") or [] if v["severity"] == "warn"][:6]}

    out = {"ok": True, "clip_id": int(plan_id), "applied": bool(apply), "changes": planned["changes"],
           "mix": new_mix, "music_duck": new_audio.get("music_duck") or [],
           "before": brief(before), "after": brief(after), "after_summary": after.get("summary")}
    if apply:
        fresh = {**raw_rs, "mix": new_mix, "audio": new_audio}
        if save is not None:
            save(fresh)
        else:
            store.set_clip_plan_render_settings(int(plan_id), fresh)
        out["note"] = ("сведение записано (render_settings.mix и audio.music_duck) — перерендерь; after — прогноз "
                       "по той же модели, после рендера проверь audio_mix_report")
    else:
        out["note"] = "dry run: ничего не записано; apply=true — записать"
    return out
