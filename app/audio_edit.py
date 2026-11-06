"""Звуковой монтаж клипа: J/L-склейки, звук по дорожкам, громкость во времени, эффекты на отрезке.

Всё хранится в ``render_settings`` клипа:

* ``mute`` / ``gain`` с ``track`` (app/audio_mute.py) — тишина/громкость ОДНОЙ дорожки исходника
  (мат — на микрофоне, игра звучит). Без ``track`` — как раньше: весь звук, финальным проходом.
* ``audio`` (этот модуль, ``normalize_audio``):

  - ``joins``  — ``[{piece, lead, lag}]``: J/L на стыках. ``piece`` — id куска (segment id: кусок
    подрезали или переставили — J/L едет с ним). ``lead`` (J) — звук куска начинается на столько
    секунд РАНЬШЕ его картинки (под хвостом прошлого куска); ``lag`` (L) — звук куска тянется на
    столько секунд ПОСЛЕ его картинки (под головой следующего). ≤ ``MAX_SHIFT_SEC``.
  - ``source_keys`` / ``music_keys`` — ключи громкости ``[{t, gain_db, ease}]`` (шкала clip) звука
    исходника и фоновой музыки. 0 дБ — как без ключей; между ключами — по ``ease`` ключа-цели.
  - ``music_duck`` — ручной ducking музыки ``[{start, end, gain_db}]`` поверх автоматического
    (sidechain под речью остаётся).
  - ``music_dips`` — «выключить музыку перед ударом» ``[{at, before}]``: музыка уходит в тишину за
    ``before`` (0.3–0.8 с) до ``at`` и возвращается ровно на ``at``.
  - ``fx`` — эффекты на отрезке ``[{start, end, fx, track?, amount?, rate?, keep_pitch?}]`` (шкала
    clip): ``muffle`` (за стеной), ``underwater``, ``echo``, ``reverb``, ``phone``, ``radio``,
    ``denoise``, ``speed`` (``rate`` 0.5–2: замедлить/ускорить звук, длина отрезка та же),
    ``reverse``, ``beep`` (тон ~900 Гц −18 dBFS вместо звука), ``noise`` (тихий розовый шум вместо звука).

* ``mute`` цензуры мата со ``style`` (app/audio_mute.py) — те же эффекты на отрезке (``censor_fx``):
  beep, noise, reverse, lowpass (muffle — lowpass 400 Гц), stretch (speed 0.6, тон ниже); края — короткие
  ``CENSOR_RAMP_SEC``, чтобы исходное слово не просачивалось по краям, как у тишины.

Как рендерится (решения):

* Всё, что «по исходнику» (дорожки, эффекты, ключи исходника), накладывается при рендере КУСКА —
  там дорожки ещё раздельные. Отрезки клипа переводятся в секунды исходника (``clip_span_to_source``),
  поэтому тишина/эффект едут вместе со своим звуком через вырезы, наплывы и J/L.
* J/L: картинка режется как раньше. На стыке звук куска A продолжается из исходника (L) или звук
  куска B начинается раньше из исходника (J) — «ручки» (handles) звука читаются отдельно — и два
  звука сходятся коротким кроссфейдом ``JL_XFADE_SEC`` (или под переходом, если он есть). Длина
  ролика не меняется. Звук каждого куска остаётся синхронным со своей картинкой: слова не
  «переезжают» — J/L решает, ЧЕЙ звук слышен у стыка. Слова, чей звук заменён, не слышны;
  ``jl_speech_report`` предупреждает (ставь J/L на паузы — их даёт ``audio_map``).
* Музыка (ключи, ручной ducking, провалы) — в проходе музыки, во времени готового ролика.

Чистые функции (нормализация, выражения громкости, графы ffmpeg, планы стыков) — без медиа,
их проверяют тесты.

Где что лежит. Здесь — операции над источником и план для рендера. Вынесено: нормализация ``app/audio_edit_norm.py``, выражения и графы ffmpeg
``app/audio_edit_graph.py``, J/L-склейки и подпись услышанного ``app/audio_edit_jl.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any

from app.audio_edit_norm import (  # noqa: F401
    MAX_SHIFT_SEC, MAX_KEYS, MAX_DUCKS, MAX_DIPS, MAX_FX, MIN_FX_SEC, MAX_TIME_FX_SEC, KEY_MIN_DB, KEY_MAX_DB,
    DIP_MIN_SEC, DIP_MAX_SEC, DIP_DEFAULT_SEC, EASES, FX_KINDS, TIME_FX, _num, _clamp, normalize_joins,
    normalize_keys, normalize_duck, normalize_dips, normalize_fx, normalize_audio,
)
from app.audio_edit_graph import (  # noqa: F401
    FX_RAMP_SEC, GEN_FX, BEEP_HZ, NOISE_AMPLITUDE, CENSOR_FX, TAIL_FX, _f, span_factor, spans_factor, _ease_expr,
    keys_db_expr, keys_factor_expr, keys_value, fx_chain, censor_fx, gen_source, _time_fx_chain, fx_graph,
    _ops_for_track, has_source_ops, source_graph,
)
from app.audio_edit_jl import (  # noqa: F401
    JL_XFADE_SEC, JL_MAX_SHARE, part_shifts, plan_jl, handle_windows, jl_audio_filters, jl_speech_report,
    HANDLE_ASR_MARGIN_SEC, MAX_HANDLE_WORDS, piece_length, normalize_handle_words, handle_extents, _jl_windows,
    heard_subtitles, _runs, split_handle_words,
)

DIP_FADE_SEC = 0.06
DIP_BACK_SEC = 0.01
DUCK_RAMP_SEC = 0.12


# sine у ffmpeg — амплитуда 1/8, это и есть −18 dBFS (пик); volume не нужен.
BEEP_DBFS = -18.0


CENSOR_RAMP_SEC = 0.008


AUDIO_KEYS = ("joins", "source_keys", "music_keys", "music_duck", "music_dips", "fx")


# --------------------------------------------------------------------------- #
# Шкала clip → секунды исходника
# --------------------------------------------------------------------------- #


def clip_span_to_source(clock: Any, a: float, b: float) -> list[tuple[float, float]]:
    """Отрезок шкалы clip → отрезки исходника (через стык кусков — по куску на часть)."""
    from app.piece_speed import to_source

    pa, pb = clock.clip_range_to_plain(float(a), float(b))
    out = []
    for index, seg in enumerate(clock.segments):
        p0 = clock.pre.plain_starts[index]
        p1 = p0 + clock.lengths[index]
        lo, hi = max(pa, p0), min(pb, p1)
        if hi - lo > 1e-4:
            x, y = to_source(seg, lo - p0), to_source(seg, hi - p0)  # скорость/реверс куска
            out.append((round(min(x, y), 3), round(max(x, y), 3)))
    return out


def source_ops(render: dict, clock: Any, mix_tracks: list[int] | None = None) -> tuple[list[dict], list[str]]:
    """Операции звука исходника (секунды исходника) из настроек клипа: mute/gain с ``track`` и
    ``audio.fx``. ``mix_tracks`` — дорожки смеси рендера: операции других дорожек не слышны —
    пропускаются с пометкой. → (ops, notes)."""
    from app.audio_mute import DUCK_RAMP_SEC, is_fx_style, normalize_gain, normalize_mute, span_floor, span_style

    audio = normalize_audio(render.get("audio"))
    heard = set(mix_tracks or [0])
    ops: list[dict] = []
    notes: list[str] = []
    skipped: set[int] = set()

    def keep(track: int | None) -> bool:
        if track is None or track in heard:
            return True
        skipped.add(track)
        return False

    for m in normalize_mute(render.get("mute")):
        if is_fx_style(m):
            # Мат со стилем-эффектом (писк, шум, реверс…): эффект на отрезке своей дорожки
            # или всего звука исходника; финальный проход тишины его пропускает.
            if keep(m.get("track")):
                fx = censor_fx(span_style(m)) or {}
                for a, b in clip_span_to_source(clock, m["start"], m["end"]):
                    ops.append({**fx, "kind": "fx", "start": a, "end": b, "track": m.get("track"),
                                "ramp": CENSOR_RAMP_SEC, "by": m.get("by")})
        elif m.get("track") is not None and keep(m["track"]):
            for a, b in clip_span_to_source(clock, m["start"], m["end"]):
                floor = span_floor(m)
                ops.append({"kind": "vol", "track": m["track"], "start": a, "end": b, "factor": floor,
                            **({"ramp": DUCK_RAMP_SEC} if floor > 0 else {})})
    for g in normalize_gain(render.get("gain")):
        if g.get("track") is not None and keep(g["track"]):
            for a, b in clip_span_to_source(clock, g["start"], g["end"]):
                ops.append({"kind": "vol", "track": g["track"], "start": a, "end": b,
                            "factor": round(10 ** (g["gain_db"] / 20), 5)})
    for item in audio.get("fx") or []:
        if not keep(item.get("track")):
            continue
        for a, b in clip_span_to_source(clock, item["start"], item["end"]):
            ops.append({**item, "kind": "fx", "start": a, "end": b, "track": item.get("track")})
    if skipped:
        notes.append(f"дорожки {sorted(skipped)} нет в звуке ролика (set_source_audio tracks) — их тишина/"
                     "громкость/эффекты не слышны")
    return ops, notes


def ops_in_window(ops: list[dict], start: float, end: float, margin: float = 0.0) -> list[dict]:
    """Операции, задевающие окно исходника [start, end] (± margin): кусок их и получит."""
    return [op for op in ops if op["end"] > start - margin and op["start"] < end + margin]


# --------------------------------------------------------------------------- #
# Музыка: ключи, ручной ducking, провалы перед ударом
# --------------------------------------------------------------------------- #


def music_automation(render: dict, mapper: Any = None) -> dict | None:
    """Автоматизация музыки в секундах ГОТОВОГО ролика (``mapper`` — clip → final, None — то же)."""
    audio = normalize_audio(render.get("audio"))
    keys, ducks, dips = audio.get("music_keys") or [], audio.get("music_duck") or [], audio.get("music_dips") or []
    if not (keys or ducks or dips):
        return None

    def at(t: float) -> float:
        return float(t) if mapper is None else float(mapper.clamp(t))

    out = {
        "keys": [{**k, "t": round(at(k["t"]), 3)} for k in keys],
        "duck": [{**d, "start": round(at(d["start"]), 3), "end": round(at(d["end"]), 3)} for d in ducks],
        "dips": [{"at": round(at(d["at"]), 3), "before": d["before"]} for d in dips],
    }
    out["keys"] = normalize_keys(out["keys"])
    out["duck"] = [d for d in out["duck"] if d["end"] - d["start"] >= 0.05]
    # Рампы ducking из сведения (render_settings.mix: duck_attack / duck_release), иначе DUCK_RAMP_SEC.
    from app.audio_mix import normalize_mix

    mix = normalize_mix(render.get("mix"))
    for key, name in (("duck_attack", "ramp_in"), ("duck_release", "ramp_out")):
        if mix.get(key) is not None and out["duck"]:
            out[name] = mix[key]
    return out


def music_volume_expr(auto: dict | None, tv: str = "t") -> str:
    """Множитель громкости музыки: ключи × ручной ducking × провалы перед ударом."""
    if not auto:
        return "1"
    factors = []
    if auto.get("keys"):
        factors.append(keys_factor_expr(auto["keys"], tv))
    for d in auto.get("duck") or []:
        factors.append(span_factor(d["start"], d["end"], 10 ** (d["gain_db"] / 20), tv,
                                   float(auto.get("ramp_in") or DUCK_RAMP_SEC),
                                   float(auto.get("ramp_out") or DUCK_RAMP_SEC)))
    for d in auto.get("dips") or []:
        a = d["at"] - d["before"]
        factors.append(span_factor(a, d["at"] + DIP_BACK_SEC, 0.0, tv, DIP_FADE_SEC, DIP_BACK_SEC))
    return "*".join(factors) or "1"


# --------------------------------------------------------------------------- #
# План для рендера
# --------------------------------------------------------------------------- #


def wants_render_plan(render: dict) -> bool:
    """Есть ли у клипа хоть что-то из звукового монтажа (иначе рендер ровно прежний)."""
    from app.audio_mute import MUTE_TAGS, is_fx_style

    if normalize_audio(render.get("audio")):
        return True
    if any(isinstance(m, dict) and m.get("by") in MUTE_TAGS and is_fx_style(m) for m in render.get("mute") or []):
        return True  # мат со стилем-эффектом рендерится в звуке куска
    return any(isinstance(m, dict) and m.get("track") is not None
               for m in [*(render.get("mute") or []), *(render.get("gain") or [])])


def render_plan(plan: dict, source: dict, final_mapper: Any = None, cut_plan: Any = None) -> dict | None:
    """Что рендеру сделать со звуком клипа: ``ops`` (секунды исходника), ``keys`` и ``clip0`` по
    частям (ключи громкости исходника), ``shifts`` (J/L по склейкам частей), ``music``
    (автоматизация музыки, секунды готового ролика), ``notes``. None — ничего (рендер прежний)."""
    from app.canvas import is_canvas
    from app.source_audio import _streams_from_metadata, source_tracks
    from app.timebase import ClipClock

    render = plan.get("render_settings") or {}
    if not wants_render_plan(render):
        return None
    if is_canvas(source):
        # Холст: звука исходника нет, но музыка (ключи, ручной ducking под речью, провалы) — есть.
        # Раньше план холста был None целиком, и ducking музыки под озвучкой (объяснялки, ИИ-видео)
        # молча не применялся: у «совы» (клип 887) музыка шла во всю громкость под голосом.
        music = music_automation(render, final_mapper)
        return {"notes": [], "music": music} if music else None
    segments = plan.get("segments") or []
    if not segments:
        return None
    audio = normalize_audio(render.get("audio"))
    clock = ClipClock(segments, render.get("transition"))
    parts = list(cut_plan.parts) if cut_plan is not None else list(segments)
    # Дорожек в метаданных не видно — верим, что звук есть (ffprobe на каждый рендер не гоняем).
    streams = _streams_from_metadata(source.get("metadata_json") or source.get("metadata"))
    has_audio = streams is None or any(s.get("codec_type") == "audio" for s in streams)
    out: dict = {"notes": []}
    if has_audio:
        ops, notes = source_ops(render, clock, source_tracks(source))
        out["notes"] += notes
        if ops:
            out["ops"] = ops
        if audio.get("source_keys"):
            out["keys"] = audio["source_keys"]
        out["clip0"] = [round(clock.clip_starts[int(p.get("piece", i)) if cut_plan is not None else i]
                              + float(p.get("local_start") or 0.0), 3) for i, p in enumerate(parts)]
        shifts = part_shifts(parts, segments, audio.get("joins"))
        if any(shifts):
            out["shifts"] = shifts
    music = music_automation(render, final_mapper)
    if music:
        out["music"] = music
    if not (out.get("ops") or out.get("keys") or out.get("shifts") or out.get("music")):
        return {"notes": out["notes"]} if out["notes"] else None
    return out


def voice_track(source: dict) -> int | None:
    """Дорожка голоса записи, если она известна: своя дорожка распознавания (``speech_tracks`` —
    одна) внутри смеси рендера из нескольких дорожек. Иначе None (голос и игра не разделены)."""
    from app.source_audio import normalize_tracks, source_tracks

    speech = normalize_tracks(source.get("speech_tracks"))
    mix = source_tracks(source) or [0]
    if speech and len(speech) == 1 and len(mix) > 1 and speech[0] in mix:
        return speech[0]
    return None


def plan_source_ops(plan: dict, source: dict) -> list[dict]:
    """Операции звука исходника клипа (секунды исходника) — для listen и find_noises: они режут
    звук из исходника сами и должны слышать то же, что рендер."""
    from app.source_audio import source_tracks
    from app.timebase import ClipClock

    render = plan.get("render_settings") or {}
    if not wants_render_plan(render) or not plan.get("segments"):
        return []
    ops, _notes = source_ops(render, ClipClock(plan["segments"], render.get("transition")), source_tracks(source))
    return ops


def part_edit(plan: dict | None, index: int, start: float, end: float) -> dict | None:
    """Операции звука одной части (``plan`` — ``render_plan``) для её команды ffmpeg."""
    if not plan:
        return None
    ops = ops_in_window(plan.get("ops") or [], start, end, MAX_SHIFT_SEC + 0.5)
    keys = plan.get("keys") or []
    if not (ops or keys):
        return None
    clip0 = (plan.get("clip0") or [])
    return {"ops": ops, "keys": keys, "src0": round(float(start), 3),
            "clip0": clip0[index] if index < len(clip0) else 0.0}


# --------------------------------------------------------------------------- #
# Перенос за кусками и переходом (app/piece_remap.py)
# --------------------------------------------------------------------------- #


def remap_audio(audio: Any, mapper: Any) -> dict:
    """``render_settings.audio`` вслед за кусками/переходом: ключи и провалы — точки, ducking и
    эффекты — отрезки (целиком в отрезанном — пропадают); J/L — по id куска, не двигаются."""
    from app.piece_remap import remap_spans

    clean = normalize_audio(audio)
    if not clean:
        return clean
    out = dict(clean)
    for name in ("source_keys", "music_keys"):
        if clean.get(name):
            out[name] = normalize_keys([{**k, "t": mapper.clamp(k["t"])} for k in clean[name]])
    if clean.get("music_dips"):
        out["music_dips"] = normalize_dips([{**d, "at": mapper.clamp(d["at"])} for d in clean["music_dips"]])
    for name in ("music_duck", "fx"):
        if clean.get(name):
            out[name] = remap_spans(clean[name], mapper, 0.1)
    return {k: v for k, v in out.items() if v}
