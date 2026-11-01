"""Озвучка RU переведённого клипа (закадровый перевод) — ПЛАТНАЯ, только с явного согласия.

Что делает: русские фразы перевода (``app/translation.py``: строки субтитров с окнами
оригинальных реплик) → речь по фразе через TTS → каждая фраза подгоняется под своё окно
→ одна дорожка на весь клип → «Файл для монтажа» (audio) → audio-слои ``dub_ru_*`` клипа
поверх оригинала. Оригинальный голос под фразами приглушается тем же механизмом, что у
звуков-слоёв (``props.duck`` → ``motion_render.AUDIO_DUCK_VOLUME``, ≈ −10 дБ): так звучит
обычный русский закадровый перевод — оригинал слышно, но тихо.

Слои — по группам фраз (пауза между фразами < ``GROUP_GAP_SEC`` — одна группа): каждая
группа — отдельный слой со своим ``offset`` в общей дорожке. Вырезы и переходы двигают
слой целиком — фразы остаются на своей речи; ducking — только пока звучит перевод.

Подгонка фразы под окно (``fit_plan``): окно — от начала реплики до начала следующей
(не дальше ``MAX_SPILL_SEC`` за конец реплики). Длиннее окна — ускоряем ``atempo`` до
``TEMPO_MAX`` (1.25); всё ещё длиннее — обрезаем с коротким затуханием и пишем в отчёт
``overflow_sec`` (лучше сократить перевод этой строки). Короче окна — по умолчанию
просто тишина после фразы (замедление речи звучит хуже); ``stretch=True`` — замедлить
до ``TEMPO_MIN`` (0.8).

Деньги. ДО синтеза всегда считается оценка: символы × цена провайдера за символ
(``PRICES_RUB_PER_CHAR`` — цены Polza из GET /models, их надо перепроверять). Синтез
запускается, только если: провайдер бесплатный (``none`` — сухой прогон тоном), ИЛИ
озвучка включена владельцем (``DUBBING_ENABLED=1``), передан ``confirm=true`` и оценка
не больше бюджета запуска (``budget_rub``, по умолчанию 200 ₽, и не выше
``DUBBING_MAX_BUDGET_RUB``). Чат агента дополнительно спрашивает владельца карточкой.
"""

from __future__ import annotations

import base64
import math
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.settings import settings

DEFAULT_PROVIDER = "polza"
PROVIDERS = ("polza", "none")
DEFAULT_BUDGET_RUB = 200.0
# Цена TTS Polza, ₽ за символ. ПРОВЕРЕНО 2026-11-01 — цены меняются: перед крупной озвучкой
# перепроверь и поправь здесь (оценка не должна занижать).
#
# * Прайс: GET /api/v1/models → top_provider.pricing.tts_per_million_characters (бесплатно).
#   eleven-v4 там 2 278.8 ₽ / 1 млн символов.
# * ЗАМЕР: настоящий прогон 2026-11-01 (3 фразы, 90 символов кириллицы, eleven-v4, голос brian)
#   стоил 0.4193 ₽ (usage.cost_rub в ответе = списание с баланса) — 4 659 ₽ / 1 млн символов,
#   в ~2.04 раза дороже прайса. Поэтому eleven-v4 — по замеру, остальные модели — прайс ×
#   PRICE_MARKUP (не мерили; запас, чтобы не занизить). Фактическая цена каждого запуска
#   пишется из usage.cost_rub провайдера в отчёт (dub.cost_rub, cost_source: provider).
PRICE_CHECKED = "2026-11-01"
PRICE_MARKUP = 2.05
PRICES_RUB_PER_CHAR: dict[str, float] = {
    "elevenlabs/eleven-v4": 0.4193 / 90,  # замер
    "openai/tts-1": 1745.22 / 1e6 * PRICE_MARKUP,
    "openai/tts-1-hd": 3490.44 / 1e6 * PRICE_MARKUP,
    "microsoft/mai-voice-2": 2506.70 / 1e6 * PRICE_MARKUP,
    "google/gemini-3.8-flash-tts": 2443.308 / 1e6 * PRICE_MARKUP,
    "minimax/speech-2.8-hd": 11394.08 / 1e6 * PRICE_MARKUP,
}
MEASURED_MODELS = ("elevenlabs/eleven-v4",)
FALLBACK_PRICE_RUB_PER_CHAR = 11394.08 / 1e6 * PRICE_MARKUP
MAX_PHRASE_CHARS = 5000  # предел input у /audio/speech

TEMPO_MIN = 0.8
TEMPO_MAX = 1.25
MAX_SPILL_SEC = 0.5
NEXT_GAP_SEC = 0.05
GROUP_GAP_SEC = 0.8
TRIM_FADE_SEC = 0.06
LAYER_PREFIX = "dub_ru"
# Сухой прогон: тон длиной как русская речь (~14 символов в секунду).
DRY_CHARS_PER_SEC = 14.0


class DubError(ValueError):
    """Озвучка не запущена: выключена, нет согласия, дороже бюджета, нет перевода."""


# --------------------------------------------------------------------------- #
# Провайдеры TTS
# --------------------------------------------------------------------------- #


@dataclass
class Speech:
    path: Path
    cost_rub: float | None = None
    raw_usage: dict = field(default_factory=dict)


class TTSProvider(Protocol):
    name: str
    paid: bool
    model: str
    voice: str

    def price_rub_per_char(self) -> float: ...

    def synthesize(self, text: str, out_path: Path) -> Speech: ...


class DryRunTTS:
    """Бесплатный сухой прогон: вместо речи — тихий тон длиной как русская фраза.

    Проверить окна, подгонку и ducking на слух, не тратя ни рубля."""

    name = "none"
    paid = False

    def __init__(self, model: str = "dry-run", voice: str = "tone") -> None:
        self.model, self.voice = model or "dry-run", voice or "tone"

    def price_rub_per_char(self) -> float:
        return 0.0

    def synthesize(self, text: str, out_path: Path) -> Speech:
        length = max(0.3, len(text) / DRY_CHARS_PER_SEC)
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
              f"sine=frequency=520:sample_rate=48000:duration={length:.3f}", "-af", "volume=0.25",
              "-ac", "1", str(out_path)])
        return Speech(out_path, 0.0)


class PolzaTTS:
    """Polza ``POST /api/v1/audio/speech`` (синхронно: JSON ``{audio: base64, contentType, usage}``).

    ПЛАТНО: вызывать только из ``dub_clip`` после всех проверок."""

    name = "polza"
    paid = True

    def __init__(self, model: str | None = None, voice: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, timeout: float = 120) -> None:
        self.model = (model or settings.polza_tts_model).strip()
        self.voice = (voice or settings.polza_tts_voice).strip()
        self.api_key = (api_key if api_key is not None else settings.polza_api_key).strip()
        self.base_url = (base_url or settings.polza_base_url).rstrip("/")
        self.timeout = timeout

    def price_rub_per_char(self) -> float:
        return PRICES_RUB_PER_CHAR.get(self.model, FALLBACK_PRICE_RUB_PER_CHAR)

    def synthesize(self, text: str, out_path: Path) -> Speech:
        import httpx

        if not self.api_key:
            raise DubError("POLZA_API_KEY не настроен")
        response = httpx.post(
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "input": text[:MAX_PHRASE_CHARS], "voice": self.voice,
                  "response_format": "mp3"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Polza TTS {response.status_code}: {_error_detail(response)}")
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("audio/") or ctype.startswith("application/octet-stream"):
            out_path.write_bytes(response.content)
            return Speech(out_path, None)
        data = response.json()
        audio = data.get("audio") if isinstance(data, dict) else None
        if not audio:
            raise RuntimeError("Polza TTS: в ответе нет audio")
        out_path.write_bytes(base64.b64decode(audio))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost_rub", usage.get("cost"))
        try:
            cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost = None
        return Speech(out_path, cost, {k: usage[k] for k in ("characters", "cost_rub", "cost") if k in usage})


def get_tts_provider(name: str | None, model: str | None = None, voice: str | None = None) -> TTSProvider:
    key = str(name or DEFAULT_PROVIDER).strip().lower()
    if key in ("none", "dry-run", "dry", "mock"):
        return DryRunTTS()
    if key == "polza":
        return PolzaTTS(model=model, voice=voice)
    raise DubError(f"provider: {', '.join(PROVIDERS)}")


# --------------------------------------------------------------------------- #
# Фразы, оценка, подгонка
# --------------------------------------------------------------------------- #


def dub_phrases(payload: dict | None) -> list[dict]:
    """Русские фразы перевода ``{i, start, end, text, speaker?}`` (шкала plain — как субтитры)."""
    payload = payload or {}
    if not isinstance(payload.get("translation"), dict):
        raise DubError("клип не переведён — сначала translate_subtitles (озвучиваем русский перевод)")
    out = []
    for i, line in enumerate(payload.get("lines") or []):
        text = " ".join(str(line.get("text") or "").split())
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start:
            out.append({"i": i, "start": round(start, 3), "end": round(end, 3), "text": text[:MAX_PHRASE_CHARS],
                        **({"speaker": line["speaker"]} if line.get("speaker") else {})})
    if not out:
        raise DubError("в переводе нет фраз")
    return out


def budget_cap(budget_rub: float | None) -> float:
    """Бюджет запуска: из запроса (по умолчанию 200 ₽), но не выше DUBBING_MAX_BUDGET_RUB."""
    try:
        wanted = float(budget_rub) if budget_rub is not None else DEFAULT_BUDGET_RUB
    except (TypeError, ValueError):
        wanted = DEFAULT_BUDGET_RUB
    if not math.isfinite(wanted) or wanted < 0:
        wanted = DEFAULT_BUDGET_RUB
    return round(min(wanted, float(settings.dubbing_max_budget_rub)), 2)


def estimate_cost(phrases: list[dict], provider: TTSProvider, budget_rub: float | None = None) -> dict:
    """Оценка ДО синтеза: символы × цена за символ. Ничего не вызывает (бесплатно)."""
    chars = sum(len(p["text"]) for p in phrases)
    price = provider.price_rub_per_char()
    rub = round(chars * price, 2)
    cap = budget_cap(budget_rub)
    out = {
        "provider": provider.name, "model": provider.model, "voice": provider.voice,
        "chars": chars, "phrases": len(phrases),
        "price_rub_per_char": price, "price_checked": PRICE_CHECKED,
        "rub": rub, "budget_rub": cap, "over_budget": bool(provider.paid and rub > cap),
        "enabled": bool(settings.dubbing_enabled or not provider.paid), "paid": provider.paid,
    }
    if provider.paid and provider.model not in PRICES_RUB_PER_CHAR:
        out["note"] = "цены модели нет в таблице — оценка по самой дорогой известной; проверь GET /models"
    elif provider.paid and provider.model not in MEASURED_MODELS:
        out["note"] = f"цена модели не замерена — прайс ×{PRICE_MARKUP:g} (запас)"
    return out


def phrase_windows(phrases: list[dict], total: float) -> list[float]:
    """Конец окна каждой фразы: до начала следующей, не дальше MAX_SPILL_SEC за конец реплики."""
    out = []
    for k, p in enumerate(phrases):
        nxt = phrases[k + 1]["start"] - NEXT_GAP_SEC if k + 1 < len(phrases) else float("inf")
        end = min(p["end"] + MAX_SPILL_SEC, nxt, total if total > 0 else float("inf"))
        out.append(round(max(end, p["start"] + 0.1), 3))
    return out


def fit_plan(duration: float, window: float, stretch: bool = False) -> dict:
    """Как уложить фразу длиной ``duration`` в окно ``window``: tempo, action, overflow_sec."""
    duration, window = max(0.01, float(duration)), max(0.05, float(window))
    ratio = duration / window
    if ratio > 1.0:
        tempo = min(ratio, TEMPO_MAX)
        fitted = duration / tempo
        if fitted > window + 0.01:
            return {"tempo": round(tempo, 4), "action": "trim", "overflow_sec": round(fitted - window, 3)}
        return {"tempo": round(tempo, 4), "action": "tempo", "overflow_sec": 0.0}
    if stretch and ratio >= TEMPO_MIN and ratio < 0.98:
        return {"tempo": round(ratio, 4), "action": "tempo", "overflow_sec": 0.0}
    return {"tempo": 1.0, "action": "pad", "overflow_sec": 0.0}


def fit_args(src: Path, dst: Path, plan: dict, window: float) -> list[str]:
    """ffmpeg: фраза → ровно ``window`` секунд (atempo, обрезка с затуханием, тишина в конце)."""
    chain = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=mono"]
    if abs(plan["tempo"] - 1.0) >= 1e-3:
        chain.append(f"atempo={plan['tempo']:.4f}")
    if plan["action"] == "trim":
        chain += [f"atrim=0:{window:.3f}", f"afade=t=out:st={max(0.0, window - TRIM_FADE_SEC):.3f}:d={TRIM_FADE_SEC}"]
    chain.append(f"apad=whole_dur={window:.3f}")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", ",".join(chain),
            "-t", f"{window:.3f}", "-ar", "48000", "-ac", "1", str(dst)]


def mix_args(pieces: list[tuple[Path, float]], total: float, dst: Path) -> list[str]:
    """ffmpeg: фразы по своим местам (adelay) в одну дорожку длиной клипа."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path, _start in pieces:
        args += ["-i", str(path)]
    graph = []
    for n, (_path, start) in enumerate(pieces):
        graph.append(f"[{n}:a]adelay=delays={int(round(start * 1000))}:all=1[d{n}]")
    joined = "".join(f"[d{n}]" for n in range(len(pieces)))
    graph.append(f"{joined}amix=inputs={len(pieces)}:duration=longest:normalize=0:dropout_transition=0,"
                 f"apad=whole_dur={total:.3f}[out]")
    codec = ["-c:a", "libmp3lame", "-b:a", "192k"] if dst.suffix == ".mp3" else []
    return args + ["-filter_complex", ";".join(graph), "-map", "[out]", "-t", f"{total:.3f}",
                   "-ar", "48000", "-ac", "2", *codec, str(dst)]


def probe_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                           "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=False)
    try:
        return float((proc.stdout or "0").strip() or 0)
    except ValueError:
        return 0.0


def build_dub_track(phrases: list[dict], tts: TTSProvider, total: float, out_path: Path, work: Path,
                    stretch: bool = False) -> tuple[list[dict], float | None]:
    """Синтез → подгонка → сведение. Возвращает (отчёт по фразам, фактическая цена ₽ или None)."""
    windows = phrase_windows(phrases, total)
    report, fitted = [], []
    spent: float | None = 0.0
    for k, (p, wend) in enumerate(zip(phrases, windows)):
        raw = work / f"p{k:03d}.mp3"
        speech = tts.synthesize(p["text"], raw)
        if speech.cost_rub is None:
            spent = None
        elif spent is not None:
            spent += speech.cost_rub
        length = probe_duration(speech.path)
        window = round(wend - p["start"], 3)
        plan = fit_plan(length, window, stretch)
        dst = work / f"f{k:03d}.wav"
        _run(fit_args(speech.path, dst, plan, window))
        fitted.append((dst, p["start"]))
        report.append({"i": p["i"], "start": p["start"], "end": p["end"], "window_end": wend, "text": p["text"],
                       "chars": len(p["text"]), "duration": round(length, 3), **plan})
    _run(mix_args(fitted, total, out_path))
    return report, (round(spent, 4) if spent is not None else None)


def dub_groups(report: list[dict], gap: float = GROUP_GAP_SEC) -> list[tuple[float, float]]:
    """Фразы подряд (пауза < gap) — одна группа ``(начало, конец окна)`` в шкале plain."""
    groups: list[list[float]] = []
    for item in report:
        a, b = float(item["start"]), float(item["window_end"])
        if groups and a - groups[-1][1] < gap:
            groups[-1][1] = max(groups[-1][1], b)
        else:
            groups.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in groups]


def _minus_cuts(start: float, end: float, cuts: list[dict]) -> list[tuple[float, float]]:
    """[start, end] без вырезов клипа: слой не должен перекрывать вырез — рендер сдвинул бы его
    целиком, и речь после выреза отстала бы на длину выреза."""
    spans = [(start, end)]
    for cut in sorted(cuts or [], key=lambda c: float(c.get("start", 0))):
        try:
            ca, cb = float(cut["start"]), float(cut["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out = []
        for a, b in spans:
            if cb <= a or ca >= b:
                out.append((a, b))
                continue
            if ca > a:
                out.append((a, ca))
            if cb < b:
                out.append((cb, b))
        spans = out
    return [(a, b) for a, b in spans if b - a >= 0.1]


def dub_layers(groups: list[tuple[float, float]], asset_id: int, clock: Any, cuts: list[dict] | None = None) -> list[dict]:
    """audio-слои ``dub_ru_N``: начало/конец — шкала clip (с наплывами), offset — место куска
    группы в дорожке (шкала plain, как субтитры). ``cuts`` — ``render_settings.cuts`` (plain):
    группа через вырез — двумя слоями по обе стороны выреза."""
    layers = []
    for a, b in groups:
        for pa, pb in _minus_cuts(a, b, cuts or []):
            start = clock.plain_to_clip(pa)
            end = max(start + 0.1, clock.plain_to_clip(pb))
            n = len(layers) + 1
            layers.append({
                "id": f"{LAYER_PREFIX}_{n}", "type": "audio", "name": f"Озвучка RU {n}", "start": round(start, 3),
                "end": round(end, 3),
                "props": {"asset_id": int(asset_id), "mode": "track", "volume": 1.0,
                          "offset": round(pa, 3), "duck": True, "fadeIn": 0.02, "fadeOut": 0.05},
            })
    return layers


def is_dub_layer(layer: Any) -> bool:
    return isinstance(layer, dict) and str(layer.get("id") or "").startswith(LAYER_PREFIX)


# --------------------------------------------------------------------------- #
# Главный вход
# --------------------------------------------------------------------------- #


def dub_clip(store: Any, clip_plan_id: int, provider: str | None = None, voice: str | None = None,
             model: str | None = None, confirm: bool = False, budget_rub: float | None = None,
             stretch: bool = False, remove: bool = False, tts: TTSProvider | None = None) -> dict:
    """Оценить или сделать озвучку RU клипа. Без ``confirm`` — ТОЛЬКО оценка (ничего не платим).

    ``tts`` — готовый провайдер (тесты подменяют). ``remove`` — снять слои озвучки."""
    from app import modules
    from app.timebase import ClipClock

    modules.require("dubbing", store)
    plan = store.get_clip_plan(clip_plan_id)
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    if remove:
        render = dict(plan.get("render_settings") or {})
        layers = [l for l in render.get("layers") or [] if not is_dub_layer(l)]
        removed = len(render.get("layers") or []) - len(layers)
        if removed:
            store.set_clip_plan_render_settings(clip_plan_id, {**render, "layers": layers})
        tr = payload.get("translation")
        if isinstance(tr, dict) and tr.get("dub"):
            store.set_clip_plan_subtitles(clip_plan_id, {**payload, "translation": {k: v for k, v in tr.items()
                                                                                   if k != "dub"}})
        return {"ok": True, "status": "removed", "layers_removed": removed}

    phrases = dub_phrases(payload)
    tts = tts or get_tts_provider(provider, model=model, voice=voice)
    estimate = estimate_cost(phrases, tts, budget_rub)
    if not confirm:
        note = (f"оценка: {estimate['chars']} символов ≈ {estimate['rub']:.2f} ₽ ({tts.model}, цена от "
                f"{PRICE_CHECKED}); синтез — confirm=true, только с согласия владельца")
        if tts.paid and not settings.dubbing_enabled:
            note += "; озвучка выключена (DUBBING_ENABLED=0) — включает владелец"
        if estimate["over_budget"]:
            note += f"; дороже бюджета {estimate['budget_rub']:.0f} ₽ — откажу"
        return {"ok": True, "status": "estimate", "estimate": estimate, "note": note}
    if tts.paid:
        if not settings.dubbing_enabled:
            raise DubError("озвучка выключена (DUBBING_ENABLED=0): платный синтез включает только владелец")
        if estimate["over_budget"]:
            raise DubError(f"оценка {estimate['rub']:.2f} ₽ больше бюджета {estimate['budget_rub']:.2f} ₽ — не запускаю")

    clock = ClipClock.for_plan(plan)
    total = round(clock.plain_total, 3)
    work = settings.tmp_dir / f"dub-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        from app.montage_assets import assets_dir, probe_asset

        track = work / "dub.mp3"
        report, spent = build_dub_track(phrases, tts, total, track, work, stretch)
        dest = assets_dir() / f"{uuid4().hex}-dub-ru-{clip_plan_id}.mp3"
        shutil.move(str(track), dest)
        meta = probe_asset(dest)
        asset = store.create_montage_asset(
            kind="audio", label=f"Озвучка RU · клип {clip_plan_id}"[:120],
            description=f"TTS {tts.name}/{tts.model}, голос {tts.voice}, {estimate['chars']} символов"[:500],
            tags="озвучка", file_path=str(dest), original_filename=dest.name, mime_type="audio/mpeg",
            size_bytes=dest.stat().st_size, **{**meta, "width": 0, "height": 0},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    render = dict(plan.get("render_settings") or {})
    layers = dub_layers(dub_groups(report), int(asset["id"]), clock, render.get("cuts"))
    kept = [l for l in render.get("layers") or [] if not is_dub_layer(l)]
    store.set_clip_plan_render_settings(clip_plan_id, {**render, "layers": kept + layers})
    overflows = [r for r in report if r["action"] == "trim"]
    dub = {
        "provider": tts.name, "model": tts.model, "voice": tts.voice, "asset_id": int(asset["id"]),
        "layers": [l["id"] for l in layers], "chars": estimate["chars"],
        "estimate_rub": estimate["rub"], "cost_rub": spent if spent is not None else estimate["rub"],
        "cost_source": "provider" if spent is not None else "estimate",
        "overflows": len(overflows), "phrases": report, "created_at": round(time.time()),
    }
    fresh = store.get_clip_plan_subtitles(clip_plan_id) or payload
    if isinstance(fresh.get("translation"), dict):
        store.set_clip_plan_subtitles(clip_plan_id, {**fresh, "translation": {**fresh["translation"], "dub": dub}})
    note = f"озвучка: {len(layers)} слоёв audio (dub_ru_*), оригинал под фразами приглушён (duck)"
    if overflows:
        note += (f"; {len(overflows)} фраз не влезли даже при ×{TEMPO_MAX} и обрезаны — сократи их перевод "
                 "(edit_subtitle_lines) и озвучь снова")
    return {"ok": True, "status": "done", "estimate": estimate, "dub": {k: v for k, v in dub.items()
                                                                          if k != "phrases"},
            "overflow_phrases": [{"i": r["i"], "text": r["text"], "overflow_sec": r["overflow_sec"]} for r in overflows],
            "note": note}


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg (озвучка): {(proc.stderr or proc.stdout or '')[-300:]}")


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.status_code)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or response.status_code)[:300]
    return str(error or response.status_code)[:300]
