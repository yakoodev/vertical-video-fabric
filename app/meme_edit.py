"""Мем-эдит игры: из одной записи, трека музыки и библиотеки видеомемов — быстрый эдит, как реф владельца
(D:/vvf-offload/memes/ref_37872): геймплей рубится по долям музыки, на каждом моменте (нок, килл, смерть, реакция) —
врезка видеомема во весь кадр со своим звуком, удар камерой и вспышка на попадании, синий негатив на главном моменте,
2–4 мультяшных жёлтых слова («мульт-жёлтый»), музыка подложкой, смешные фразы в микрофон — с субтитрами.

    plan_meme_edit(store, source_id, aspect="9:16") -> файл клипа (vvf.clip/1: куски + render)
    build_meme_edit(store, source_id) -> два клипа (9:16 и 16:9) из одного монтажа, рендер заданиями

Монтаж собирается из того, что уже есть в сервисе: куски и вырезы (``render.cuts``), вставки видеомемов
(``render.inserts``, как ``place_video_meme``: full, звук клипа выключен, мем вровень с голосом), эффекты кадра
(``fx`` flash_frame / negate), камера (наезд + тряска), текстовые слои, музыка (``set_music``), шумодав микрофона
(``render.audio.fx`` denoise), мат — ``censor_profanity`` (середина слова, звёздочки). Неодобренные мемы не берутся.

Раскладка по времени — app/meme_edit_plan.py, события — app/meme_edit_events.py, стиль слов — app/meme_edit_text.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import meme_edit_events as EV
from app import meme_edit_plan as MP
from app.meme_edit_text import word_layer

_log = logging.getLogger(__name__)

ASPECTS = ("9:16", "16:9")
TARGET_MIN_SEC, TARGET_MAX_SEC = 15.0, 90.0
DEFAULT_TARGET_SEC = 40.0
NEGATE_COLOR = "#6fa8ff"  # синий «запретная зона», как в рефе
PUNCH_ZOOM = 1.22
PUNCH_SHAKE = 0.5
MAX_FLASHES = 3
MAX_PUNCHES = 12
BIG_SFX = "sfx:freesound/bass-drop.ogg"
MUSIC_PROBE_SEC = 70.0
TITLES = {"9:16": "Мем-эдит · вертикаль", "16:9": "Мем-эдит · горизонталь"}


# --------------------------------------------------------------------------- #
# Что берём из сервиса
# --------------------------------------------------------------------------- #


def meme_library(store: Any) -> list[dict]:
    """Одобренные видеомемы с файлом (только их можно ставить), в виде для app/meme_edit_plan."""
    from app import video_memes as VM

    out = []
    for meme in VM.list_memes(store, approved=True):
        if not meme.get("file_path") or not Path(meme["file_path"]).is_file() or not meme.get("duration_sec"):
            continue
        out.append({**meme, "meme_id": int(meme["id"])})
    return out


def mic_track(source: dict) -> int | None:
    """Дорожка микрофона: по подписям дорожек (app/source_audio.mic_track), заданная речь (speech_tracks), иначе у
    записи ровно из двух дорожек — вторая (NVIDIA/OBS: 0 — игра, 1 — микрофон)."""
    from app.source_audio import audio_streams, mic_track as titled_mic, normalize_tracks

    found = titled_mic(source)
    if found is not None:
        return found
    speech = normalize_tracks(source.get("speech_tracks"))
    if speech:
        return speech[0]
    streams = audio_streams(source)
    return 1 if len(streams) == 2 else None


def _music_file(store: Any, music_track_id: Any, music_asset_id: Any) -> tuple[dict | None, list[str]]:
    """Трек фона: ``music_track_id`` (list_music), файл библиотеки ``music_asset_id`` (звук) или сам — трек с темпом
    ближе к 128 BPM. Ответ — {kind: track|asset, id, path, label, start_hint} или None."""
    notes: list[str] = []
    if music_asset_id:
        asset = store.get_montage_asset(int(music_asset_id))
        if asset.get("kind") != "audio":
            raise ValueError(f"файл {music_asset_id} — не звук (kind {asset.get('kind')})")
        return {"kind": "asset", "id": int(asset["id"]), "path": asset["file_path"], "label": asset.get("label"),
                "start_hint": 0.0, "length": float(asset.get("duration_sec") or 0)}, notes
    tracks = store.list_audio_tracks()
    if music_track_id:
        track = store.get_audio_track(int(music_track_id))
    elif tracks:
        from app.music_energy import ensure_energy

        tracks = ensure_energy(store, tracks)
        track = min(tracks, key=lambda t: abs(float(t.get("bpm") or 60) - 128.0))
        notes.append(f"музыка не задана — взял «{track.get('label')}» (темп ближе к 128 BPM); своя — music_track_id")
    else:
        notes.append("музыки в библиотеке нет — эдит без подложки, склейки по сетке 120 BPM (add_music_from_url)")
        return None, notes
    hint = 0.0
    for key in ("drop_sec", "energy_start_sec"):
        value = track.get(key)
        if value is not None and float(value) < float(track.get("duration_sec") or 0) - 30:
            hint = float(value)
            break
    return {"kind": "track", "id": int(track["id"]), "path": track["file_path"], "label": track.get("label"),
            "start_hint": hint, "length": float(track.get("duration_sec") or 0)}, notes


def music_grid(path: str | Path, start_hint: float = 0.0) -> dict | None:
    """Сетка долей трека с ``start_hint``: период и старт с сильной доли (секунды трека) — склейки эдита в долю."""
    import subprocess

    from app.audio_map import RATE, _pcm_f32, beat_grid
    from app.settings import settings

    out = Path(settings.tmp_dir) / f"memeedit-music-{uuid4().hex}.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start_hint):.3f}",
                        "-t", f"{MUSIC_PROBE_SEC:.1f}", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE),
                        str(out)], capture_output=True, timeout=180, check=False)
        if not out.is_file():
            return None
        grid = beat_grid(_pcm_f32(out))
    finally:
        out.unlink(missing_ok=True)
    if not grid:
        return None
    return {**grid, "start_sec": round(max(0.0, start_hint) + float(grid["first_downbeat"]), 3)}


def _base_render(store: Any) -> dict:
    from app.clip_settings import normalize_render_settings
    from app.render_presets import effective_preset

    preset = effective_preset(store, None)
    return normalize_render_settings({"preset_id": preset["id"]} if preset else {})


# --------------------------------------------------------------------------- #
# Монтаж
# --------------------------------------------------------------------------- #


def _clamp_target(value: Any) -> float:
    try:
        target = float(value)
    except (TypeError, ValueError):
        target = DEFAULT_TARGET_SEC
    return min(TARGET_MAX_SEC, max(TARGET_MIN_SEC, target))


def prepare(store: Any, source_id: int, *, music_track_id: Any = None, music_asset_id: Any = None,
            target_sec: Any = DEFAULT_TARGET_SEC, events: Any = None, seed: int = 0, game: str = "apex",
            words: int = 3, library: list[dict] | None = None, grid: dict | None = None) -> dict:
    """Всё, что общее у вертикали и горизонтали: события, музыка, окна, куски и вырезы."""
    source = store.get_source(int(source_id))
    duration = float(source.get("duration_sec") or 0)
    if duration < 5:
        raise ValueError("запись короче 5 с — эдит не из чего собрать")
    mic = mic_track(source)
    notes: list[str] = []
    if events is not None:
        found, origin = EV.normalize_events(events, duration), "given"
    else:
        found, origin = EV.detect_events(source, game=game, mic=mic)
    if origin == "audio":
        notes.append("события найдены по звуку (app.game_events нет в сборке): нок/килл — догадка по пику перестрелки")
    music, music_notes = _music_file(store, music_track_id, music_asset_id)
    notes += music_notes
    if grid is None and music is not None:
        grid = music_grid(music["path"], music["start_hint"])
        if grid is None:
            notes.append(f"у трека «{music['label']}» не слышно пульса — склейки по сетке 120 BPM")
    period = float(grid["period"]) if grid else MP.DEFAULT_PERIOD
    library = meme_library(store) if library is None else library
    if not library:
        notes.append("одобренных видеомемов нет — эдит без врезок (одобрить: «Общие файлы → Видеомемы»)")
    timeline = MP.plan_timeline(found, duration=duration, period=period, target_sec=_clamp_target(target_sec),
                                library=library, seed=int(seed or 0), words=max(0, min(4, int(words))))
    if not timeline.windows:
        raise ValueError("; ".join(timeline.warnings) or "эдит не собрался")
    pieces, cuts = MP.to_pieces(timeline, duration)
    return {"source": source, "duration": duration, "mic": mic, "events": found, "events_origin": origin,
            "music": music, "grid": grid, "period": period, "timeline": timeline, "pieces": pieces, "cuts": cuts,
            "notes": notes + timeline.warnings}


def _inserts(timeline: MP.Timeline) -> list[dict]:
    out = []
    for w in timeline.windows:
        if w.role != "meme" or not w.meme:
            continue
        item = {"asset_id": int(w.meme["asset_id"]), "at": round(w.clip_start, 2), "duration": w.insert_sec,
                "mode": "full", "duck": True, "duck_volume": 0.0, "volume": 1.0,
                "reason": f"видеомем «{w.meme.get('title') or w.meme['meme_id']}» на {w.event['kind'] if w.event else 'момент'}"}
        if w.meme.get("has_audio", True):
            item["level"] = "speech"  # реакция говорит вместо клипа — вровень с голосом
        out.append(item)
    return out


def _hits(timeline: MP.Timeline) -> list[tuple[MP.Window, float]]:
    """Моменты-попадания: окно и секунда события в шкале clip."""
    out = []
    for w in timeline.windows:
        if w.role in ("hit", "voice", "chop") and w.event and w.start <= float(w.event["t"]) <= w.end:
            out.append((w, round(w.clip_start + float(w.event["t"]) - w.start, 3)))
    return out


def _camera(timeline: MP.Timeline) -> list[dict]:
    """Наезд-удар с тряской на каждом попадании (нок, килл, сильный выстрел в рубке)."""
    keys: list[dict] = []
    last = -10.0
    punches = [(w, t) for w, t in _hits(timeline)
               if w.role == "hit" or (w.event and float(w.event.get("score", 0)) >= 0.6)]
    for w, t in punches[:MAX_PUNCHES]:
        end = w.clip_start + w.length
        if t - last < 0.8 or t + 0.3 > end:
            continue
        back = min(end - 0.02, t + 0.45)
        keys += [{"t": round(max(0.0, t - 0.02), 3), "zoom": 1.0, "ease": "outCubic"},
                 {"t": round(t + 0.07, 3), "zoom": PUNCH_ZOOM if not w.big else PUNCH_ZOOM + 0.08,
                  "shake": PUNCH_SHAKE, "ease": "inOutCubic"},
                 {"t": round(back, 3), "zoom": 1.0, "shake": 0.0}]
        last = back
    return keys


def _fx_layers(timeline: MP.Timeline) -> list[dict]:
    """Вспышка на попаданиях (до трёх), синий негатив и удар басом на главном моменте."""
    layers: list[dict] = []
    flashes = 0
    for w, t in _hits(timeline):
        if w.role != "hit":
            continue
        end = w.clip_start + w.length
        if w.big:
            neg_end = min(end, t + 1.6)
            if neg_end - t >= 0.3:
                layers.append({"type": "fx", "name": "Негатив на главном моменте", "start": t, "end": round(neg_end, 3),
                               "track": 1, "props": {"kind": "negate", "intensity": 1, "color": NEGATE_COLOR},
                               "in": {"type": "fade", "duration": 0.06}, "out": {"type": "fade", "duration": 0.12}})
            layers.append({"type": "audio", "name": "Удар басом", "start": t, "end": round(t + 1.5, 3),
                           "props": {"asset_id": BIG_SFX, "volume": 0.5, "role": "sfx"}})
        elif flashes < MAX_FLASHES and w.event and w.event["kind"] in MP.BIG_KINDS and t + 0.22 <= end:
            flashes += 1
            layers.append({"type": "fx", "name": "Вспышка на попадании", "start": t, "end": round(t + 0.2, 3),
                           "track": 1, "props": {"kind": "flash_frame", "intensity": 0.7, "color": "#ffffff"},
                           "in": None, "out": {"type": "fade", "duration": 0.1}})
    return layers


def _word_layers(timeline: MP.Timeline, aspect: str, seed: int) -> list[dict]:
    out = []
    n = 0
    for w in timeline.windows:
        if not w.word or not w.event:
            continue
        rel = max(0.0, float(w.event["t"]) - w.start) + 0.04
        final_start = w.final_start + rel
        if final_start < MP.NO_TEXT_BEFORE_SEC:
            continue
        length = min(1.3, w.length - rel)
        if length < 0.5:
            continue
        start = round(w.clip_start + rel, 3)
        out.append(word_layer(w.word, start, round(start + length, 3), aspect, index=n + seed))
        n += 1
    return out


def spec_for(prepared: dict, aspect: str, base_render: dict, *, subtitles: bool = True, seed: int = 0) -> dict:
    """Файл клипа (vvf.clip/1) одного формата из общего монтажа ``prepare``."""
    from app.formats import normalize_format

    aspect = normalize_format(aspect)
    timeline: MP.Timeline = prepared["timeline"]
    clip_total = round(sum(p["end_sec"] - p["start_sec"] for p in prepared["pieces"]), 3)
    render = dict(base_render)
    render.update({
        "format": aspect, "layers_format": aspect,
        "transition": {"type": "cut"},
        "cuts": prepared["cuts"],
        "inserts": _inserts(timeline),
        "layers": _fx_layers(timeline) + _word_layers(timeline, aspect, seed),
        "camera": _camera(timeline),
        "end_hold_sec": 0.0,
        "subs_on": bool(subtitles and prepared["mic"] is not None),
    })
    music = prepared["music"]
    start = float((prepared["grid"] or {}).get("start_sec") or (music or {}).get("start_hint") or 0.0)
    if music and music["kind"] == "track":
        render.update({"music_on": True, "track_id": music["id"], "music_start_sec": round(start, 2)})
    elif music:
        render["layers"].append({"type": "audio", "name": "Музыка эдита", "start": 0.0, "end": clip_total,
                                 "props": {"asset_id": music["id"], "mode": "track", "role": "music", "volume": 0.8,
                                           "offset": round(start, 3), "fadeOut": 0.4}})
    if prepared["mic"] is not None:
        render["audio"] = {"fx": [{"start": 0.0, "end": clip_total, "fx": "denoise", "track": prepared["mic"],
                                   "amount": 0.5}]}
    focus = [{"t": 0.0, "x": 0.5}] if aspect != "16:9" else None  # прицел Apex — по центру кадра
    pieces = [{**p, **({"focus": focus} if focus else {})} for p in prepared["pieces"]]
    notes = "Мем-эдит (app/meme_edit.py): " + "; ".join(
        f"{w['role']} {w['source'][0]:.1f}–{w['source'][1]:.1f}" + (f" ({w['event']['kind']})" if w.get("event") else "")
        + (f" → мем #{w['meme']['meme_id']}" if w.get("meme") else "")
        for w in MP.summary(timeline))
    return {"schema": "vvf.clip/1", "title": TITLES.get(aspect, f"Мем-эдит {aspect}"),
            "description": "Мем-эдит: геймплей под музыку, врезки видеомемов на моментах.",
            "notes": notes[:3900], "pieces": pieces, "render": render}


def plan_meme_edit(store: Any, source_id: int, *, music_asset_id: Any = None, music_track_id: Any = None,
                   target_sec: Any = DEFAULT_TARGET_SEC, aspect: str = "9:16", events: Any = None, seed: int = 0,
                   game: str = "apex", words: int = 3, subtitles: bool = True) -> dict:
    """Файл клипа мем-эдита одного формата (без записи в базу): куски, вырезы, вставки мемов, эффекты, слова,
    музыка. Проверить — ``app.clip_spec.validate_spec`` на клипе из этих кусков."""
    prepared = prepare(store, source_id, music_track_id=music_track_id, music_asset_id=music_asset_id,
                       target_sec=target_sec, events=events, seed=seed, game=game, words=words)
    spec = spec_for(prepared, aspect, _base_render(store), subtitles=subtitles, seed=seed)
    spec["meme_edit"] = report(prepared)
    return spec


def report(prepared: dict) -> dict:
    timeline: MP.Timeline = prepared["timeline"]
    music = prepared["music"]
    return {
        "duration": timeline.total, "period": round(prepared["period"], 4),
        "bpm": (prepared["grid"] or {}).get("bpm"), "events": len(prepared["events"]),
        "events_origin": prepared["events_origin"], "mic_track": prepared["mic"],
        "music": {k: music[k] for k in ("kind", "id", "label")} if music else None,
        "music_start_sec": (prepared["grid"] or {}).get("start_sec"),
        "memes": [w.meme["meme_id"] for w in timeline.windows if w.meme],
        "words": [w.word for w in timeline.windows if w.word],
        "windows": MP.summary(timeline), "notes": prepared["notes"],
    }


# --------------------------------------------------------------------------- #
# Сборка: клипы, субтитры, мат, рендер
# --------------------------------------------------------------------------- #


def _ensure_source_audio(store: Any, source: dict, mic: int | None) -> str | None:
    """Игра + микрофон в звук ролика (микрофон авто-громче игры), речь — по микрофону; только если владелец ещё не
    выбрал дорожки у этой записи."""
    if mic is None:
        return None
    from app.source_audio import is_default_mix, set_source_audio

    if not is_default_mix(source.get("audio_mix") if source.get("audio_mix") is not None else source.get("audio_tracks")):
        return None
    try:
        set_source_audio(store, int(source["id"]), [{"track": 0, "gain_db": 0}, {"track": mic, "gain_db": "auto"}],
                         [mic])
    except ValueError as exc:
        return f"дорожки записи не выставлены: {exc}"
    return f"звук записи: игра + микрофон (дорожка {mic}, авто-громкость), речь — по микрофону"


def _voice_spans(timeline: MP.Timeline) -> list[tuple[float, float]]:
    return [(w.clip_start, w.clip_start + w.length) for w in timeline.windows if w.role in ("voice", "hit")]


def keep_voice_words(payload: dict, spans: list[tuple[float, float]]) -> dict:
    """Субтитры только на фразах-реакциях и моментах: обрывки слов в рубке (0.5–1 с) не показываем."""
    from app.ai.speakers import lines_from_words
    from app.clip_subtitles import normalize_lines

    words = [w for w in payload.get("words") or []
             if any(a - 0.05 <= (float(w["start"]) + float(w["end"])) / 2 <= b + 0.05 for a, b in spans)]
    lines = normalize_lines(lines_from_words(words), payload.get("duration")) if words else []
    return {**payload, "words": words, "lines": lines}


def _subtitles(store: Any, plan_ids: list[int], timeline: MP.Timeline) -> list[str]:
    """Распознать речь первого клипа (Whisper по микрофону), оставить фразы реакций, отдать второму, заглушить мат."""
    from app.censor import censor_plan
    from app.clip_subtitles import generate_for_plan
    from app.subtitles.registry import QUALITY_WHISPER_MODEL

    notes: list[str] = []
    try:
        payload = generate_for_plan(store, plan_ids[0], provider="whisper", model=QUALITY_WHISPER_MODEL)
    except Exception as exc:  # noqa: BLE001 - без субтитров эдит всё равно годен
        return [f"субтитры не распознаны: {str(exc)[:160]}"]
    payload = keep_voice_words(payload, _voice_spans(timeline))
    for plan_id in plan_ids:
        store.set_clip_plan_subtitles(plan_id, {k: v for k, v in payload.items() if k != "pieces"})
        try:
            out = censor_plan(store, plan_id, track="voice")
            if out.get("words"):
                notes.append(f"клип {plan_id}: мат заглушён серединой слова ({len(out['words'])})")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"клип {plan_id}: мат не проверен: {str(exc)[:120]}")
    notes.append(f"субтитры: {len(payload['words'])} слов в фразах-реакциях")
    return notes


def build_meme_edit(store: Any, source_id: int, *, music_asset_id: Any = None, music_track_id: Any = None,
                    target_sec: Any = DEFAULT_TARGET_SEC, aspects: Any = ASPECTS, events: Any = None, seed: int = 0,
                    game: str = "apex", words: int = 3, subtitles: bool = True, render: bool = True) -> dict:
    """Два клипа из одного монтажа (9:16 и 16:9 — разные клипы, как вертикаль и горизонталь одного момента):
    куски, файл клипа, субтитры фраз и мат, рендер заданиями (очередь тяжёлых — по одному)."""
    from app.clip_spec import apply_spec, export_spec
    from app.formats import normalize_format
    from app.manual_clips import create_manual_clip

    wanted = [normalize_format(a) for a in (aspects or ASPECTS)]
    prepared = prepare(store, source_id, music_track_id=music_track_id, music_asset_id=music_asset_id,
                       target_sec=target_sec, events=events, seed=seed, game=game, words=words)
    notes = list(prepared["notes"])
    audio_note = _ensure_source_audio(store, prepared["source"], prepared["mic"])
    if audio_note:
        notes.append(audio_note)
    base = _base_render(store)
    clips: list[dict] = []
    for aspect in wanted:
        spec = spec_for(prepared, aspect, base, subtitles=subtitles, seed=seed)
        plan = create_manual_clip(store, int(source_id), spec["title"], [
            {k: p[k] for k in ("start_sec", "end_sec", "title")} for p in spec["pieces"]], spec["description"])
        current = export_spec(store, int(plan["id"]))
        pieces = [{**piece, **({"focus": want["focus"]} if want.get("focus") else {})}
                  for piece, want in zip(current["pieces"], spec["pieces"])]
        apply_spec(store, int(plan["id"]), {**current, "notes": spec["notes"], "pieces": pieces,
                                            "render": spec["render"]})
        clips.append({"clip_id": int(plan["id"]), "format": aspect, "title": spec["title"]})
    if subtitles and prepared["mic"] is not None and clips:
        notes += _subtitles(store, [c["clip_id"] for c in clips], prepared["timeline"])
    if render:
        from app import render_jobs as RJ

        for clip in clips:
            job = RJ.start(store, clip["clip_id"], requested_by="meme_edit")
            clip["render_id"] = job["render_id"]
    return {"ok": True, "clips": clips, "time": "final", **report(prepared), "notes": notes}
