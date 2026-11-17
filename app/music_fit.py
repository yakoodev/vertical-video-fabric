"""Музыка «под конец ролика»: старт трека подбирается так, чтобы его НАСТОЯЩАЯ концовка легла на конец клипа.

Раньше фон играл с 0 с (или с ``music_start_sec``) и обрывался там, где кончился ролик, — посреди фразы, а затухание
рендера только маскировало обрыв. Идея из motion-video-kit (``backtime-music.py``, MIT): трек «отсчитывается от
конца» — финальный удар трека ложится на конец картинки, а ролик открывается с середины трека. Трек при этом играет
одним куском: ничего не режем, не петляем, не склеиваем.

Что такое концовка (``track_ending``, огибающая 10 мс по моно 8 кГц):

* ``hit`` — финальный удар: сильная атака у конца громкой части, после которой громкость падает и уже не
  возвращается (дальше только хвост-звучание ``ring`` не дольше ``RING_MAX_SEC``). Ложится на конец клипа с
  запасом ``tail`` — хвост звучит на последних долях секунды (и на стоп-кадре ``end_hold``, если он есть);
* ``fade`` — трек сам затухает (или за ударом долгий хвост с долями): конец затухания (где он ушёл под голос,
  ``FADE_FLOOR_DB``) ложится на конец клипа;
* ``cut`` — звук обрывается разом или файл кончается громко (обрезанный трек): место обрыва — на конец клипа или
  чуть позже (его закроет затухание рендера), но не раньше: иначе рендер зациклил бы трек с начала.

Автопоиск может ошибиться (в motion-video-kit он брал не тот удар в 3 концовках из 6): своя опора — ``end_at``
(``set_music(track_end_sec=…)``), свой старт — ``start_sec``.

Подбор старта (``fit_start``): ``start = anchor − (T − tail)``; дальше кандидаты рядом — сильная доля (если у трека
слышна сетка, ``audio_map.beat_grid`` по окну вокруг старта), просто доля, 0 с (старт меньше секунды — с самого
начала), и старт как есть. Берётся тот, у которого удар ложится ближе к цели, с предпочтением сильной доли; все
кандидаты держат ограничения: трек не кончается раньше ролика (иначе рендер зациклит его с начала), удар — до
конца ролика, старт не раньше ``min_start`` (тихое вступление у промо, своё значение агента).

Где живёт: ``render_settings.music_fit`` — ``{mode: "end"}`` (подбирать; по умолчанию у ``set_music`` без
start_sec и у нового трека в редакторе, ``editor_save``), ``{mode: "off"}`` (старт выбран владельцем или агентом —
не трогаем), None — старые клипы, как было; ещё ``min_start``, ``end_at`` и ``start``/``prev`` (что записал подбор —
отличить его старт от сдвинутого руками). Подобранный старт
пишется в ``music_start_sec``: превью, карта долей, реакция слоёв и рендер слышат одно и то же. Перед рендером
(``refit_plan``) старт пересчитывается под текущую длину ролика (вырезы, стоп-кадры меняют её) — со сдвигом на
целые такты, если сетка известна, чтобы привязанные к долям слои не съехали; клип с текстом песни (слои
``lyric-…``) не трогается — там старт держит синхрон слов.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

VERSION = 1
MODES = ("end", "off")
RATE = 8000
HOP = 0.01
SILENCE_DB = -60.0
# «Тело» трека — этот перцентиль громкости окон 10 мс; дальше всё относительно него.
BODY_PERCENTILE = 90
# Звучит, пока громче тела минус столько (ниже — тишина/шум хвоста файла).
AUDIBLE_DB = 40.0
# Громкая часть — сглаженная (0.25 с) громкость не ниже тела минус столько.
LOUD_DB = 10.0
# Затухание «ушло под голос»: тише тела на столько — дальше не слышно под речью клипа.
FADE_FLOOR_DB = 30.0
# Финальный удар: атака (рост за 30 мс) и падение после него (средняя за 1.5 с после удара ниже удара).
HIT_RISE_DB = 6.0
HIT_LEVEL_DB = 12.0
HIT_DROP_DB = 8.0
HIT_SEARCH_SEC = 4.0
# Хвост удара длиннее — это уже затухание трека (под ним доли), а не звон последнего аккорда.
RING_MAX_SEC = 3.5
# Обрыв: за столько секунд до конца звука было громко, а дальше — тишина.
CUT_STOP_SEC = 0.15
# Хвост удара на конце клипа: не меньше / не больше (и не больше доли ролика).
TAIL_MIN_SEC = 0.4
TAIL_MAX_SEC = 1.0
TAIL_SHARE = 0.3
# Насколько удар может уйти от цели при подгонке к доле.
SNAP_SLACK_SEC = 1.0
ZERO_SNAP_SEC = 1.0
# Сетка долей: окно вокруг старта и доверие.
GRID_BEFORE_SEC = 8.0
GRID_AFTER_SEC = 24.0
GRID_MIN_CONFIDENCE = 0.25
# Пересчёт перед рендером: меньше этого сдвига — не трогаем.
REFIT_MIN_SHIFT_SEC = 0.05
LYRIC_PREFIX = "lyric"


# --------------------------------------------------------------------------- #
# Огибающая и концовка трека
# --------------------------------------------------------------------------- #


def envelope(samples: Any, rate: int = RATE, hop: float = HOP) -> Any:
    """Громкость окон ``hop`` с, дБ полной шкалы (numpy)."""
    import numpy as np

    step = max(1, int(round(rate * hop)))
    count = len(samples) // step
    if count <= 0:
        return np.zeros(0)
    blocks = np.asarray(samples[: count * step], dtype=np.float64).reshape(count, step)
    return 10 * np.log10(np.mean(blocks * blocks, axis=1) + 1e-12)


def _smooth(r: Any, frames: int) -> Any:
    """Скользящая средняя по мощности (центрированная), дБ."""
    import numpy as np

    power = 10 ** (r / 10)
    kernel = np.ones(frames) / frames
    return 10 * np.log10(np.convolve(power, kernel, mode="same") + 1e-12)


def _power_mean(values: Any) -> float:
    import numpy as np

    if len(values) == 0:
        return -120.0
    return float(10 * np.log10(np.mean(10 ** (np.asarray(values) / 10)) + 1e-12))


def ending_from_envelope(r: Any, hop: float = HOP) -> dict | None:
    """Концовка по огибающей: {kind: hit|fade|cut, anchor, ring, sound_end, duration}; None — трек немой."""
    import numpy as np

    r = np.asarray(r, dtype=np.float64)
    n = len(r)
    if n < int(2 / hop):
        return None
    audible = r[r > SILENCE_DB]
    if len(audible) < int(1 / hop):
        return None
    body = float(np.percentile(audible, BODY_PERCENTILE))
    duration = round(n * hop, 3)
    above = np.nonzero(r > body - AUDIBLE_DB)[0]
    if len(above) == 0:
        return None
    end_i = int(above[-1]) + 1
    sound_end = round(end_i * hop, 3)
    sm = _smooth(r, max(1, int(round(0.25 / hop))))
    loud = np.nonzero(sm[:end_i] > body - LOUD_DB)[0]
    if len(loud) == 0:
        return None
    loud_i = int(loud[-1])
    base = {"duration": duration, "sound_end": sound_end, "body_db": round(body, 1)}
    tail_frames = r[max(0, n - int(0.5 / hop)):]
    if n - end_i <= int(0.15 / hop) and _power_mean(tail_frames) > body - HIT_LEVEL_DB:
        # Файл кончается громко — обрезанный трек: опора — сам конец файла.
        return {**base, "kind": "cut", "anchor": duration, "ring": 0.0}
    before_stop = r[max(0, end_i - int(CUT_STOP_SEC / hop)):end_i]
    if len(before_stop) and float(np.max(before_stop)) > body - HIT_LEVEL_DB:
        # Звук обрывается разом (громко → тишина за доли секунды, дальше пустота файла): опора — где оборвался.
        return {**base, "kind": "cut", "anchor": sound_end, "ring": 0.0}
    below = np.nonzero(sm[loud_i:end_i] < body - FADE_FLOOR_DB)[0]
    quiet_at = round((loud_i + (int(below[0]) if len(below) else end_i - loud_i)) * hop, 3)
    lo = max(3, loud_i - int(HIT_SEARCH_SEC / hop))
    hi = min(end_i - 1, loud_i + int(0.5 / hop))
    hit = None
    for i in range(hi, lo - 1, -1):
        if r[i] <= body - HIT_LEVEL_DB or r[i] - float(np.min(r[i - 3:i])) <= HIT_RISE_DB:
            continue
        if r[i] < float(np.max(r[max(0, i - 5):i + 6])):
            continue
        after = r[i + 5:i + int(1.5 / hop)]
        later = r[i + int(0.3 / hop):end_i]
        # Удар — последний: после него громкость падает и ничего громкого (атаки у тела) уже нет.
        if r[i] - _power_mean(after) >= HIT_DROP_DB and (len(later) == 0 or float(np.max(later)) < r[i] - 3.0):
            hit = i
            break
    if hit is not None and quiet_at - hit * hop <= RING_MAX_SEC:
        at = round(hit * hop, 3)
        return {**base, "kind": "hit", "anchor": at, "ring": round(max(0.0, quiet_at - at), 3)}
    # Удара нет или за ним долгий хвост с долями (трек затухает, а не звенит): опора — где затухание ушло под голос.
    fade_from = round(min(loud_i, hit if hit is not None else loud_i) * hop, 3)
    return {**base, "kind": "fade", "anchor": quiet_at, "ring": 0.0, "fade_from": fade_from}


def decode(path: str | Path) -> Any:
    from app.music_energy import decode_pcm

    return decode_pcm(path)


def track_ending(path: str | Path, samples: Any = None) -> dict | None:
    """Концовка файла трека (кеш по файлу: путь, размер, время правки)."""
    from app.audio_map import _cached, _file_stamp

    path = Path(path)

    def compute() -> dict:
        x = decode(path) if samples is None else samples
        return {"ending": ending_from_envelope(envelope(x))}

    key = {"v": VERSION, "kind": "music_end", "file": _file_stamp(path)}
    try:
        return _cached(key, compute).get("ending")
    except Exception as exc:  # noqa: BLE001 - не прочитали трек — подбора нет, музыка как раньше
        _log.info("music_fit: ending of %s failed: %s", path.name, exc)
        return None


def local_grid(samples: Any, around: float, rate: int = RATE) -> dict | None:
    """Сетка долей трека в окне вокруг секунды ``around`` (секунды трека); None — пульса не слышно."""
    from app.audio_map import beat_grid

    a = max(0.0, around - GRID_BEFORE_SEC)
    b = around + GRID_AFTER_SEC
    part = samples[int(a * rate):int(b * rate)]
    try:
        grid = beat_grid(part, rate, t0=a)
    except Exception:  # noqa: BLE001 - сетка — бонус
        return None
    if not grid or float(grid.get("confidence") or 0) < GRID_MIN_CONFIDENCE:
        return None
    return grid


# --------------------------------------------------------------------------- #
# Подбор старта
# --------------------------------------------------------------------------- #


def tail_for(ending: dict, total: float, hold: float = 0.0) -> float:
    """Сколько секунд до конца ролика должна прозвучать опора концовки (у удара — его хвост)."""
    if ending.get("kind") != "hit":
        return 0.0
    ring = float(ending.get("ring") or 0.0)
    tail = max(float(hold or 0.0), min(ring, TAIL_MAX_SEC), TAIL_MIN_SEC)
    return round(min(tail, max(0.0, total * TAIL_SHARE)), 3)


def _grid_points(grid: dict, lo: float, hi: float, kind: str) -> list[float]:
    period = float(grid["period"])
    first = float(grid["first_downbeat"] if kind == "downbeat" else grid["first_beat"])
    if kind == "downbeat":
        period *= int(grid.get("downbeat_every") or 4)
    k0 = math.ceil((lo - first) / period)
    out = []
    k = k0
    while first + k * period <= hi:
        out.append(round(first + k * period, 3))
        k += 1
    return out


def fit_start(ending: dict | None, total: float, *, hold: float = 0.0, min_start: float = 0.0,
              grid: dict | None = None, track_len: float | None = None,
              only: list[float] | None = None) -> dict:
    """Старт трека, при котором его концовка ложится на конец ролика длиной ``total`` (с учётом стоп-кадра
    ``hold`` в его конце). → {ok, start_sec, …} или {ok: False, reason}.

    ``only`` — проверять только эти старты (пересчёт со сдвигом на целые такты)."""
    if not ending:
        return {"ok": False, "reason": "не нашёл у трека концовки (немой или не прочитался)"}
    total = float(total)
    if total <= 0.5:
        return {"ok": False, "reason": "у ролика нет длины"}
    anchor = float(ending["anchor"])
    length = float(track_len if track_len is not None else ending.get("duration") or anchor)
    kind = ending["kind"]
    tail = tail_for(ending, total, hold)
    ideal = anchor - (total - tail)
    if ideal < -1e-6:
        return {"ok": False, "reason": (f"трек короче ролика до своей концовки ({anchor:.1f} с из {total:.1f} с ролика)"),
                "ending": ending}
    # Допуск удара относительно цели: у удара хвост может стать длиннее/короче; затухание и конец файла — около T.
    if kind == "hit":
        tail_lo, tail_hi = 0.15, tail + SNAP_SLACK_SEC
    elif kind == "cut":
        tail_lo, tail_hi = -0.5, 0.0  # файл не должен кончиться раньше ролика: рендер зациклит трек с начала
    else:
        tail_lo, tail_hi = -1.0, SNAP_SLACK_SEC
    candidates: list[tuple[float, str]] = []
    if only is not None:
        candidates = [(float(c), "bar") for c in only]
    else:
        candidates.append((ideal, "exact"))
        if ideal < ZERO_SNAP_SEC:
            candidates.append((0.0, "zero"))
        if grid:
            span = float(grid["period"]) * int(grid.get("downbeat_every") or 4)
            candidates += [(t, "downbeat") for t in _grid_points(grid, ideal - span, ideal + span, "downbeat")]
            candidates += [(t, "beat") for t in _grid_points(grid, ideal - grid["period"], ideal + grid["period"], "beat")]
    penalty = {"downbeat": 0.0, "zero": 0.0, "bar": 0.0, "beat": 0.25, "exact": 0.5}
    best = None
    rejected_by_intro = False
    for start, how in candidates:
        if start < -1e-6:
            continue
        start = max(0.0, start)
        if start < min_start - 1e-6:
            rejected_by_intro = True
            continue
        landed = anchor - start
        got_tail = total - landed
        if not (tail_lo - 1e-6 <= got_tail <= tail_hi + 1e-6):
            continue
        if length and start + total > length + 0.05 and kind != "cut":
            continue
        score = abs(got_tail - tail) + penalty.get(how, 0.5)
        if best is None or score < best[0] - 1e-9:
            best = (score, start, how, landed, got_tail)
    if best is None:
        if rejected_by_intro:
            return {"ok": False, "ending": ending,
                    "reason": (f"концовка не помещается: старт попал бы раньше {min_start:g} с трека (вступление)")}
        return {"ok": False, "ending": ending, "reason": "концовку не удалось положить на конец ролика"}
    _score, start, how, landed, got_tail = best
    return {"ok": True, "start_sec": round(start, 2), "snapped": how, "ending": ending,
            "lands_at": round(landed, 2), "clip_end": round(total, 2), "tail": round(got_tail, 2)}


def describe(fit: dict) -> str:
    """Человеческая строка для агента/владельца."""
    if not fit.get("ok"):
        return "Концовку трека на конец ролика не положил: " + str(fit.get("reason") or "")
    ending = fit["ending"]
    what = {"hit": "финальный удар", "fade": "затухание трека", "cut": "конец файла"}.get(ending["kind"], "концовка")
    snap = {"downbeat": ", старт на сильной доле", "beat": ", старт на доле", "zero": ", трек с самого начала",
            "bar": ", сдвиг на целые такты"}.get(fit.get("snapped") or "", "")
    lead = fit["tail"]
    where = (f"за {lead:g} с до конца" if lead > 0.05 else "в самый конец" if lead > -0.05
             else f"на {-lead:g} с позже конца (доиграет под затухание)")
    return (f"Трек с {fit['start_sec']:g} с: {what} ({float(ending['anchor']):.1f} с трека) ляжет на "
            f"{fit['lands_at']:g} с ролика, {where}{snap}")


# --------------------------------------------------------------------------- #
# Клип: длина, подбор при set_music, пересчёт перед рендером
# --------------------------------------------------------------------------- #


def normalize_fit(raw: Any) -> dict | None:
    """``render_settings.music_fit``: None — старый клип (как было), иначе {mode: end|off, min_start?, end_at?,
    start?}. ``min_start`` — старт не раньше (вступление), ``end_at`` — своя опора концовки (секунда трека, где
    финальный удар), ``start`` — старт, который записал сам подбор (не совпадает с music_start_sec — его меняли)."""
    if isinstance(raw, str):
        raw = {"mode": raw}
    if not isinstance(raw, dict) or raw.get("mode") not in MODES:
        return None
    out: dict = {"mode": raw["mode"]}
    for key in ("min_start", "end_at", "start", "prev"):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0.0 <= value <= 3600.0:
            out[key] = round(value, 2)
    return out


def fit_mode(rs: dict) -> str | None:
    fit = normalize_fit(rs.get("music_fit"))
    return fit["mode"] if fit else None


def clip_total(store: Any, plan: dict, end_hold: float | None = None) -> tuple[float, float]:
    """(сколько секунд под музыкой: ролик со стоп-кадрами + стоп-кадр в конце, сам стоп-кадр в конце). Оценка по
    часам клипа (куски, скорость, наплывы, вырезы) — как спланирует рендер."""
    from app.freeze import finished_with_freezes
    from app.tool_core import plan_clock

    rs = plan.get("render_settings") or {}
    final = float(plan_clock(store, plan).final_duration)
    final = finished_with_freezes(final, rs.get("freezes"))
    if end_hold is None:
        from app.render_finish import FinishRender

        end_hold = FinishRender._end_hold_sec(plan)
    hold = float(end_hold or 0.0)
    return round(final + hold, 3), hold


def has_lyrics(rs: dict) -> bool:
    """Слои текста песни (lyric_layers): их время — от старта трека, сдвигать его нельзя."""
    return any(isinstance(layer, dict) and str(layer.get("id") or "").startswith(LYRIC_PREFIX)
               for layer in rs.get("layers") or [])


def manual_ending(end_at: float, track_len: float) -> dict:
    """Опора, названная агентом/владельцем (секунда финального удара) — без поиска."""
    return {"kind": "hit", "anchor": round(float(end_at), 3), "ring": round(max(0.0, track_len - float(end_at)), 3),
            "duration": round(track_len, 3), "manual": True}


def fit_for_track(track: dict, total: float, hold: float, *, min_start: float = 0.0, end_at: float | None = None,
                  bars_from: float | None = None) -> dict:
    """Подбор старта трека ``track`` под ``total`` с музыки. ``bars_from`` — пересчёт: только этот старт ± целые
    такты (или доли), чтобы сетка долей в ролике не сдвинулась — слои, привязанные к долям, остаются на долях."""
    path = Path(str(track.get("file_path") or ""))
    if not path.is_file():
        return {"ok": False, "reason": "файла трека нет"}
    try:
        samples = decode(path)
    except Exception as exc:  # noqa: BLE001 - битый файл: подбора нет
        return {"ok": False, "reason": f"трек не прочитался: {str(exc)[:120]}"}
    track_len = len(samples) / RATE
    ending = manual_ending(end_at, track_len) if end_at is not None else track_ending(path, samples)
    if not ending:
        return {"ok": False, "reason": "не нашёл у трека концовки"}
    tail = tail_for(ending, total, hold)
    ideal = float(ending["anchor"]) - (total - tail)
    if bars_from is not None:
        grid = local_grid(samples, bars_from)
        if grid:
            out: dict = {"ok": False, "reason": "не нашёл старт"}
            for every, how in ((int(grid.get("downbeat_every") or 4), "bar"), (1, "beat")):
                step = float(grid["period"]) * every
                k = round((ideal - bars_from) / step)
                only = [bars_from + (k + d) * step for d in (-1, 0, 1)]
                out = fit_start(ending, total, hold=hold, min_start=min_start, track_len=track_len, only=only)
                if out.get("ok"):
                    out["snapped"] = how
                    return out
            return out
    grid = local_grid(samples, ideal) if ideal >= 0 else None
    return fit_start(ending, total, hold=hold, min_start=min_start, grid=grid, track_len=track_len)


def min_start_for(track: dict, promo: bool) -> float:
    """Минимальный пропуск вступления: у промо — тихое вступление трека (energy_start_sec), иначе 0."""
    if not promo:
        return 0.0
    from app.music_energy import needs_energy

    if needs_energy(track) or track.get("energy_start_sec") is None:
        return 0.0
    return float(track["energy_start_sec"])


def fit_plan(store: Any, plan: dict, track: dict, fit_cfg: dict, end_hold: float | None = None,
             bars_from: float | None = None) -> dict:
    """Подбор старта для клипа ``plan`` (длина ролика — по его часам)."""
    total, hold = clip_total(store, plan, end_hold)
    return fit_for_track(track, total, hold, min_start=float(fit_cfg.get("min_start") or 0.0),
                         end_at=fit_cfg.get("end_at"), bars_from=bars_from)


def refit_plan(store: Any, plan: dict, end_hold: float | None = None) -> tuple[dict, dict | None]:
    """Перед рендером: у ``music_fit.mode == "end"`` старт пересчитывается под нынешнюю длину ролика и пишется в
    настройки клипа (превью и рендер слышат одно). → (план — с новым стартом или тот же, итог или None)."""
    from app.clip_settings import normalize_render_settings

    rs = normalize_render_settings(plan.get("render_settings") or {})
    cfg = normalize_fit(rs.get("music_fit"))
    if not cfg or cfg["mode"] != "end" or not rs.get("music_on") or not rs.get("track_id"):
        return plan, None
    if has_lyrics(rs):
        return plan, {"ok": False, "reason": "у клипа текст песни — старт держит синхрон слов, не трогаю"}
    current = float(rs.get("music_start_sec") or 0.0)
    # Старт уже подобран (его и записал подбор) — пересчёт только на целые такты: доли в ролике не съедут.
    fitted = cfg.get("start") is not None and abs(float(cfg["start"]) - current) < 0.01
    try:
        track = store.get_audio_track(int(rs["track_id"]))
        fit = fit_plan(store, plan, track, cfg, end_hold, bars_from=current if fitted else None)
    except Exception as exc:  # noqa: BLE001 - пересчёт — надстройка, рендер важнее
        _log.warning("music_fit: refit of clip plan %s failed: %s", plan.get("id"), exc)
        return plan, {"ok": False, "reason": str(exc)[:160]}
    if not fit.get("ok") or (fitted and abs(float(fit["start_sec"]) - current) < REFIT_MIN_SHIFT_SEC):
        return plan, fit
    fresh = store.get_clip_plan(int(plan["id"]))
    frs = dict(fresh.get("render_settings") or {})
    if fit_mode(frs) != "end" or frs.get("track_id") != rs.get("track_id"):
        return plan, fit  # пока считали, музыку поменяли — не перетираем
    frs["music_start_sec"] = float(fit["start_sec"])
    frs["music_fit"] = {**cfg, "start": float(fit["start_sec"]), "prev": current}
    store.set_clip_plan_render_settings(int(plan["id"]), frs)
    _log.info("music_fit: clip plan %s music start %.2f -> %.2f (%s)", plan["id"], current, fit["start_sec"],
              describe(fit))
    return store.get_clip_plan(int(plan["id"])), fit


def editor_save(current: dict, incoming: dict) -> dict:
    """Сохранение настроек из редактора, который о ``music_fit`` не знает: что поставить в incoming.

    * новый трек со стартом 0 (редактор сам сбрасывает старт при выборе трека) — ``end``: подберёт рендер;
    * тот же трек, старт сдвинули — владелец выбрал сам: ``off``, его старт не трогаем;
    * старт равен прежнему подобранному (``prev``: редактор открыт до пересчёта и прислал старое) — это не выбор,
      остаётся подобранный старт;
    * иначе — как было."""
    cur = normalize_fit(current.get("music_fit"))
    out = dict(incoming)
    out["music_fit"] = cur
    if not incoming.get("music_on") or not incoming.get("track_id"):
        return out
    try:
        new_start = float(incoming.get("music_start_sec") or 0.0)
        old_start = float(current.get("music_start_sec") or 0.0)
    except (TypeError, ValueError):
        return out
    if incoming.get("track_id") != current.get("track_id"):
        if abs(new_start) < 0.01:
            out["music_fit"] = {"mode": "end", **({"min_start": cur["min_start"]} if cur and "min_start" in cur else {})}
        return out
    if abs(new_start - old_start) < 0.01:
        return out
    if cur and cur["mode"] == "end" and cur.get("prev") is not None and abs(new_start - float(cur["prev"])) < 0.01:
        out["music_start_sec"] = old_start
        return out
    out["music_fit"] = {"mode": "off"}
    return out


