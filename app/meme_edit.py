"""Мем-эдит игры: нарезка лучших боёв из НЕСКОЛЬКИХ записей под музыку владельца, с видеомемами после реплик.

Так, как одобрил владелец (пилот 2, D:/vvf-offload/memes/pilot2/storyboard.md, v1 → v4): «нарезка норм» на v3 и
«переходов хайповых, с моей музыкой темп соблюсти» → v4. Отвергнутое первое авто-демо (рубка по 0.5 с, мемы на
громкие места, моменты по громкости) — больше не так.

1. **Бои целиком** (app/meme_edit_fights.py): события игры (app/game_events) + стрельба по звуку + реплики → бой от
   первого контакта до конца и реакции, внутри вырезано только мёртвое время; лучшие бои до длины ролика, слабые
   выкинуты целиком; порядок: быстрый понятный первым, победа последней.
2. **Мемы по смыслу** (app/meme_edit_memes.py): ~1 на 22 с, после реплики-сетапа, «эхо» слова игрока — первым;
   только удар мема 0.8–2.2 с без пауз (app/meme_trim.py, кэш склеек — файл библиотеки не меняется).
3. **Всё в долю** (app/meme_edit_plan.py): склейки, мемы и переходы на долях трека, трек короче ролика —
   повтор на такте под стыком блоков; переходы на каждом стыке и вырезе, разные, со звуком из пака.
4. **Блок = клип** (app/meme_edit_blocks.py) — рендер по одному (клип держит ≤ 40 кусков/вставок), потом склейка с
   одной подложкой музыки, даки −12/−16 дБ, тишина перед последней репликой (app/meme_edit_concat.py) → готовый
   ролик обычным клипом (как загруженный файл) — его можно скачать. Плюс вертикаль 9:16 «лучшее» 60–90 с из трёх
   сильнейших боёв.

Долгое — фоном: app/meme_edit_job.py (статус и прогресс). Здесь — сбор входа и план.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from app import meme_edit_fights as F
from app import meme_edit_inputs as I
from app import meme_edit_memes as M
from app import meme_edit_plan as P
from app import meme_edit_team as T

_log = logging.getLogger(__name__)

DEFAULT_TARGET_SEC = 300.0
TARGET_MIN_SEC, TARGET_MAX_SEC = 30.0, 600.0
VERTICAL_MIN_SEC, VERTICAL_MAX_SEC = 60.0, 90.0
DEFAULT_PERIOD = 0.5  # без музыки — сетка 120 BPM
TRANSCRIBE_PAD = (8.0, 12.0)  # слова распознаются вокруг боёв с таким запасом (до, после)
FORMATS = {"horizontal": "16:9", "vertical": "9:16"}


def mic_track(source: dict) -> int | None:
    """Дорожка микрофона: по подписям дорожек, заданная речь (speech_tracks), иначе у записи ровно из двух дорожек —
    вторая (NVIDIA/OBS: 0 — игра, 1 — микрофон)."""
    from app.source_audio import audio_streams, mic_track as titled_mic, normalize_tracks

    found = titled_mic(source)
    if found is not None:
        return found
    speech = normalize_tracks(source.get("speech_tracks"))
    if speech:
        return speech[0]
    return 1 if len(audio_streams(source)) == 2 else None


def resolve_sources(store: Any, source_ids: Any = None, project_id: Any = None) -> list[dict]:
    """Записи эдита: ``source_ids`` по порядку или видео проекта ``project_id`` (горизонтальные записи с файлом)."""
    ids: list[int] = []
    if source_ids:
        if not isinstance(source_ids, (list, tuple)):
            source_ids = [source_ids]
        for sid in source_ids:
            if int(sid) not in ids:
                ids.append(int(sid))
    elif project_id is not None:
        from app.projects_core import project_sources

        ids = [int(s["id"]) for s in project_sources(store, int(project_id))]
    if not ids:
        raise ValueError("нужны записи: source_ids [..] или project_id")
    out = []
    for sid in ids:
        source = store.get_source(sid)
        if source.get("source_type") == "clip_upload":
            continue
        if project_id is not None and not source_ids:
            w, h = int(source.get("width") or 0), int(source.get("height") or 0)
            if w and h and w < h:  # вертикальные готовые ролики проекта — не записи игры
                continue
            if not Path(str(source.get("local_path") or "")).is_file():
                continue
        out.append(source)
    if not out:
        raise ValueError("у проекта нет записей игры с файлом")
    return out


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


def gather(store: Any, sources: list[dict], *, game: str = "apex", teammates: bool = True,
           say: Callable[[str], None] | None = None) -> dict[int, dict]:
    """По каждой записи: события, стрельба, слова игрока и тиммейтов (только вокруг боёв), бои."""
    say = say or (lambda _s: None)
    out: dict[int, dict] = {}
    for n, source in enumerate(sources, 1):
        sid = int(source["id"])
        duration = float(source.get("duration_sec") or 0.0)
        say(f"запись {n}/{len(sources)}: события игры")
        events = I.source_events(store, sid, game)
        shots = I.shot_spans(source)
        mic = mic_track(source)
        rough = F.find_fights(sid, events, shots, [], [], duration)
        windows = I.spans_union([[f.start, f.end] for f in rough], *TRANSCRIBE_PAD, duration)
        owner: list[dict] = []
        team: list[dict] = []
        if windows:
            say(f"запись {n}/{len(sources)}: речь ({len(windows)} окон)")
        for lo, hi in windows:
            if mic is not None:
                owner += I.track_words(source, mic, lo, hi)
            if teammates:
                team += I.track_words(source, 0, lo, hi)
        team = I.drop_bleed(team, owner)
        team, noise = T.filter_team(team, owner)  # шум дорожки игры вместо речи тиммейтов — прочь
        fights = F.find_fights(sid, events, shots, owner, team, duration)
        out[sid] = {"source": source, "duration": duration, "mic": mic, "events": events, "shots": shots,
                    "owner": owner, "team": team, "team_noise": noise, "fights": fights}
    return out


def _music(store: Any, music_track_id: Any, music_asset_id: Any, notes: list[str]) -> tuple[dict | None, dict]:
    music, more = I.music_file(store, music_track_id, music_asset_id)
    notes += more
    grid = None
    if music is not None:
        grid = I.song_grid(music["path"], music.get("start_hint") or 0.0)
        if grid is None:
            notes.append(f"у трека «{music['label']}» не слышно пульса — склейки по сетке 120 BPM")
    if grid is None:
        grid = {"period": DEFAULT_PERIOD, "bpm": 120.0, "downbeat": 0.0, "usable_sec": None}
    return music, grid


def punch_lengths(picks: list[M.Pick]) -> tuple[dict[int, list], dict[int, float]]:
    """Удар каждого выбранного мема: куски (эхо-слово игрока — в середине выбора) и их длина."""
    from app.meme_trim import punch_parts, swear_spans, total

    parts: dict[int, list] = {}
    for p in picks:
        mid = int(p.meme["meme_id"])
        words = p.meme.get("transcript_words")
        parts[mid] = punch_parts(words, p.meme.get("voiced"), float(p.meme.get("duration_sec") or 0),
                                 focus=p.echo or p.cand.text.split(), skip=swear_spans(words))
    return parts, {mid: total(v) for mid, v in parts.items()}


def _one(fights: list[F.Fight], library: list[dict], data: dict[int, dict], grid: dict, *,
         vertical: bool) -> tuple[dict, dict[int, list], list[str]]:
    est = sum(F.with_memes(f.length) for f in fights)
    picks, notes = M.assign_memes(fights, library, est)
    parts, lengths = punch_lengths(picks)
    plan = P.plan_reel(fights, picks, lengths, period=float(grid["period"]), usable_sec=grid.get("usable_sec"),
                       durations={sid: d["duration"] for sid, d in data.items()},
                       words={sid: (d["owner"], d["team"]) for sid, d in data.items()},
                       shots={sid: d["shots"] for sid, d in data.items()}, vertical=vertical)
    plan["music"] = P.music_segments(plan["total"], plan["seams"], float(grid.get("downbeat") or 0.0),
                                     float(grid["period"]), grid.get("usable_sec"))
    return plan, parts, notes


def plan_edit(store: Any, *, source_ids: Any = None, project_id: Any = None, music_track_id: Any = None,
              music_asset_id: Any = None, target_sec: Any = DEFAULT_TARGET_SEC, vertical: bool = True,
              vertical_sec: Any = VERTICAL_MAX_SEC, horizontal: bool = True, teammates: bool = True,
              game: str = "apex", say: Callable[[str], None] | None = None) -> dict:
    """План эдита: бои всех записей, выбор и порядок, мемы, доли, переходы, музыка — для горизонтали (до
    ``target_sec``) и вертикали «лучшее» (60–``vertical_sec`` с из трёх сильнейших боёв)."""
    say = say or (lambda _s: None)
    sources = resolve_sources(store, source_ids, project_id)
    notes: list[str] = []
    data = gather(store, sources, game=game, teammates=teammates, say=say)
    all_fights = [f for d in data.values() for f in d["fights"]]
    noise = [n for d in data.values() for n in d.get("team_noise") or []]
    if noise:
        notes.append(f"тиммейты: {len(noise)} реплик шума выброшено (" + "; ".join(
            f"«{n['text'][:30]}» — {n['why'].split(':')[0]}" for n in noise[:3]) + ")")
    if not all_fights:
        raise ValueError("в записях не нашлось боёв (нет нокаутов, убийств, смертей и плотной стрельбы)")
    say("план: бои, мемы, доли")
    music, grid = _music(store, music_track_id, music_asset_id, notes)
    library = I.meme_library(store)
    if not library:
        notes.append("одобренных видеомемов нет — эдит без мемов (одобрить: «Общие файлы → Видеомемы»)")
    out: dict[str, Any] = {"sources": [int(s["id"]) for s in sources], "music": music, "grid": grid,
                           "fights": F.briefs(sorted(all_fights, key=lambda f: -f.score)), "notes": notes,
                           "mic": {sid: d["mic"] for sid, d in data.items()}, "formats": {}}
    target = _clamp(target_sec, TARGET_MIN_SEC, TARGET_MAX_SEC, DEFAULT_TARGET_SEC)
    if horizontal:
        chosen, more = F.select_fights(all_fights, target)
        notes += more
        plan, parts, more = _one(F.order_fights(chosen), library, data, grid, vertical=False)
        notes += more
        out["formats"]["horizontal"] = {"aspect": "16:9", "plan": plan, "parts": parts}
    if vertical:
        vmax = _clamp(vertical_sec, VERTICAL_MIN_SEC, 180.0, VERTICAL_MAX_SEC)
        good = [f for f in all_fights if f.score >= F.MIN_SCORE] or all_fights
        best = F.pick_vertical(good, max_sec=vmax, min_sec=min(VERTICAL_MIN_SEC, vmax))
        plan, parts, more = _one(best, library, data, grid, vertical=True)
        if plan["total"] > vmax + 5:
            notes.append(f"вертикаль {plan['total']:.0f} с длиннее {vmax:.0f} с: бои не режутся, а короче сильных нет")
        notes += [f"вертикаль: {n}" for n in more]
        out["formats"]["vertical"] = {"aspect": "9:16", "plan": plan, "parts": parts}
    out["library"] = {int(m["meme_id"]): {k: m.get(k) for k in ("file_path", "has_audio", "title")} for m in library}
    return out


def summary(edit: dict) -> dict:
    """План без тяжёлого — для ответа инструмента и статуса задания."""
    return {"sources": edit["sources"], "music": {k: (edit["music"] or {}).get(k) for k in ("kind", "id", "label")}
            if edit.get("music") else None, "bpm": edit["grid"].get("bpm"), "fights": edit["fights"][:30],
            "formats": {k: P.summary(v["plan"]) for k, v in edit["formats"].items()}, "notes": edit["notes"]}


# --------------------------------------------------------------------------- #
# Сборка блоков
# --------------------------------------------------------------------------- #


def _base_render(store: Any) -> dict:
    from app.clip_settings import normalize_render_settings
    from app.render_presets import effective_preset

    preset = effective_preset(store, None)
    return normalize_render_settings({"preset_id": preset["id"]} if preset else {})


def ensure_source_audio(store: Any, source: dict, mic: int | None) -> str | None:
    """Игра + микрофон в звук ролика (микрофон авто-громче игры), речь — по микрофону; только если владелец ещё не
    выбирал дорожки у этой записи."""
    if mic is None:
        return None
    from app.source_audio import is_default_mix, set_source_audio

    current = source.get("audio_mix") if source.get("audio_mix") is not None else source.get("audio_tracks")
    if not is_default_mix(current):
        return None
    try:
        set_source_audio(store, int(source["id"]), [{"track": 0, "gain_db": 0}, {"track": mic, "gain_db": "auto"}],
                         [mic])
    except ValueError as exc:
        return f"дорожки записи {source['id']} не выставлены: {exc}"
    return None


def build_block_clip(store: Any, block: dict, aspect: str, *, mic: int | None, library: dict[int, dict],
                     parts: dict[int, list], index: int, count: int, base: dict, label: str = "") -> int:
    """Блок → новый скрытый клип (куски, файл клипа, слова двух голосов, мат); рендер — отдельно."""
    from app.ai.speakers import lines_from_words
    from app.censor import censor_plan
    from app.clip_spec import apply_spec, export_spec
    from app.clip_subtitles import save_lines
    from app.manual_clips import create_manual_clip
    from app.meme_edit_blocks import spec_for

    spec = spec_for(block, aspect, base, mic=mic, library=library, parts=parts, index=index, count=count, label=label)
    plan = create_manual_clip(store, int(block["source_id"]), spec["title"],
                              [{k: p[k] for k in ("start_sec", "end_sec", "title")} for p in spec["pieces"]],
                              spec["description"])
    plan_id = int(plan["id"])
    current = export_spec(store, plan_id)
    pieces = [{**piece, **({"focus": want["focus"]} if want.get("focus") else {})}
              for piece, want in zip(current["pieces"], spec["pieces"])]
    apply_spec(store, plan_id, {**current, "notes": spec["notes"], "pieces": pieces, "render": spec["render"]})
    words = [{"word": w["word"], "start": w["start"], "end": w["end"], "speaker": w["speaker"]} for w in block["words"]]
    if words:
        save_lines(store, plan_id, lines_from_words(words), words)
        try:
            censor_plan(store, plan_id, track=mic if mic is not None else None)
        except ValueError as exc:
            _log.info("meme edit block %s: censor skipped: %s", plan_id, exc)
    store.set_clip_plan_flags(plan_id, hidden=True)  # блок — заготовка ролика, в списке клипов не нужен
    return plan_id
