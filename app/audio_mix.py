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
"""

from __future__ import annotations

import copy
import math
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

STEMS = ("voice", "source", "music", "sfx", "video")
STEM_LABELS = {"voice": "голос (слои)", "source": "звук исходника", "music": "музыка", "sfx": "звуки",
               "video": "звук видео-слоёв"}
ROLES = ("voice", "music", "sfx", "ambience")
FRAME_SEC = 0.1
TARGET_LUFS = -14.0
TRUE_PEAK_MAX_DB = -1.0
LOUDNESS_TOLERANCE_LU = 1.0
# Музыка под речью: не меньше MUSIC_UNDER_MIN_DB тише голоса (иначе «громко»), цель — 15–18.
MUSIC_UNDER_MIN_DB = 12.0
MUSIC_UNDER_GOOD_DB = 15.0
MUSIC_UNDER_TARGET_DB = 18.0
MUSIC_UNDER_MAX_DB = 30.0  # тише — музыку под речью почти не слышно
# Звук видео-слоя под речью (фон сцены): не громче речи − этого.
VIDEO_UNDER_MIN_DB = 10.0
# Звук «закрывает» слово: под речью громче голоса минус этот запас.
SFX_MASK_DB = 3.0
# Звук слоя громче речи больше чем на столько (дБ) — «звуки громче голоса».
SFX_OVER_VOICE_DB = 2.0
# Речь — кадры, где голос+исходник не тише их уровня больше чем на столько (LU), и не тише −50.
SPEECH_BELOW_LEVEL_LU = 12.0
SPEECH_FLOOR_LUFS = -50.0
SPEECH_MERGE_SEC = 0.35
SPEECH_CORE_LU = 6.0
SILENCE_LUFS = -50.0
SILENCE_MIN_SEC = 1.0
MAX_RANGES = 6
# Лимитер финального прохода: срезать больше — громкость «качает», удары плющатся.
LIMITER_HEAVY_DB = 3.0
SILENT = -120.0

# Роль слоя по имени/id, если ``props.role`` не задан.
VOICE_WORDS = ("озвуч", "голос", "voice", "narr", "tts", "dub", "реплик", "дубляж", "речь", "speech", "диктор")
MUSIC_WORDS = ("музык", "music", "song", "песн", "трек", "bgm", "саундтрек")
LONG_TRACK_SEC = 4.0
LONG_TRACK_SHARE = 0.4


# --------------------------------------------------------------------------- #
# Настройки сведения (render_settings.mix)
# --------------------------------------------------------------------------- #

MIX_DB_RANGE = (-40.0, 12.0)
LAYER_DB_RANGE = (-30.0, 12.0)
TARGET_RANGE = (-24.0, -9.0)
MAX_LAYER_GAINS = 200


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def normalize_mix(raw: Any) -> dict:
    """``render_settings.mix`` — ручки сведения; пустые поля не хранятся ({} — рендер как раньше).

    * ``target_lufs`` — громкость готового ролика (−24..−9; нет — сервис, −14);
    * ``music_db`` — громкость фонового трека в дБ к файлу (−40..+12) вместо подгонки под голос;
    * ``sidechain`` — false: без автоматического sidechain под речью (ducking — отрезками music_duck);
    * ``duck_attack`` / ``duck_release`` — рампы ручного ducking, с (0.02..1 / 0.02..2);
    * ``groups`` — поправка громкости групп слоёв {voice, sfx, music, video} в дБ;
    * ``layer_gains`` — поправка слоя {id: дБ} (−30..+12) поверх его громкости;
    * ``voice_fx`` / ``music_fx`` — цепочки эффектов голоса и музыки (app/audio_fx.py);
    * ``auto`` — что сделал auto_mix (справка, рендер не читает)."""
    src = raw if isinstance(raw, dict) else {}
    out: dict = {}
    target = _num(src.get("target_lufs"))
    if target is not None:
        out["target_lufs"] = round(_clamp(target, *TARGET_RANGE), 1)
    music = _num(src.get("music_db"))
    if music is not None:
        out["music_db"] = round(_clamp(music, *MIX_DB_RANGE), 2)
    if src.get("sidechain") is False or src.get("sidechain") in (0, "0", "false"):
        out["sidechain"] = False
    for key, hi in (("duck_attack", 1.0), ("duck_release", 2.0)):
        value = _num(src.get(key))
        if value is not None:
            out[key] = round(_clamp(value, 0.02, hi), 3)
    groups = {}
    for name, value in (src.get("groups") or {}).items() if isinstance(src.get("groups"), dict) else []:
        db = _num(value)
        if name in ("voice", "sfx", "music", "video") and db is not None and abs(db) >= 0.05:
            groups[name] = round(_clamp(db, *LAYER_DB_RANGE), 2)
    if groups:
        out["groups"] = groups
    gains = {}
    raw_gains = src.get("layer_gains") if isinstance(src.get("layer_gains"), dict) else {}
    for lid, value in list(raw_gains.items())[:MAX_LAYER_GAINS]:
        db = _num(value)
        if db is not None and abs(db) >= 0.05 and str(lid).strip():
            gains[str(lid)[:64]] = round(_clamp(db, *LAYER_DB_RANGE), 2)
    if gains:
        out["layer_gains"] = gains
    for key in ("voice_fx", "music_fx"):
        if src.get(key):
            from app.audio_fx import TIMELESS_BANNED, normalize_chain

            # Голос и музыка идут по шкале ролика: эффекты, меняющие длину/направление, — только у слоёв.
            chain = [e for e in normalize_chain(src.get(key)) if e["fx"] not in TIMELESS_BANNED]
            if chain:
                out[key] = chain
    if isinstance(src.get("auto"), dict):
        auto = {str(k)[:40]: (v[:200] if isinstance(v, str) else v) for k, v in src["auto"].items()
                if isinstance(v, (int, float, str, bool)) and (not isinstance(v, float) or math.isfinite(v))}
        if auto:
            out["auto"] = dict(list(auto.items())[:20])
    return out


def mix_layer_gain_db(mix: dict | None, layer_id: str, role: str) -> float:
    """Поправка громкости слоя из сведения: группа роли + свой слой (дБ)."""
    if not mix:
        return 0.0
    group = {"ambience": "video"}.get(role, role)
    total = float((mix.get("groups") or {}).get(group) or 0.0) + float((mix.get("layer_gains") or {}).get(layer_id) or 0.0)
    return round(_clamp(total, *LAYER_DB_RANGE), 2)


def layer_role(layer: dict, duration: float | None = None) -> str:
    """Роль звука слоя: ``props.role`` → имя/id (озвучка, голос, музыка…) → видео — ``video`` →
    длинная дорожка — ``music`` → иначе ``sfx``."""
    props = layer.get("props") or {}
    role = str(props.get("role") or "")
    if role in ROLES:
        return "video" if role == "ambience" and layer.get("type") == "video" else role
    if layer.get("type") == "video":
        return "video"
    text = f"{layer.get('id') or ''} {layer.get('name') or ''}".lower()
    if any(word in text for word in VOICE_WORDS):
        return "voice"
    if any(word in text for word in MUSIC_WORDS):
        return "music"
    try:
        length = float(layer.get("end") or 0) - float(layer.get("start") or 0)
    except (TypeError, ValueError):
        length = 0.0
    if props.get("mode") == "track" and length >= max(LONG_TRACK_SEC, LONG_TRACK_SHARE * float(duration or 0)):
        return "music"
    return "sfx"


# --------------------------------------------------------------------------- #
# Замер: ebur128
# --------------------------------------------------------------------------- #

_FRAME_RE = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+|-inf|nan)\s+S:\s*(-?[\d.]+|-inf|nan)")
_I_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)")
_LRA_RE = re.compile(r"Loudness range:\s*\n\s*LRA:\s*(-?[\d.]+)")
_TP_RE = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)")


def _db(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return SILENT
    return value if math.isfinite(value) else SILENT


def parse_ebur128(stderr: str) -> dict:
    """Лог ``ebur128=peak=true`` → ``{m: [...], s: [...], i, lra, tp}`` (m/s — по 0.1 с)."""
    m: list[float] = []
    s: list[float] = []
    for match in _FRAME_RE.finditer(stderr):
        m.append(max(SILENT, _db(match.group(2))))
        s.append(max(SILENT, _db(match.group(3))))
    tail = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    i = _I_RE.search(tail)
    lra = _LRA_RE.search(tail)
    tp = _TP_RE.search(tail)
    return {"m": m, "s": s, "i": _db(i.group(1)) if i else SILENT, "lra": _db(lra.group(1)) if lra else 0.0,
            "tp": _db(tp.group(1)) if tp else SILENT}


def ebur128(path: str | Path, pre: list[str] | None = None, af: str = "") -> dict:
    """Громкость файла по BS.1770 (``pre`` — аргументы до -i, ``af`` — фильтры до замера)."""
    chain = (af + "," if af else "") + "ebur128=peak=true"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *(pre or []), "-i", str(path), "-vn", "-af", chain, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60 * 10,
    )
    return parse_ebur128(proc.stderr)


def db2p(db: float) -> float:
    return 0.0 if db <= SILENT + 1 else 10 ** (db / 10)


def p2db(power: float) -> float:
    return SILENT if power <= 1e-12 else 10 * math.log10(power)


def power_mean(values: list[float]) -> float:
    return p2db(sum(db2p(v) for v in values) / len(values)) if values else SILENT


def power_sum(*series: list[float]) -> list[float]:
    """Поэлементная сумма мощностей (громкость суммы некоррелированных звуков)."""
    n = max((len(s) for s in series), default=0)
    return [p2db(sum(db2p(s[i]) for s in series if i < len(s))) for i in range(n)]


# --------------------------------------------------------------------------- #
# Модель: стемы так, как их сведёт рендер
# --------------------------------------------------------------------------- #


def _run(args: list[str], timeout: int = 60 * 10) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "ffmpeg failed").strip()[-600:])


def _out_args(label: str, duration: float, dst: Path) -> list[str]:
    return ["-map", f"[{label}]", "-t", f"{duration:.3f}", "-c:a", "pcm_f32le", "-ar", "48000", "-ac", "2", str(dst)]


def _finish(label: str, duration: float) -> str:
    return (f"[{label}]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,apad,"
            f"atrim=0:{duration:.3f}[stemout]")


def silence_wav(dst: Path, duration: float) -> Path:
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
          f"anullsrc=r=48000:cl=stereo", "-t", f"{duration:.3f}", "-c:a", "pcm_f32le", str(dst)])
    return dst


def mix_wavs(paths: list[Path], dst: Path, duration: float) -> Path:
    """Сумма стемов (normalize=0 — как amix рендера)."""
    if not paths:
        return silence_wav(dst, duration)
    if len(paths) == 1:
        shutil.copyfile(paths[0], dst)
        return dst
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in paths:
        args += ["-i", str(p)]
    graph = "".join(f"[{i}:a]" for i in range(len(paths))) + \
        f"amix=inputs={len(paths)}:duration=longest:normalize=0:dropout_transition=0[m];" + _finish("m", duration)
    _run(args + ["-filter_complex", graph, *_out_args("stemout", duration, dst)])
    return dst


def _atempo(rate: float) -> str:
    """atempo старых ffmpeg — 0.5..2 на экземпляр: большие множители — цепочкой."""
    parts = []
    r = float(rate)
    while r > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.6f}")
    return ",".join(parts)


def _source_stem(store: Any, plan: dict, source: dict, duration: float, dst: Path, notes: list[str]) -> Path | None:
    """Звук исходника кусками подряд (дорожки смеси, скорость/реверс куска); без J/L и эффектов."""
    from app.piece_speed import is_reversed, seg_length
    from app.source_audio import mix_chain, source_mix

    path = Path(source.get("local_path") or "")
    segments = plan.get("segments") or []
    if not path.is_file() or not segments:
        return None
    tracks = source_mix(source) or [{"track": 0, "gain_db": 0.0}]
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    graph: list[str] = []
    labels = []
    for n, seg in enumerate(segments):
        a, b = float(seg["start_sec"]), float(seg["end_sec"])
        if b - a < 0.02:
            continue
        args += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(path)]
        k = len(labels)
        graph.append(mix_chain(k, tracks, f"s{k}m"))
        length = seg_length(seg)
        chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        speed = float(seg.get("speed") or 1.0)
        mode = str(seg.get("speed_audio") or "")
        if mode == "mute":
            chain.append("volume=0")
        else:
            if is_reversed(seg):
                chain.append("areverse")
            if abs(speed - 1.0) > 1e-3:
                chain.append(_atempo(speed))
        chain += ["apad", f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS"]
        graph.append(f"[s{k}m]{','.join(chain)}[s{k}]")
        labels.append(f"[s{k}]")
    if not labels:
        return None
    graph.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[src]")
    graph.append(_finish("src", duration))
    try:
        _run(args + ["-filter_complex", ";".join(graph), *_out_args("stemout", duration, dst)])
    except RuntimeError as exc:
        notes.append(f"звук видео не прочитан: {str(exc)[:160]}")
        return None
    return dst


def _music_settings(store: Any, rs: dict, mix: dict) -> dict | None:
    """Музыка клипа как у рендера (``render._resolve_music_settings`` + автоматизация клипа)."""
    from app.audio_edit import music_automation

    if not rs.get("music_on") or not rs.get("track_id"):
        return None
    try:
        track = store.get_audio_track(int(rs["track_id"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not Path(track.get("file_path") or "").is_file():
        return None
    volume = track.get("volume")
    music = {
        "track_path": track["file_path"], "volume": 0.5 if volume is None else volume,
        "start_sec": float(rs.get("music_start_sec") or 0.0), "loop": True, "duck": True, "duck_amount": None,
        "automation": music_automation(rs), "track_id": int(track["id"]), "label": track.get("label") or "",
    }
    if mix.get("sidechain") is False:
        music["sidechain"] = False
    if mix.get("music_db") is not None:
        music["fixed_db"] = float(mix["music_db"])
    if mix.get("music_fx"):
        music["fx"] = mix["music_fx"]
    return music


def _music_stem(music: dict, key: Path, duration: float, dst: Path) -> tuple[Path, float]:
    """Стем музыки: ``key`` — звук клипа до музыки (по нему sidechain и подгонка громкости)."""
    from app.render import music_stem_filter, music_volume_for

    settings = {**music, "duration_sec": duration, "hold_sec": 0.0}
    settings["volume"] = music_volume_for(key, settings)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(key), "-stream_loop", "-1",
            "-i", str(music["track_path"])]
    graph = music_stem_filter(settings) + ";" + _finish("aout", duration)
    _run(args + ["-filter_complex", graph, *_out_args("stemout", duration, dst)])
    return dst, float(settings["volume"])


def _items_stem(items: list[dict], duration: float, dst: Path, chain_of: Any = None) -> Path:
    from app.motion_render import audio_item_chain, audio_item_inputs

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    graph = []
    for n, item in enumerate(items):
        args += audio_item_inputs(item)
        graph.append((chain_of or audio_item_chain)(n, item) + f"[a{n}]")
    labels = "".join(f"[a{n}]" for n in range(len(items)))
    if len(items) == 1:
        graph.append(f"{labels}anull[g]")
    else:
        graph.append(f"{labels}amix=inputs={len(items)}:duration=longest:normalize=0:dropout_transition=0[g]")
    graph.append(_finish("g", duration))
    _run(args + ["-filter_complex", ";".join(graph), *_out_args("stemout", duration, dst)])
    return dst


def _duck_filter(spans: list[tuple[float, float]]) -> str:
    from app.motion_render import AUDIO_DUCK_VOLUME

    if not spans:
        return ""
    cond = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)
    return f"volume=enable='{cond}':volume={AUDIO_DUCK_VOLUME}"


def _apply_af(src: Path, af: str, dst: Path) -> Path:
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", af, "-c:a", "pcm_f32le", str(dst)])
    return dst


_ITEM_CACHE: dict[tuple, float] = {}
_ITEM_LOCK = threading.Lock()


def item_peak_lufs(item: dict) -> float:
    """Самая громкая momentary-громкость (400 мс) звука слоя — с его громкостью и цепочкой."""
    from app.motion_render import audio_item_chain, audio_item_inputs

    try:
        mtime = Path(item["path"]).stat().st_mtime
    except OSError:
        return SILENT
    key = (item["path"], mtime, round(item["offset"], 3), item.get("read"), round(item["volume"], 5),
           round(item["len"], 3), str(item.get("fx") or ""), item.get("loop"), item.get("tempo"))
    with _ITEM_LOCK:
        if key in _ITEM_CACHE:
            return _ITEM_CACHE[key]
    chain = audio_item_chain(0, item, delay=False)
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *audio_item_inputs(item), "-filter_complex",
         f"{chain},apad=pad_dur=0.4,ebur128=peak=true[o]", "-map", "[o]", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=120,
    )
    got = parse_ebur128(proc.stderr)
    value = max(got["m"]) if got["m"] else SILENT
    with _ITEM_LOCK:
        _ITEM_CACHE[key] = value
    return value


def _clip_duration(store: Any, plan: dict) -> float:
    from app.agent_tools import plan_clock

    return float(plan_clock(store, plan).clip_duration)


def build_model(store: Any, plan_id: int, workdir: Path, overrides: dict | None = None) -> dict:
    """Стемы клипа в ``workdir``: ``{duration, stems: {kind: Path}, items, music, notes, canvas}``.

    ``overrides`` — {mix?, audio?, layers?}: прогноз «а если так» (auto_mix) без записи в клип."""
    from app.canvas import is_canvas
    from app.clip_settings import normalize_render_settings
    from app.motion import plan_motion
    from app.motion_render import audio_layer_items, plan_is_speechless, resolve_layer_assets, speech_level_db

    plan = store.get_clip_plan(int(plan_id))
    rs = normalize_render_settings(plan.get("render_settings") or {})
    raw_mix = (plan.get("render_settings") or {}).get("mix")
    mix = normalize_mix(raw_mix)
    if overrides:
        if "mix" in overrides:
            mix = normalize_mix(overrides["mix"])
        if "audio" in overrides:
            from app.audio_edit import normalize_audio

            rs = {**rs, "audio": normalize_audio(overrides["audio"])}
        if "layers" in overrides:
            rs = {**rs, "layers": overrides["layers"]}
    source = store.get_source(int(plan["source_id"]))
    canvas = is_canvas(source)
    duration = max(0.5, _clip_duration(store, plan))
    notes: list[str] = []
    stems: dict[str, Path] = {}
    workdir.mkdir(parents=True, exist_ok=True)
    silent = silence_wav(workdir / "silence.wav", duration)

    if not canvas:
        got = _source_stem(store, plan, source, duration, workdir / "source.wav", notes)
        if got is not None and mix.get("voice_fx"):
            from app.audio_fx import chain_filters

            got = _apply_af(got, chain_filters(mix["voice_fx"], "vo"), workdir / "source.fx.wav")
        if got is not None:
            stems["source"] = got
            notes.append("звук исходника — по кускам подряд, без J/L, тишины, эффектов отрезка и переходов "
                         "(для соотношения голос/музыка хватает)")

    music_info = None
    music = _music_settings(store, rs, mix)
    if music is not None:
        key = stems.get("source") or silent
        path, volume = _music_stem(music, key, duration, workdir / "music.wav")
        stems["music"] = path
        music_info = {"track_id": music["track_id"], "label": music["label"], "volume": round(volume, 4),
                      "volume_db": round(20 * math.log10(volume), 2) if volume > 0 else SILENT,
                      "fixed": music.get("fixed_db") is not None,
                      "sidechain": music.get("sidechain") is not False,
                      "duck_spans": len((music.get("automation") or {}).get("duck") or [])}

    motion = plan_motion({**rs})
    from app.motion import has_layer_sound

    sound_layers = [layer for layer in (motion.get("layers") or []) if has_layer_sound(layer)]
    items: list[dict] = []
    if sound_layers:
        sound_layers, assets, layer_notes = resolve_layer_assets(store, sound_layers)
        notes += layer_notes
        effects = any(layer["type"] == "audio" and layer["props"].get("mode") != "track" for layer in sound_layers)
        speech_db = None
        if effects:
            base = [p for k, p in stems.items() if k in ("source", "music")]
            base_path = mix_wavs(base, workdir / "base.wav", duration) if base else silent
            speech_db = speech_level_db(base_path)
        try:
            speechless = bool(stems.get("source")) and plan_is_speechless(store, plan)
        except Exception:  # noqa: BLE001
            speechless = False
        items = audio_layer_items(sound_layers, assets, duration, speech_db, speechless, notes=notes, mix=mix)
        ducks = [(it["start"], it["end"]) for it in items if it.get("duck")]
        if ducks:
            for kind in ("source", "music"):
                if kind in stems:
                    stems[kind] = _apply_af(stems[kind], _duck_filter(ducks), workdir / f"{kind}.duck.wav")
        groups = {"voice": [], "sfx": [], "music": [], "video": []}
        for item in items:
            groups[{"ambience": "video"}.get(item["role"], item["role"])].append(item)
        for kind, group in groups.items():
            if not group:
                continue
            path = _items_stem(group, duration, workdir / f"layers-{kind}.wav")
            if kind == "music" and "music" in stems:
                stems["music"] = mix_wavs([stems["music"], path], workdir / "music.all.wav", duration)
            else:
                stems[kind] = path
    speechless = False
    if "source" in stems and "voice" not in stems:
        try:
            speechless = plan_is_speechless(store, plan)
        except Exception:  # noqa: BLE001 - не знаем — считаем, что речь есть
            speechless = False
    return {"plan": plan, "rs": rs, "mix": mix, "duration": duration, "stems": stems, "items": items,
            "music": music_info, "notes": notes, "canvas": canvas, "source": source, "speechless": speechless}


# --------------------------------------------------------------------------- #
# Анализ
# --------------------------------------------------------------------------- #


def _ranges(mask: list[bool], merge: float = SPEECH_MERGE_SEC, step: float = FRAME_SEC,
            shift: float = 0.0) -> list[tuple[float, float]]:
    """Подряд идущие кадры маски → отрезки (с), дырки короче ``merge`` склеены."""
    out: list[list[float]] = []
    for i, on in enumerate(mask):
        if not on:
            continue
        a = max(0.0, i * step + shift)
        b = a + step
        if out and a - out[-1][1] <= merge + 1e-9:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def _frames(meas: dict, n: int) -> list[float]:
    m = list(meas.get("m") or [])
    return (m + [SILENT] * n)[:n]


def speech_mask(dialog: list[float], level: float) -> list[bool]:
    """Кадры речи: громкость голоса+исходника не ниже уровня − SPEECH_BELOW_LEVEL_LU и не тише пола."""
    floor = max(SPEECH_FLOOR_LUFS, level - SPEECH_BELOW_LEVEL_LU)
    return [v >= floor for v in dialog]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return SILENT
    s = sorted(values)
    k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[k]


def _r(value: float, digits: int = 1) -> float | None:
    return None if value <= SILENT + 1 else round(value, digits)


def _spark(values: list[float], lo: float = -40.0, hi: float = -6.0) -> str:
    bars = "▁▂▃▄▅▆▇█"
    out = []
    for v in values:
        if v <= SILENT + 1:
            out.append(" ")
            continue
        u = (min(hi, max(lo, v)) - lo) / (hi - lo)
        out.append(bars[min(len(bars) - 1, int(u * len(bars)))])
    return "".join(out)


def _timeline(short: list[float], duration: float) -> dict:
    step = 0.5 if duration <= 90 else 1.0
    every = max(1, int(round(step / FRAME_SEC)))
    points = [(round((i + 1) * FRAME_SEC, 1), v) for i, v in enumerate(short)][every - 1::every]
    values = [v for _t, v in points]
    return {"step_sec": step, "points": [{"t": t, "lufs": _r(v)} for t, v in points],
            "line": _spark(values), "line_scale": "▁ −40 … █ −6 LUFS (short-term, 3 с)"}


def analyze(model: dict, target: float = TARGET_LUFS, render_path: Path | None = None,
            final_measure: dict | None = None) -> dict:
    """Замеры стемов → цифры и вердикты (см. вверху)."""
    from app.render import LOUDNESS_PEAK_DB as LOUDNESS_PEAK
    from app.render import loudness_plan

    duration = float(model["duration"])
    n = max(1, int(round(duration / FRAME_SEC)))
    stems: dict[str, Path] = model["stems"]
    meas = {kind: ebur128(path) for kind, path in stems.items()}
    frames = {kind: _frames(m, n) for kind, m in meas.items()}
    silent_series = [SILENT] * n
    dialog_parts = [frames[k] for k in ("voice", "source") if k in frames]
    dialog = power_sum(*dialog_parts) if dialog_parts else silent_series
    dialog_level = p2db(sum(db2p(v) for v in dialog if v > SPEECH_FLOOR_LUFS) /
                        max(1, sum(1 for v in dialog if v > SPEECH_FLOOR_LUFS)))
    speechless_source = "voice" not in frames and "source" in frames and _source_speechless(model)
    mask = speech_mask(dialog, dialog_level) if dialog_parts and not speechless_source else [False] * n
    speech_frames = [i for i, on in enumerate(mask) if on]
    speech_sec = round(len(speech_frames) * FRAME_SEC, 1)
    speech_level = power_mean([dialog[i] for i in speech_frames]) if speech_frames else SILENT
    # «Ядро» речи — кадры, где голос в полную силу: на краях фраз окно 400 мс наполовину тишина, и
    # покадровое сравнение там врёт (музыка «громче» голоса на его же атаке).
    core_frames = [i for i in speech_frames if dialog[i] >= speech_level - SPEECH_CORE_LU]

    stems_out = {}
    for kind in STEMS:
        if kind not in meas:
            continue
        m = meas[kind]
        active = sum(1 for v in frames[kind] if v > SILENCE_LUFS)
        stems_out[kind] = {"label": STEM_LABELS[kind], "lufs": _r(m["i"]), "true_peak": _r(m["tp"]),
                           "active_sec": round(active * FRAME_SEC, 1),
                           "under_speech_lufs": _r(power_mean([frames[kind][i] for i in speech_frames]))
                           if speech_frames else None}

    # Сумма стемов — что придёт в финальный проход громкости.
    mix_path = Path(str(next(iter(stems.values())).parent / "mix.wav")) if stems else None
    mix_meas = ebur128(mix_wavs(list(stems.values()), mix_path, duration)) if mix_path else \
        {"m": [], "s": [], "i": SILENT, "tp": SILENT, "lra": 0.0}
    mix_frames = _frames(mix_meas, n)
    gain, limiter_db, aim = 0.0, 0.0, SILENT
    if mix_meas["i"] > SILENT + 1:
        # Тот же план, что у финального прохода (render.loudness_plan): линейная поправка, сверх потолка
        # +20 дБ — в пределах запаса пиков; что выше потолка пика — срежет лимитер (≤ 3 дБ без потерь).
        lp = loudness_plan({"input_i": mix_meas["i"], "input_tp": mix_meas["tp"]}, target)
        gain = float(lp["gain_db"])
        limiter_db = max(0.0, float(lp.get("overshoot_db") or 0.0))
        aim = float(lp.get("aim_lufs", mix_meas["i"] + gain))
    predicted = {"integrated": _r(aim),
                 "true_peak": _r(min(mix_meas["tp"] + gain, LOUDNESS_PEAK)) if mix_meas["tp"] > SILENT + 1 else None,
                 "final_gain_db": round(gain, 2), "limiter_db": round(limiter_db, 1),
                 "lra": round(mix_meas["lra"], 1)}
    final = None
    if final_measure is None and render_path is not None and Path(render_path).is_file():
        final_measure = ebur128(render_path)
    if final_measure is not None:
        final = {"integrated": _r(final_measure["i"]), "true_peak": _r(final_measure["tp"]),
                 "lra": round(final_measure["lra"], 1), "timeline": _timeline(final_measure["s"], duration)}

    out: dict = {"duration": round(duration, 2), "target": {"lufs": target, "true_peak": TRUE_PEAK_MAX_DB,
                                                           "music_under_voice_db": [MUSIC_UNDER_MIN_DB,
                                                                                   MUSIC_UNDER_TARGET_DB]},
                 "stems": stems_out, "predicted": predicted,
                 "speech": {"seconds": speech_sec, "level_lufs": _r(speech_level),
                            "spans": len(_ranges(mask)), "detected": "по громкости голоса и звука исходника"},
                 "timeline": final["timeline"] if final else _timeline(mix_meas["s"], duration)}
    if final:
        out["final"] = {k: v for k, v in final.items() if k != "timeline"}
    verdicts: list[dict] = []

    def verdict(severity: str, check: str, message: str, fix: str = "", suggest: dict | None = None, **extra: Any) -> None:
        item = {"severity": severity, "check": check, "message": message}
        if fix:
            item["fix"] = fix
        if suggest:
            item["suggest"] = suggest
        item.update(extra)
        verdicts.append(item)

    # ---- голос против музыки
    music_db_now = (model.get("music") or {}).get("volume_db")
    if speech_frames and "music" in frames:
        music_under = power_mean([frames["music"][i] for i in speech_frames])
        diff = speech_level - music_under
        per = [(i, dialog[i] - frames["music"][i]) for i in core_frames]
        worst = _percentile([d for _i, d in per], 0.1)
        bad_mask = [False] * n
        for i, d in per:
            if d < MUSIC_UNDER_MIN_DB:
                bad_mask[i] = True
        bad = _worst_ranges(bad_mask, per, n)
        vm = {"diff_db": _r(diff), "worst_db": _r(worst), "speech_lufs": _r(speech_level),
              "music_lufs": _r(music_under), "too_loud_at": bad}
        out["voice_vs_music"] = vm
        delta = diff - MUSIC_UNDER_TARGET_DB  # < 0 — музыку убавить на |delta|
        known = music_db_now is not None and music_db_now > SILENT + 1
        new_db = round(music_db_now + delta, 1) if known else None
        suggest = {"tool": "auto_mix", "args": {"music_under_voice_db": -MUSIC_UNDER_TARGET_DB}}
        manual = f"set_mix music_db={new_db:g} (сейчас {music_db_now:+.1f} дБ к файлу трека)" if known else ""
        if diff < MUSIC_UNDER_MIN_DB:
            now = (f"музыка ГРОМЧЕ голоса на {-diff:.1f} дБ" if diff < 0 else
                   f"голос выше музыки всего на {diff:.1f} дБ")
            verdict("warn", "music",
                    f"музыка громче нормы на {MUSIC_UNDER_TARGET_DB - diff:.0f} дБ под речью: {now} "
                    f"(норма — музыка на {MUSIC_UNDER_MIN_DB:g}–{MUSIC_UNDER_TARGET_DB:g} дБ тише речи)",
                    f"убавь музыку на {-delta:.0f} дБ под речью: auto_mix (сам выставит громкость и ducking)"
                    + (f" или {manual}" if manual else ""), suggest, diff_db=round(diff, 1),
                    music_db_suggested=new_db)
        elif diff < MUSIC_UNDER_GOOD_DB:
            verdict("info", "music",
                    f"музыка на грани: голос выше её на {diff:.1f} дБ (лучше {MUSIC_UNDER_GOOD_DB:g}–"
                    f"{MUSIC_UNDER_TARGET_DB:g})", f"убавь на {-delta:.0f} дБ — auto_mix" + (f" или {manual}" if manual else ""),
                    suggest, diff_db=round(diff, 1), music_db_suggested=new_db)
        elif diff > MUSIC_UNDER_MAX_DB:
            up = round(diff - MUSIC_UNDER_TARGET_DB, 1)
            verdict("info", "music", f"музыку под речью почти не слышно: на {diff:.0f} дБ тише голоса",
                    f"можно поднять на {up:g} дБ (auto_mix) — или так задумано",
                    {"tool": "auto_mix", "args": {}}, diff_db=round(diff, 1),
                    music_db_suggested=round(music_db_now + up, 1) if music_db_now is not None and music_db_now > SILENT + 1
                    else None)
        if bad and diff >= MUSIC_UNDER_MIN_DB:
            spots = ", ".join(f"{r['start']:.1f}–{r['end']:.1f} с" for r in bad[:3])
            verdict("info", "music", f"местами музыка всё же громкая под речью ({spots}): голос выше всего на "
                                     f"{min(r['diff_db'] for r in bad):.0f} дБ",
                    "set_music_duck duck на этих отрезках (−6…−10 дБ) или auto_mix", t=bad[0]["start"])
    elif "music" in frames and not speech_frames:
        out["voice_vs_music"] = {"diff_db": None, "note": "речи нет — музыка звучит одна"}

    # ---- звук видео-слоёв под речью
    if speech_frames and "video" in frames:
        under = power_mean([frames["video"][i] for i in speech_frames])
        diff = speech_level - under
        out["voice_vs_video"] = {"diff_db": _r(diff)}
        if diff < VIDEO_UNDER_MIN_DB and under > SILENCE_LUFS:
            verdict("warn", "video", f"звук видео-слоёв под речью громкий: голос выше всего на {diff:.1f} дБ",
                    f"убавь видео-слои на {VIDEO_UNDER_MIN_DB + 4 - diff:.0f} дБ: auto_mix или set_mix groups.video",
                    {"tool": "auto_mix", "args": {}}, diff_db=round(diff, 1))

    # ---- звуки против голоса
    item_rows = []
    if model.get("items"):
        for item in model["items"]:
            if item["role"] not in ("sfx", "video"):
                continue
            peak = item_peak_lufs(item)
            a, b = item["start"], item["end"]
            i0, i1 = max(0, _frame_of(a)), min(n, _frame_of(b) + 4)
            over_speech = [i for i in range(i0, i1) if mask[i]]
            ref = power_mean([dialog[i] for i in over_speech]) if over_speech else speech_level
            row = {"layer_id": item["layer_id"], "role": item["role"], "start": round(a, 2), "end": round(b, 2),
                   "peak_lufs": _r(peak), "over_speech": bool(over_speech),
                   "vs_voice_db": _r(peak - speech_level) if speech_level > SILENT + 1 and peak > SILENT + 1 else None}
            if item.get("mix_db"):
                row["mix_db"] = item["mix_db"]
            if speech_level > SILENT + 1 and peak > SILENT + 1:
                limit = (ref - SFX_MASK_DB) if over_speech else (speech_level + SFX_OVER_VOICE_DB)
                row["excess_db"] = round(peak - limit, 1)
            item_rows.append(row)
    if item_rows:
        out["layers"] = item_rows
    if speech_frames and "sfx" in frames:
        masking = [False] * n
        per = []
        for i in core_frames:
            d = dialog[i] - frames["sfx"][i]
            if frames["sfx"][i] > SILENCE_LUFS and d < SFX_MASK_DB:
                masking[i] = True
            per.append((i, d))
        ranges = _worst_ranges(masking, per, n)
        out["voice_vs_sfx"] = {"masking_at": ranges,
                               "max_sfx_lufs": _r(max(frames["sfx"])),
                               "speech_lufs": _r(speech_level)}
        for r in ranges[:3]:
            who = [row["layer_id"] for row in item_rows if row["start"] < r["end"] and row["end"] > r["start"]]
            need = min(SFX_MASK_DB - r["diff_db"], -LAYER_DB_RANGE[0] - 3)
            verdict("warn", "sfx",
                    f"на {r['start']:.1f}–{r['end']:.1f} с звук{'и' if len(who) > 1 else ''} "
                    f"{', '.join(who) or 'слоёв'} {'громче' if r['diff_db'] < 0 else 'почти как'} голос"
                    f"{'а' if r['diff_db'] < 0 else ''} ({-r['diff_db']:+.0f} дБ к речи) — слова тонут",
                    f"убавь на {need + 3:.0f} дБ (set_mix layer_gains {{{', '.join(repr(w) for w in who[:3])}: "
                    f"{-(need + 3):.0f}}}) или сдвинь звук в паузу; auto_mix сделает сам",
                    {"tool": "auto_mix", "args": {}}, t=r["start"], layers=who)
    loud_rows = [row for row in item_rows if (row.get("excess_db") or 0) > 0 and not row["over_speech"]]
    if loud_rows:
        worst_row = max(loud_rows, key=lambda r: r["excess_db"])
        verdict("warn", "sfx",
                f"звуки громче голоса: {len(loud_rows)} шт., громче всех {worst_row['layer_id']} на "
                f"{worst_row['start']:.1f} с (на {worst_row['vs_voice_db']:+.0f} дБ к речи)",
                f"убавь на {worst_row['excess_db'] + 2:.0f} дБ: auto_mix (выровняет каждый звук под голос) или "
                f"set_mix layer_gains", {"tool": "auto_mix", "args": {}}, t=worst_row["start"],
                layers=[r["layer_id"] for r in loud_rows][:10])

    # ---- итоговая громкость и пики
    final_i = final["integrated"] if final else predicted["integrated"]
    final_tp = final["true_peak"] if final else predicted["true_peak"]
    where = "готовый рендер" if final else "прогноз финального прохода"
    if final_i is not None and abs(final_i - target) > LOUDNESS_TOLERANCE_LU:
        verdict("warn" if final else "info", "loudness",
                f"громкость ролика {final_i:.1f} LUFS ({where}), цель {target:g} (±{LOUDNESS_TOLERANCE_LU:g})",
                "перерендерь (финальный проход выравнивает сам); тише цели при пике у потолка — пики мешают "
                "(убавь громкие звуки, auto_mix)" if final else "финальный проход поднимет/опустит сам",
                {"tool": "auto_mix", "args": {"target_lufs": target}})
    if final_tp is not None and final_tp > TRUE_PEAK_MAX_DB:
        verdict("warn", "peak", f"true peak {final_tp:.1f} dBTP ({where}) — выше {TRUE_PEAK_MAX_DB:g}: на площадках захрипит",
                "перерендерь; если повторяется — убавь самые громкие звуки (auto_mix)")
    if final and final_tp is not None and final_tp > -0.1:
        verdict("warn", "clipping", f"клиппинг: пик {final_tp:.1f} dBTP в готовом файле", "перерендерь, убавь звуки")
    if limiter_db > LIMITER_HEAVY_DB:
        verdict("warn" if limiter_db > 6 else "info", "peak",
                f"пики сведения на {limiter_db:.0f} дБ выше потолка — лимитер их срежет, громкость «качнёт»",
                "убавь самые громкие звуки/удары (auto_mix выровняет звуки под голос)", {"tool": "auto_mix", "args": {}})

    # ---- дыры тишины
    quiet = [v < SILENCE_LUFS for v in mix_frames]
    gaps = [(a, b) for a, b in _ranges(quiet, merge=0.05, shift=FRAME_SHIFT)
            if b - a >= SILENCE_MIN_SEC and a > 0.3 and b < duration - 0.3]
    # Стемы — по шкале clip ДО вырезов: тишина внутри выреза в ролик не попадёт (QA 02.11, клип 892:
    # «тишина 18.6–19.6 с» указывала прямо в вырез 18.47–19.66 с).
    gaps = silence_after_cuts(gaps, (model.get("rs") or {}).get("cuts") or [])
    if gaps:
        out["silence_gaps"] = [{"start": a, "end": b} for a, b in gaps[:MAX_RANGES]]
        verdict("info", "silence", "тишина в ролике: " + ", ".join(f"{a:.1f}–{b:.1f} с" for a, b in gaps[:3]),
                "если не задумано — музыка/фон или подрежь паузу", t=gaps[0][0])
    if not dialog_parts:
        verdict("info", "speech", "голоса нет (ни слоёв-голоса, ни звука исходника) — соотношение голос/музыка не меряю")
    elif not speech_frames:
        verdict("info", "speech", "речь не найдена — соотношение голос/музыка не меряю")
    out["verdicts"] = verdicts
    out["summary"] = summary(out)
    return out


def silence_after_cuts(gaps: list[tuple[float, float]], cuts: list[dict],
                       min_len: float | None = None) -> list[tuple[float, float]]:
    """Дыры тишины (шкала clip до вырезов) без того, что вырезано: от дыры остаются куски вне вырезов;
    куски одной дыры по обе стороны выреза в ролике стоят встык — их длина складывается. Остаётся то, что
    в готовом ролике длиннее ``min_len`` (SILENCE_MIN_SEC); время — по шкале clip, от первого куска до
    последнего."""
    min_len = SILENCE_MIN_SEC if min_len is None else min_len
    spans = sorted((float(c["start"]), float(c["end"])) for c in cuts or [] if float(c["end"]) > float(c["start"]))
    if not spans:
        return gaps
    out: list[tuple[float, float]] = []
    for a, b in gaps:
        bits, cursor = [], a
        for c0, c1 in spans:
            if c1 <= cursor or c0 >= b:
                continue
            if c0 > cursor:
                bits.append((cursor, c0))
            cursor = max(cursor, c1)
        if cursor < b:
            bits.append((cursor, b))
        if bits and sum(y - x for x, y in bits) >= min_len - 1e-6:
            out.append((round(bits[0][0], 2), round(bits[-1][1], 2)))
    return out


def _source_speechless(model: dict) -> bool:
    """Звук исходника без речи (игра, музыка) — «голосом» его не считаем."""
    return bool(model.get("speechless"))


# Кадр i momentary-громкости — окно 400 мс, кончающееся на (i+1)·0.1 с: середина окна — i·0.1 − 0.1.
FRAME_SHIFT = -0.1


def _frame_of(t: float) -> int:
    return int(round((t - FRAME_SHIFT) / FRAME_SEC))


def _worst_ranges(mask: list[bool], per: list[tuple[int, float]], n: int) -> list[dict]:
    diffs = dict(per)
    out = []
    for a, b in _ranges(mask, merge=0.3, shift=FRAME_SHIFT):
        i0, i1 = _frame_of(a), _frame_of(b)
        values = [diffs[i] for i in range(i0, i1) if i in diffs and mask[i]]
        if values and b - a >= 0.2:
            out.append({"start": round(max(0.0, a), 2), "end": round(b, 2), "diff_db": round(min(values), 1)})
    out.sort(key=lambda r: r["diff_db"])
    return out[:MAX_RANGES]


def summary(report: dict) -> str:
    warns = [v for v in report.get("verdicts") or [] if v["severity"] == "warn"]
    parts = []
    vm = report.get("voice_vs_music") or {}
    if vm.get("diff_db") is not None:
        parts.append(f"голос над музыкой {vm['diff_db']:+.1f} дБ под речью")
    final = report.get("final") or report.get("predicted") or {}
    if final.get("integrated") is not None:
        parts.append(f"{final['integrated']:.1f} LUFS" + (" (рендер)" if report.get("final") else " (прогноз)"))
    if final.get("true_peak") is not None:
        parts.append(f"пик {final['true_peak']:.1f} dBTP")
    head = "сведение в норме" if not warns else f"замечаний {len(warns)}: " + "; ".join(w["message"] for w in warns[:2])
    return head + (" — " + ", ".join(parts) if parts else "")


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
