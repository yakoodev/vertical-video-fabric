"""Сведение: настройка, ffmpeg, дорожки-стемы, модель клипа (из ``app/audio_mix.py``)."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

ROLES = ("voice", "music", "sfx", "ambience")


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
