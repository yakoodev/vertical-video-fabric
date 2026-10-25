"""Стоп-кадр посреди клипа (``render_settings.freezes``, инструмент ``freeze_frame``).

Кадр готового ролика в момент ``at`` замирает на ``duration`` секунд (0.2–2.0):

* ``audio: "silence"`` (по умолчанию) — стоп-кадр ВСТАВЛЯЕТСЯ: ролик длиннее на
  ``duration``, звук исходника на это время молчит, музыка фона играет дальше (она ложится
  на весь готовый ролик). Всё после ``at`` — субтитры, слои, камера, звуки слоёв, вставки,
  тишина/громкость отрезков — сдвигается на ``duration``.
* ``audio: "continue"`` — стоп-кадр ПОВЕРХ: длина та же, звук исходника идёт дальше
  (стример договаривает), картинка стоит и продолжается с ``at + duration``.

``layers``: ``"freeze"`` (по умолчанию) — слои, камера и анимированные субтитры стоят вместе
с кадром; ``"continue"`` — продолжают анимацию на замершем кадре.

Время ``at`` — шкала ``final`` (готовый ролик после вырезов, БЕЗ стоп-кадров — как
finished_duration, contact_sheet, listen); несколько стоп-кадров — каждый в своей ``at``.

Как это делает рендер. Основа (куски после вырезов, со звуком исходника) получает стоп-кадры
одним проходом ffmpeg (``build_freeze_args``) — до компоновщика, субтитров и музыки, поэтому
музыка играет и на вставленном стоп-кадре. Всё, что ложится позже во времени готового
ролика, переводится ``FreezeMap`` (вставки сдвигают время, как вырезы его съедают — тот же
интерфейс, что у ``app.cuts.CutMap``). Слои со стоящими слоями — не сдвигаются, а
компоновщик читает их во «времени слоёв» (``comp.freezes``, ``motion_time`` — то же в Node).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

FREEZE_MIN_SEC = 0.2
FREEZE_MAX_SEC = 2.0
AUDIO_MODES = ("silence", "continue")
LAYER_MODES = ("freeze", "continue")
MAX_FREEZES = 8
# Между стоп-кадрами — хоть немного живого видео.
MIN_GAP_SEC = 0.1


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def normalize_freeze(raw: Any) -> dict | None:
    """``{at, duration, audio, layers}`` или None (мусор)."""
    if not isinstance(raw, dict):
        return None
    at, duration = _num(raw.get("at")), _num(raw.get("duration", 1.0))
    if at is None or at < 0 or duration is None:
        return None
    audio = raw.get("audio") if raw.get("audio") in AUDIO_MODES else "silence"
    layers = raw.get("layers") if raw.get("layers") in LAYER_MODES else "freeze"
    return {
        "at": round(at, 3),
        "duration": round(min(FREEZE_MAX_SEC, max(FREEZE_MIN_SEC, duration)), 3),
        "audio": audio,
        "layers": layers,
    }


def normalize_freezes(raw: Any) -> list[dict]:
    """Список стоп-кадров по времени, без наложений (позже пришедший на занятое место — прочь)."""
    items = [f for f in (normalize_freeze(r) for r in (raw if isinstance(raw, list) else [])) if f]
    out: list[dict] = []
    for item in sorted(items, key=lambda f: f["at"]):
        if out and item["at"] < _occupied_until(out[-1]) + MIN_GAP_SEC:
            continue
        out.append(item)
        if len(out) >= MAX_FREEZES:
            break
    return out


def _occupied_until(freeze: dict) -> float:
    """До какой секунды (без стоп-кадров) занято: вставка — точка, поверх — отрезок."""
    return freeze["at"] + (0.0 if is_insert(freeze) else freeze["duration"])


def is_insert(freeze: dict) -> bool:
    return freeze.get("audio", "silence") == "silence"


def holds_layers(freeze: dict) -> bool:
    return freeze.get("layers", "freeze") == "freeze"


def freeze_problem(raw: Any, finished: float | None = None) -> str | None:
    """Почему стоп-кадр из запроса не годится (None — годится): длительность вне 0.2–2 с,
    момент за концом ролика, неизвестный режим — ошибка, а не молчаливая поправка."""
    if not isinstance(raw, dict):
        return "нужен {at, duration, audio, layers}"
    at, duration = _num(raw.get("at")), _num(raw.get("duration", 1.0))
    if at is None or at < 0:
        return "at — секунда готового ролика ≥ 0"
    if duration is None or not FREEZE_MIN_SEC <= duration <= FREEZE_MAX_SEC:
        return f"duration — {FREEZE_MIN_SEC:g}..{FREEZE_MAX_SEC:g} с"
    if raw.get("audio") not in (None, *AUDIO_MODES):
        return 'audio — "silence" (стоп-кадр вставляется, звук исходника молчит) или "continue" (звук идёт)'
    if raw.get("layers") not in (None, *LAYER_MODES):
        return 'layers — "freeze" (слои стоят с кадром) или "continue" (слои анимируются дальше)'
    if finished is not None:
        tail = duration if raw.get("audio") == "continue" else 0.0
        if at + tail > finished - 0.05:
            return f"ролик длится {finished:.2f} с — стоп-кадр в {at:g} с не помещается"
    return None


class FreezeMap:
    """Время готового ролика без стоп-кадров → со стоп-кадрами (интерфейс ``app.cuts.CutMap``).

    Вставка в ``at`` сдвигает всё строго после ``at`` на ``duration``; отрезок через ``at``
    растягивается (слово, строка, слой, тишина продолжаются на стоп-кадре). Стоп-кадры поверх
    (звук идёт) время не двигают. ``only`` — какие стоп-кадры учитывать."""

    def __init__(self, freezes: list[dict] | None, only: Any = None):
        chosen = [f for f in normalize_freezes(freezes) if is_insert(f) and (only is None or only(f))]
        self.inserts = [(f["at"], f["duration"]) for f in chosen]

    def __bool__(self) -> bool:
        return bool(self.inserts)

    def __call__(self, t: float) -> float:
        t = float(t)
        return t + sum(d for at, d in self.inserts if at < t - 1e-9)

    def clamp(self, t: float) -> float:
        return self(t)

    def inside(self, _t: float) -> bool:
        return False

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self(start), self(end)
        return None if b - a < min_len else (a, b)

    def duration(self, total: float) -> float:
        return self(total)

    def added(self, total: float) -> float:
        """Сколько секунд вставлено в ролик длиной ``total``."""
        return round(self(total) - float(total), 3)


class ChainMap:
    """Два перевода времени подряд (вырезы, потом стоп-кадры) — тот же интерфейс."""

    def __init__(self, first: Any, second: Any):
        self.first, self.second = first, second

    def __bool__(self) -> bool:
        return True

    def __call__(self, t: float) -> float | None:
        mid = self.first(t)
        return None if mid is None else self.second(mid)

    def clamp(self, t: float) -> float:
        return self.second.clamp(self.first.clamp(t))

    def inside(self, t: float) -> bool:
        return self(t) is None

    def range(self, start: float, end: float, min_len: float = 1e-6) -> tuple[float, float] | None:
        a, b = self.clamp(start), self.clamp(end)
        return None if b - a < min_len else (a, b)

    def duration(self, total: float) -> float:
        return max(0.0, self.clamp(total))


def chain(cut_mapper: Any, freezes: list[dict] | None) -> Any:
    """Перевод времени готового ролика без вырезов → с вырезами и стоп-кадрами (или None)."""
    fm = FreezeMap(freezes)
    if cut_mapper and fm:
        return ChainMap(cut_mapper, fm)
    if fm:
        return fm
    return cut_mapper or None


def freeze_zones(zones: list[dict] | None, freezes: list[dict] | None) -> list[dict] | None:
    """Зоны анимированного фона основы (``t0..t1`` кусков) — во время основы со стоп-кадрами."""
    fm = FreezeMap(freezes)
    if not zones or not fm:
        return zones
    return [{**z, "t0": round(fm(float(z["t0"])), 3), "t1": round(fm(float(z["t1"])), 3)} for z in zones]


def comp_freezes(freezes: list[dict] | None) -> list[dict]:
    """``comp.freezes`` для компоновщика — нужен, только если хоть один держит слои."""
    items = normalize_freezes(freezes)
    if not any(holds_layers(f) for f in items):
        return []
    return [{"at": f["at"], "d": f["duration"], "insert": is_insert(f), "hold": holds_layers(f)} for f in items]


def motion_time(u: float, freezes: list[dict] | None) -> float:
    """Время готового ролика (со стоп-кадрами) → время слоёв (без стоп-кадров; слои у
    стоп-кадров «continue» уже сдвинуты ``FreezeMap`` — их время идёт дальше). То же
    считает компоновщик (node/motion-render.ts ``motionTime``)."""
    off_all = off_cont = 0.0
    for f in normalize_freezes(freezes):
        at, d = f["at"], f["duration"]
        at_post = at + off_all
        if u < at_post:
            break
        insert, hold = is_insert(f), holds_layers(f)
        if u < at_post + d:
            if hold:
                return round(at + off_cont, 6)
            if insert:
                return round(at + off_cont + (u - at_post), 6)
            return round(u - off_all + off_cont, 6)
        if insert:
            off_all += d
            if not hold:
                off_cont += d
    return round(u - off_all + off_cont, 6)


def post_to_pre(u: float, freezes: list[dict] | None) -> dict:
    """Момент готового ролика СО стоп-кадрами → что в нём видно и слышно (секунды без стоп-кадров):
    ``video`` — кадр основы, ``layers`` — время слоёв (без сдвига ``FreezeMap``), ``audio`` —
    звук и субтитры; ``freeze`` — номер стоп-кадра, на котором ``u``, или None."""
    off = 0.0
    for index, f in enumerate(normalize_freezes(freezes)):
        at, d = f["at"], f["duration"]
        at_post = at + off
        if u < at_post:
            break
        if u < at_post + d:
            video = at
            audio = at if is_insert(f) else u - off
            layers = at if holds_layers(f) else at + (u - at_post)
            return {"video": round(video, 3), "layers": round(layers, 3), "audio": round(audio, 3), "freeze": index}
        if is_insert(f):
            off += d
    pre = round(u - off, 3)
    return {"video": pre, "layers": pre, "audio": pre, "freeze": None}


def finished_with_freezes(finished: float, freezes: list[dict] | None) -> float:
    return round(FreezeMap(freezes)(finished) if freezes else finished, 3)


def shift_voice(voice: dict | None, freezes: list[dict] | None, only: Any = None) -> dict | None:
    """Состояния голоса PNGtuber (``comp.voice``: {id: {t0, fps, states, levels}}) — вставить
    молчание на вставленные стоп-кадры (``only`` — какие), чтобы рот шёл со звуком."""
    inserts = [f for f in normalize_freezes(freezes) if is_insert(f) and (only is None or only(f))]
    if not voice or not inserts:
        return voice
    out = {}
    for key, track in voice.items():
        fps = float(track.get("fps") or 30)
        t0 = float(track.get("t0") or 0)
        states = str(track.get("states") or "")
        levels = list(track.get("levels") or [])
        shift = 0.0
        for f in sorted(inserts, key=lambda f: -f["at"]):
            if f["at"] < t0 - 1e-9:
                shift += f["duration"]  # слой начался после стоп-кадра — едет целиком
                continue
            k = int(round((f["at"] - t0) * fps))
            if k >= len(states):
                continue
            n = int(round(f["duration"] * fps))
            states = states[:k] + "0" * n + states[k:]
            if levels:
                levels = levels[:k] + [0] * n + levels[k:]
        out[key] = {**track, "t0": round(t0 + shift, 6), "states": states, **({"levels": levels} if levels else {})}
    return out


def build_freeze_args(src: Path, dst: Path, freezes: list[dict], fps: float, has_audio: bool,
                      duration: float) -> list[str] | None:
    """ffmpeg: стоп-кадры в основу. Видео режется на куски, кадр в ``at`` держится ``duration``
    (вставкой или поверх), звук исходника на вставке — тишина, поверх — как был. None — нечего."""
    items = [f for f in normalize_freezes(freezes) if f["at"] < duration - 1.0 / max(1.0, fps)]
    if not items:
        return None
    frame = 1.0 / max(1.0, float(fps))
    v_parts: list[str] = []
    a_parts: list[str] = []
    labels_v: list[str] = []
    labels_a: list[str] = []
    cursor = 0.0
    inserts = any(is_insert(f) for f in items)
    for k, f in enumerate(items):
        at, d = f["at"], f["duration"]
        if at > cursor + 1e-6:
            v_parts.append(f"[v{k}a]trim=start={cursor:.4f}:end={at:.4f},setpts=PTS-STARTPTS[pv{k}]")
            labels_v.append(f"[pv{k}]")
            if has_audio and inserts:
                a_parts.append(f"[a{k}a]atrim=start={cursor:.4f}:end={at:.4f},asetpts=PTS-STARTPTS[pa{k}]")
                labels_a.append(f"[pa{k}]")
        v_parts.append(
            f"[v{k}b]trim=start={at:.4f}:end={at + frame:.4f},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={d:.4f},trim=duration={d:.4f}[fv{k}]"
        )
        labels_v.append(f"[fv{k}]")
        if is_insert(f):
            if has_audio:
                a_parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.4f}[fa{k}]")
                labels_a.append(f"[fa{k}]")
            cursor = at
        else:
            if has_audio and inserts:
                # Звук поверх стоп-кадра идёт как был.
                a_parts.append(f"[a{k}b]atrim=start={at:.4f}:end={at + d:.4f},asetpts=PTS-STARTPTS[fa{k}]")
                labels_a.append(f"[fa{k}]")
            cursor = at + d
    tail = len(items)
    v_parts.append(f"[v{tail}a]trim=start={cursor:.4f},setpts=PTS-STARTPTS[pv{tail}]")
    labels_v.append(f"[pv{tail}]")
    if has_audio and inserts:
        a_parts.append(f"[a{tail}a]atrim=start={cursor:.4f},asetpts=PTS-STARTPTS[pa{tail}]")
        labels_a.append(f"[pa{tail}]")
    # Входы кусков: каждый trim читает свою копию потока (split/asplit).
    v_inputs = [p[p.index("[") + 1:p.index("]")] for p in v_parts]
    graph = [f"[0:v]split={len(v_inputs)}" + "".join(f"[{name}]" for name in v_inputs)]
    graph += v_parts
    graph.append("".join(labels_v) + f"concat=n={len(labels_v)}:v=1:a=0,fps={float(fps):g},format=yuv420p[vout]")
    maps = ["-map", "[vout]"]
    if has_audio and inserts:
        a_inputs = [p[p.index("[") + 1:p.index("]")] for p in a_parts if p.startswith("[")]
        graph.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"asplit={len(a_inputs)}" + "".join(f"[{name}]" for name in a_inputs))
        graph += a_parts
        graph.append("".join(labels_a) + f"concat=n={len(labels_a)}:v=0:a=1[aout]")
        maps += ["-map", "[aout]", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    elif has_audio:
        maps += ["-map", "0:a", "-c:a", "copy"]
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-filter_complex", ";".join(graph), *maps,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-movflags", "+faststart", str(dst),
    ]
