"""Рендер: музыка клипа. Громкость под речь, цепочка фильтров, аргументы смешивания (``app/render.py``)."""

from __future__ import annotations

import math
from pathlib import Path

from app.render_exec import _ff_number
from app.render_loudness import end_fade_filter
from app.store import AppStore


def build_ffmpeg_music_args(input_path: Path, output_path: Path, music: dict) -> list[str]:
    """Mix a background music track over an already-rendered clip.

    The clip video is stream-copied (untouched); only audio is rebuilt. The
    music can loop to fill the clip, fade in/out, and duck under speech via a
    sidechain compressor keyed on the original audio.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    track_path = Path(music["track_path"])
    loop = bool(music.get("loop"))
    held = _music_hold(music) > 0
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
    ]
    if loop:
        args.extend(["-stream_loop", "-1"])
    args.extend(["-i", str(track_path)])
    args.extend(
        [
            "-filter_complex",
            _music_filter(music),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            # Со стоп-кадром звук длиннее картинки на hold_sec (хвост музыки): -shortest
            # его бы срезал. Картинку до той же длины дотянет финальный проход (tpad).
            *([] if held else ["-shortest"]),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return args


# Смещение старта музыки — не дальше часа трека.
MAX_MUSIC_START_SEC = 3600.0

# Фон — на столько тише голоса клипа (по громким местам), ещё до приглушения под
# речь. Громкость дорожки (volume 0.5 — обычный фон) сдвигает это относительно.
MUSIC_UNDER_SPEECH_DB = 16.0
MUSIC_NOMINAL_VOLUME = 0.5


def music_volume_for(clip_path: Path, music: dict) -> float:
    """Множитель громкости музыки от голоса ЭТОГО клипа, а не абсолютный.

    С абсолютным 0.5 готовый трек (−14 LUFS) под тихой записью голоса (−32 LUFS)
    выходил громче речи — как эффекты (см. motion_render.EFFECT_UNDER_SPEECH_DB).
    """
    from app.motion_render import audio_levels, speech_level_db

    if music.get("fixed_db") is not None:
        # Громкость выставило сведение (auto_mix / set_mix music_db): ровно она, без подгонки.
        return 10 ** (max(-40.0, min(12.0, float(music["fixed_db"]))) / 20)
    raw = music.get("volume")
    user = max(0.0, float(raw if raw is not None else MUSIC_NOMINAL_VOLUME))
    if user <= 0:
        return 0.0
    speech = speech_level_db(clip_path)
    # Громкость — того куска трека, что зазвучит (со смещением старта).
    track = audio_levels(music["track_path"], seconds=60, start=float(music.get("start_sec") or 0))
    if speech is None or not track:
        return user
    gain_db = speech - MUSIC_UNDER_SPEECH_DB - track[0] + 20 * math.log10(user / MUSIC_NOMINAL_VOLUME)
    return 10 ** (max(-40.0, min(6.0, gain_db)) / 20)


def _music_hold(music: dict) -> float:
    """Стоп-кадр, на котором доигрывает музыка (0 — без него или длина клипа неизвестна)."""
    try:
        hold = float(music.get("hold_sec") or 0)
        duration = float(music.get("duration_sec") or 0)
    except (TypeError, ValueError):
        return 0.0
    return hold if hold > 0 and duration > 0 and math.isfinite(hold) else 0.0


def _music_sidechain(music: dict, music_label: str, key_label: str, out_label: str) -> str:
    """Мягкий ducking музыки под речью (sidechain по звуку клипа): только заметная речь тянет
    музыку вниз, под ней она остаётся слышна. ``duck_amount`` — глубина."""
    duck_amount = max(0.0, min(1.0, float(music.get("duck_amount") if music.get("duck_amount") is not None else 0.6)))
    ratio = _ff_number(1.5 + duck_amount * 3.5)
    return (f"[{music_label}][{key_label}]sidechaincompress=threshold=0.1:ratio={ratio}:attack=20:release=400:"
            f"makeup=1[{out_label}]")


def music_wants_sidechain(music: dict) -> bool:
    """Sidechain под речью: включён в пресете (duck) и не снят сведением (mix.sidechain false)."""
    return bool(music.get("duck")) and music.get("sidechain") is not False


def music_stem_filter(music: dict) -> str:
    """Только музыка — как она прозвучит в ролике (громкость, старт, ключи, ducking по звуку
    клипа ``[0:a]``), без самого звука клипа: стем для замера сведения (app/audio_mix.py).
    Выход ``[aout]``."""
    _voice_chain, _voice, music_str = _music_chain({**music, "hold_sec": 0})
    if music_wants_sidechain(music):
        return f"{music_str};[0:a]aresample=48000[key];" + _music_sidechain(music, "music", "key", "aout")
    return f"{music_str};[music]anull[aout]"


def _music_filter(music: dict) -> str:
    """Граф музыки поверх клипа. ``hold_sec`` — стоп-кадр в конце: голос клипа
    затухает на своём конце (как end_fade_filter) и дотягивается тишиной, а музыка
    играет дальше и затухает к концу стоп-кадра — фейд кончается в самом конце файла."""
    _voice_chain, voice, music_str = _music_chain(music)
    if music_wants_sidechain(music):
        return (
            f"{music_str};"
            f"{voice}asplit=2[spk][key];"
            + _music_sidechain(music, "music", "key", "ducked") + ";"
            "[spk][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
        )
    return (
        f"{music_str};"
        f"{voice}[music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aresample=48000[aout]"
    )


def _music_chain(music: dict) -> tuple[str, str, str]:
    """(граф голоса со стоп-кадром — или пусто, метка голоса, граф музыки до ``[music]``; граф
    голоса уже входит в граф музыки)."""
    volume = max(0.0, float(music.get("volume") if music.get("volume") is not None else 0.25))
    fade_in = max(0.0, float(music.get("fade_in_sec") or 0))
    fade_out = max(0.0, float(music.get("fade_out_sec") or 0))
    duration = max(0.0, float(music.get("duration_sec") or 0))
    hold = _music_hold(music)
    voice = "[0:a]"
    voice_chain = ""
    if hold > 0:
        total = duration + hold
        speech_fade = end_fade_filter(duration)
        voice_chain = "[0:a]" + (speech_fade + "," if speech_fade else "") + f"apad=whole_dur={_ff_number(total)}[voice];"
        voice = "[voice]"
        # Затухание музыки — на весь стоп-кадр (или дольше, если так задано в пресете).
        fade_out = max(fade_out, hold)
        duration = total

    start_sec = max(0.0, float(music.get("start_sec") or 0))
    music_chain = [f"volume={_ff_number(volume)}"]
    if music.get("fx"):
        # Цепочка эффектов музыки клипа (render_settings.mix.music_fx, app/audio_fx.py): эквалайзер
        # (освободить место голосу), фильтр, ревёрб… — статично, на весь трек.
        from app.audio_fx import chain_filters

        music_chain.append(chain_filters(music["fx"], "mu"))
    if start_sec > 0:
        # Музыка с секунды трека start_sec: начало отрезается (с петлёй — только у первого круга).
        music_chain[:0] = [f"atrim=start={_ff_number(start_sec)}", "asetpts=PTS-STARTPTS"]
    if music.get("automation"):
        from app.audio_edit import music_volume_expr

        music_chain.append(f"volume='{music_volume_expr(music['automation'])}':eval=frame")
    music_chain[0] = "[1:a]" + music_chain[0]
    if fade_in > 0:
        music_chain.append(f"afade=t=in:st=0:d={_ff_number(fade_in)}")
    if fade_out > 0 and duration > 0:
        start = max(0.0, duration - fade_out)
        music_chain.append(f"afade=t=out:st={_ff_number(start)}:d={_ff_number(fade_out)}")
    return voice_chain, voice, voice_chain + ",".join(music_chain) + "[music]"


def _resolve_music_settings(store: AppStore, preset: dict) -> dict | None:
    track_id = preset.get("music_track_id")
    if not track_id or int(track_id) <= 0:
        return None
    track = store.get_audio_track(int(track_id))
    # Volume lives on the track itself; a per-render override (stashed by
    # _preset_with_music) wins when present.
    override = preset.get("music_volume_override")
    volume = override if override is not None else track.get("volume")
    if volume is None:
        volume = 0.5
    return {
        "track_path": track["file_path"],
        "volume": volume,
        "start_sec": preset.get("music_start_sec") or 0.0,
        "loop": bool(preset.get("music_loop", True)),
        "fade_in_sec": preset.get("music_fade_in_sec"),
        "fade_out_sec": preset.get("music_fade_out_sec"),
        "duck": bool(preset.get("music_duck", True)),
        "duck_amount": preset.get("music_duck_amount"),
        # Ключи, ручной ducking, провалы перед ударом (app/audio_edit.py), секунды готового ролика.
        "automation": preset.get("music_automation"),
        **_music_mix_fields(preset.get("mix")),
    }


def _music_mix_fields(mix: dict | None) -> dict:
    """Сведение клипа → поля прохода музыки: ``fixed_db`` (громкость трека к файлу, без подгонки под
    голос), ``sidechain`` false (ducking — отрезками), ``fx`` (цепочка эффектов музыки)."""
    out: dict = {}
    if not mix:
        return out
    if mix.get("music_db") is not None:
        out["fixed_db"] = float(mix["music_db"])
    if mix.get("sidechain") is False:
        out["sidechain"] = False
    if mix.get("music_fx"):
        out["fx"] = mix["music_fx"]
    return out
