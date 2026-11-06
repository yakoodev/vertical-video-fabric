"""Звук клипа: выражения громкости и графы эффектов ffmpeg (из ``app/audio_edit.py``)."""

from __future__ import annotations

from typing import Any

from app.audio_mute import ENVELOPE_FRAMES
from app.audio_edit_norm import TIME_FX, _clamp

FX_RAMP_SEC = 0.03


# Свой звук вместо исходного (генератор ffmpeg, а не фильтр звука): тон и шум.
GEN_FX = ("beep", "noise")
BEEP_HZ = 900


NOISE_AMPLITUDE = 0.08  # розовый шум anoisesrc: RMS около −30 dBFS — тише речи, но сплошной


# Стиль глушения мата (app/audio_mute.MUTE_STYLES) → эффект на отрезке.
CENSOR_FX: dict[str, dict] = {
    "beep": {"fx": "beep"},
    "noise": {"fx": "noise"},
    "reverse": {"fx": "reverse"},
    "lowpass": {"fx": "muffle", "amount": 1.0},  # lowpass 400 Гц ×2 (vibrato underwater — не нужно)
    "stretch": {"fx": "speed", "rate": 0.6},  # растянута, тон ниже (asetrate)
}
TAIL_FX = {"echo": 0.9, "reverb": 0.6}


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


def censor_fx(style: str) -> dict | None:
    """Эффект на отрезке для стиля глушения мата; None — стиль-громкость (тишина, duck)."""
    found = CENSOR_FX.get(str(style or ""))
    return dict(found) if found else None


def gen_source(kind: str, length: float) -> str:
    """Генератор ffmpeg для ``beep``/``noise`` длиной ``length`` с (без входа: свой звук)."""
    if kind == "noise":
        return f"anoisesrc=color=pink:sample_rate=48000:amplitude={NOISE_AMPLITUDE}:duration={length:.3f}:seed=7"
    return f"sine=frequency={BEEP_HZ}:sample_rate=48000:duration={length:.3f}"


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
        ramp = max(0.001, float(item.get("ramp") or FX_RAMP_SEC))
        env = f"min(clip(({tv}-{_f(a)})/{ramp:.3f},0,1),clip(({_f(b)}-{tv})/{ramp:.3f},0,1))"
        p = f"{uid}f{n}"
        nxt = out_label if n == len(items) - 1 else f"{p}o"
        kind = item["fx"]
        if kind in GEN_FX:
            # Тон/шум вместо звука: исходник на отрезке гаснет, генератор встаёт на его место
            # (adelay — к началу отрезка в потоке, края — afade той же длины, что рампа).
            lo = max(a, stream0)
            length = b - lo
            if length <= 0.005:
                out.append(f"[{current}]anull[{nxt}]")
                current = nxt
                continue
            fade = min(ramp, length / 2)
            ms = int(round((lo - stream0) * 1000))
            out.append(f"[{current}]aformat=sample_rates=48000:channel_layouts=stereo,{ENVELOPE_FRAMES},"
                       f"volume='1-{env}':eval=frame[{p}dd]")
            # pan, а не aformat: моно → стерео у aformat тише на 3 дБ, писк был бы −21 dBFS.
            out.append(f"{gen_source(kind, length)},pan=stereo|c0=c0|c1=c0,aformat=sample_rates=48000,"
                       f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={length - fade:.3f}:d={fade:.3f},"
                       f"adelay={ms}:all=1,apad[{p}ww]")
            out.append(f"[{p}dd][{p}ww]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{nxt}]")
            current = nxt
            continue
        out.append(f"[{current}]aformat=sample_rates=48000:channel_layouts=stereo,{ENVELOPE_FRAMES},"
                   f"asplit=2[{p}d][{p}w]")
        out.append(f"[{p}d]volume='1-{env}':eval=frame[{p}dd]")
        if kind in TIME_FX:
            chain, need = _time_fx_chain(item)
            s0 = max(0.0, a - stream0)
            ms = int(round(s0 * 1000))
            out.append(f"[{p}w]atrim=start={s0:.3f}:end={s0 + need:.3f},asetpts=PTS-STARTPTS,{chain},"
                       f"apad,atrim=end={b - a:.3f},asetpts=PTS-STARTPTS,adelay={ms}:all=1,apad,"
                       f"{ENVELOPE_FRAMES},volume='{env}':eval=frame[{p}ww]")
        elif kind in TAIL_FX:
            out.append(f"[{p}w]volume='{env}':eval=frame,{fx_chain(kind, item.get('amount', 0.5))}[{p}ww]")
        elif kind == "chain":
            from app.audio_fx import chain_filters

            chain = chain_filters(item.get("chain"), p, b - a, offset=max(0.0, a - stream0))
            out.append(f"[{p}w]{chain},aresample=48000,{ENVELOPE_FRAMES},"
                       f"volume='{env}':eval=frame[{p}ww]")
        else:
            out.append(f"[{p}w]{fx_chain(kind, item.get('amount', 0.5))},{ENVELOPE_FRAMES},"
                       f"volume='{env}':eval=frame[{p}ww]")
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
        vols = [o for o in own if o.get("kind") == "vol"]
        if vols:
            expr = "*".join(span_factor(float(o["start"]), float(o["end"]), float(o.get("factor", 0.0)), tv,
                                        float(o.get("ramp") or 0.03)) for o in vols)
            chain.append(f"{ENVELOPE_FRAMES},volume='{expr}':eval=frame")
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
