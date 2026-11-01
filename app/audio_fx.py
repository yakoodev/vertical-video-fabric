"""Цепочки звуковых эффектов: эквалайзер, компрессор, де-эссер, ревёрб, дилей, тон, темп, фильтры
со свипом, стерео, перегруз/лоу-фай, пресеты (телефон, рация, под водой…).

Цепочка — список ``[{fx, …параметры}]`` по порядку (как педали): ``normalize_chain`` чистит её
(неизвестное — отброшено с пометкой, числа — в рамки), ``chain_filters`` собирает ЛИНЕЙНУЮ строку
фильтров ffmpeg (через запятую — встаёт в любой граф без своих меток), ``chain_rate`` — во сколько
раз цепочка меняет длину звука (speed/tempo: слою нужно прочитать больше/меньше файла).

Где живут цепочки:

* у звукового слоя — ``props.fx`` (app/motion.py, рендер — app/motion_render.py);
* у голоса — ``render_settings.mix.voice_fx`` (холст: слои-голос, клип с исходником — звук исходника);
* у музыки клипа — ``render_settings.mix.music_fx`` (проход музыки, app/render.py);
* на отрезке звука исходника — ``set_audio_fx`` с ``fx: "chain"`` (app/audio_edit.py) — та же цепочка.

Пресеты старых эффектов отрезка (``muffle``, ``underwater``, ``echo``, ``reverb``, ``phone``,
``radio``, ``denoise``) звучат ровно как в ``set_audio_fx``: это одна функция ``audio_edit.fx_chain``.

Свип фильтра (``lowpass``/``highpass`` с ``to``): частота плавно (экспонентой) едет от ``f`` к ``to``
за ``sweep_start..sweep_end`` секунд звука слоя — ``asendcmd`` раз в ``SWEEP_STEP_SEC``.
Всё чистое (строки), без медиа — проверяют тесты, ffmpeg гоняют тесты на синтетике.
"""

from __future__ import annotations

import math
from typing import Any

MAX_CHAIN = 8
# Меняют длину или направление звука: на отрезке исходника, у голоса и музыки клипа (шкала ролика)
# их нет — только у звукового слоя (он читает больше/меньше своего файла).
TIMELESS_BANNED = ("speed", "tempo", "reverse")
MAX_BANDS = 6
SWEEP_STEP_SEC = 0.04
SAMPLE_RATE = 48000

# Параметр: (умолчание, мин, макс, единица/пояснение). None в умолчании — «нет» (необязателен).
EFFECTS: dict[str, dict] = {
    "eq": {
        "label": "Эквалайзер",
        "about": "срез низа/верха, полки и «присутствие» голоса; bands — до 6 полос {f, gain, q}",
        "params": {
            "low_cut": (0.0, 0.0, 1000.0, "Гц, срез низа (0 — выкл; голос 80–120)"),
            "high_cut": (0.0, 0.0, 20000.0, "Гц, срез верха (0 — выкл)"),
            "low": (0.0, -12.0, 12.0, "дБ, полка 120 Гц (тело)"),
            "presence": (0.0, -12.0, 12.0, "дБ на 3.5 кГц (разборчивость голоса, +2…+4)"),
            "high": (0.0, -12.0, 12.0, "дБ, полка 8 кГц (воздух)"),
        },
    },
    "highpass": {
        "label": "Срез низа (high-pass)",
        "about": "убирает низ ниже f; to — свип частоты к to (переход «в трубу»)",
        "params": {
            "f": (120.0, 20.0, 12000.0, "Гц"),
            "to": (None, 20.0, 12000.0, "Гц — свип к этой частоте"),
            "sweep_start": (None, 0.0, 600.0, "с от начала звука — начало свипа (0)"),
            "sweep_end": (None, 0.0, 600.0, "с — конец свипа (конец звука)"),
        },
    },
    "lowpass": {
        "label": "Срез верха (low-pass)",
        "about": "глушит верх выше f («за стеной»); to — свип (уход в глухо на переход и обратно)",
        "params": {
            "f": (8000.0, 60.0, 20000.0, "Гц"),
            "to": (None, 60.0, 20000.0, "Гц — свип к этой частоте"),
            "sweep_start": (None, 0.0, 600.0, "с от начала звука"),
            "sweep_end": (None, 0.0, 600.0, "с"),
        },
    },
    "compressor": {
        "label": "Компрессор",
        "about": "выравнивает громкость: всё выше порога сжимается в ratio раз",
        "params": {
            "threshold_db": (-18.0, -60.0, 0.0, "дБ"),
            "ratio": (3.0, 1.0, 20.0, ":1"),
            "attack_ms": (10.0, 0.1, 500.0, "мс"),
            "release_ms": (150.0, 10.0, 3000.0, "мс"),
            "makeup_db": (0.0, 0.0, 24.0, "дБ подъёма после"),
            "knee_db": (2.8, 1.0, 8.0, "дБ"),
        },
    },
    "deesser": {
        "label": "Де-эссер",
        "about": "убирает свист «с/ш/щ» у голоса",
        "params": {"amount": (0.5, 0.0, 1.0, "сила")},
    },
    "gate": {
        "label": "Гейт",
        "about": "глушит тихий фон между фразами",
        "params": {"threshold_db": (-45.0, -80.0, -10.0, "дБ"), "release_ms": (150.0, 10.0, 2000.0, "мс")},
    },
    "denoise": {
        "label": "Шумодав",
        "about": "FFT-шумодав микрофона",
        "params": {"amount": (0.5, 0.0, 1.0, "сила")},
    },
    "reverb": {
        "label": "Ревёрб",
        "about": "пространство: room — комната, hall — зал, plate — пластина (яркий вокал), cave — пещера",
        "params": {"preset": ("room", None, None, "room | hall | plate | cave"), "mix": (0.3, 0.0, 1.0, "доля эффекта")},
    },
    "delay": {
        "label": "Дилей / эхо",
        "about": "повторы через ms с затуханием feedback",
        "params": {
            "ms": (250.0, 20.0, 2000.0, "мс между повторами"),
            "feedback": (0.35, 0.0, 0.9, "затухание повторов"),
            "mix": (0.35, 0.0, 1.0, "громкость повторов"),
            "taps": (3, 1, 6, "сколько повторов"),
        },
    },
    "pitch": {
        "label": "Тон",
        "about": "сдвиг тона в полутонах, длина та же (−5 — «монстр», +7 — «бурундук»)",
        "params": {"semitones": (0.0, -12.0, 12.0, "полутона")},
    },
    "speed": {
        "label": "Скорость (с тоном)",
        "about": "как пластинка: быстрее — выше; слой читает больше/меньше файла",
        "params": {"rate": (1.0, 0.5, 2.0, "×")},
    },
    "tempo": {
        "label": "Темп (без тона)",
        "about": "быстрее/медленнее, тон тот же",
        "params": {"rate": (1.0, 0.5, 2.0, "×")},
    },
    "stereo": {
        "label": "Ширина стерео",
        "about": "0 — моно, 1 — как есть, 2 — шире",
        "params": {"width": (1.0, 0.0, 2.5, "×")},
    },
    "distortion": {
        "label": "Перегруз",
        "about": "мягкий клиппинг (грязный, «рваный» звук)",
        "params": {"drive": (0.4, 0.0, 1.0, "сила")},
    },
    "bitcrush": {
        "label": "Лоу-фай (bitcrush)",
        "about": "меньше бит и частоты — «8-бит», ретро-игры",
        "params": {
            "bits": (8.0, 2.0, 16.0, "бит"),
            "downsample": (1.0, 1.0, 50.0, "×, уменьшение частоты"),
            "mix": (0.6, 0.0, 1.0, "доля эффекта"),
        },
    },
    "tremolo": {
        "label": "Тремоло",
        "about": "дрожание громкости",
        "params": {"rate": (6.0, 0.5, 20.0, "Гц"), "depth": (0.5, 0.0, 1.0, "глубина")},
    },
    "reverse": {"label": "Задом наперёд", "about": "звук слоя наоборот", "params": {}},
    "gain": {"label": "Усиление", "about": "громкость в дБ", "params": {"db": (0.0, -30.0, 12.0, "дБ")}},
    # Старые эффекты отрезка (set_audio_fx) — тем же звуком: audio_edit.fx_chain.
    "legacy": {
        "label": "Эффект отрезка",
        "about": "muffle, underwater, echo, reverb, phone, radio, denoise — как в set_audio_fx",
        "params": {"kind": ("muffle", None, None, "muffle | underwater | echo | reverb | phone | radio | denoise"),
                   "amount": (0.5, 0.0, 1.0, "сила")},
    },
}
LEGACY_KINDS = ("muffle", "underwater", "echo", "reverb", "phone", "radio", "denoise")
REVERB_PRESETS: dict[str, dict] = {
    # (задержки мс, затухания при mix 1) — многоотводное эхо как плотная реверберация.
    "room": {"delays": (11, 19, 29, 37, 47, 59), "decays": (0.55, 0.48, 0.42, 0.36, 0.3, 0.24), "label": "комната"},
    "hall": {"delays": (37, 61, 89, 127, 173, 229, 293), "decays": (0.6, 0.55, 0.5, 0.44, 0.38, 0.31, 0.25),
             "label": "зал"},
    "plate": {"delays": (7, 13, 17, 23, 31, 41, 53), "decays": (0.5, 0.47, 0.44, 0.4, 0.36, 0.3, 0.24),
              "label": "пластина (яркий вокал)", "bright": True},
    "cave": {"delays": (53, 97, 151, 211, 283, 367, 461), "decays": (0.7, 0.62, 0.55, 0.48, 0.41, 0.34, 0.27),
             "label": "пещера"},
}

# Пресеты: готовые цепочки. Время свипа — от начала звука до конца (если не задано).
PRESETS: dict[str, dict] = {
    "telephone": {"about": "телефонная трубка", "chain": [{"fx": "legacy", "kind": "phone", "amount": 0.5}]},
    "radio": {"about": "рация / радиоэфир", "chain": [{"fx": "legacy", "kind": "radio", "amount": 0.5}]},
    "underwater": {"about": "под водой", "chain": [{"fx": "legacy", "kind": "underwater", "amount": 0.6}]},
    "muffle": {"about": "за стеной, глухо", "chain": [{"fx": "legacy", "kind": "muffle", "amount": 0.6}]},
    "megaphone": {"about": "мегафон: узкая полоса + перегруз",
                  "chain": [{"fx": "eq", "low_cut": 500, "high_cut": 4000, "presence": 6},
                            {"fx": "distortion", "drive": 0.35}, {"fx": "gain", "db": -2}]},
    "lofi": {"about": "лоу-фай: глухо, 8 бит, узко",
             "chain": [{"fx": "lowpass", "f": 3500}, {"fx": "bitcrush", "bits": 8, "downsample": 2, "mix": 0.5},
                       {"fx": "stereo", "width": 0.6}]},
    "voice_clean": {"about": "голос чище: срез низа, де-эссер, лёгкая компрессия, разборчивость",
                    "chain": [{"fx": "eq", "low_cut": 90, "presence": 3}, {"fx": "deesser", "amount": 0.4},
                              {"fx": "compressor", "threshold_db": -20, "ratio": 3, "attack_ms": 8,
                               "release_ms": 120, "makeup_db": 2}]},
    "voice_broadcast": {"about": "радийный голос: плотнее, ближе",
                        "chain": [{"fx": "eq", "low_cut": 70, "low": 2, "presence": 4, "high": 2},
                                  {"fx": "compressor", "threshold_db": -24, "ratio": 4, "attack_ms": 5,
                                   "release_ms": 100, "makeup_db": 4}, {"fx": "deesser", "amount": 0.5}]},
    "voice_warm": {"about": "тёплый голос: мягче верх",
                   "chain": [{"fx": "eq", "low_cut": 80, "low": 2, "high": -3},
                             {"fx": "compressor", "threshold_db": -20, "ratio": 2.5, "makeup_db": 2}]},
    "small_room": {"about": "немного комнаты", "chain": [{"fx": "reverb", "preset": "room", "mix": 0.25}]},
    "hall": {"about": "большой зал", "chain": [{"fx": "reverb", "preset": "hall", "mix": 0.35}]},
    "cave": {"about": "пещера, эхо гулкое", "chain": [{"fx": "reverb", "preset": "cave", "mix": 0.45}]},
    "dream": {"about": "мечтательно: пластина + мягкий верх",
              "chain": [{"fx": "lowpass", "f": 6000}, {"fx": "reverb", "preset": "plate", "mix": 0.45}]},
    "echo": {"about": "эхо на слове", "chain": [{"fx": "delay", "ms": 300, "feedback": 0.4, "mix": 0.4, "taps": 3}]},
    "slapback": {"about": "короткий «слэпбэк» (рок-н-ролл)",
                 "chain": [{"fx": "delay", "ms": 110, "feedback": 0.2, "mix": 0.35, "taps": 1}]},
    "monster": {"about": "низкий «монстр»", "chain": [{"fx": "pitch", "semitones": -5}, {"fx": "eq", "low": 3}]},
    "chipmunk": {"about": "бурундук", "chain": [{"fx": "pitch", "semitones": 7}]},
    "robot": {"about": "робот: лоу-фай + тремоло",
              "chain": [{"fx": "bitcrush", "bits": 6, "downsample": 3, "mix": 0.5},
                        {"fx": "tremolo", "rate": 12, "depth": 0.4}]},
    "sweep_in": {"about": "свип перехода: из глухого в полный звук (lowpass 300 → 18000)",
                 "chain": [{"fx": "lowpass", "f": 300, "to": 18000}]},
    "sweep_out": {"about": "свип перехода: уход в глухо (lowpass 18000 → 300)",
                  "chain": [{"fx": "lowpass", "f": 18000, "to": 300}]},
    "thin_out": {"about": "уход «в трубу» (highpass 40 → 1500)", "chain": [{"fx": "highpass", "f": 40, "to": 1500}]},
    "slowdown": {"about": "замедление с понижением тона (как выключили)", "chain": [{"fx": "speed", "rate": 0.75}]},
}


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


def _norm_effect(raw: dict, where: str, problems: list[str]) -> dict | None:
    kind = str(raw.get("fx") or raw.get("type") or "").strip().lower()
    if kind in LEGACY_KINDS and kind not in EFFECTS:
        raw = {"fx": "legacy", "kind": kind, **({"amount": raw["amount"]} if "amount" in raw else {})}
        kind = "legacy"
    spec = EFFECTS.get(kind)
    if spec is None:
        problems.append(f"{where}: эффекта {kind or '?'!r} нет (есть: {', '.join(EFFECTS)})")
        return None
    out: dict = {"fx": kind}
    for name, (default, lo, hi, _unit) in spec["params"].items():
        value = raw.get(name)
        if name == "preset":
            preset = str(value or default)
            if preset not in REVERB_PRESETS:
                problems.append(f"{where}.preset: {preset!r} — из {', '.join(REVERB_PRESETS)}")
                preset = str(default)
            out[name] = preset
            continue
        if name == "kind":
            legacy = str(value or default)
            if legacy not in LEGACY_KINDS:
                problems.append(f"{where}.kind: {legacy!r} — из {', '.join(LEGACY_KINDS)}")
                return None
            out[name] = legacy
            continue
        number = _num(value)
        if number is None:
            if value is not None:
                problems.append(f"{where}.{name}: нужно число")
            if default is None:
                continue
            number = float(default)
        if number < lo or number > hi:
            problems.append(f"{where}.{name}: {number:g} вне {lo:g}..{hi:g} — подрезано")
            number = _clamp(number, lo, hi)
        out[name] = int(round(number)) if isinstance(default, int) and not isinstance(default, bool) else round(number, 3)
    if kind == "eq":
        bands = []
        for i, band in enumerate((raw.get("bands") or [])[:MAX_BANDS] if isinstance(raw.get("bands"), list) else []):
            f = _num((band or {}).get("f")) if isinstance(band, dict) else None
            g = _num((band or {}).get("gain")) if isinstance(band, dict) else None
            if f is None or g is None:
                problems.append(f"{where}.bands[{i}]: нужны f (Гц) и gain (дБ)")
                continue
            q = _num(band.get("q"))
            bands.append({"f": round(_clamp(f, 20.0, 20000.0), 1), "gain": round(_clamp(g, -24.0, 24.0), 2),
                          "q": round(_clamp(q if q is not None else 1.0, 0.1, 10.0), 3)})
        if bands:
            out["bands"] = bands
    if kind in ("lowpass", "highpass") and "to" not in out:
        out.pop("sweep_start", None)
        out.pop("sweep_end", None)
    return out


def normalize_chain(raw: Any, problems: list[str] | None = None) -> list[dict]:
    """Цепочка эффектов: список ``{fx, …}`` | имя пресета | ``{preset: имя}`` | смесь. Неизвестное
    выброшено (с пометкой в ``problems``), числа в рамках, не длиннее ``MAX_CHAIN``."""
    sink: list[str] = problems if problems is not None else []
    if raw is None or raw == "" or raw == []:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[dict] = []
    for i, item in enumerate(items):
        where = f"fx[{i}]"
        if isinstance(item, str):
            item = {"preset": item}
        if not isinstance(item, dict):
            sink.append(f"{where}: нужен объект {{fx, …}} или имя пресета")
            continue
        if item.get("preset") is not None and not item.get("fx"):
            name = str(item["preset"]).strip().lower()
            preset = PRESETS.get(name)
            if preset is None:
                sink.append(f"{where}: пресета {name!r} нет (есть: {', '.join(PRESETS)})")
                continue
            for k, eff in enumerate(preset["chain"]):
                got = _norm_effect(eff, f"{where}<{name}>[{k}]", sink)
                if got:
                    out.append(got)
            continue
        got = _norm_effect(item, where, sink)
        if got:
            out.append(got)
    if len(out) > MAX_CHAIN:
        sink.append(f"больше {MAX_CHAIN} эффектов — лишние отброшены")
        out = out[:MAX_CHAIN]
    return out


def chain_rate(chain: list[dict] | None) -> float:
    """Во сколько раз цепочка ускоряет звук (speed × tempo): слой длиной L читает L·rate файла."""
    rate = 1.0
    for eff in chain or []:
        if eff.get("fx") in ("speed", "tempo"):
            rate *= float(eff.get("rate") or 1.0)
    return rate


def has_sweep(chain: list[dict] | None) -> bool:
    return any(eff.get("fx") in ("lowpass", "highpass") and eff.get("to") is not None for eff in chain or [])


# --------------------------------------------------------------------------- #
# Фильтры ffmpeg
# --------------------------------------------------------------------------- #


def _atempo(rate: float) -> str:
    """atempo старых ffmpeg — 0.5..2 на экземпляр: длинные множители — цепочкой."""
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


def _sweep(kind: str, eff: dict, name: str, length: float | None, offset: float = 0.0) -> str:
    """asendcmd с частотой по экспоненте от f к to (шаг SWEEP_STEP_SEC); ``offset`` — секунда потока,
    с которой начинается звук (эффект на отрезке исходника)."""
    f0, f1 = float(eff["f"]), float(eff["to"])
    t0 = float(eff.get("sweep_start") or 0.0)
    end_default = length if length and length > 0 else t0 + 1.0
    t1 = float(eff["sweep_end"]) if eff.get("sweep_end") is not None else end_default
    if t1 <= t0 + 0.01:
        t1 = t0 + 0.01
    steps = max(2, min(400, int(math.ceil((t1 - t0) / SWEEP_STEP_SEC))))
    cmds = []
    for i in range(steps + 1):
        u = i / steps
        f = f0 * (f1 / f0) ** u
        cmds.append(f"{offset + t0 + (t1 - t0) * u:.3f} {kind}@{name} f {f:.1f}")
    return "asendcmd=c='" + ";".join(cmds) + "'"


def effect_filter(eff: dict, uid: str = "fx", length: float | None = None, sample_rate: int = SAMPLE_RATE,
                  offset: float = 0.0) -> str:
    """Один эффект → фильтры ffmpeg через запятую (без меток). ``length`` — длина звука, с
    (свип без конца — до конца звука), ``uid`` — уникальное имя экземпляра фильтра для свипа."""
    kind = eff["fx"]
    if kind == "eq":
        parts = []
        if float(eff.get("low_cut") or 0) > 0:
            parts.append(f"highpass=f={float(eff['low_cut']):g}:p=2")
        if float(eff.get("high_cut") or 0) > 0:
            parts.append(f"lowpass=f={float(eff['high_cut']):g}:p=2")
        if abs(float(eff.get("low") or 0)) > 0.01:
            parts.append(f"bass=g={float(eff['low']):g}:f=120:w=0.7")
        if abs(float(eff.get("presence") or 0)) > 0.01:
            parts.append(f"equalizer=f=3500:t=q:w=1.2:g={float(eff['presence']):g}")
        if abs(float(eff.get("high") or 0)) > 0.01:
            parts.append(f"treble=g={float(eff['high']):g}:f=8000:w=0.7")
        for band in eff.get("bands") or []:
            parts.append(f"equalizer=f={band['f']:g}:t=q:w={band['q']:g}:g={band['gain']:g}")
        return ",".join(parts) or "anull"
    if kind in ("lowpass", "highpass"):
        if eff.get("to") is not None:
            name = f"sw{uid}"
            return f"{_sweep(kind, eff, name, length, offset)},{kind}@{name}=f={float(eff['f']):g}:p=2"
        return f"{kind}=f={float(eff['f']):g}:p=2"
    if kind == "compressor":
        threshold = 10 ** (float(eff["threshold_db"]) / 20)
        makeup = 10 ** (float(eff.get("makeup_db") or 0) / 20)
        return (f"acompressor=threshold={threshold:.5f}:ratio={float(eff['ratio']):g}:attack={float(eff['attack_ms']):g}"
                f":release={float(eff['release_ms']):g}:makeup={makeup:.4f}:knee={float(eff['knee_db']):g}")
    if kind == "deesser":
        a = float(eff.get("amount", 0.5))
        return f"deesser=i={a:.3f}:m={0.3 + 0.5 * a:.3f}:f=0.5:s=o"
    if kind == "gate":
        threshold = 10 ** (float(eff["threshold_db"]) / 20)
        return f"agate=threshold={threshold:.5f}:ratio=4:attack=5:release={float(eff['release_ms']):g}"
    if kind == "denoise":
        return f"afftdn=nr={round(8 + 22 * float(eff.get('amount', 0.5)))}:nf=-45:tn=1"
    if kind == "reverb":
        spec = REVERB_PRESETS[str(eff.get("preset") or "room")]
        mix = float(eff.get("mix", 0.3))
        decays = [d * mix for d in spec["decays"]]
        out_gain = 1.0 / math.sqrt(1.0 + sum(d * d for d in decays) * 2.0)
        chain = (f"aecho=1:{out_gain:.3f}:{'|'.join(str(d) for d in spec['delays'])}:"
                 f"{'|'.join(f'{max(0.001, d):.3f}' for d in decays)}")
        return chain + (",treble=g=2:f=6000" if spec.get("bright") else "")
    if kind == "delay":
        ms, fb, mix, taps = float(eff["ms"]), float(eff["feedback"]), float(eff["mix"]), int(eff.get("taps") or 3)
        delays = "|".join(f"{ms * (k + 1):g}" for k in range(taps))
        decays = "|".join(f"{max(0.001, mix * fb ** k):.3f}" for k in range(taps))
        return f"aecho=1:1:{delays}:{decays}"
    if kind == "pitch":
        s = float(eff.get("semitones") or 0.0)
        if abs(s) < 0.01:
            return "anull"
        r = 2 ** (s / 12)
        return f"asetrate={round(sample_rate * r)},aresample={sample_rate},{_atempo(1 / r)}"
    if kind == "speed":
        r = float(eff.get("rate") or 1.0)
        return "anull" if abs(r - 1) < 1e-3 else f"asetrate={round(sample_rate * r)},aresample={sample_rate}"
    if kind == "tempo":
        r = float(eff.get("rate") or 1.0)
        return "anull" if abs(r - 1) < 1e-3 else _atempo(r)
    if kind == "stereo":
        w = float(eff.get("width", 1.0))
        if w <= 0.001:
            return "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1"
        return f"extrastereo=m={w:g}:c=1"
    if kind == "distortion":
        d = float(eff.get("drive", 0.4))
        return f"volume={6 + 24 * d:.1f}dB,asoftclip=type=tanh,volume={-(4 + 20 * d):.1f}dB"
    if kind == "bitcrush":
        return (f"acrusher=bits={float(eff['bits']):g}:samples={float(eff['downsample']):g}"
                f":mix={float(eff['mix']):g}:mode=log:aa=0.5")
    if kind == "tremolo":
        return f"tremolo=f={float(eff['rate']):g}:d={max(0.01, min(1.0, float(eff['depth']))):g}"
    if kind == "reverse":
        return "areverse"
    if kind == "gain":
        return f"volume={float(eff.get('db') or 0):g}dB"
    if kind == "legacy":
        from app.audio_edit import fx_chain

        return fx_chain(str(eff["kind"]), float(eff.get("amount", 0.5)))
    return "anull"


def chain_filters(chain: list[dict] | None, uid: str = "c", length: float | None = None, offset: float = 0.0) -> str:
    """Цепочка → линейная строка фильтров ffmpeg (``anull`` — пусто). Формат на входе — любой:
    в начале цепочки звук приводится к 48 кГц стерео (как во всём рендере)."""
    parts = [effect_filter(eff, f"{uid}{n}", length, offset=offset) for n, eff in enumerate(chain or [])]
    parts = [p for p in parts if p and p != "anull"]
    if not parts:
        return "anull"
    return ",".join([f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo", *parts])


def describe_chain(chain: list[dict] | None) -> str:
    """Коротко по-русски: «Эквалайзер → Компрессор → Ревёрб (зал)»."""
    out = []
    for eff in chain or []:
        label = EFFECTS.get(eff.get("fx"), {}).get("label", eff.get("fx"))
        if eff.get("fx") == "reverb":
            label += f" ({REVERB_PRESETS.get(eff.get('preset'), {}).get('label', eff.get('preset'))})"
        elif eff.get("fx") == "legacy":
            label = str(eff.get("kind"))
        elif eff.get("fx") in ("lowpass", "highpass") and eff.get("to") is not None:
            label += f" свип {eff['f']:g}→{eff['to']:g} Гц"
        out.append(label)
    return " → ".join(out)


def reference() -> dict:
    """Справочник для агента (``audio_fx_reference``): эффекты, параметры, пресеты, где применять."""
    effects = {}
    for kind, spec in EFFECTS.items():
        params = {}
        for name, (default, lo, hi, unit) in spec["params"].items():
            item: dict = {"default": default, "about": unit}
            if lo is not None:
                item.update(min=lo, max=hi)
            params[name] = item
        if kind == "eq":
            params["bands"] = {"about": f"до {MAX_BANDS} полос [{{f Гц 20..20000, gain дБ −24..24, q 0.1..10}}]"}
        effects[kind] = {"label": spec["label"], "about": spec["about"], "params": params}
    return {
        "ok": True,
        "effects": effects,
        "reverb_presets": {k: v["label"] for k, v in REVERB_PRESETS.items()},
        "presets": {k: {"about": v["about"], "chain": v["chain"]} for k, v in PRESETS.items()},
        "max_chain": MAX_CHAIN,
        "where": {
            "layer": "звуковой слой: set_audio_chain target=layer layer_id=… (или props.fx в add_layers/update_layer)",
            "voice": "голос: set_audio_chain target=voice — холст: слои-голос (озвучка, реплики), клип с "
                     "исходником: весь звук исходника",
            "music": "фоновая музыка клипа: set_audio_chain target=music",
            "span": "отрезок звука исходника: set_audio_fx [{start, end, fx: \"chain\", chain: […]}]",
        },
        "rules": [
            "цепочка — по порядку, как педали: эквалайзер → компрессор → де-эссер → пространство (ревёрб/дилей)",
            "свип: lowpass/highpass с to — частота едет от f к to за sweep_start..sweep_end (с от начала звука "
            "слоя, по умолчанию весь слой); переход «из глухого» — пресет sweep_in, «в глухо» — sweep_out",
            "speed/tempo меняют, сколько файла читает слой (длина слоя та же); pitch длину не меняет",
            "громкость после эффектов проверяет audio_mix_report; финал всё равно −14 LUFS, пик ≤ −1 dBTP",
            "звуки — только настоящие записи паков (search_pack kind sfx); синтетику не подставляем",
        ],
    }
