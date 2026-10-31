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
    ``reverse``.

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
"""

from __future__ import annotations

import math
from typing import Any

MAX_SHIFT_SEC = 1.5
JL_XFADE_SEC = 0.08
# J/L не забирает больше этой доли соседнего куска (иначе кусок «съеден» чужим звуком).
JL_MAX_SHARE = 0.45
MAX_KEYS = 40
MAX_DUCKS = 20
MAX_DIPS = 12
MAX_FX = 20
MIN_FX_SEC = 0.1
MAX_TIME_FX_SEC = 12.0
KEY_MIN_DB = -60.0
KEY_MAX_DB = 12.0
DIP_MIN_SEC = 0.15
DIP_MAX_SEC = 2.0
DIP_DEFAULT_SEC = 0.5
DIP_FADE_SEC = 0.06
DIP_BACK_SEC = 0.01
DUCK_RAMP_SEC = 0.12
FX_RAMP_SEC = 0.03
EASES = ("linear", "smooth", "in", "out", "step")
FX_KINDS: dict[str, str] = {
    "muffle": "приглушённо, «за стеной»",
    "underwater": "«под водой»",
    "echo": "эхо (повторы)",
    "reverb": "ревёрб (зал)",
    "phone": "телефон",
    "radio": "рация",
    "denoise": "шумодав",
    "speed": "замедлить/ускорить звук (rate)",
    "reverse": "задом наперёд",
}
TIME_FX = ("speed", "reverse")
TAIL_FX = {"echo": 0.9, "reverb": 0.6}
AUDIO_KEYS = ("joins", "source_keys", "music_keys", "music_duck", "music_dips", "fx")


# --------------------------------------------------------------------------- #
# Нормализация
# --------------------------------------------------------------------------- #


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


def normalize_joins(raw: Any) -> list[dict]:
    """``[{piece, lead, lag}]``: piece — id куска (целое), lead/lag 0..MAX_SHIFT_SEC, нули выброшены,
    повтор куска — последний."""
    out: dict[int, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        piece = _num(item.get("piece"))
        if piece is None or piece != int(piece):
            continue
        entry = {"piece": int(piece)}
        for key in ("lead", "lag"):
            value = _num(item.get(key))
            if value is not None and value > 0.005:
                entry[key] = round(_clamp(value, 0.0, MAX_SHIFT_SEC), 3)
        if len(entry) > 1:
            out[entry["piece"]] = entry
        else:
            out.pop(entry["piece"], None)
    return list(out.values())


def normalize_keys(raw: Any) -> list[dict]:
    """``[{t, gain_db, ease}]`` по времени, повтор момента — последний, gain_db −60..+12."""
    found: dict[float, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        t, g = _num(item.get("t")), _num(item.get("gain_db"))
        if t is None or g is None or t < 0:
            continue
        ease = str(item.get("ease") or "linear")
        found[round(t, 3)] = {"t": round(t, 3), "gain_db": round(_clamp(g, KEY_MIN_DB, KEY_MAX_DB), 1),
                              "ease": ease if ease in EASES else "linear"}
    keys = [found[t] for t in sorted(found)]
    return keys[:MAX_KEYS]


def normalize_duck(raw: Any) -> list[dict]:
    """Ручной ducking музыки ``[{start, end, gain_db}]``: gain_db −60..0 (не 0), ≥ 0.1 с."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        a, b, g = _num(item.get("start")), _num(item.get("end")), _num(item.get("gain_db"))
        if a is None or b is None:
            continue
        g = -12.0 if g is None else _clamp(g, KEY_MIN_DB, 0.0)
        a = max(0.0, a)
        if b - a >= 0.1 and g < 0:
            out.append({"start": round(a, 3), "end": round(b, 3), "gain_db": round(g, 1)})
    out.sort(key=lambda d: (d["start"], d["end"]))
    return out[:MAX_DUCKS]


def normalize_dips(raw: Any) -> list[dict]:
    """«Тишина музыки перед ударом» ``[{at, before}]``: before 0.15..2 с (по умолчанию 0.5)."""
    out: dict[float, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            item = {"at": item}
        if not isinstance(item, dict):
            continue
        at = _num(item.get("at"))
        if at is None or at <= 0:
            continue
        before = _num(item.get("before"))
        before = DIP_DEFAULT_SEC if before is None else _clamp(before, DIP_MIN_SEC, DIP_MAX_SEC)
        before = min(before, at)
        if before >= DIP_MIN_SEC - 1e-9:
            out[round(at, 3)] = {"at": round(at, 3), "before": round(before, 3)}
    return [out[k] for k in sorted(out)][:MAX_DIPS]


def normalize_fx(raw: Any) -> list[dict]:
    """Эффекты на отрезке (см. вверху): известный ``fx``, ≥ MIN_FX_SEC, speed/reverse ≤ 12 с."""
    from app.audio_mute import span_track

    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("fx") or "")
        a, b = _num(item.get("start")), _num(item.get("end"))
        if kind not in FX_KINDS or a is None or b is None:
            continue
        a = max(0.0, a)
        if kind in TIME_FX:
            b = min(b, a + MAX_TIME_FX_SEC)
        if b - a < MIN_FX_SEC:
            continue
        entry: dict = {"start": round(a, 3), "end": round(b, 3), "fx": kind}
        amount = _num(item.get("amount"))
        if amount is not None and kind not in TIME_FX:
            entry["amount"] = round(_clamp(amount, 0.0, 1.0), 2)
        if kind == "speed":
            rate = _num(item.get("rate"))
            entry["rate"] = round(_clamp(rate if rate is not None else 0.75, 0.5, 2.0), 3)
            if item.get("keep_pitch"):
                entry["keep_pitch"] = True
        track = span_track(item)
        if track is not None:
            entry["track"] = track
        out.append(entry)
    out.sort(key=lambda d: (d["start"], d["end"]))
    return out[:MAX_FX]


def normalize_audio(raw: Any) -> dict:
    """``render_settings.audio`` целиком; пустые части не хранятся ({} — звук как раньше)."""
    src = raw if isinstance(raw, dict) else {}
    out = {
        "joins": normalize_joins(src.get("joins")),
        "source_keys": normalize_keys(src.get("source_keys")),
        "music_keys": normalize_keys(src.get("music_keys")),
        "music_duck": normalize_duck(src.get("music_duck")),
        "music_dips": normalize_dips(src.get("music_dips")),
        "fx": normalize_fx(src.get("fx")),
    }
    return {k: v for k, v in out.items() if v}


# --------------------------------------------------------------------------- #
# Выражения громкости ffmpeg (переменная времени — строка ``tv``)
# --------------------------------------------------------------------------- #


def _f(value: float) -> str:
    return f"{value:.3f}"


def span_factor(a: float, b: float, factor: float, tv: str = "t", ramp_in: float = 0.03,
                ramp_out: float | None = None) -> str:
    """Множитель: ``factor`` внутри [a, b], 1 вне, линейные рампы ВНУТРИ краёв."""
    ramp_out = ramp_in if ramp_out is None else ramp_out
    inside = (f"min(clip(({tv}-{_f(a)})/{max(ramp_in, 0.001):.3f},0,1),"
              f"clip(({_f(b)}-{tv})/{max(ramp_out, 0.001):.3f},0,1))")
    return f"(1-{1 - factor:.5f}*{inside})"


def spans_factor(spans: list[tuple[float, float, float]], tv: str = "t", ramp: float = 0.03) -> str:
    return "*".join(span_factor(a, b, k, tv, ramp) for a, b, k in spans) or "1"


def _ease_expr(u: str, ease: str) -> str:
    if ease == "smooth":
        return f"({u}*{u}*(3-2*{u}))"
    if ease == "in":
        return f"({u}*{u})"
    if ease == "out":
        return f"(1-(1-{u})*(1-{u}))"
    if ease == "step":
        return "0"
    return u


def keys_db_expr(keys: list[dict], tv: str = "t") -> str:
    """Громкость в дБ по ключам: до первого — первый, после последнего — последний; между —
    по ``ease`` ключа-цели (step — держит прежний до самого ключа)."""
    if not keys:
        return "0"
    expr = f"{keys[-1]['gain_db']:g}"
    for k0, k1 in reversed(list(zip(keys, keys[1:]))):
        t0, t1 = float(k0["t"]), float(k1["t"])
        d0, d1 = float(k0["gain_db"]), float(k1["gain_db"])
        span = max(t1 - t0, 1e-3)
        u = f"clip(({tv}-{_f(t0)})/{span:.3f},0,1)"
        seg = f"({d0:g}+{d1 - d0:g}*{_ease_expr(u, str(k1.get('ease') or 'linear'))})"
        expr = f"if(lt({tv},{_f(t1)}),{seg},{expr})"
    return f"if(lt({tv},{_f(float(keys[0]['t']))}),{keys[0]['gain_db']:g},{expr})"


def keys_factor_expr(keys: list[dict], tv: str = "t") -> str:
    """Множитель громкости по ключам (−60 дБ — практически тишина)."""
    if not keys:
        return "1"
    return f"pow(10,({keys_db_expr(keys, tv)})/20)"


def keys_value(keys: list[dict], t: float) -> float:
    """То же, что ``keys_db_expr``, — числом (превью, тесты, audio_map)."""
    if not keys:
        return 0.0
    if t < keys[0]["t"]:
        return float(keys[0]["gain_db"])
    for k0, k1 in zip(keys, keys[1:]):
        if t < k1["t"]:
            u = _clamp((t - k0["t"]) / max(k1["t"] - k0["t"], 1e-3), 0.0, 1.0)
            ease = k1.get("ease") or "linear"
            u = {"smooth": u * u * (3 - 2 * u), "in": u * u, "out": 1 - (1 - u) ** 2, "step": 0.0}.get(ease, u)
            return k0["gain_db"] + (k1["gain_db"] - k0["gain_db"]) * u
    return float(keys[-1]["gain_db"])


# --------------------------------------------------------------------------- #
# Эффекты на отрезке
# --------------------------------------------------------------------------- #


def fx_chain(kind: str, amount: float = 0.5) -> str:
    """Фильтры ffmpeg «мокрого» сигнала эффекта (без времени — его задаёт огибающая)."""
    a = _clamp(float(amount), 0.0, 1.0)
    if kind == "muffle":
        cutoff = round(1400 - 1000 * a)  # 0 → 1400 Гц, 1 → 400 Гц
        return f"lowpass=f={cutoff}:p=2,lowpass=f={cutoff}:p=2,volume=2dB"
    if kind == "underwater":
        cutoff = round(700 - 400 * a)
        return f"lowpass=f={cutoff}:p=2,lowpass=f={cutoff}:p=2,vibrato=f=3.5:d={0.1 + 0.3 * a:.2f},volume=3dB"
    if kind == "echo":
        d1, d2 = round(220 + 200 * a), round(440 + 400 * a)
        return f"aecho=0.8:0.7:{d1}|{d2}:{0.35 + 0.25 * a:.2f}|{0.18 + 0.2 * a:.2f}"
    if kind == "reverb":
        k = 0.25 + 0.35 * a
        decays = "|".join(f"{k * f:.2f}" for f in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3))
        return f"aecho=0.8:0.85:23|41|67|97|131|173:{decays}"
    if kind == "phone":
        return "highpass=f=380:p=2,highpass=f=380:p=2,lowpass=f=3200:p=2,lowpass=f=3200:p=2,volume=4dB"
    if kind == "radio":
        return ("highpass=f=600:p=2,highpass=f=600:p=2,lowpass=f=2400:p=2,lowpass=f=2400:p=2,"
                f"acrusher=bits={round(12 - 4 * a)}:mix={0.15 + 0.25 * a:.2f}:mode=log,volume=6dB")
    if kind == "denoise":
        return f"afftdn=nr={round(8 + 22 * a)}:nf=-45:tn=1"
    return "anull"


def _time_fx_chain(item: dict) -> tuple[str, float]:
    """(фильтры, сколько секунд звука нужно с начала отрезка) для speed/reverse."""
    length = float(item["end"]) - float(item["start"])
    if item["fx"] == "reverse":
        return "areverse", length
    rate = float(item.get("rate") or 0.75)
    if item.get("keep_pitch"):
        return f"atempo={rate:.4f}", length * rate
    return f"asetrate={round(48000 * rate)},aresample=48000", length * rate


def fx_graph(label: str, items: list[dict], tv: str, stream0: float, out_label: str, uid: str) -> list[str]:
    """Цепочка эффектов на ``[label]`` → ``[out_label]``. ``items`` — секунды той же шкалы, что
    ``tv`` (исходник), ``stream0`` — сколько это при t=0 потока (для atrim). Сухой сигнал вне
    отрезка, мокрый внутри, рампы FX_RAMP_SEC; у эха/ревёрба хвост доигрывает после конца."""
    out: list[str] = []
    current = label
    for n, item in enumerate(items):
        a, b = float(item["start"]), float(item["end"])
        env = f"min(clip(({tv}-{_f(a)})/{FX_RAMP_SEC},0,1),clip(({_f(b)}-{tv})/{FX_RAMP_SEC},0,1))"
        p = f"{uid}f{n}"
        nxt = out_label if n == len(items) - 1 else f"{p}o"
        out.append(f"[{current}]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[{p}d][{p}w]")
        out.append(f"[{p}d]volume='1-{env}':eval=frame[{p}dd]")
        kind = item["fx"]
        if kind in TIME_FX:
            chain, need = _time_fx_chain(item)
            s0 = max(0.0, a - stream0)
            ms = int(round(s0 * 1000))
            out.append(f"[{p}w]atrim=start={s0:.3f}:end={s0 + need:.3f},asetpts=PTS-STARTPTS,{chain},"
                       f"apad,atrim=end={b - a:.3f},asetpts=PTS-STARTPTS,adelay={ms}:all=1,apad,"
                       f"volume='{env}':eval=frame[{p}ww]")
        elif kind in TAIL_FX:
            out.append(f"[{p}w]volume='{env}':eval=frame,{fx_chain(kind, item.get('amount', 0.5))}[{p}ww]")
        else:
            out.append(f"[{p}w]{fx_chain(kind, item.get('amount', 0.5))},volume='{env}':eval=frame[{p}ww]")
        out.append(f"[{p}dd][{p}ww]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{nxt}]")
        current = nxt
    return out


# --------------------------------------------------------------------------- #
# Звук исходника куска: дорожки + операции
# --------------------------------------------------------------------------- #


def _ops_for_track(ops: list[dict], track: int | None) -> list[dict]:
    return [op for op in ops or [] if op.get("track") == track]


def has_source_ops(edit: dict | None) -> bool:
    return bool(edit and (edit.get("ops") or edit.get("keys")))


def source_graph(input_index: int, tracks: Any, edit: dict, out_label: str) -> str:
    """Граф звука исходника куска с операциями (замена ``source_audio.mix_chain``).

    ``tracks`` — смесь дорожек (номера или ``[{track, gain_db}]``, пусто — первая).
    ``edit``: ``ops`` — операции в секундах ИСХОДНИКА (``{kind: vol, track, start, end, factor}`` и
    ``{kind: fx, …}``), ``src0`` — секунда исходника при t=0 потока, ``keys`` — ключи громкости
    шкалы clip и ``clip0`` — секунда клипа при t=0. Операции дорожки — до смешивания, общие — после."""
    from app.source_audio import _ff_db, _mix_items

    items = _mix_items(tracks)
    ops = list(edit.get("ops") or [])
    src0 = float(edit.get("src0") or 0.0)
    tv = f"(t+{src0:.3f})"
    parts: list[str] = []
    labels: list[str] = []
    uid = out_label
    for n, (track, gain) in enumerate(items):
        own = _ops_for_track(ops, track)
        chain = [f"volume={_ff_db(gain)}"] if gain else []
        vols = [(float(o["start"]), float(o["end"]), float(o.get("factor", 0.0)))
                for o in own if o.get("kind") == "vol"]
        if vols:
            chain.append(f"volume='{spans_factor(vols, tv)}':eval=frame")
        fx = [o for o in own if o.get("kind") == "fx"]
        label = f"{uid}_t{n}"
        if fx:
            parts.append(f"[{input_index}:a:{track}]{','.join([*chain, 'aresample=48000'])}[{label}i]")
            parts.extend(fx_graph(f"{label}i", fx, tv, src0, label, f"{uid}t{n}"))
        else:
            parts.append(f"[{input_index}:a:{track}]{','.join(chain) or 'anull'}[{label}]")
        labels.append(f"[{label}]")
    mixed = f"{uid}_mix"
    if len(labels) == 1:
        parts.append(f"{labels[0]}anull[{mixed}]")
    else:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0[{mixed}]")
    common_fx = [o for o in ops if o.get("track") is None and o.get("kind") == "fx"]
    current = mixed
    if common_fx:
        parts.append(f"[{current}]aresample=48000[{uid}_rs]")
        parts.extend(fx_graph(f"{uid}_rs", common_fx, tv, src0, f"{uid}_fx", f"{uid}c"))
        current = f"{uid}_fx"
    keys = edit.get("keys") or []
    if keys:
        ctv = f"(t+{float(edit.get('clip0') or 0.0):.3f})"
        parts.append(f"[{current}]volume='{keys_factor_expr(keys, ctv)}':eval=frame[{out_label}]")
    else:
        parts.append(f"[{current}]anull[{out_label}]")
    return ";".join(parts)


# --------------------------------------------------------------------------- #
# Шкала clip → секунды исходника
# --------------------------------------------------------------------------- #


def clip_span_to_source(clock: Any, a: float, b: float) -> list[tuple[float, float]]:
    """Отрезок шкалы clip → отрезки исходника (через стык кусков — по куску на часть)."""
    pa, pb = clock.clip_range_to_plain(float(a), float(b))
    out = []
    for index, seg in enumerate(clock.segments):
        p0 = clock.pre.plain_starts[index]
        p1 = p0 + clock.lengths[index]
        lo, hi = max(pa, p0), min(pb, p1)
        if hi - lo > 1e-4:
            start = float(seg["start_sec"])
            out.append((round(start + lo - p0, 3), round(start + hi - p0, 3)))
    return out


def source_ops(render: dict, clock: Any, mix_tracks: list[int] | None = None) -> tuple[list[dict], list[str]]:
    """Операции звука исходника (секунды исходника) из настроек клипа: mute/gain с ``track`` и
    ``audio.fx``. ``mix_tracks`` — дорожки смеси рендера: операции других дорожек не слышны —
    пропускаются с пометкой. → (ops, notes)."""
    from app.audio_mute import normalize_gain, normalize_mute

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
        if m.get("track") is not None and keep(m["track"]):
            for a, b in clip_span_to_source(clock, m["start"], m["end"]):
                ops.append({"kind": "vol", "track": m["track"], "start": a, "end": b, "factor": 0.0})
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
# J/L-склейки
# --------------------------------------------------------------------------- #


def part_shifts(parts: list[dict], segments: list[dict], joins: Any) -> list[float]:
    """Сдвиг звука на каждой склейке частей: + — L (звук A тянется под B), − — J (звук B раньше).

    Только на стыке разных кусков (последняя часть куска i → первая часть куска i+1); джамп-каты
    внутри куска — без J/L. ``lag`` куска A и ``lead`` куска B на одном стыке вычитаются."""
    cfg = {j["piece"]: j for j in normalize_joins(joins)}
    if not cfg or len(parts) < 2:
        return [0.0] * max(0, len(parts) - 1)
    ids = [s.get("id") for s in segments]
    out = []
    for k, (left, right) in enumerate(zip(parts, parts[1:])):
        li = left.get("piece", k) if "piece" in left else k
        ri = right.get("piece", k + 1) if "piece" in right else k + 1
        if li == ri or li >= len(ids) or ri >= len(ids):
            out.append(0.0)
            continue
        lag = float((cfg.get(ids[li]) or {}).get("lag") or 0.0)
        lead = float((cfg.get(ids[ri]) or {}).get("lead") or 0.0)
        out.append(round(lag - lead, 3))
    return out


def plan_jl(durations: list[float], overlaps: list[float], shifts: list[float], audio_mode: str = "smooth",
            jump_joins: list[bool] | None = None) -> dict:
    """План звука склеек с J/L: для каждой части ``head``/``tail`` (с: + — дочитать из исходника,
    − — отрезать), для каждой склейки ``xfade`` (0 — встык с де-кликом) и ``shift`` после
    ограничений. Сумма длин минус кроссфейды = длина картинки (как без J/L)."""
    n = len(durations)
    head = [0.0] * n
    tail = [0.0] * n
    xfade: list[float] = []
    used: list[float] = []
    for k in range(max(0, n - 1)):
        o = float(overlaps[k]) if k < len(overlaps) else 0.0
        s = float(shifts[k]) if k < len(shifts) else 0.0
        jump = bool(jump_joins) and k < len(jump_joins or []) and bool(jump_joins[k])
        room = JL_MAX_SHARE * min(durations[k], durations[k + 1])
        if jump:
            s = 0.0
        s = math.copysign(min(abs(s), MAX_SHIFT_SEC, room), s) if s else 0.0
        if abs(s) < 0.01:
            s = 0.0
        if s == 0.0:
            if o > 0 and audio_mode == "smooth":
                xfade.append(round(o, 3))
            elif o > 0:
                tail[k] -= o / 2
                head[k + 1] -= o / 2
                xfade.append(0.0)
            else:
                xfade.append(0.0)
        elif o > 0:
            tail[k] += s
            head[k + 1] -= s
            xfade.append(round(o, 3))
        else:
            pad = JL_XFADE_SEC / 2
            tail[k] += s + pad
            head[k + 1] += pad - s
            xfade.append(round(2 * pad, 3))
        used.append(round(s, 3))
    return {"head": [round(h, 3) for h in head], "tail": [round(t, 3) for t in tail],
            "xfade": xfade, "shift": used}


def handle_windows(parts: list[dict], plan: dict) -> list[dict]:
    """Какие «ручки» звука прочитать из исходника: ``[{part, side, start, length}]`` (секунды
    исходника; start < 0 — до начала записи, добьётся тишиной)."""
    out = []
    for k, part in enumerate(parts):
        head, tail = plan["head"][k], plan["tail"][k]
        if head > 1e-3:
            out.append({"part": k, "side": "head", "start": round(float(part["start_sec"]) - head, 3),
                        "length": round(head, 3)})
        if tail > 1e-3:
            out.append({"part": k, "side": "tail", "start": round(float(part["end_sec"]), 3),
                        "length": round(tail, 3)})
    return out


def jl_audio_filters(n: int, durations: list[float], plan: dict, handles: dict[tuple[int, str], int],
                     jump_joins: list[bool] | None, smooth: bool, out_label: str = "amain") -> list[str]:
    """Звук склейки с J/L: каждая часть ровно своей длины, ручки (входы ``handles[(k, side)]``)
    дописаны/лишнее отрезано, стыки — кроссфейдом ``xfade`` или встык с де-кликом."""
    from app.transitions import DECLICK_SEC, JUMP_DECLICK_SEC

    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    filters: list[str] = []
    lengths: list[float] = []
    for k in range(n):
        base = f"[{k}:a]{fmt},apad,atrim=end={durations[k]:.3f},asetpts=PTS-STARTPTS"
        head, tail = plan["head"][k], plan["tail"][k]
        if head < -1e-3:
            base += f",atrim=start={-head:.3f},asetpts=PTS-STARTPTS"
        length = durations[k] + min(0.0, head)
        if tail < -1e-3:
            base += f",atrim=end={length + tail:.3f},asetpts=PTS-STARTPTS"
            length += tail
        label = f"j{k}"
        filters.append(f"{base}[{label}b]")
        current = f"{label}b"
        if (k, "head") in handles:
            filters.append(f"[{handles[(k, 'head')]}:a]{fmt},apad,atrim=end={head:.3f},asetpts=PTS-STARTPTS[{label}h]")
            filters.append(f"[{label}h][{current}]concat=n=2:v=0:a=1[{label}c]")
            current = f"{label}c"
            length += head
        if (k, "tail") in handles:
            filters.append(f"[{handles[(k, 'tail')]}:a]{fmt},apad,atrim=end={tail:.3f},asetpts=PTS-STARTPTS[{label}t]")
            filters.append(f"[{current}][{label}t]concat=n=2:v=0:a=1[{label}e]")
            current = f"{label}e"
            length += tail
        filters.append(f"[{current}]anull[n{k}]")
        lengths.append(length)
    previous = "n0"
    acc = lengths[0] if lengths else 0.0
    for k in range(1, n):
        label = out_label if k == n - 1 else f"ja{k}"
        cf = plan["xfade"][k - 1]
        if cf > 0:
            filters.append(f"[{previous}][n{k}]acrossfade=d={cf:.3f}:c1=tri:c2=tri[{label}]")
            acc += lengths[k] - cf
        else:
            jump = bool(jump_joins) and k - 1 < len(jump_joins or []) and bool(jump_joins[k - 1])
            if smooth or jump:
                fade = JUMP_DECLICK_SEC if jump else DECLICK_SEC
                filters.append(f"[{previous}]afade=t=out:st={max(0.0, acc - fade):.3f}:d={fade}[{label}f]")
                filters.append(f"[n{k}]afade=t=in:st=0:d={fade}[{label}i]")
                filters.append(f"[{label}f][{label}i]concat=n=2:v=0:a=1[{label}]")
            else:
                filters.append(f"[{previous}][n{k}]concat=n=2:v=0:a=1[{label}]")
            acc += lengths[k]
        previous = label
    if n == 1:
        filters.append(f"[n0]anull[{out_label}]")
    return filters


def jl_speech_report(segments: list[dict], joins: Any, words: list[dict], transition: Any = None) -> list[dict]:
    """Слова, чей звук J/L заменяет звуком соседнего куска (их не будет слышно, а субтитры
    останутся), по каждому стыку с J/L. ``words`` — слова субтитров «кусками подряд»."""
    from app.transitions import part_overlaps

    shifts = part_shifts(segments, segments, joins)
    if not any(shifts):
        return []
    lengths = [max(0.0, float(s["end_sec"]) - float(s["start_sec"])) for s in segments]
    plan = plan_jl(lengths, part_overlaps(segments, transition), shifts)
    out = []
    bound = 0.0
    for k, s in enumerate(plan["shift"]):
        bound += lengths[k]
        if not s:
            continue
        if s > 0:  # L: голова куска B не слышна
            lo, hi, who = bound, bound + s, "начало куска {}".format(k + 1)
        else:  # J: хвост куска A не слышен
            lo, hi, who = bound + s, bound, "конец куска {}".format(k)
        lost = [str(w.get("word") or "").strip() for w in words or []
                if _num(w.get("start")) is not None and _num(w.get("end")) is not None
                and float(w["start"]) < hi - 0.03 and float(w["end"]) > lo + 0.03]
        out.append({"join": k, "shift": s, "kind": "L" if s > 0 else "J", "hidden": who,
                    "hidden_sec": round(abs(s), 3), "lost_words": [w for w in lost if w][:8]})
    return out


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
    return out


def music_volume_expr(auto: dict | None, tv: str = "t") -> str:
    """Множитель громкости музыки: ключи × ручной ducking × провалы перед ударом."""
    if not auto:
        return "1"
    factors = []
    if auto.get("keys"):
        factors.append(keys_factor_expr(auto["keys"], tv))
    for d in auto.get("duck") or []:
        factors.append(span_factor(d["start"], d["end"], 10 ** (d["gain_db"] / 20), tv, DUCK_RAMP_SEC))
    for d in auto.get("dips") or []:
        a = d["at"] - d["before"]
        factors.append(span_factor(a, d["at"] + DIP_BACK_SEC, 0.0, tv, DIP_FADE_SEC, DIP_BACK_SEC))
    return "*".join(factors) or "1"


# --------------------------------------------------------------------------- #
# План для рендера
# --------------------------------------------------------------------------- #


def wants_render_plan(render: dict) -> bool:
    """Есть ли у клипа хоть что-то из звукового монтажа (иначе рендер ровно прежний)."""
    if normalize_audio(render.get("audio")):
        return True
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
    if not wants_render_plan(render) or is_canvas(source):
        return None
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
