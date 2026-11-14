"""HUD игры по кадрам: шаблоны надписей Apex Legends и лента убийств (app/game_events.py).

Кадр приводится к 1920×1080 (16:9; иначе HUD не ищем), дальше всё — в пикселях 1080p:

* плашки очков в центре под прицелом («НОКАУТ <ник> +150 ⌃», «СОДЕЙСТВИЕ В НОКАУТЕ», «УНИЧТОЖЕНИЕ
  ОТРЯДА»; английское «KNOCKED DOWN») — шаблон слова (``cv2.matchTemplate``, TM_CCOEFF_NORMED по яркости)
  и обязательный значок очков ⌃ справа в той же строке: без него слово не засчитывается;
* баннеры сверху («НОВЫЙ УРОВЕНЬ», «МАКС. УРОВЕНЬ»), «ВЫ ЧЕМПИОН» по центру;
* состояния игрока: панель «Нокаут-щит» внизу (игрок сбит), вкладка «В ЭФИРЕ» вверху (убит, смотрит
  за отрядом или сводку);
* лента убийств справа сверху: строка, где ник игрока (оранжевый) стоит СЛЕВА (он убийца), без красного
  значка нокаута — убийство (добил / истёк кровью).

Шаблоны — вырезки из записей владельца (assets/game_events/apex/*.png, русский клиент, 1440p → 1080p).
Чистая часть (``analyze_frame``, ``hud_events``) работает на numpy-массивах и проверяется на синтетике.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

W, H = 1920, 1080
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "game_events"

# Плашки очков: строка y≈762–792 (1080p); полоса с запасом на другой масштаб HUD.
BANNER_ROI = (400, 735, 1520, 820)
CHEVRON_THR = 0.80
# Лента убийств: строки по ~32 px, правый край ≈ x 1789.
FEED_ROI = (1000, 190, 1860, 405)
FEED_RIGHT = 1789
FEED_ROW0, FEED_ROW_STEP, FEED_ROWS = 223.5, 32.3, 6  # центры строк ленты по y
FEED_CONTRAST = 20
FEED_MAX_GAP = 70  # px пустоты внутри строки ленты
FEED_OTHER_ORANGE = 8  # столбцов оранжевых букв левее ника — не больше


@dataclass(frozen=True)
class Spec:
    name: str        # имя файла без .png
    kind: str        # событие (knock, squad_wipe, …) или служебное (assist, chevron)
    roi: tuple[int, int, int, int]  # x0, y0, x1, y1 в 1080p
    thr: float       # порог совпадения
    style: str = "banner"  # banner — показ на 1–3 с; state — держится, событие на входе в состояние
    rearm: float = 12.0    # state: новое событие — не раньше, чем через столько секунд после прошлого
    shrink: int = 1  # сравнивать в уменьшении (крупные надписи)
    needs_chevron: bool = False
    detail: str = ""


SPECS: dict[str, tuple[Spec, ...]] = {
    "apex": (
        Spec("knock_ru", "knock", BANNER_ROI, 0.72, needs_chevron=True, detail="НОКАУТ"),
        Spec("knock_en", "knock", BANNER_ROI, 0.70, needs_chevron=True, detail="KNOCKED DOWN"),
        # Повторный нокаут (сбитого и поднятого врага) очков не даёт — плашка без значка ⌃.
        Spec("reknock_ru", "knock", BANNER_ROI, 0.75, detail="ПОВТОРНЫЙ НОКАУТ"),
        Spec("assist_ru", "assist", BANNER_ROI, 0.72, needs_chevron=True, detail="СОДЕЙСТВИЕ В НОКАУТЕ"),
        Spec("wipe_ru", "squad_wipe", BANNER_ROI, 0.72, needs_chevron=True, detail="УНИЧТОЖЕНИЕ ОТРЯДА"),
        Spec("levelup_ru", "level_up", (480, 170, 1440, 320), 0.70, shrink=2, detail="НОВЫЙ УРОВЕНЬ"),
        Spec("maxlevel_ru", "max_level", (480, 170, 1440, 320), 0.70, shrink=2, detail="МАКС. УРОВЕНЬ"),
        Spec("champion_ru", "win", (240, 300, 1680, 680), 0.62, shrink=4, detail="ВЫ ЧЕМПИОН"),
        Spec("kshield_ru", "death", (700, 840, 1220, 960), 0.72, style="state", detail="сбит (Нокаут-щит)"),
        Spec("spectate_ru", "death", (480, 0, 1300, 110), 0.72, style="state", rearm=180.0, shrink=2, detail="убит (режим зрителя)"),
    ),
}
CHEVRON = Spec("chevron", "chevron", BANNER_ROI, CHEVRON_THR)


@lru_cache(maxsize=8)
def load_templates(game: str = "apex") -> tuple[tuple[Spec, Any], ...]:
    """(Spec, серый шаблон uint8) — шаблоны игры с диска; уменьшенные для ``shrink``."""
    import cv2

    out = []
    for spec in (*SPECS.get(game, ()), CHEVRON):
        img = cv2.imread(str(TEMPLATE_DIR / game / f"{spec.name}.png"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        if spec.shrink > 1:
            img = cv2.resize(img, (img.shape[1] // spec.shrink, img.shape[0] // spec.shrink), interpolation=cv2.INTER_AREA)
        out.append((spec, img))
    return tuple(out)


def _match(gray: Any, spec: Spec, tpl: Any) -> tuple[float, int]:
    """Лучшее совпадение шаблона в своей области: (оценка, x левого края в 1080p)."""
    import cv2

    x0, y0, x1, y1 = spec.roi
    roi = gray[y0:y1, x0:x1]
    if spec.shrink > 1:
        roi = cv2.resize(roi, (roi.shape[1] // spec.shrink, roi.shape[0] // spec.shrink), interpolation=cv2.INTER_AREA)
    if roi.shape[0] < tpl.shape[0] or roi.shape[1] < tpl.shape[1]:
        return 0.0, 0
    res = cv2.matchTemplate(roi, tpl, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(res)
    return float(best), x0 + int(loc[0]) * spec.shrink


# --------------------------------------------------------------------------- #
# Лента убийств
# --------------------------------------------------------------------------- #

def feed_rows(bgr: Any) -> list[dict]:
    """Строки ленты убийств с ником игрока слева: [{y, x0, x1, knock}] (1080p).

    Ник игрока — оранжевый (тон 15–24 по OpenCV; ник напарника в ленте жёлто-зелёный, 28+, полупрозрачная плашка гасит насыщенность до ~100),
    названия легенд в «Вражеский щит пробит — Октейн» — краснее (11–16) и стоят у правого края. Строка
    игрока-убийцы: оранжевого ≥ 70 px в ширину и он кончается левее правого края ленты на 120+ px
    (справа — значок и ник жертвы). Красный значок (тон ≤ 5 / ≥ 175) правее ника — нокаут, иначе
    убийство."""
    import cv2
    import numpy as np

    x0, y0, x1, y1 = FEED_ROI
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    orange = (h >= 15) & (h <= 24) & (s >= 80) & (v >= 150)
    red = ((h <= 5) | (h >= 172)) & (s >= 110) & (v >= 120)
    white = (s <= 60) & (v >= 190)
    # Штрихи букв и значков: ярче своего окружения (гладкое небо или стена — нет).
    strokes = (white | (s >= 90) & (v >= 140)) & (v.astype(np.int16) - cv2.blur(v, (9, 9)).astype(np.int16) >= 18)
    edge = FEED_RIGHT - x0
    rows: list[dict] = []
    for k in range(FEED_ROWS):
        yc = int(round(FEED_ROW0 + k * FEED_ROW_STEP)) - y0
        lo, hi = max(0, yc - 11), min(orange.shape[0], yc + 11)
        if hi - lo < 12:
            break
        band = orange[lo:hi]
        has = band.sum(axis=0) >= 2
        for a, b in _clusters(has, gap=12):
            if b - a < 45 or x0 + b > FEED_RIGHT - 120:
                continue
            # Буквы, а не пятно фона: оранжевое меньше половины рамки и ярче плашки под ним; сразу правее
            # ника — белое (причина «[Потеря крови]» или значок оружия).
            box, vals = band[:, a:b + 1], v[lo:hi, a:b + 1]
            if box.mean() > 0.5 or float(np.median(vals[box])) - float(np.median(vals[~box])) < FEED_CONTRAST:
                continue
            if int(white[lo:hi, b + 4:b + 70].sum()) < 20:
                continue
            # Ник убийцы — первый текст строки; в чате «Вы — Спецумение [Пилон…] не готово» левее оранжевого
            # слова стоит ещё оранжевое «Вы» (значки рядом с ником — мелкие, до FEED_OTHER_ORANGE столбцов).
            if int((orange & strokes)[lo:hi, max(0, a - 200):max(0, a - 12)].any(axis=0).sum()) > FEED_OTHER_ORANGE:
                continue
            # Строка ленты прижата вправо: белый ник жертвы кончается у FEED_RIGHT (инвентарь, карта,
            # подписи предметов так не выглядят).
            tail = white[lo:hi, edge - 45:edge + 3]
            if int(tail.sum()) < 15 or tail.mean() > 0.45:
                continue
            # Одна строка от ника до ника жертвы: между ними причина/оружие/значки без пустых разрывов
            # (вывеска на фоне + чужая строка чата справа так не выглядят).
            texty = strokes[lo:hi, b + 1:edge].any(axis=0)
            if _longest_false(texty) > FEED_MAX_GAP:
                continue
            knock = int(red[lo:hi, b + 1:edge - 20].sum()) >= 8
            rows.append({"y": y0 + yc, "x0": x0 + a, "x1": x0 + b, "knock": knock})
            break
    return rows


# --------------------------------------------------------------------------- #
# Кадр → наблюдение
# --------------------------------------------------------------------------- #

def _longest_false(mask: Any) -> int:
    """Самый длинный подряд идущий кусок False."""
    best = run = 0
    for on in mask:
        run = 0 if on else run + 1
        best = max(best, run)
    return best


def _clusters(mask: Any, gap: int) -> list[tuple[int, int]]:
    """Отрезки True вдоль строки, разрывы ≤ gap сливаются: [(начало, конец)] включительно."""
    import numpy as np

    idx = np.flatnonzero(mask)
    if not len(idx):
        return []
    out = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > gap + 1:
            out.append((start, prev))
            start = i
        prev = i
    out.append((start, prev))
    return out


def analyze_frame(gray: Any, bgr: Any | None = None, game: str = "apex") -> dict:
    """Наблюдение по кадру 1920×1080: ``{spec_name: (score, x)}`` и ``feed`` (строки ленты; bgr нужен
    только для неё — без цвета лента не смотрится)."""
    obs: dict[str, Any] = {}
    templates = load_templates(game)
    # Значок очков ⌃ ищем первым: нет его — слова плашек не сравниваем (так вдвое быстрее).
    for spec, tpl in templates:
        if spec.kind == "chevron":
            obs[spec.name] = _match(gray, spec, tpl)
    banner = obs.get("chevron", (0.0, 0))[0] >= CHEVRON_THR
    for spec, tpl in templates:
        if spec.kind != "chevron":
            obs[spec.name] = _match(gray, spec, tpl) if banner or not spec.needs_chevron else (0.0, 0)
    obs["feed"] = feed_rows(bgr) if bgr is not None else []
    return obs


# --------------------------------------------------------------------------- #
# Наблюдения по времени → события
# --------------------------------------------------------------------------- #

BANNER_GAP = 0.8     # разрыв внутри одного показа плашки, с
BANNER_SHIFT = 6     # сдвиг слова (другой ник — другая длина строки) — новая плашка, px
BANNER_MIN_FRAMES = 2
STATE_MIN_SEC = 1.0  # состояние засчитывается, если держится столько
STATE_KEEP_DROP = 0.25  # удержание состояния — по порогу ниже входного на столько
FEED_TRACK_PX = 8
FEED_TRACK_GAP = 1.6
FEED_MIN_SEC = 0.75
FEED_WIDTH_PX = 5   # ширина ника игрока в ленте — одна на запись


def _score(best: float, thr: float) -> float:
    return round(min(1.0, max(0.0, 0.55 + 0.45 * (best - thr) / max(1e-6, 1.0 - thr))), 3)


def hud_events(frames: list[tuple[float, dict]], game: str = "apex") -> list[dict]:
    """События по наблюдениям [(t, obs)] (t — секунды исходника, по возрастанию)."""
    specs = {spec.name: spec for spec, _ in load_templates(game)}
    events: list[dict] = []
    step = _step(frames)

    # Плашки и баннеры: подряд идущие кадры с совпадением → один показ (по сдвигу слова — разные).
    for name, spec in specs.items():
        if spec.kind in ("chevron",) or spec.style != "banner":
            continue
        runs: list[dict] = []
        for t, obs in frames:
            best, x = obs.get(name, (0.0, 0))
            if best < spec.thr or not _banner_ok(spec, obs, best):
                continue
            last = runs[-1] if runs else None
            if last and t - last["t1"] <= BANNER_GAP + 1e-6 and (abs(x - last["x"]) <= BANNER_SHIFT or not spec.needs_chevron):
                last.update(t1=t, n=last["n"] + 1, best=max(last["best"], best), x=x)
            else:
                runs.append({"t0": t, "t1": t, "x": x, "n": 1, "best": best})
        min_frames = BANNER_MIN_FRAMES if step <= 0.34 else 1
        for run in runs:
            if run["n"] < min_frames or spec.kind == "assist":
                if spec.kind == "assist" and run["n"] >= min_frames:
                    events.append(_event(run["t0"], "knock", _score(run["best"], spec.thr) * 0.8,
                                         spec.detail + " (помощь)"))
                continue
            events.append(_event(run["t0"], spec.kind, _score(run["best"], spec.thr), spec.detail))

    # Состояния: событие при входе, если держится STATE_MIN_SEC и прошлое было раньше spec.rearm.
    for name, spec in specs.items():
        if spec.style != "state":
            continue
        on_since: float | None = None
        fired_end = -1e9
        fired = False
        best_run = 0.0
        for t, obs in frames:
            best, _x = obs.get(name, (0.0, 0))
            # Гистерезис: вход — по порогу, удержание — по пониженному (надпись над панелью без подложки
            # на светлом песке скачет 0.5–0.9 от кадра к кадру).
            if best >= spec.thr or (on_since is not None and best >= spec.thr - STATE_KEEP_DROP):
                if on_since is None:
                    on_since, best_run, fired = t, best, False
                best_run = max(best_run, best)
                # Мигание состояния (кадр без совпадения посреди) не даёт второго события: ждём spec.rearm.
                if not fired and t - on_since >= STATE_MIN_SEC - 1e-6 and on_since - fired_end > spec.rearm:
                    events.append(_event(on_since, spec.kind, _score(best_run, spec.thr), spec.detail))
                    fired = True
                if fired:
                    fired_end = t
            else:
                on_since = None

    # После победы матч кончен: на подиуме и в сводке «строки ленты» — это фон, не убийства.
    wins = [e["t"] for e in events if e["kind"] == "win"]
    knocks = [e["t"] for e in events if e["kind"] == "knock" and "помощь" not in e["detail"]]
    events.extend(k for k in _feed_events(frames, knocks) if not any(3.0 < k["t"] - w < 240.0 for w in wins))
    return _dedupe_levels(sorted(events, key=lambda e: e["t"]))


def _step(frames: list[tuple[float, dict]]) -> float:
    if len(frames) < 2:
        return 0.25
    gaps = sorted(b[0] - a[0] for a, b in zip(frames, frames[1:]))
    return gaps[len(gaps) // 2]


def _banner_ok(spec: Spec, obs: dict, best: float) -> bool:
    if spec.needs_chevron:
        chev, cx = obs.get("chevron", (0.0, 0))
        if chev < CHEVRON_THR:
            return False
        # «СОДЕЙСТВИЕ В НОКАУТЕ» содержит «НОКАУТ»: при помощи нокаут не засчитываем.
        if spec.kind == "knock":
            assist, _ = obs.get("assist_ru", (0.0, 0))
            if assist >= 0.72 and assist >= best - 0.15:
                return False
    if spec.kind == "level_up":
        other, _ = obs.get("maxlevel_ru", (0.0, 0))
        return best > other
    if spec.kind == "max_level":
        other, _ = obs.get("levelup_ru", (0.0, 0))
        return best > other
    return True


def feed_tracks(frames: list[tuple[float, dict]]) -> list[dict]:
    """Строки ленты с ником игрока по времени: одна строка съезжает вниз, но ник в ней стоит на том же x
    (его место задаёт длина остального текста строки) — трек = те же x0/x1 ± FEED_TRACK_PX с разрывами
    ≤ FEED_TRACK_GAP с. → [{t0, t1, n, kill_votes, knock_votes, x0, x1}]."""
    tracks: list[dict] = []
    for t, obs in frames:
        for row in obs.get("feed") or []:
            hit = next((tr for tr in reversed(tracks) if t - tr["t1"] <= FEED_TRACK_GAP and t > tr["t1"]
                        and abs(tr["x0"] - row["x0"]) <= FEED_TRACK_PX and abs(tr["x1"] - row["x1"]) <= FEED_TRACK_PX), None)
            if hit is None:
                hit = {"t0": t, "t1": t, "n": 0, "kill_votes": 0, "knock_votes": 0, "x0": row["x0"], "x1": row["x1"]}
                tracks.append(hit)
            hit["t1"] = t
            hit["n"] += 1
            hit["knock_votes" if row["knock"] else "kill_votes"] += 1
    return tracks


def _feed_events(frames: list[tuple[float, dict]], knock_times: list[float] | None = None) -> list[dict]:
    """Убийства из ленты: трек строки «ник игрока слева», замеченный в нескольких кадрах, где значка
    нокаута нет в большинстве кадров (красный значок в одном кадре — дым или фон за плашкой).

    Ник игрока в записи один — и ширина его в ленте одна. Её узнаём по строкам нокаута рядом с плашкой
    «НОКАУТ» (``knock_times``), иначе — по ширине, повторившейся хотя бы в двух треках. Строка другой
    ширины (оранжевый ник напарника в «изготавливает: …», вывеска) — не убийство игрока; ширину узнать
    не из чего — убийство с низкой оценкой и пометкой."""
    step = _step(frames)
    need = max(2, int(round(FEED_MIN_SEC / max(step, 1e-3))))
    tracks = [tr for tr in feed_tracks(frames) if tr["n"] >= need]
    width = lambda tr: tr["x1"] - tr["x0"]  # noqa: E731
    owner = [width(tr) for tr in tracks if tr["knock_votes"] >= tr["kill_votes"]
             and any(-1.0 <= tr["t0"] - k <= 3.0 for k in knock_times or [])]
    if not owner:
        widths = [width(tr) for tr in tracks]
        owner = [w for w in widths if sum(abs(w - o) <= FEED_WIDTH_PX for o in widths) >= 2]
    out = []
    for tr in tracks:
        if tr["kill_votes"] <= tr["knock_votes"]:
            continue
        share = tr["kill_votes"] / tr["n"]
        if not owner:
            out.append(_event(tr["t0"], "kill", 0.3 + 0.2 * share, "лента убийств: игрок добил (ник не подтверждён)"))
        elif any(abs(width(tr) - w) <= FEED_WIDTH_PX for w in owner):
            out.append(_event(tr["t0"], "kill", 0.55 + 0.35 * share, "лента убийств: игрок добил"))
    return out


def _dedupe_levels(events: list[dict]) -> list[dict]:
    """Максимальный уровень тоже показывается баннером уровня: «новый уровень» рядом с «макс.» — прочь."""
    maxes = [e["t"] for e in events if e["kind"] == "max_level"]
    return [e for e in events if not (e["kind"] == "level_up" and any(abs(e["t"] - m) <= 4.0 for m in maxes))]


def _event(t: float, kind: str, score: float, detail: str) -> dict:
    return {"t": round(float(t), 2), "kind": kind, "score": round(float(score), 3), "detail": detail}
