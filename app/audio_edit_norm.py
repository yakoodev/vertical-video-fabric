"""Звук клипа: нормализация настроек (из ``app/audio_edit.py``)."""

from __future__ import annotations

import math
from typing import Any

MAX_SHIFT_SEC = 1.5


MAX_KEYS = 40
# Автосведение (app/audio_mix.auto_mix) ставит ducking на каждую фразу — до 80 отрезков.
MAX_DUCKS = 80
MAX_DIPS = 12
MAX_FX = 20
MIN_FX_SEC = 0.1
MAX_TIME_FX_SEC = 12.0
KEY_MIN_DB = -60.0
KEY_MAX_DB = 12.0
DIP_MIN_SEC = 0.15
DIP_MAX_SEC = 2.0
DIP_DEFAULT_SEC = 0.5


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
    "beep": "писк (тон вместо звука)",
    "noise": "тихий розовый шум вместо звука",
    # Любая цепочка эффектов (app/audio_fx.py) на отрезке: эквалайзер, компрессор, ревёрб, тон…
    # кроме меняющих длину (speed/tempo/reverse — для них свои fx отрезка).
    "chain": "цепочка эффектов (chain: [{fx, …}] или preset)",
}
TIME_FX = ("speed", "reverse")


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
        if kind == "chain":
            from app.audio_fx import TIMELESS_BANNED, normalize_chain

            chain = [e for e in normalize_chain(item.get("chain") if item.get("chain") is not None
                                                else item.get("preset")) if e["fx"] not in TIMELESS_BANNED]
            if not chain:
                continue
            entry["chain"] = chain
            entry.pop("amount", None)
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
