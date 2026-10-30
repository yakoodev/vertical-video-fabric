"""PNGtuber: огибающая голоса → состояния, время клипа ↔ источника, анимации с альфой, схема,
инструмент агента и проход рендера (docs/motion/spec.md, раздел «PNGtuber»)."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from app import motion
from app.anim_frames import (
    ANIM_FRAME_PATTERN,
    anim_frame_width,
    animation_info,
    build_anim_frames_args,
    build_sheet_args,
    describe,
    prepare_anim_frames,
    sheet_grid,
)
from app.motion import normalize_layers, validate_layers
from app.pngtuber import (
    PCM_RATE,
    auto_threshold,
    default_placement,
    frame_sources,
    layer_voice,
    levels_for_points,
    pcm_frame_levels,
    plan_voice,
    state_at,
    voice_levels,
    voice_states,
)
from app.timebase import ClipClock

FPS = 30.0


# ------------------------------------------------------------------ синтетический звук


def _pcm(level_at, start: float, length: float, rate: int = PCM_RATE) -> bytes:
    """PCM 16 бит моно: синус 220 Гц амплитудой ``level_at(секунда)`` (0 — тишина)."""
    n = int(round(length * rate))
    out = []
    for i in range(n):
        t = start + i / rate
        amp = level_at(t)
        out.append(int(max(-1.0, min(1.0, amp * math.sin(2 * math.pi * 220 * t))) * 32767))
    return struct.pack(f"<{n}h", *out)


def _speech(t: float) -> float:
    """«Речь» в секундах источника: тон 14.0–15.0 (обычный голос), 16.0–16.5 — крик, иначе тишина."""
    if 14.0 <= t < 15.0:
        return 0.3  # RMS ≈ −13.5 дБ: говорит
    if 16.0 <= t < 16.5:
        return 0.95  # RMS ≈ −3.4 дБ: кричит
    return 0.0


def _reader(level_at=_speech):
    calls = []

    def read(_sid, start, length):
        calls.append((round(start, 3), round(length, 3)))
        return _pcm(level_at, start, length)

    read.calls = calls  # type: ignore[attr-defined]
    return read


# ------------------------------------------------------------------ огибающая → состояния


def test_states_threshold_hold_attack_shout():
    silence, talk, loud = -80.0, -25.0, -5.0
    lv = [silence] * 10 + [talk] * 10 + [silence] * 3 + [talk] * 5 + [loud] * 6 + [silence] * 20
    st = voice_states(lv, FPS, threshold_db=-38, hold=0.18, attack=0.03, shout_db=-12)
    s = "".join(map(str, st))
    assert s[:10] == "0" * 10  # тишина — молчит
    assert s[10:20] == "1" * 10  # голос над порогом — сразу говорит (attack 1 кадр)
    assert s[20:23] == "111"  # пауза 0.1 с короче hold 0.18 — рот не хлопает
    assert s[28:34] == "222222"  # громче shout_db — кричит
    assert s[34:34 + 5] == "22222"  # крик тоже держится hold (5 кадров)
    assert s[-10:] == "0" * 10  # после hold — молчит
    # Ниже порога — не говорит; порог выше голоса — молчит весь клип.
    assert set(voice_states(lv, FPS, threshold_db=-3, shout_db=None)) == {0}
    # Без shout_db (нет состояния shout) крик — просто «говорит».
    assert 2 not in voice_states(lv, FPS, shout_db=None)


def test_attack_ignores_clicks_and_hold_zero_closes_at_once():
    # Щелчок клавиатуры на 1 кадр с attack 0.1 с (3 кадра) рот не открывает.
    lv = [-80.0] * 5 + [-20.0] + [-80.0] * 5 + [-20.0] * 4 + [-80.0] * 5
    st = voice_states(lv, FPS, attack=0.1, hold=0.0)
    assert st[5] == 0
    assert st[11:15] == [0, 0, 1, 1]  # открылся на 3-м кадре голоса
    assert st[15] == 0  # hold 0 — закрылся сразу
    assert voice_states([], FPS) == []
    assert voice_states([None, None], FPS) == [0, 0]  # звука нет — молчит


def test_auto_threshold_from_noise_floor():
    noise = [-62.0 + (i % 3) for i in range(60)]
    voiced = [-22.0 + (i % 4) for i in range(40)]
    thr = auto_threshold(noise + voiced)
    assert -55 < thr < -40  # над шумом микрофона и ниже голоса
    st = voice_states(noise + voiced, FPS, threshold_db=thr, hold=0)
    assert set(st[:60]) == {0} and set(st[60:]) == {1}
    assert auto_threshold([]) == motion.VOICE_THRESHOLD_DB == -45.0
    # Шумный микрофон (−40) — порог поднимается вместе с шумом: шум + 12, речь −15 − 12 выше.
    assert auto_threshold([-40.0] * 50 + [-15.0] * 50) == -28.0


def test_auto_threshold_catches_quiet_phrase_and_ignores_empty_mic():
    """Клипы 583–585: при −38 тихие фразы (−42 дБ) рот не открывали; "auto" (не выше речи − 12) — открывают."""
    noise = [-63.0 + (i % 3) for i in range(80)]
    quiet = [-42.0 + (i % 2) for i in range(20)]
    thr = auto_threshold(noise + quiet)
    assert -55.0 <= thr <= -42.0
    st = voice_states(noise + quiet, FPS, threshold_db=thr, hold=0)
    assert set(st[:80]) == {0} and set(st[80:]) == {1}
    # Очень тихий микрофон (шум −80): порог не ниже −55 — шорохи рот не открывают.
    assert auto_threshold([-80.0] * 50 + [-30.0] * 10) == -55.0
    # Сплошная речь (окно без пауз) — порог на 12 дБ ниже неё; ровный тихий шум — над ним.
    assert auto_threshold([-20.0] * 60) == -32.0
    assert auto_threshold([-70.0] * 60) == -55.0


def _aligned_mic(n_cycles: int = 6):
    """Выровненный микрофон по кадрам 30 к/с: фраза 1.5 с речи (−26 ± 2 дБ), пауза 1.5 с — шум
    −48 ± 1 и вдохи до −41 (выше старого потолка −40 почти вплотную)."""
    levels: list[float] = []
    for _ in range(n_cycles):
        levels += [-26.0 + (k % 5) - 2 for k in range(45)]
        levels += [(-41.0 if k % 15 == 7 else -48.0 + (k % 3) - 1) for k in range(45)]
    return levels


def test_auto_threshold_follows_speech_on_aligned_mic_and_keeps_pauses_closed():
    """Выровненный микрофон: шум −48, речь −26 → порог ≈ −38 (речь − 12), в паузах рот закрыт.
    С потолком −40 вдохи (−41…−40) и шум у самого порога держали рот открытым в тишине."""
    levels = _aligned_mic()
    thr = auto_threshold(levels)
    assert thr == pytest.approx(-38.0, abs=1.0)
    st = voice_states(levels, FPS, threshold_db=thr)  # hold по умолчанию
    hold_n = round(motion.VOICE_HOLD_SEC * FPS)
    for cycle in range(6):
        speech = st[cycle * 90: cycle * 90 + 45]
        pause = st[cycle * 90 + 45 + hold_n + 1: cycle * 90 + 90]
        assert speech[2:] == [1] * 43  # говорит (после attack)
        assert set(pause) == {0}, cycle  # молчит — рот закрыт
    assert motion.VOICE_HOLD_SEC == pytest.approx(0.15)


def test_auto_threshold_quiet_mic_still_opens_the_mouth():
    """Тихий микрофон: речь −42 при шуме −60 — рот открывается (порог под речью, не выше −40 по шуму)."""
    levels: list[float] = []
    for _ in range(5):
        levels += [-42.0 + (k % 3) - 1 for k in range(30)] + [-60.0 + (k % 3) - 1 for k in range(60)]
    thr = auto_threshold(levels)
    assert -55.0 <= thr <= -50.0
    st = voice_states(levels, FPS, threshold_db=thr, hold=0)
    assert all(st[c * 90 + 2: c * 90 + 30] == [1] * 28 for c in range(5))
    assert all(set(st[c * 90 + 31: c * 90 + 90]) == {0} for c in range(5))


def test_auto_threshold_on_gated_mic_sits_under_speech():
    """Шумодав (гейт OBS): между фразами цифровой ноль — «шума» нет, порог — речь − 12."""
    from app.pngtuber import SILENCE_DB, gated

    levels = ([SILENCE_DB] * 60 + [-26.0 + (k % 3) - 1 for k in range(30)] + [-44.0] * 5) * 3
    assert gated(levels) and not gated(_aligned_mic())
    thr = auto_threshold(levels)
    assert thr == pytest.approx(-38.0, abs=1.0)
    st = voice_states(levels, FPS, threshold_db=thr, hold=0)
    assert st[62:90] == [1] * 28 and set(st[90:95]) == {0}  # хвост гейта (−44) рот не открывает


def test_open_outside_speech_pct_against_subtitle_words():
    from app.pngtuber import open_outside_speech_pct

    track = {"t0": 0.0, "fps": 10.0, "states": "0011110000" + "1111111111"}  # 2 с по 10 кадров
    words = [{"t0": 0.2, "t1": 0.55, "w": "да"}]
    # Речь 0.05–0.7 с (слово ± 0.15): кадры 2–5 внутри, вторая секунда — рот открыт без слов.
    assert open_outside_speech_pct(track, words) == 50.0
    assert open_outside_speech_pct(track, words + [{"t0": 1.0, "t1": 1.85, "w": "нет"}]) == 0.0
    assert open_outside_speech_pct(track, []) is None and open_outside_speech_pct(None, words) is None


def test_default_threshold_is_auto_and_window_uses_whole_layer():
    """Порог по умолчанию — auto; кадр агента (окно) берёт порог со всего слоя, как рендер."""
    from app.pngtuber import voice_config

    layer = _layer()
    assert "threshold_db" not in layer["props"]["voice"]  # не задан — auto, а не число
    assert voice_config(layer["props"])["threshold_db"] == motion.VOICE_AUTO

    def level(t):  # шум −50 дБ, тихая фраза 14.0–15.0 (−30 дБ)
        return 0.045 if 14.0 <= t < 15.0 else 0.0045

    clock = ClipClock(SEGS, None)
    points = lambda times: frame_sources(clock, SEGS, times, "clip")  # noqa: E731
    whole = layer_voice(layer, FPS, read=_reader(level), points_of=points)
    assert whole["threshold_auto"] and whole["noise_db"] == pytest.approx(-50, abs=1.5)
    assert -45 <= whole["threshold_db"] <= -40
    # Окно целиком внутри фразы: по окну порог вышел бы −40 (потолок), со всего слоя — как у рендера.
    win = layer_voice(layer, FPS, read=_reader(level), window=(4.2, 4.8), points_of=points)
    assert win["threshold_db"] == whole["threshold_db"]
    assert state_at(win, 4.5) == 1


def test_levels_envelope_for_bounce():
    lv = [-80.0] * 3 + [-26.0] * 4 + [-80.0] * 10
    st = voice_states(lv, FPS, hold=0.0)
    env = voice_levels(lv, st, FPS, -38, None)
    assert env[:3] == [0, 0, 0]
    assert 40 <= env[3] <= 60  # 12 дБ над порогом из 24 — половина
    assert env[7] < env[6] and env[-1] <= 1  # после речи плавно гаснет


def test_pcm_levels_on_synthetic_tone_and_silence():
    pcm = _pcm(_speech, 13.0, 4.0)
    times = [13.5, 14.5, 15.5, 16.2]
    lv = pcm_frame_levels(pcm, PCM_RATE, 13.0, times, FPS)
    assert lv[0] <= -89 and lv[2] <= -89
    assert lv[1] == pytest.approx(-13.5, abs=1.0)
    assert lv[3] == pytest.approx(-3.4, abs=1.0)


# ------------------------------------------------------------------ время клипа ↔ источника


SEGS = [{"start_sec": 10.0, "end_sec": 20.0, "source_id": 1}]


def _layer(start=0.0, end=8.0, **voice):
    return normalize_layers([{"id": "av", "type": "pngtuber", "start": start, "end": end,
                              "props": {"states": {"idle": 1, "talk": 2, "shout": 3}, "voice": voice}}])[0]


def test_frame_sources_follow_cuts_and_pieces():
    clock = ClipClock(SEGS, None, [{"start": 1.0, "end": 3.0}], FPS)  # вырезаны 11–13 с источника
    assert clock.final_duration == 8.0
    final = frame_sources(clock, SEGS, [0.5, 2.0, 7.9, 9.0], "final")
    assert final[:3] == [(1, 10.5), (1, 14.0), (1, 19.9)] and final[3] is None  # за концом — нет звука
    # Шкала clip (до вырезов) — без сдвига.
    assert frame_sources(clock, SEGS, [2.0], "clip") == [(1, 12.0)]
    # Два куска разных мест источника: второй — со своей секунды.
    two = [{"start_sec": 0.0, "end_sec": 5.0}, {"start_sec": 40.0, "end_sec": 45.0}]
    assert frame_sources(ClipClock(two, None), two, [1.0, 6.0], "clip", default_source=7) == [(7, 1.0), (7, 41.0)]


def test_layer_voice_after_cuts_opens_mouth_on_the_voice():
    """С вырезом 11–13 с голос источника 14–15 с звучит на 2–3 с готового ролика — там и рот."""
    plan = {"source_id": 1, "segments": SEGS, "render_settings": {"cuts": [{"start": 1.0, "end": 3.0}]}}
    clock = ClipClock.for_plan(plan, FPS)
    track = layer_voice(_layer(), FPS, read=_reader(),
                        points_of=lambda times: frame_sources(clock, SEGS, times, "final"))
    assert track["fps"] == FPS and len(track["states"]) == 240
    assert state_at(track, 1.5) == 0 and state_at(track, 2.5) == 1 and state_at(track, 3.5) == 0
    assert state_at(track, 4.2) == 2  # крик (16.0–16.5 источника)
    # Без учёта вырезов (шкала clip) голос был бы на 4–5 с — проверка, что шкалы различаются.
    plain = layer_voice(_layer(), FPS, read=_reader(),
                        points_of=lambda times: frame_sources(clock, SEGS, times, "clip"))
    assert state_at(plain, 2.5) == 0 and state_at(plain, 4.5) == 1


def test_layer_voice_window_and_asset_voice():
    plan_clock = ClipClock(SEGS, None)
    read = _reader()
    # Порог задан числом — читается только окно (порог "auto" слушает весь слой, см. ниже).
    win = layer_voice(_layer(threshold_db=-38), FPS, read=read, window=(3.5, 4.6),
                      points_of=lambda times: frame_sources(plan_clock, SEGS, times, "clip"))
    assert win["t0"] == pytest.approx(3.5) and len(win["states"]) == 33
    assert state_at(win, 4.5) == 1 and state_at(win, 3.6) == 0
    assert read.calls and all(length < 2.0 for _s, length in read.calls)  # читается только окно
    # Голос из файла: секунда файла = время от start слоя (start 10 → файл 4.5 с на 14.5 клипа).
    asset_layer = _layer(start=10.0, end=20.0, asset_id=9)
    track = layer_voice(asset_layer, FPS, read=_reader(lambda t: 0.3 if 4.0 <= t < 5.0 else 0.0),
                        points_of=lambda times: pytest.fail("у голоса из файла источник не нужен"))
    assert state_at(track, 14.5) == 1 and state_at(track, 12.0) == 0


def test_levels_read_in_spans_not_per_frame():
    clock = ClipClock([{"start_sec": 0.0, "end_sec": 5.0}, {"start_sec": 40.0, "end_sec": 45.0}], None)
    segs = clock.segments
    times = [k / FPS for k in range(300)]
    read = _reader(lambda t: 0.0)
    levels_for_points(frame_sources(clock, segs, times, "clip", 1), FPS, read)
    assert len(read.calls) == 2  # по куску источника на кусок клипа


# ------------------------------------------------------------------ схема, нормализация, линт


def test_pngtuber_schema_normalization_and_validation():
    layer = normalize_layers([{"type": "pngtuber", "props": {
        "states": {"idle": 5, "talk": "6"}, "voice": {"track": "1", "threshold_db": "auto"}, "bounce": True}}])[0]
    props = layer["props"]
    assert props["states"] == {"idle": "5", "talk": "6"}
    assert props["voice"] == {"track": 1, "threshold_db": "auto", "hold": 0.15, "attack": 0.03, "shout_db": -12.0}
    assert props["w"] == 300 and props["bounce"] == {"amount": 10} and "pixelated" not in props  # pixelated — авто
    assert motion.layer_asset_refs(layer) == [5, 6]
    with_voice_file = normalize_layers([{"type": "pngtuber", "props": {"states": {"idle": 1, "talk": 2, "shout": 3},
                                                                       "voice": {"asset_id": 4}}}])[0]
    assert motion.layer_asset_refs(with_voice_file) == [1, 2, 3, 4]
    problems = validate_layers([{"type": "pngtuber", "props": {"states": {"idle": 5}, "voice": {"track": 9}}}])
    assert any("states.idle" in p and "talk" in p for p in problems)
    assert any("voice.track" in p for p in problems)
    assert validate_layers([layer]) == []  # нормализованный слой чист
    schema = motion.schema_summary()
    assert "pngtuber" in schema["types"] and "voice" in schema["props"]["pngtuber"]
    assert schema["pngtuber"]["states"]
    assert motion.has_pngtuber([layer]) and not motion.has_pngtuber([{**layer, "hidden": True}])


def test_old_layers_unchanged_by_pngtuber():
    """Обратная совместимость: старые слои нормализуются как раньше, comp без PNGtuber — без voice."""
    from app.motion_render import build_compositor_args, write_comp

    old = [{"id": "t", "type": "text", "start": 0, "end": 2, "props": {"text": "Привет"}},
           {"id": "g", "type": "image", "start": 0, "end": 2, "props": {"asset_id": 3, "w": 400}}]
    norm = normalize_layers(old)
    assert set(norm[1]["props"]) == {"asset_id", "src", "w", "radius", "border", "crop", "flipX"}
    assert not motion.has_pngtuber(norm)
    return_path = write_comp(Path(__import__("tempfile").mkdtemp()) / "c.json", width=108, height=192, fps=30,
                             duration=2, layers=norm, camera=[])
    comp = json.loads(return_path.read_text(encoding="utf-8"))
    assert "voice" not in comp
    args = build_compositor_args(return_path, return_path, command=["node", "x.mjs"])
    assert "--anims" not in args
    args = build_compositor_args(return_path, return_path, command=["node", "x.mjs"], anims_path=Path("a.json"))
    assert args[args.index("--anims") + 1] == "a.json"


def test_lint_and_summary_know_pngtuber():
    from app.agent_tools import _layer_box, _layer_summary, cover_share, lint_layers
    from app.aspect import layer_size

    layer = _layer()
    assert _layer_box(layer)[0] == pytest.approx(300 / 1920 / 2)
    assert "PNGtuber" in _layer_summary(layer)
    assert cover_share(layer) == 0.0  # прозрачный аватар видео не «закрывает»
    assert layer_size(layer) == (300.0, 300.0)
    assert layer_size(layer, {"1": (112, 224)}) == (300.0, 600.0)
    assert isinstance(lint_layers([layer], 8.0), list)


def test_default_placement_above_safe_zone_and_left_of_watermark():
    p = default_placement("9:16")
    assert p["w"] == 300
    assert p["x"] + 150 / 1080 <= 1.0 and p["y"] + 150 / 1920 <= 0.80  # над подписью/кнопками площадки
    land = default_placement("16:9")
    assert land["x"] + land["w"] / 2 / 1920 <= 0.86  # не в водяном знаке канала


# ------------------------------------------------------------------ анимации с альфой


def _probe_gif(frames=5, delay=0.06):
    def probe(_path):
        return {"streams": [{"codec_name": "gif", "width": 112, "height": 112, "pix_fmt": "bgra"}],
                "frames": [{"duration_time": str(delay)} for _ in range(frames)]}
    return probe


def test_animation_info_gif_apng_webm_and_static(tmp_path):
    gif = tmp_path / "a.gif"
    gif.write_bytes(b"GIF89a")
    info = animation_info(gif, probe=_probe_gif())
    assert info["animated"] and info["frames"] == 5 and info["fps"] == pytest.approx(16.667, abs=0.01)
    assert info["times"] == [0.0, 0.06, 0.12, 0.18, 0.24] and info["duration"] == pytest.approx(0.3)
    assert info["alpha"] and info["decoder"] is None
    assert "5 кадров" in describe(info) and "16.667 fps" in describe(info)
    webm = tmp_path / "a.webm"
    webm.write_bytes(b"x")
    alpha = animation_info(webm, probe=lambda _p: {
        "streams": [{"codec_name": "vp9", "width": 64, "height": 64, "pix_fmt": "yuv420p", "tags": {"alpha_mode": "1"}}],
        "frames": [{"duration_time": "0.04"}] * 3})
    assert alpha["animated"] and alpha["decoder"] == "libvpx-vp9"
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"x")
    plain = animation_info(mp4, probe=lambda _p: {"streams": [{"codec_name": "h264", "width": 64, "height": 64}],
                                                  "frames": [{"duration_time": "0.04"}] * 3})
    assert not plain["animated"]  # обычное видео — дело video-слоя
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    assert not animation_info(png, probe=lambda _p: {"streams": [{"codec_name": "png"}], "frames": [{}]})["animated"]


def test_animated_webp_detected_but_not_decodable(tmp_path):
    def chunk(tag, body):
        return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")

    vp8x = bytes([0x10 | 0x02, 0, 0, 0]) + (111).to_bytes(3, "little") + (111).to_bytes(3, "little")
    anmf = lambda ms: chunk(b"ANMF", bytes(12) + ms.to_bytes(3, "little") + bytes(1))  # noqa: E731
    body = b"WEBP" + chunk(b"VP8X", vp8x) + chunk(b"ANIM", bytes(6)) + anmf(60) + anmf(60) + anmf(60)
    path = tmp_path / "a.webp"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    info = animation_info(path, probe=lambda _p: pytest.fail("WebP разбирается сам"))
    assert info["animated"] and info["frames"] == 3 and info["width"] == 112 and info["alpha"]
    assert info["decodable"] is False and "GIF или APNG" in describe(info)


def test_anim_frames_args_keep_alpha_and_every_frame(tmp_path):
    info = {"frames": 5, "width": 112, "height": 112, "decoder": None}
    args = build_anim_frames_args(Path("a.gif"), tmp_path, info, 112)
    assert args[args.index("-fps_mode") + 1] == "passthrough"  # GIF с разными задержками — кадр в кадр
    assert args[args.index("-pix_fmt") + 1] == "rgba" and args[args.index("-c:v") + 1] == "png"
    assert "-vf" not in args  # пиксель-арт не пережимается
    assert args[-1].endswith(ANIM_FRAME_PATTERN)
    webm = build_anim_frames_args(Path("a.webm"), tmp_path, {**info, "width": 1920, "decoder": "libvpx-vp9"}, 600)
    assert webm[webm.index("-c:v") + 1] == "libvpx-vp9" and webm.index("-c:v") < webm.index("-i")
    assert "scale=600:-2" in webm[webm.index("-vf") + 1]
    assert anim_frame_width({"width": 112}, 900) == 112
    assert anim_frame_width({"width": 1920}, 300) == 344  # 300 × запас, чётная
    grid = sheet_grid(5, 112, 112)
    assert (grid["cols"], grid["rows"], grid["frame_w"]) == (3, 2, 112)
    assert sheet_grid(400, 1920, 1080)["frame_w"] * 20 <= 2048
    sheet = build_sheet_args(Path("a.gif"), tmp_path / "s.png", info, grid)
    vf = sheet[sheet.index("-vf") + 1]
    assert "flags=neighbor" in vf and "tile=3x2" in vf and "color=0x00000000" in vf


def test_prepare_anim_frames_manifest_only_for_animations(tmp_path):
    files = {k: tmp_path / f"{k}.gif" for k in ("1", "2", "3")}
    for f in files.values():
        f.write_bytes(b"GIF89a")
    infos = {str(files["1"]): {"animated": True, "frames": 5, "times": [0, 0.06, 0.12, 0.18, 0.24], "duration": 0.3,
                               "width": 112, "height": 112},
             str(files["2"]): {"animated": False, "frames": 1},
             str(files["3"]): {"animated": True, "frames": 3, "decodable": False, "times": [0, .1, .2], "duration": .3}}
    ran = []

    def run(args, timeout):
        ran.append(args)
        out = Path(args[-1]).parent
        for i in range(4):  # ffmpeg отдал меньше кадров, чем в метаданных — времена по факту
            (out / f"{i:05d}.png").write_bytes(b"png")

    layers = normalize_layers([
        {"id": "av", "type": "pngtuber", "props": {"states": {"idle": 1, "talk": 2}}},
        {"id": "w", "type": "image", "props": {"asset_id": 3}},
        {"id": "h", "type": "image", "hidden": True, "props": {"asset_id": 1}},
    ])
    path, notes = prepare_anim_frames(layers, {k: str(v) for k, v in files.items()}, tmp_path, out_width=1080,
                                      run=run, info_of=lambda p: infos[str(p)])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert list(manifest) == ["1"] and len(ran) == 1
    assert manifest["1"]["count"] == 4 and manifest["1"]["times"] == [0, 0.06, 0.12, 0.18]
    assert manifest["1"]["duration"] == pytest.approx(0.24) and manifest["1"]["pattern"] == ANIM_FRAME_PATTERN
    assert any("WebP" in n for n in notes)
    assert prepare_anim_frames([], {}, tmp_path, out_width=1080, run=run) == (None, [])


def test_real_gif_frames_with_alpha(tmp_path):
    """Настоящий GIF (ffmpeg): кадры раскладываются в PNG RGBA, прозрачные пиксели — прозрачные."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("нет ffmpeg")
    gif = tmp_path / "a.gif"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red@0.0:s=16x16:d=0.2:r=10,format=rgba",
                    "-f", "lavfi", "-i", "color=blue:s=8x8:d=0.2:r=10",
                    "-filter_complex", "[0][1]overlay=4:4,split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128",
                    str(gif)], check=True)
    info = animation_info(gif)
    assert info["animated"] and info["frames"] == 2 and info["alpha"]
    run = lambda args, timeout: subprocess.run(args, check=True, timeout=timeout)  # noqa: E731
    path, notes = prepare_anim_frames(normalize_layers([{"id": "a", "type": "pngtuber",
                                                         "props": {"states": {"idle": 1, "talk": 1}}}]),
                                      {"1": str(gif)}, tmp_path, out_width=1080, run=run)
    assert not notes
    entry = json.loads(path.read_text(encoding="utf-8"))["1"]
    assert entry["count"] == 2 and entry["times"] == [0.0, 0.1]
    first = Path(entry["dir"]) / "00000.png"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(first), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    assert raw[3] == 0  # угол прозрачный
    center = (8 * 16 + 8) * 4
    assert raw[center + 3] == 255 and raw[center + 2] > 200  # синий квадрат непрозрачный


# ------------------------------------------------------------------ инструмент агента и рендер


def _plan_with_assets(tmp_path, monkeypatch, streams=2):
    from test_motion import _render_fixture

    store, service, plan, banner, calls, comps, kwargs = _render_fixture(tmp_path, monkeypatch)
    ids = []
    for name in ("idle", "talk"):
        path = tmp_path / f"{name}.gif"
        path.write_bytes(b"GIF89a")
        ids.append(store.create_montage_asset(kind="gif", label=name, file_path=str(path), width=112, height=112)["id"])
    meta = {"streams": [{"codec_type": "video"}] + [{"codec_type": "audio"}] * streams}
    store.update_source(plan["source_id"], metadata=meta)
    return store, service, plan, ids, comps, kwargs, calls


def test_add_pngtuber_tool_defaults_mic_track_and_checks_voice(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    store, _service, plan, (idle, talk), *_ = _plan_with_assets(tmp_path, monkeypatch)
    monkeypatch.setattr("app.pngtuber.source_reader", lambda store, track, notes: _reader(
        lambda t: 0.3 if 1.0 <= t < 2.0 else 0.0))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "bounce": 8})
    assert ok, out
    layer = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert layer["type"] == "pngtuber" and layer["start"] == 0 and layer["end"] == pytest.approx(5.0)
    assert layer["props"]["voice"]["track"] == 1  # у записи 2 дорожки — микрофон
    assert layer["props"]["w"] == 300 and layer["x"] > 0.8 and layer["y"] < 0.8
    assert layer["props"]["bounce"] == {"amount": 8}
    assert "дорожка 1" in out["voice"]
    assert out["voice_check"]["talking_share"] == pytest.approx(1.18 / 5, abs=0.05)  # 1 с голоса + hold
    bad, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "track": 5})
    assert not ok and "дорожек" in bad["error"]
    bad, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle})
    assert not ok and "talk_asset" in bad["error"]


def test_voice_check_reports_mouth_open_outside_subtitle_speech(tmp_path, monkeypatch):
    """Рот открыт, пока стример молчит (шум/дыхание над порогом), — voice_check это видит по словам
    субтитров клипа (± 0.15 с) и подсказывает поднять порог."""
    from app.agent_tools import ToolContext, run_tool

    store, _service, plan, (idle, talk), *_ = _plan_with_assets(tmp_path, monkeypatch)
    # Речь 1–2 с (−13.5 дБ), шум −50 дБ, «вдох» 3.0–3.6 с на −27 дБ — громче порога, но слов там нет.
    monkeypatch.setattr("app.pngtuber.source_reader", lambda store, track, notes: _reader(
        lambda t: 0.3 if 1.0 <= t < 2.0 else (0.06 if 3.0 <= t < 3.6 else 0.0045)))
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 1.0, "end": 2.0, "text": "привет всем"}],
        "words": [{"word": "привет", "start": 1.0, "end": 1.5}, {"word": "всем", "start": 1.5, "end": 2.0}],
    })
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "id": "av"})
    assert ok, out
    check = out["voice_check"]
    assert check["open_outside_speech_pct"] == pytest.approx(0.75 / 5 * 100, abs=3)  # вдох + hold
    assert "вне речи" in check["hint"] and "threshold_db выше" in check["hint"]
    assert "речи" in check["threshold"]
    # Порог выше вдоха — вне речи рот закрыт, подсказки нет.
    out, ok = run_tool(ctx, "update_layer", {"id": "av", "patch": {"props": {"voice": {"track": 1,
                                                                                       "threshold_db": -22}}}})
    assert ok, out
    assert out["voice_check"]["open_outside_speech_pct"] == 0.0 and out["voice_check"]["hint"] == "ок"
    # Без субтитров — не с чем сверить.
    store.set_clip_plan_subtitles(plan["id"], None)
    out, ok = run_tool(ctx, "update_layer", {"id": "av", "patch": {"x": 0.7}})
    assert ok and out["voice_check"]["open_outside_speech_pct"] is None


def test_add_pngtuber_replaces_instead_of_stacking(tmp_path, monkeypatch):
    """Клипы 583–585: add_pngtuber с id (и без) молча добавлял второй аватар — теперь заменяет."""
    from app.agent_tools import ToolContext, run_tool

    store, _service, plan, (idle, talk), *_ = _plan_with_assets(tmp_path, monkeypatch)
    monkeypatch.setattr("app.pngtuber.source_reader", lambda store, track, notes: _reader(
        lambda t: 0.3 if 1.0 <= t < 2.0 else 0.0))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    def tubers():
        return [l for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"] if l["type"] == "pngtuber"]

    run_tool(ctx, "add_layers", {"layers": [{"id": "title", "type": "text", "start": 0, "end": 2, "x": 0.5, "y": 0.2,
                                             "props": {"text": "ЧЕМПИОН"}}]})
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "id": "av"})
    assert ok, out
    assert [l["id"] for l in tubers()] == ["av"]
    # Тот же id — замена (новые x и порог), а не второй слой с другим id.
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "id": "av", "x": 0.2,
                                             "threshold_db": -50})
    assert ok, out
    assert out["replaced"]["id"] == "av" and "voice_check" in out
    assert [(l["id"], l["x"]) for l in tubers()] == [("av", 0.2)]
    assert tubers()[0]["props"]["voice"]["threshold_db"] == -50
    # Без id — заменяется аватар клипа (один на клип по умолчанию); порядок слоёв тот же.
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": talk, "talk_asset": idle})
    assert ok, out
    assert [l["id"] for l in tubers()] == ["av"] and tubers()[0]["props"]["states"]["idle"] == str(talk)
    assert [l["id"] for l in store.get_clip_plan(plan["id"])["render_settings"]["layers"]] == ["title", "av"]
    # allow_multiple — второй аватар.
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "allow_multiple": True})
    assert ok, out
    assert len(tubers()) == 2 and any("2 аватара" in w for w in out["warnings"])
    # id чужого слоя (текст) — ошибка, а не подмена титра аватаром.
    bad, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "id": "title"})
    assert not ok and "не PNGtuber" in bad["error"]


def test_update_layer_returns_voice_check(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    store, _service, plan, (idle, talk), *_ = _plan_with_assets(tmp_path, monkeypatch)
    # Шум −50 дБ, голос 1–2 с на −30 дБ.
    monkeypatch.setattr("app.pngtuber.source_reader", lambda store, track, notes: _reader(
        lambda t: 0.045 if 1.0 <= t < 2.0 else 0.0045))
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_pngtuber", {"idle_asset": idle, "talk_asset": talk, "id": "av"})
    assert ok, out
    check = out["voice_check"]
    assert check["talking_share"] == pytest.approx(1.18 / 5, abs=0.05)
    assert check["threshold"].startswith("auto") and -45 <= check["threshold_db"] <= -40
    # Порог ниже шума — рот открыт всё время; ответ update_layer сразу это показывает.
    out, ok = run_tool(ctx, "update_layer", {"id": "av", "patch": {"props": {"voice": {"track": 1,
                                                                                       "threshold_db": -60}}}})
    assert ok, out
    assert out["voice_check"]["talking_share"] > 0.9 and out["voice_check"]["threshold"] == "задан: -60 дБ"
    assert "выше" in out["voice_check"]["hint"]
    # Не PNGtuber — без voice_check.
    run_tool(ctx, "add_layers", {"layers": [{"id": "t", "type": "text", "start": 0, "end": 1, "props": {"text": "А"}}]})
    out, ok = run_tool(ctx, "update_layer", {"id": "t", "patch": {"x": 0.4}})
    assert ok and "voice_check" not in out


def test_render_passes_voice_and_anims_to_compositor(tmp_path, monkeypatch):
    store, service, plan, (idle, talk), comps, kwargs, calls = _plan_with_assets(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(plan["id"], {"layers": [
        {"id": "av", "type": "pngtuber", "start": 0, "end": 5,
         "props": {"states": {"idle": idle, "talk": talk}, "voice": {"track": 1}}}]})
    seen: list[tuple] = []

    def reader(store_, track, notes):
        seen.append(track)
        return _reader(lambda t: 0.3 if 2.0 <= t < 3.0 else 0.0)

    monkeypatch.setattr("app.pngtuber.source_reader", reader)
    monkeypatch.setattr("app.anim_frames.animation_info", lambda p: {
        "animated": True, "frames": 2, "times": [0.0, 0.06], "duration": 0.12, "width": 112, "height": 112})
    clip = service.render_clip_plan(plan["id"], **kwargs)
    assert clip["status"] == "succeeded", clip.get("error")
    comp = comps[0]
    assert seen == [1]
    track = comp["voice"]["av"]
    assert track["fps"] == 25.0 and state_at(track, 2.5) == 1 and state_at(track, 1.0) == 0
    compositor = next(args for kind, args in calls if kind == "compositor")
    assert "--anims" in compositor


def test_voice_api_for_preview(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module

    store, _service, plan, (idle, talk), *_ = _plan_with_assets(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr("app.pngtuber.source_reader", lambda store_, track, notes: _reader(
        lambda t: 0.3 if 1.0 <= t < 1.5 else 0.0))
    monkeypatch.setattr("app.settings.settings.auth_enabled", False)
    client = TestClient(main_module.app)
    headers: dict = {}
    layers = [{"id": "av", "type": "pngtuber", "start": 0, "end": 3, "props": {"states": {"idle": idle, "talk": talk}}}]
    res = client.post(f"/api/clip-plans/{plan['id']}/voice", json={"layers": layers, "fps": 30}, headers=headers)
    assert res.status_code == 200, res.text
    track = res.json()["voice"]["av"]
    assert len(track["states"]) == 90 and state_at(track, 1.2) == 1 and state_at(track, 2.5) == 0
    anim = client.get(f"/api/montage-assets/{idle}/anim", headers=headers)
    assert anim.status_code == 200 and anim.json() == {"animated": False}  # «GIF» без кадров — статичный


def test_frame_durations_from_old_ffprobe_fields():
    """ffprobe 5.1 (контейнер) отдаёт pkt_duration_time вместо duration_time — 30 fps не должно стать 10."""
    from app.anim_frames import _frame_durations, animation_info

    frames = [{"best_effort_timestamp_time": f"{i * 0.033:.3f}", "pkt_duration_time": "0.033000"} for i in range(9)]
    assert _frame_durations(frames) == [0.033] * 9
    # Нет ни одного поля длительности — по меткам времени, последний кадр как предыдущий.
    bare = [{"best_effort_timestamp_time": f"{i * 0.05:.2f}"} for i in range(4)]
    assert [round(d, 3) for d in _frame_durations(bare)] == [0.05, 0.05, 0.05, 0.05]

    probe = lambda _p: {  # noqa: E731
        "streams": [{"codec_name": "vp9", "width": 1024, "height": 1024, "pix_fmt": "yuv420p",
                     "tags": {"alpha_mode": "1"}}],
        "frames": frames,
    }
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "talk.webm"
        p.write_bytes(b"x")
        info = animation_info(p, probe=probe)
    assert info["frames"] == 9 and abs(info["fps"] - 30.3) < 0.5
