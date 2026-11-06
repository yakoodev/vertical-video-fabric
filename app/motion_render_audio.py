"""Рендер слоёв: звук видео-слоёв и цепочки аудио-элементов (из ``app/motion_render.py``)."""

from __future__ import annotations

from pathlib import Path

from app.motion import VIDEO_MAX_SPEED, VIDEO_MIN_SPEED, VIDEO_TAIL

# Края звука-дорожки (звук video, audio mode track): короткий фейд, чтобы обрезка посреди
# волны не щёлкала. 15 мс — затухания не слышно, а щелчка нет.
TRACK_EDGE_FADE = 0.015


def _probe_sound(path: Path) -> tuple[float, bool]:
    """(длина файла, с; есть ли звук) — для звука video-слоя."""
    from app.montage_assets import probe_asset

    meta = probe_asset(path)
    return float(meta.get("duration_sec") or 0.0), bool(meta.get("has_audio"))


def video_sound_item(layer: dict, path: str, duration: float, file_duration: float | None,
                     has_audio: bool = True) -> tuple[dict | None, str | None]:
    """Звук video-слоя с ``props.audio``: что подмешать, или (None, пометка), почему тишина.

    Время — как у картинки (``video_file_time``): секунда файла = from + (t − start)·speed;
    за концом файла loop — снова с from, иначе тишина (freeze держит кадр, но не звук);
    hold — стоп-кадр, звука нет; speed вне VIDEO_AUDIO_MIN..MAX — звука нет (atempo дальше
    звучит кашей)."""
    from app.motion import VIDEO_AUDIO_MAX_SPEED, VIDEO_AUDIO_MIN_SPEED, track_gain

    props = layer.get("props") or {}
    lid = layer.get("id")
    if not has_audio:
        return None, f"слой {lid}: у видео нет звуковой дорожки — audio ничего не даст"
    if props.get("hold"):
        return None, None  # стоп-кадр — тишина, это ожидаемо
    speed = min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    if not (VIDEO_AUDIO_MIN_SPEED - 1e-6 <= speed <= VIDEO_AUDIO_MAX_SPEED + 1e-6):
        return None, (f"слой {lid}: speed {speed:g} вне {VIDEO_AUDIO_MIN_SPEED:g}..{VIDEO_AUDIO_MAX_SPEED:g} — "
                      "звук видео не играет")
    start = float(layer["start"])
    end = min(float(layer["end"]), float(duration))
    begin = max(0.0, float(props.get("from") or 0.0))
    if end - start < 0.05:
        return None, None
    length = end - start  # секунды клипа
    loop_span = None
    if file_duration and file_duration > 0:
        rest = file_duration - VIDEO_TAIL - begin
        if rest <= 0.05:
            return None, None  # from за концом файла — кадр стоит, звука нет
        if props.get("loop") and rest < length * speed:
            loop_span = rest
        else:
            length = min(length, rest / speed)
    if length < 0.05:
        return None, None
    return {
        "path": path, "start": start, "end": start + length, "len": length, "offset": begin,
        # Сколько секунд файла читать: с петлёй — до конца файла (петлю делает aloop).
        "read": None if loop_span else length * speed, "loop": loop_span, "tempo": speed,
        "volume": track_gain(props), "fade_in": TRACK_EDGE_FADE, "fade_out": TRACK_EDGE_FADE,
        "duck": False, "track": True,
    }, None


def _layer_fx(props: dict, role: str, mix: dict | None) -> dict:
    """Цепочка эффектов звука слоя: своя (``props.fx``), у голоса — ещё и общая голоса клипа
    (``mix.voice_fx``) после неё. Пусто — ``{}`` (цепочка ровно прежняя)."""
    chain = list(props.get("fx") or [])
    if role == "voice" and mix and mix.get("voice_fx"):
        chain += list(mix["voice_fx"])
    return {"fx": chain} if chain else {}


def audio_item_inputs(item: dict) -> list[str]:
    """Аргументы входа ffmpeg звука слоя: с какой секунды файла и сколько читать."""
    args: list[str] = []
    if item["offset"] > 0:
        args += ["-ss", f"{item['offset']:.3f}"]
    if item["read"] is not None:
        args += ["-t", f"{item['read']:.3f}"]
    return args + ["-i", item["path"]]


def audio_item_chain(n: int, item: dict, delay: bool = True) -> str:
    """Цепочка звука слоя со входа ``n`` (без выходной метки): громкость, фейды, сдвиг на ``start``."""
    length = item["len"]
    # Звуки пака — моно: без стерео на входе amix сводил ВСЁ в моно (клип 575: музыка
    # потеряла стерео, а громкость потом мерилась уже по моно-дорожке).
    chain = [f"[{n}:a]aresample=48000", "aformat=channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
    if item["loop"]:
        # Петля видео: от from до конца файла по кругу (как loop картинки).
        chain.append(f"aloop=loop=-1:size={max(1, int(round(item['loop'] * 48000)))}")
    if abs(item["tempo"] - 1.0) >= 1e-3:
        chain.append(f"atempo={item['tempo']:.4f}")
    if item["loop"] or abs(item["tempo"] - 1.0) >= 1e-3:
        # Ровно длина слоя в клипе (петля бесконечна, atempo округляет).
        chain += [f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS"]
    chain.append(f"volume={item['volume']:.4f}")
    if item.get("fx"):
        # Цепочка эффектов слоя (app/audio_fx.py) — после громкости, до фейдов: хвост ревёрба
        # затухает вместе со слоем; speed/tempo уже прочитали больше файла — длина ровно слоя.
        from app.audio_fx import chain_filters

        chain.append(chain_filters(item["fx"], f"l{n}_", length))
        chain += ["apad", f"atrim=0:{length:.3f}", "asetpts=PTS-STARTPTS"]
    if item["fade_in"] > 0:
        chain.append(f"afade=t=in:st=0:d={item['fade_in']:.3f}")
    if item["fade_out"] > 0:
        chain.append(f"afade=t=out:st={max(0.0, length - item['fade_out']):.3f}:d={item['fade_out']:.3f}")
    if delay:
        chain.append(f"adelay=delays={int(round(item['start'] * 1000))}:all=1")
    return ",".join(chain)
