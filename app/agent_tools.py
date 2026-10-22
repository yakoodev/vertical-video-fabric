"""Инструменты ИИ-монтажёра: один реестр для встроенного чата и для MCP.

Зачем один реестр. Клип правят два вида агентов: встроенный ассистент в
редакторе (Gemini с function calling, ``app/ai/editor_agent.py``) и внешние
агенты — Claude Code, Codex, любой MCP-клиент (``app/agent_api.py`` → ``/mcp``).
Если у каждого свой набор ручек, они разъезжаются: один умеет ставить титры, другой
нет, проверки разные. Поэтому инструмент описан ровно один раз — имя, описание
(рус + англ), JSON Schema параметров и обработчик ``handler(ctx, args)`` — а
встроенный агент и MCP только переводят его в свой протокол.

Правила, общие для всех инструментов:

* Время — секунды готового клипа (как субтитры на дорожке редактора), кадр —
  опорные 1080×1920, ``x``/``y`` — доли кадра. Контракт слоёв — ``docs/motion/spec.md``.
* Всё, что меняет клип, проходит через те же нормализацию и проверку, что и правка
  человека (``app.motion`` для слоёв, ``app.clip_spec`` для кусков). Ошибка не
  роняет агента: инструмент возвращает ``ok: false`` и список ``problems``, чтобы
  модель сама исправилась.
* Перед первой правкой вызывается ``ctx.before_change()`` — это снимок истории
  (Ctrl+Z). Встроенный агент делает ОДИН снимок на весь свой ход («ИИ: …»),
  MCP — по снимку на каждый вызов, меняющий клип.
"""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app import motion
from app.clip_settings import normalize_render_settings
from app.backgrounds import BACKGROUND_KINDS as BG_KINDS

# Стили фона для подсказок инструментов (app/backgrounds.py).
BG_KINDS_HINT = ("aurora (переливы, по умолчанию) | grid (неоновая перспективная сетка) | bokeh (блики) | "
                 "waves (светящиеся волны) | gradient | solid (статичные)")

# Безопасные зоны вертикального ролика: сверху — шапка площадки, снизу — подпись,
# кнопки и прогресс-бар TikTok/Shorts/Reels. Текст там читается плохо или закрыт.
SAFE_TOP = 0.10
SAFE_BOTTOM = 0.80
# Не больше стольких графических слоёв на экране одновременно — дальше каша.
MAX_SIMULTANEOUS = 3
MAX_LAYERS_PER_CALL = 30
TEXTUAL_TYPES = {"text", "counter", "callout"}


class ToolError(Exception):
    """Понятная модели ошибка: сообщение + список конкретных проблем."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


@dataclass
class ImageResult:
    """Картинка как результат инструмента (кадр клипа) — модель её «видит»."""

    data: bytes
    mime_type: str
    info: dict = field(default_factory=dict)


@dataclass
class ToolContext:
    store: Any
    clip_plan_id: int | None = None
    # Хук перед первой правкой: снимок истории. Вызывается не чаще раза за контекст.
    on_first_change: Callable[[], None] | None = None
    changed: bool = False

    def before_change(self) -> None:
        if not self.changed and self.on_first_change is not None:
            self.on_first_change()
        self.changed = True

    @property
    def plan_id(self) -> int:
        if self.clip_plan_id is None:
            raise ToolError("не выбран клип (clip_id)")
        return int(self.clip_plan_id)


@dataclass
class Tool:
    name: str
    title: str  # короткая русская подпись для UI
    description: str  # что делает и когда звать (рус + англ) — это читает модель
    parameters: dict
    handler: Callable[[ToolContext, dict], Any]
    mutating: bool = False
    scope: str = "clip"  # clip — нужен клип; global — нет (поиск, справочники)
    label: Callable[[dict], str] | None = None

    def step_label(self, args: dict) -> str:
        try:
            if self.label is not None:
                text = self.label(args or {})
                if text:
                    return text[:160]
        except Exception:  # noqa: BLE001 - подпись не должна ронять шаг
            pass
        return self.title

    def public(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "parameters": self.parameters,
            "mutating": self.mutating,
            "scope": self.scope,
        }


# --------------------------------------------------------------------------- #
# Библиотека шрифтов/эмодзи/паков (app/motion_assets.py) и моушн-кадр
# (app/motion_render.py) импортируются лениво: реестр грузится и без них, а
# инструмент честно говорит, чего не хватает.
# --------------------------------------------------------------------------- #


def _motion_assets():
    try:
        from app import motion_assets
    except ImportError as exc:  # pragma: no cover
        raise ToolError(f"библиотека шрифтов/эмодзи/паков недоступна: {exc}") from exc
    return motion_assets


# --------------------------------------------------------------------------- #
# Клип: время, куски, настройки
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def clip_timeline(store: Any, clip_plan_id: int) -> dict:
    """Куски клипа на шкале ГОТОВОГО клипа (как в docs/motion/spec.md).

    Переход-наплыв съедает по ``overlap`` на каждом стыке, поэтому кусок N
    начинается раньше, чем в простой склейке. Считаем тем же
    ``motion.clip_timeline``, что и рендер, — время слоёв совпадает с видео.
    """
    plan = store.get_clip_plan(clip_plan_id)
    segments = plan.get("segments") or []
    render = normalize_render_settings(plan.get("render_settings") or {})
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    starts, total = motion.clip_timeline(spans, render.get("transition"))
    pieces = []
    for index, (seg, (src_start, src_end), start) in enumerate(zip(segments, spans, starts)):
        pieces.append(
            {
                "index": index,
                "segment_id": seg["id"],
                "title": seg.get("title") or "",
                # Таймкоды исходника — те же имена, что в файле клипа, trim_piece и create_clip;
                # source_start/source_end — прежние имена (алиасы, их читают старые скрипты).
                "start_sec": round(src_start, 3),
                "end_sec": round(src_end, 3),
                "source_start": round(src_start, 3),
                "source_end": round(src_end, 3),
                "clip_start": round(start, 3),
                "clip_end": round(start + (src_end - src_start), 3),
                "focus": seg.get("focus") or [],
                "fit": seg.get("fit"),
            }
        )
    return {"plan": plan, "render": render, "pieces": pieces, "duration": round(total, 3),
            "plain_duration": round(sum(e - s for s, e in spans), 3)}


def _to_clip_time(pieces: list[dict], plain_t: float) -> float:
    """Время «куски встык» (так хранятся субтитры) → время готового клипа."""
    cursor = 0.0
    for piece in pieces:
        length = piece["source_end"] - piece["source_start"]
        if plain_t < cursor + length or piece is pieces[-1]:
            return round(piece["clip_start"] + (plain_t - cursor), 3)
        cursor += length
    return round(plain_t, 3)


def _clip_duration(ctx: ToolContext) -> float:
    return float(clip_timeline(ctx.store, ctx.plan_id)["duration"])


def plan_clock(store: Any, plan: dict):
    """Шкалы времени клипа (app/timebase.py) — с кадрами пресета, как спланирует рендер."""
    from app.render import effective_preset
    from app.timebase import ClipClock

    try:
        preset = effective_preset(store, (plan.get("render_settings") or {}).get("preset_id")) or {}
        fps = float(preset.get("fps") or 30)
    except Exception:  # noqa: BLE001 - без пресета — 30 кадров
        fps = 30.0
    return ClipClock.for_plan(plan, fps)


def _clock(ctx: ToolContext):
    return plan_clock(ctx.store, ctx.store.get_clip_plan(ctx.plan_id))


def _source_keyed(ctx: ToolContext) -> bool:
    """У источника клипа есть хромакей (персонаж на зелёнке вырезается)?"""
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    return bool(ctx.store.get_source(int(plan["source_id"])).get("chroma_key"))


def _current_render(ctx: ToolContext) -> dict:
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    return normalize_render_settings(plan.get("render_settings") or {})


def _save_render(ctx: ToolContext, render: dict) -> dict:
    ctx.before_change()
    ctx.store.set_clip_plan_render_settings(ctx.plan_id, render)
    return _current_render(ctx)


def _layers_of(render: dict) -> list[dict]:
    layers = render.get("layers")
    return [dict(layer) for layer in layers] if isinstance(layers, list) else []


def _camera_of(render: dict) -> list[dict]:
    camera = render.get("camera")
    return [dict(key) for key in camera] if isinstance(camera, list) else []


# --------------------------------------------------------------------------- #
# Слои: нормализация, проверка и «ремесленные» предупреждения
# --------------------------------------------------------------------------- #


def _validate_new_layers(ctx: ToolContext, raw: list, duration: float) -> list[str]:
    """Проверка той же функцией, что и файл клипа, плюс «файл/эмодзи/стикер есть»."""

    def asset_exists(asset_id: int) -> bool:
        try:
            ctx.store.get_montage_asset(int(asset_id))
            return True
        except (KeyError, TypeError, ValueError):
            return False

    return motion.validate_layers(raw, duration, asset_exists=asset_exists, ref_exists=_ref_checker())


def _ref_checker() -> Callable[[str], bool] | None:
    """Ссылки emoji:/sticker:/sfx: проверяем, только когда модуль паков уже есть —
    иначе все они выглядели бы пропавшими (так же делает /layers/validate)."""
    from app.motion_render import pack_refs_available, resolve_pack_ref

    if not pack_refs_available():
        return None
    return lambda ref: resolve_pack_ref(ref) is not None


def text_lines(props: dict) -> int:
    """Сколько строк займёт текст слоя: явные переносы + перенос по ширине ``maxWidth``.

    Агент-монтажёр: хук на y 0.13 в две строки вылез за верхнюю безопасную зону, а
    проверка считала его однострочным (видела только переносы «\\n»). Ширину буквы
    берём с запасом: жирные заглавные (хук) ≈ 0.68 кегля, обычный текст ≈ 0.58."""
    text = str(props.get("text") or "")
    size = _num(props.get("size"), 96.0) or 96.0
    width = _num(props.get("maxWidth"), 920.0) or 920.0
    heavy = bool(props.get("uppercase")) or text.upper() == text
    factor = 0.68 if heavy and (_num(props.get("weight"), 800.0) or 800.0) >= 700 else 0.58
    per_line = max(1, int(width / (size * factor)))
    total = 0
    for chunk in text.split("\n"):
        line, count = 0, 1
        for word in chunk.split():
            need = len(word) if line == 0 else line + 1 + len(word)
            if line and need > per_line:
                count, line = count + 1, len(word)
            else:
                line = need
        total += count
    return max(1, total)


def text_half_height(props: dict, scale: float = 1.0) -> float:
    """Половина высоты текстового слоя в долях кадра 1920 (строки × кегль × межстрочный)."""
    size = _num(props.get("size"), 96.0) or 96.0
    line_height = _num(props.get("lineHeight"), 1.15) or 1.15
    return size * line_height * text_lines(props) * scale / 1920 / 2


def safe_text_y(props: dict, y: float, scale: float = 1.0, top: float = SAFE_TOP) -> float:
    """Центр ``y`` текста, опущенный так, чтобы верх строк был не выше ``top`` (по умолчанию
    SAFE_TOP; в split с вебкой сверху — низ полосы вебки, ``text_band``)."""
    return round(max(y, math.ceil((top + text_half_height(props, scale)) * 1000) / 1000), 3)


def cam_strip(layout: Any) -> tuple[float, float] | None:
    """Полоса вебки раскладки split на кадре 9:16: (верх, низ) в долях высоты; иначе None."""
    from app.stream_layout import normalize_layout

    if not isinstance(layout, dict) or layout.get("mode") != "split":
        return None
    layout = normalize_layout(layout)
    height = float(layout["ratio"]) + float(layout.get("gap") or 0) / 1920
    return (0.0, round(height, 4)) if layout["cam_pos"] != "bottom" else (round(1.0 - height, 4), 1.0)


def text_band(render: dict | None) -> tuple[float, float]:
    """Где можно ставить текст по высоте (верх, низ): безопасная зона площадки минус полоса
    вебки в split (агент стримов: хук на y 0.13 лёг на лицо в полосе вебки)."""
    top, bottom = SAFE_TOP, SAFE_BOTTOM
    strip = cam_strip((render or {}).get("layout"))
    if strip:
        if strip[0] <= 0.0:
            top = max(top, strip[1] + 0.015)
        else:
            bottom = min(bottom, strip[0] - 0.015)
    return round(top, 4), round(bottom, 4)


def _layer_box(layer: dict) -> tuple[float, float]:
    """Примерная половина высоты слоя в долях кадра — для проверки безопасных зон."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    scale = _num(layer.get("scale"), 1.0) or 1.0
    kind = layer.get("type")
    height = 0.0
    if kind in ("text", "counter"):
        return text_half_height(props, scale), 0.0
    if kind == "emoji":
        height = _num(props.get("size"), 160.0) or 160.0
    elif kind in ("shape", "progress", "chart"):
        height = _num(props.get("h"), 120.0) or 120.0
    elif kind == "callout":
        height = 180.0
    elif kind == "image":
        height = _num(props.get("w"), 400.0) or 400.0
    return height * scale / 1920 / 2, 0.0


MAX_COVER_SHARE = 0.4  # непрозрачная картинка/фигура больше 40 % кадра — «закрыли видео»


def cover_share(layer: dict) -> float:
    """Какую долю кадра 1080×1920 непрозрачно закрывает слой-картинка/фигура (0, если прозрачный)."""
    if layer.get("type") not in ("shape", "image"):
        return 0.0
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    if (_num(layer.get("opacity"), 1.0) or 0.0) < 0.6:
        return 0.0
    if layer.get("type") == "shape" and props.get("fill") in (None, "", "none"):
        return 0.0  # контур (рамка, стрелка) видео не закрывает
    scale = _num(layer.get("scale"), 1.0) or 1.0
    w = (_num(props.get("w"), 400.0) or 400.0) * scale
    h = (_num(props.get("h"), w) or w) * scale
    return min(1.0, (w * h) / (1080 * 1920))


def covering_problems(layers: list[dict], keyed: bool = False) -> list[str]:
    """Ошибки: слой закрывает собой видео. Владелец: «плашки перекрывают видео — говно».
    Картинки и плашки ставим на пустое место, а не поверх героя/кадра.

    ``keyed`` — источник на зелёнке: слой ``behind`` лежит ЗА персонажем и закрывает
    только фон (скрин сайта во весь кадр за втубером — это и есть монтаж)."""
    out = []
    for layer in layers:
        if layer.get("behind"):
            if not keyed:
                out.append(f"{layer.get('id') or layer.get('type')}: behind работает только у источника с хромакеем "
                           "(set_chroma_key) — без него убери behind")
            continue
        share = cover_share(layer)
        if share > MAX_COVER_SHARE:
            out.append(f"{layer.get('id') or layer.get('type')}: закрывает {share:.0%} кадра — видео не перекрывать, "
                       f"картинки и плашки ≤ {MAX_COVER_SHARE:.0%} кадра и на пустом месте (раскладка «Мем» даёт "
                       "свободный фон под видео)")
    return out


def lint_layers(layers: list[dict], duration: float | None = None, only_ids: set[str] | None = None,
                layout: dict | None = None) -> list[str]:
    """Предупреждения ремесла (не ошибки): безопасные зоны, перегруз, мелкий текст.

    Не блокируют правку — модель (или человек) решает сама, но видит, где
    интерфейс площадки закроет титр или экран превратился в кашу. ``layout`` — раскладка
    клипа: в split текст на полосе вебки ложится на лицо стримера.
    """
    warnings: list[str] = []
    strip = cam_strip(layout)
    visible = [l for l in layers if l.get("type") != "audio" and not l.get("hidden")]
    for layer in visible:
        lid = str(layer.get("id") or "?")
        if only_ids is not None and lid not in only_ids:
            continue
        y = _num(layer.get("y"), 0.5) or 0.0
        half, _ = _layer_box(layer)
        textual = layer.get("type") in TEXTUAL_TYPES
        if textual and y + half > SAFE_BOTTOM and y - half < 1.0:
            warnings.append(f"{lid}: текст заходит в нижние 20% кадра (y={y:.2f}) — там подпись и кнопки площадки")
        if textual and y - half < SAFE_TOP and y + half > 0.0:
            lines = text_lines(layer.get("props") or {}) if layer.get("type") in ("text", "counter") else 1
            warnings.append(
                f"{lid}: верх текста на {y - half:.3f} — выше безопасной зоны {SAFE_TOP:.2f} (там шапка площадки; "
                f"центр y={y:.2f}, ≈{lines} стр.) — опусти до y≥{math.ceil((SAFE_TOP + half) * 1000) / 1000:.3f} или сократи текст")
        if textual and strip and y - half < strip[1] and y + half > strip[0]:
            where = "сверху" if strip[0] <= 0 else "снизу"
            edge = (f"опусти до y≥{math.ceil((strip[1] + 0.015 + half) * 1000) / 1000:.3f}" if strip[0] <= 0
                    else f"подними до y≤{math.floor((strip[0] - 0.015 - half) * 1000) / 1000:.3f}")
            warnings.append(f"{lid}: текст на полосе вебки ({where}, {strip[0]:.2f}–{strip[1]:.2f} кадра) — ляжет на "
                            f"лицо стримера; {edge} (positions в get_clip учитывают раскладку)")
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        if layer.get("type") == "text":
            size = (_num(props.get("size"), 80.0) or 80.0) * (_num(layer.get("scale"), 1.0) or 1.0)
            if size < 44:
                warnings.append(f"{lid}: шрифт {size:.0f}px мелковат для телефона (лучше ≥ 56)")
            if len(str(props.get("text") or "")) > 60:
                warnings.append(f"{lid}: длинный титр — на экране читают 3–6 слов")
        start, end = _num(layer.get("start"), 0.0) or 0.0, _num(layer.get("end"), 0.0) or 0.0
        whole_clip = layer.get("type") == "captions" or start <= 0.05
        if duration and end > duration + 0.05 and not (whole_clip and start < duration):
            # Слой «на весь клип» (субтитры, плашка с 0 с) просто обрежется по концу — не шум.
            warnings.append(f"{lid}: заканчивается на {end:.2f} с, а клип длится {duration:.2f} с")
        if layer.get("type") == "audio" and (_num(props.get("volume"), 1.0) or 0.0) > 1.0:
            # Громкость эффектов выравнивает сервис (на 6 дБ тише речи); выше 1 — срежется.
            warnings.append(f"{lid}: volume {_num(props.get('volume'), 1.0):.2f} срежется до 1 — звуки уже "
                            "выровнены ниже речи, обычно 0.3–0.6")
        if textual and end - start < 0.6:
            warnings.append(f"{lid}: титр виден {end - start:.2f} с — не успеют прочитать")
        if layer.get("type") == "captions":
            warnings.extend(f"{lid}: {w}" for w in caption_highlight_warnings(props))
    # Перегруз: сколько видимых слоёв одновременно в момент начала каждого слоя.
    for layer in visible:
        lid = str(layer.get("id") or "?")
        if only_ids is not None and lid not in only_ids:
            continue
        t = (_num(layer.get("start"), 0.0) or 0.0) + 0.05
        together = [
            o for o in visible
            if (_num(o.get("start"), 0.0) or 0.0) <= t < (_num(o.get("end"), 0.0) or 0.0)
            and o.get("type") not in ("shape", "captions")  # фон и субтитры — не «графика» поверх
        ]
        if len(together) > MAX_SIMULTANEOUS:
            warnings.append(f"{lid}: на {t:.1f} с на экране {len(together)} слоя графики — перегруз (≤ {MAX_SIMULTANEOUS})")
    warnings.extend(caption_overlap_warnings(visible, only_ids))
    return list(dict.fromkeys(warnings))


CAPTIONS_MIN_GAP = 0.12  # две строки главных субтитров — ~0.1 высоты кадра


def caption_overlap_warnings(layers: list[dict], only_ids: set[str] | None = None) -> list[str]:
    """Два слоя субтитров (главный и «другие голоса») ближе ~0.12 по высоте — строки лягут
    друг на друга («ВОЗЬМИ ДЕЛАЕШЬ» поверх «МНЕ НУЖНО ВЫТЯНУТЬ» в проверочном прогоне)."""
    caps = [l for l in layers if l.get("type") == "captions"]
    out = []
    for i, a in enumerate(caps):
        for b in caps[i + 1:]:
            if only_ids is not None and str(a.get("id")) not in only_ids and str(b.get("id")) not in only_ids:
                continue
            ya, yb = _num(a.get("y"), 0.5) or 0.5, _num(b.get("y"), 0.5) or 0.5
            if abs(ya - yb) < CAPTIONS_MIN_GAP:
                out.append(f"субтитры {a.get('id')} (y {ya:.2f}) и {b.get('id')} (y {yb:.2f}) ближе "
                           f"{CAPTIONS_MIN_GAP} по высоте — строки лягут друг на друга; разведи y")
    return out


# Цвет звучащего слова у стилей субтитров (frontend/src/motion/layers/captions.ts).
CAPTION_ACTIVE_COLORS = {
    "hormozi": "#ffe81f", "mrbeast": "#ffd400", "karaoke": "#ffd400", "box": "#ffffff", "neon": "#ff4fd8",
    "minimal": "#ffffff", "typewriter": "#111111", "comic": "#ff3b5c", "gradient": "#ffffff", "outline": "#ffffff",
}


def _rgb(color: Any) -> tuple[int, int, int] | None:
    text = str(color or "").strip().lstrip("#")
    if len(text) not in (6, 8):
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


HIGHLIGHT_MIN_DISTANCE = 90  # цвета ближе этого (евклидово по RGB) на глаз не различить в подсветке


def colors_close(a: Any, b: Any) -> bool:
    """Два цвета почти одинаковые — подсветка одним на фоне другого не видна."""
    ra, rb = _rgb(a), _rgb(b)
    return bool(ra and rb) and sum((x - y) ** 2 for x, y in zip(ra, rb)) < HIGHLIGHT_MIN_DISTANCE ** 2


def caption_highlight_warnings(props: dict) -> list[str]:
    """Подсветка ключевых слов цветом звучащего слова не видна (hormozi красит его жёлтым)."""
    active = _rgb(props.get("activeColor") or CAPTION_ACTIVE_COLORS.get(str(props.get("style") or ""), "#ffe81f"))
    if not active:
        return []
    close = sorted({
        str(h.get("color")) for h in props.get("highlightWords") or []
        if isinstance(h, dict) and (rgb := _rgb(h.get("color"))) and sum((a - b) ** 2 for a, b in zip(rgb, active))
        < HIGHLIGHT_MIN_DISTANCE ** 2
    })
    if not close:
        return []
    return [f"highlightWords цветом {', '.join(close)} почти как звучащее слово этого стиля — подсветки не видно; "
            "бери контрастный (#FF3B30 красный, #22C55E зелёный)"]


def _layer_summary(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    kind = layer.get("type")
    what = {
        "text": lambda: f"титр «{_short(props.get('text'))}»",
        "emoji": lambda: f"эмодзи {props.get('char') or ''}",
        "image": lambda: "картинка" + (f" #{props.get('asset_id')}" if props.get("asset_id") else f" {props.get('src') or ''}"),
        "audio": lambda: f"звук {props.get('asset_id') or ''}",
        "counter": lambda: f"счётчик {props.get('from', 0)}→{props.get('to', '')}",
        "progress": lambda: "шкала",
        "chart": lambda: f"график {props.get('kind') or ''}",
        "callout": lambda: f"плашка «{_short(props.get('title'))}»",
        "shape": lambda: f"фигура {props.get('kind') or ''}",
    }.get(kind, lambda: str(kind or "слой"))()
    return f"{what} на {(_num(layer.get('start'), 0.0) or 0.0):.1f} с"


def _short(text: Any, limit: int = 32) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# Обработчики
# --------------------------------------------------------------------------- #


def _subtitles_view(store: Any, clip_plan_id: int, pieces: list[dict]) -> dict:
    """Субтитры во времени готового клипа: под слова подгоняют титры и наезды."""
    from app.clip_subtitles import words_by_line

    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    shift = (lambda t: _to_clip_time(pieces, t)) if pieces else (lambda t: round(t, 3))
    words = payload.get("words") or []
    lines = []
    raw_lines = payload.get("lines") or []
    # Каждое слово — в одной строке (раньше слово на стыке попадало в обе).
    for index, (line, own) in enumerate(zip(raw_lines, words_by_line(raw_lines, words))):
        start, end = float(line["start"]), float(line["end"])
        inside = [
            [w["word"], round(shift(float(w["start"])), 2), round(shift(float(w["end"])), 2)]
            + ([w["speaker"]] if w.get("speaker") else [])
            for w in own
        ]
        lines.append({"i": index, "start": round(shift(start), 2), "end": round(shift(end), 2),
                      "text": line["text"], "words": inside})
    return {"has_subtitles": bool(lines), "edited": bool(payload.get("edited")), "lines": lines}


def subtitle_gaps(store: Any, plan: dict, pieces: list[dict]) -> list[int]:
    """Куски клипа, где в исходнике есть речь, а в субтитрах клипа — ни слова.

    Так бывает, когда куски поменяли после распознавания: субтитры остались от
    старого набора, и у части клипа текста нет (владелец: «у второго клипа
    субтитров почему то нет»).
    """
    payload = store.get_clip_plan_subtitles(plan["id"]) or {}
    words = payload.get("words") or []
    try:
        cues = store.get_source_transcript(int(plan["source_id"])) or []
    except Exception:  # noqa: BLE001 - нет транскрипта — судим только по субтитрам
        cues = []
    gaps, offset = [], 0.0
    for index, piece in enumerate(pieces):
        length = float(piece["source_end"]) - float(piece["source_start"])
        lo, hi = offset, offset + length
        offset = hi
        if length < 2.0 or any(lo <= float(w["start"]) < hi for w in words):
            continue
        spoken = sum(
            len(str(c.get("text") or "").split())
            for c in cues
            if float(c.get("start", 0)) < float(piece["source_end"]) and float(c.get("end", 0)) > float(piece["source_start"])
        )
        if spoken >= 3 or (not cues and words):
            gaps.append(index)
    return gaps


def h_get_clip(ctx: ToolContext, _args: dict) -> dict:
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    plan, render = timeline["plan"], timeline["render"]
    source = ctx.store.get_source(int(plan["source_id"]))
    library = {a["id"]: a for a in ctx.store.list_montage_assets()}
    inserts = []
    for index, ins in enumerate(render.get("inserts") or []):
        asset = library.get(ins["asset_id"]) or {}
        inserts.append({"index": index, **ins, "asset_label": asset.get("label") or "", "asset_kind": asset.get("kind") or ""})
    layers = _layers_of(render)
    boxes = _subject_boxes(ctx)
    pieces = [{**piece, "subject_box": box} for piece, box in zip(timeline["pieces"], boxes["pieces"])] \
        if boxes["pieces"] and len(boxes["pieces"]) == len(timeline["pieces"]) else timeline["pieces"]
    return {
        "clip_id": plan["id"],
        "title": plan["title"],
        "description": plan.get("description") or "",
        "source": {"id": source["id"], "name": source.get("original_filename") or "",
                   "width": source.get("width"), "height": source.get("height"),
                   "audio": _source_audio_view(source)},
        "duration": timeline["duration"],
        **_cuts_view(plan_clock(ctx.store, plan), render, timeline["duration"]),
        "frame": {"width": 1080, "height": 1920, "safe_zone": {"top": SAFE_TOP, "bottom": SAFE_BOTTOM}},
        "framing": _framing_view(ctx, source, render),
        "pieces": pieces,
        "render": {
            "preset_id": render.get("preset_id"),
            "effective_preset": _effective_preset_view(ctx.store, render.get("preset_id")),
            "subs_on": render.get("subs_on"),
            "sub_id": render.get("sub_id"),
            "sub_pos_pct": render.get("sub_pos_pct"),
            "subtitle_zone": _subtitle_zone(render),
            "banner_on": render.get("banner_on"),
            "music_on": render.get("music_on"),
            "mirror": render.get("mirror"),
            "transition": render.get("transition"),
            "cover": (render.get("cover") or {}).get("mode"),
            "mute": render.get("mute") or [],
            "gain": render.get("gain") or [],
            **_key_view(source, render),
        },
        # Готовые места на кадре — как монтирует владелец: заголовок сверху по центру,
        # субтитры строго по центру под лицом (с учётом раскладки «вебка снизу»).
        "positions": _recommended_positions(ctx, render, boxes),
        "layers": layers,
        "camera": _camera_of(render),
        "inserts": inserts,
        "subtitles": _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"]),
        "warnings": _clip_warnings(ctx, plan, timeline, layers),
    }


def _source_audio_view(source: dict) -> dict:
    """Звуковые дорожки источника: сколько и какие выбраны (игра + микрофон — set_source_audio)."""
    from app.source_audio import audio_view

    try:
        return audio_view(source)
    except Exception:  # noqa: BLE001 - справка, не роняет get_clip
        return {}


def _effective_preset_view(store: Any, preset_id: Any) -> dict | None:
    """Каким пресетом отрендерится клип (при preset_id null — пресет по умолчанию)."""
    from app.render import effective_preset

    preset = effective_preset(store, preset_id)
    if not preset:
        return None
    return {"id": preset["id"], "name": preset.get("label") or "", "default": preset_id is None,
            "size": f"{preset.get('output_width')}×{preset.get('output_height')}"}


def _key_view(source: dict, render: dict) -> dict:
    """Хромакей источника и фон за персонажем — агент должен знать, что фон свой."""
    key = source.get("chroma_key")
    if not key:
        return {}
    return {
        "chroma_key": key,
        "backdrop": render.get("backdrop"),
        "key_note": ("Источник на зелёнке: персонаж вырезан, фон — backdrop (set_backdrop). Слои с behind:true "
                     "лежат ЗА персонажем — скрины, картинки, фон-мемы во весь кадр можно (видео не закрывают). "
                     "Без behind слой поверх персонажа: на нём ничего крупного. Крупность и место персонажа — "
                     "set_layout mode meme + video_scale (2–3) + video_y."),
    }


TIME_SCALES_NOTE = {
    "clip": ("секунды клипа ДО вырезов, с наплывами переходов: duration, pieces[].clip_start, слои, камера, "
             "вставки, субтитры, cuts, mute, gain, view_frame, at_word, find_noises, apply_cuts, set_subtitle_words"),
    "final": ("секунды ГОТОВОГО ролика (после вырезов и наплывов): finished_duration, contact_sheet и listen по "
              "умолчанию; before_cuts=true у них — шкала clip, final=true у view_frame/compose_cover — шкала final"),
    "source": "секунды исходника: pieces[].start_sec/end_sec, trim_piece, get_transcript, get_shots, чат",
    "piece": "секунды от начала куска: pieces[].focus, set_focus t",
}


def plain_cuts_to_clip(clock: Any, cuts: list[dict]) -> list[dict]:
    """Вырезы (хранятся «кусками подряд») → шкала clip; через стык с наплывом — две части с piece."""
    out = []
    multi = len(clock.lengths) > 1
    for cut in cuts:
        for part in clock.plain_range_to_clip(float(cut["start"]), float(cut["end"])):
            out.append({**cut, "start": part["start"], "end": part["end"],
                        **({"piece": part["piece"]} if multi else {})})
    return out


def _cuts_view(clock: Any, render: dict, duration: float) -> dict:
    """Вырезы и длина ГОТОВОГО ролика: агент не должен гадать, сколько останется."""
    from app.cuts import normalize_cuts

    cuts = normalize_cuts(render.get("cuts"))
    if not cuts:
        return {"time": "clip", "time_scales": TIME_SCALES_NOTE,
                "time_note": "вырезов нет: шкала clip совпадает с готовым роликом (final)"}
    final = clock.final_duration
    return {
        "time": "clip",
        "time_scales": TIME_SCALES_NOTE,
        "cuts": plain_cuts_to_clip(clock, cuts),
        "finished_duration": final,
        "time_note": (
            f"Есть вырезы: готовый ролик {final} с (duration {duration} — до вырезов). Слои, камера, "
            "вставки, субтитры и сами cuts — шкала clip (ДО вырезов, как duration) и при рендере сдвигаются "
            "сами; то, что целиком внутри выреза, пропадает. Хук с 0 с остаётся в начале. Готовый ролик — "
            "шкала final (contact_sheet, listen)."
        ),
    }


def _clip_warnings(ctx: ToolContext, plan: dict, timeline: dict, layers: list) -> list[str]:
    layout = (timeline.get("render") or {}).get("layout")
    warnings = lint_layers(layers, timeline["duration"], layout=layout) if layers else []
    gaps = subtitle_gaps(ctx.store, plan, timeline["pieces"])
    if gaps:
        warnings.insert(0, (
            f"На кусках {', '.join(map(str, gaps))} речь есть, а субтитров нет — субтитры устарели. "
            "Сначала generate_subtitles (распознать заново), потом auto_edit."
        ))
    cuts = (timeline.get("render") or {}).get("cuts") or []
    if cuts:
        from app.cuts import cut_edge_warnings

        words = (ctx.store.get_clip_plan_subtitles(plan["id"]) or {}).get("words") or []
        warnings.extend(f"Вырезы: {w}" for w in cut_edge_warnings(cuts, words, float(timeline["duration"])))
    if timeline["pieces"]:
        ending = ending_warning(ctx.store, int(plan["source_id"]), float(timeline["pieces"][-1]["source_end"]))
        if ending:
            warnings.append(f"Концовка: {ending} (trim_piece последнего куска).")
    transition = (timeline.get("render") or {}).get("transition")
    if len(timeline["pieces"]) > 1 and transition and transition.get("type") != "cut":
        from app.transitions import speech_join_warnings, speech_under_joins

        words = (ctx.store.get_clip_plan_subtitles(plan["id"]) or {}).get("words") or []
        report = speech_under_joins(plan.get("segments") or [], transition, words)
        warnings.extend(f"Переход: {w}" for w in speech_join_warnings(report, transition))
    layout = (timeline.get("render") or {}).get("layout") or {}
    if layout.get("mode") in ("split", "pip_cam"):
        source = ctx.store.get_source(int(plan["source_id"]))
        warnings.extend(f"Раскладка: {w}" for w in facecam_warnings(layout, source))
    return warnings


def h_separate_voices(ctx: ToolContext, _args: dict) -> dict:
    from app.ai.speakers import separate_voices

    ctx.before_change()
    try:
        out = separate_voices(ctx.store, ctx.plan_id)
    except (RuntimeError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    voices = len(out["counts"])
    note = ("голос один — субтитры обычные" if voices <= 1 else
            f"голосов {voices}: слова каждого помечены. Главный слой captions — props.speakers ['A'], "
            "другие голоса — второй слой captions с speakers ['B','C','D'], мельче (size ~60) и рядом "
            "(auto_edit ставит оба сам)")
    return {"ok": True, **out, "note": note}


def h_generate_subtitles(ctx: ToolContext, _args: dict) -> dict:
    from app.clip_subtitles import generate_for_plan

    from app.subtitles.registry import QUALITY_WHISPER_MODEL

    ctx.before_change()
    old_words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    # Точная модель: small на стримах выдумывает фразы под чужими голосами и музыкой.
    payload = generate_for_plan(ctx.store, ctx.plan_id, provider="whisper", model=QUALITY_WHISPER_MODEL)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, old_words, payload.get("words") or [])
    out = {"ok": True, "lines": len(payload.get("lines") or []), "words": len(payload.get("words") or [])}
    if moved:
        out["highlights"] = moved
    return out


def h_listen(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import listen_range

    try:
        return listen_range(ctx.store, ctx.plan_id, _num(args.get("from_sec")), _num(args.get("to_sec")),
                            before_cuts=bool(args.get("before_cuts")))
    except ValueError as exc:
        raise ToolError(f"listen: {exc}") from exc


def h_find_noises(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import find_noises

    try:
        # Порог не задан — от медианы звука клипа (игра громкая, разговор — тихий).
        return find_noises(ctx.store, ctx.plan_id, _num(args.get("threshold_db")))
    except ValueError as exc:
        raise ToolError(f"find_noises: {exc}") from exc


def _subject_boxes(ctx: ToolContext) -> dict:
    """Рамки героя (Gemini-зрение, кэш): ``{union, pieces}``; без зрения — пусто."""
    try:
        from app.ai.subject_box import subject_boxes

        return subject_boxes(ctx.store, ctx.plan_id)
    except Exception:  # noqa: BLE001 - без зрения — привычные места
        return {"union": None, "pieces": []}


def _framing_view(ctx: ToolContext, source: dict, render: dict) -> dict:
    """Как кадрируется 9:16: какую долю ширины исходника видно и как это менять."""
    from app.render import effective_preset
    from app.video_crop import window_share

    preset = effective_preset(ctx.store, render.get("preset_id")) or {}
    share = window_share(source, int(preset.get("output_width") or 1080), int(preset.get("output_height") or 1920))
    out: dict = {
        "window_w": share,
        "note": (
            "Окно 9:16 видит долю window_w ширины исходника; центр окна — focus куска (pieces[].focus, "
            "t — секунды от начала куска, x 0..1 — доля ширины исходника; пусто — центр 0.5). Менять — "
            "set_focus(piece, points) с cut:true на склейках планов (get_shots). Двое дальше друг от друга, "
            "чем window_w, в окно не влезут: фокус на говорящего со сменой на склейке/реплике или "
            "set_piece_fit (кусок шире окна, сверху и снизу фон). Камера (set_camera) только приближает, zoom ≥ 1."
        ),
    }
    if preset and not preset.get("smart_reframe"):
        out["warning"] = "у пресета выключен smart_reframe — фокус не действует, кадр по центру"
    return out


def _recommended_positions(ctx: ToolContext, render: dict, boxes: dict | None = None) -> dict:
    """Места для текста с учётом того, где на кадре герой (рамку даёт Gemini-зрение)."""
    from app.auto_edit import HOOK_Y, caption_y_for

    layout = (render.get("layout") or {}).get("mode") or "single"
    places = {"captions_y": caption_y_for(render), "punchline_y": 0.3, "subject": None}
    boxes = boxes if boxes is not None else _subject_boxes(ctx)
    try:
        from app.ai.subject_box import text_positions

        if boxes.get("union"):
            places = text_positions(boxes["union"], caption_y_for(render))
    except Exception:  # noqa: BLE001 - без зрения — привычные места
        pass
    hook_props = {"size": 96, "weight": 900, "uppercase": True}
    # Верх зоны текста — с учётом полосы вебки (split сверху): хук под полосой, а не на лице.
    top, _bottom = text_band(render)
    by_lines = {str(n): safe_text_y({**hook_props, "text": "\n".join(["X"] * n)}, HOOK_Y, top=top)
                for n in (1, 2, 3)}
    strip = cam_strip(render.get("layout"))
    under = f" В раскладке split вебка сверху до {strip[1]:.2f} кадра — хук под ней." if strip and strip[0] <= 0 else ""
    out = {
        "hook": {"x": 0.5, "y": by_lines["1"], "y_by_lines": by_lines,
                 "note": (f"заголовок-хук: строго по центру сверху; y={by_lines['1']} — для ОДНОЙ строки 96 px. Верх "
                          f"текста не выше {top}: две строки — y≈{by_lines['2']}, три — {by_lines['3']} (add_layers "
                          "предупредит, если верх выше зоны или текст на полосе вебки)." + under)},
        "captions": {"x": 0.5, "y": places["captions_y"], "note": "субтитры: строго x=0.5 и этот y — свободное от героя место"},
        "punchline_text": {"x": 0.5, "y": places["punchline_y"], "note": "крупное слово-панчлайн: по центру, не на герое"},
        "subject_box": places["subject"],
        "layout": layout,
        "text_band": {"top": top, "bottom": _bottom},
    }
    if strip:
        out["cam_strip"] = {"y_from": strip[0], "y_to": strip[1],
                            "note": "полоса вебки split: текст и картинки сюда не ставь — там лицо стримера"}
    if places["subject"]:
        out["subject_note"] = ("subject_box — объединение рамок героя по всем кускам (кадр 9:16); у каждого куска своя — "
                               "pieces[].subject_box. Внутри куска со сменой планов (get_shots) рамка тоже объединение "
                               "и ненадёжна — смотри source_contact_sheet с x по планам")
    return out


def _subtitle_zone(render: dict) -> dict | None:
    """Где на кадре стоят субтитры — чтобы не класть титры поверх них."""
    if not render.get("subs_on"):
        return None
    bottom = 1.0 - float(render.get("sub_pos_pct") or 12.0) / 100
    return {"y_from": round(max(0.0, bottom - 0.12), 3), "y_to": round(bottom, 3)}


def h_get_transcript(ctx: ToolContext, args: dict) -> dict:
    """Реплики исходника вокруг кусков — для подрезки (время ИСХОДНИКА)."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    cues = ctx.store.get_source_transcript(int(timeline["plan"]["source_id"]))
    lo = _num(args.get("from_sec"))
    hi = _num(args.get("to_sec"))
    if lo is None or hi is None:
        starts = [p["source_start"] for p in timeline["pieces"]] or [0.0]
        ends = [p["source_end"] for p in timeline["pieces"]] or [0.0]
        lo, hi = min(starts) - 10, max(ends) + 10
    if hi - lo > 900:
        hi = lo + 900
    out = [
        {"start": round(float(c["start"]), 2), "end": round(float(c["end"]), 2), "text": c.get("text", "")}
        for c in cues
        if float(c["end"]) > lo and float(c["start"]) < hi
    ]
    return {"time": "source", "from_sec": round(lo, 2), "to_sec": round(hi, 2), "cues": out[:400],
            "has_transcript": bool(cues)}


def _clip_time_arg(ctx: ToolContext, args: dict, key: str = "t") -> tuple[float, dict]:
    """Время кадра: шкала clip; с ``final: true`` — секунды готового ролика (переводим в clip)."""
    t = _num(args.get(key), 0.0) or 0.0
    if not args.get("final"):
        return t, {"time": "clip"}
    clip_t = _clock(ctx).final_to_clip(t)
    return clip_t, {"time": "clip", "final_t": round(t, 3)}


def h_view_frame(ctx: ToolContext, args: dict) -> ImageResult:
    t, scale = _clip_time_arg(ctx, args)
    with_layers = args.get("with_layers", True) is not False
    data, mime, info = render_clip_frame(ctx.store, ctx.plan_id, t, with_layers=with_layers)
    return ImageResult(data=data, mime_type=mime, info={"t": round(t, 3), **scale, "with_layers": with_layers, **info})


def h_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    from app.motion_render import contact_sheet_jpeg

    n = int(_num(args.get("n"), 8) or 8)
    times = args.get("times")
    if times is not None and (not isinstance(times, list) or not all(_num(t) is not None for t in times)):
        raise ToolError("times: список секунд готового клипа, до 16")
    times = [float(_num(t)) for t in times] if times else None
    width = 220 if not times or len(times) > 4 else 300
    before = bool(args.get("before_cuts"))
    data = contact_sheet_jpeg(ctx.store, ctx.plan_id, n=n, width=width, times=times, before_cuts=before)
    scale = "clip" if before and times else "final"
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={"n": len(times) if times else n, "times": times, "time": scale,
                             "note": "подписи на листе — шкала " + scale + (" (до вырезов, как слои)" if scale == "clip"
                                                                            else " (готовый ролик)")})



# ---------------------------------------------------------------- монтаж «кнопками»


def h_suggest_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import suggest_for_plan

    mode = args.get("mode") if args.get("mode") in ("pauses", "fillers", "both") else "both"
    # Явная проверка на None: keep_pad=0 («тишину у слов не оставлять») — законное значение.
    min_pause = _num(args.get("min_pause"))
    keep_pad = _num(args.get("keep_pad"))
    out = suggest_for_plan(ctx.store, ctx.plan_id, mode, 0.45 if min_pause is None else float(min_pause),
                           0.12 if keep_pad is None else float(keep_pad))
    # Подсказки считаются по словам «кусками подряд» — агенту отдаём шкалу clip (как apply_cuts
    # и get_clip): переданные как есть в apply_cuts, они встанут ровно туда же.
    clock = _clock(ctx)
    return {**out, "cuts": plain_cuts_to_clip(clock, out.get("cuts") or []),
            "items": plain_cuts_to_clip(clock, out.get("items") or []), "time": "clip",
            "before_sec": clock.clip_duration,
            "after_sec": round(max(0.0, clock.clip_duration - float(out.get("saved_sec") or 0)), 3),
            "note": "шкала clip (до вырезов, как слои и субтитры get_clip); передай cuts в apply_cuts как есть"}


def clip_cuts_to_plain(clock: Any, raw: Any) -> Any:
    """Вырезы агента (шкала clip) → «куски подряд», как их хранит рендер. ``piece`` у выреза —
    чей он в зоне наплыва (get_clip и suggest_cuts его дают)."""
    if not isinstance(raw, list) or not any(clock.pre.eaten):
        return raw  # без наплывов шкалы совпадают
    out = []
    for cut in raw:
        if not isinstance(cut, dict):
            out.append(cut)
            continue
        a, b = _num(cut.get("start")), _num(cut.get("end"))
        if a is None or b is None:
            out.append(cut)
            continue
        piece = cut.get("piece")
        piece = int(piece) if isinstance(piece, (int, float)) and not isinstance(piece, bool) else None
        start, end = clock.clip_range_to_plain(a, b, piece)
        out.append({"start": start, "end": end})
    return out


def h_apply_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import check_cuts_for_plan, cuts_total

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    raw = clip_cuts_to_plain(plan_clock(ctx.store, plan), args.get("cuts") or [])
    cuts, problems = check_cuts_for_plan(plan, raw)
    if problems:
        raise ToolError("вырезы не приняты", problems)
    ctx.before_change()
    settings = dict(_current_render(ctx))
    settings["cuts"] = cuts
    ctx.store.set_clip_plan_render_settings(ctx.plan_id, settings)
    out: dict = {"cuts": len(cuts), "saved_sec": round(cuts_total(cuts), 2), "time": "clip",
                 "finished_duration": _clock(ctx).final_duration}
    from app.cuts import cut_edge_warnings, plan_duration

    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    warnings = cut_edge_warnings(cuts, words, plan_duration(plan))
    if cuts and _source_keyed(ctx):
        # Владелец: «на 4-5 секунде персонаж моргает на склейке» — втубер стоит
        # неподвижно, и вырезанная пауза видна как рывок головы.
        warnings.append("Говорящая голова на зелёнке: вырез посреди фразы виден как рывок персонажа. "
                        "Режь только длинные паузы между фразами и прячь стык (смена плана за персонажем, "
                        "наезд камеры); короткие паузы лучше оставить.")
    if warnings:
        out["warnings"] = warnings
    return out


def h_auto_edit(ctx: ToolContext, args: dict) -> dict:
    from app.auto_edit import apply_auto_edit

    ctx.before_change()
    return apply_auto_edit(ctx.store, ctx.plan_id, style=str(args.get("style") or "hormozi"),
                           keep_layers=args.get("keep_layers", True) is not False)


def h_compose_cover(ctx: ToolContext, args: dict) -> dict:
    from app.cover_compose import compose_cover

    t, scale = _clip_time_arg(ctx, args)
    ctx.before_change()
    out = compose_cover(ctx.store, ctx.plan_id, t, str(args.get("title") or ""),
                        emoji=str(args.get("emoji") or ""), style=str(args.get("style") or "yellow"),
                        y=float(_num(args.get("y"), 0.2) or 0.2))
    return {**out, "t": round(t, 3), **scale}


def h_make_compilation(ctx: ToolContext, args: dict) -> dict:
    from app.compilation import build_compilation

    from app.compilation import _unique_ids

    try:
        ids = _unique_ids(args.get("clip_ids") or [])
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if len(ids) < 2:
        raise ToolError("нужно хотя бы два разных clip_ids")
    try:
        first = ctx.store.get_clip_plan(ids[0])
    except KeyError:
        raise ToolError(f"клип {ids[0]} не найден") from None
    return build_compilation(ctx.store, first["source_id"], ids, title=str(args.get("title") or ""),
                             countdown=args.get("countdown", True) is not False)


def render_clip_frame(store: Any, clip_plan_id: int, t: float, with_layers: bool = True) -> tuple[bytes, str, dict]:
    """Кадр готового вида на клиповом времени ``t``.

    Сначала — моушн-кадр (камера + слои тем же кодом, что и рендер,
    ``app.motion_render.render_frame_png``). Нет компоновщика или он упал — кадр основы:
    окно 9:16 исходника на этом месте (без слоёв), с пометкой в ``info``.
    """
    timeline = clip_timeline(store, clip_plan_id)
    t = min(max(0.0, t), max(0.0, timeline["duration"] - 0.04))
    try:
        from app.motion_render import render_frame_png

        layers = None if with_layers else []
        camera = None if with_layers else []
        data = render_frame_png(store, clip_plan_id, t, layers=layers, camera=camera, inserts=with_layers)
        return data, "image/png", {"kind": "motion"}
    except ImportError:
        problem = "моушн-кадр недоступен"
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - нет компоновщика/упал — агент всё равно видит основу
        problem = f"моушн-кадр не вышел: {str(exc)[:160]}"
    piece = next((p for p in timeline["pieces"] if p["clip_start"] <= t < p["clip_end"]), None)
    piece = piece or (timeline["pieces"][-1] if timeline["pieces"] else None)
    if piece is None:
        raise ToolError("у клипа нет кусков")
    from app.storyboard import plan_thumb

    local = t - piece["clip_start"]
    x = _focus_x_at(piece.get("focus") or [], local)
    source = store.get_source(int(timeline["plan"]["source_id"]))
    path = plan_thumb(source, piece["source_start"] + local, x)
    return path.read_bytes(), "image/jpeg", {"kind": "base", "note": problem + " — показан кадр основы без слоёв"}


def _focus_x_at(track: list, t: float) -> float:
    points = sorted((p for p in track if isinstance(p, dict) and "x" in p), key=lambda p: float(p.get("t", 0)))
    if not points:
        return 0.5
    x = float(points[0]["x"])
    for left, right in zip(points, points[1:]):
        if float(left.get("t", 0)) <= t < float(right.get("t", 0)):
            if right.get("cut"):
                return float(left["x"])
            span = float(right["t"]) - float(left["t"]) or 1.0
            k = (t - float(left["t"])) / span
            return float(left["x"]) + (float(right["x"]) - float(left["x"])) * k
    if t >= float(points[-1].get("t", 0)):
        x = float(points[-1]["x"])
    return x



# Владелец: «эмодзи в монтаже выглядят хуёво». Модель всё равно норовит их ставить —
# режем на входе: слои emoji отбрасываем, из текстов вычищаем символы-эмодзи.
_EMOJI_RE = re.compile(
    "[🀀-🫿☀-➿🇦-🇿⭐⭕‼⁉️‍⃣]+"
)


def _strip_emoji(layer: dict) -> dict:
    props = layer.get("props")
    if not isinstance(props, dict):
        return layer
    props = dict(props)
    for key in ("text", "title", "subtitle", "prefix", "suffix", "icon"):
        value = props.get(key)
        if isinstance(value, str) and _EMOJI_RE.search(value):
            cleaned = _EMOJI_RE.sub("", value)
            # Пробелы префикса/суффикса счётчика — часть оформления («от 50 000 ₽»):
            # раньше strip() съедал их у всех строк, и выходило «от50 000₽».
            props[key] = cleaned if key in ("prefix", "suffix") else cleaned.strip()
    if layer.get("type") == "captions":
        props["emoji"] = []
    return {**layer, "props": props}


def _no_emoji_layers(items: list[dict]) -> tuple[list[dict], int]:
    kept = [_strip_emoji(l) for l in items if l.get("type") != "emoji"]
    return kept, len(items) - len(kept)


def _norm_word(text: str) -> str:
    return re.sub(r"[^\wё]+", "", str(text or "").lower().replace("ё", "е"))


def word_time(ctx: ToolContext, ref: Any) -> float:
    """Начало слова субтитров в секундах клипа (как в get_clip).

    ``ref`` — {"line": i, "word": j} (номера из get_clip.subtitles.lines) или
    {"text": "Артас", "nth": 1} / просто строка: n-е вхождение слова. Агент-монтажёр
    просил «поставь звук на слово», а не считать секунды руками."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    lines = _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"])["lines"]
    if not lines:
        raise ToolError("at_word: у клипа нет субтитров — сначала generate_subtitles")
    if isinstance(ref, str):
        ref = {"text": ref}
    if not isinstance(ref, dict):
        raise ToolError("at_word: {line, word} или {text, nth}")
    if ref.get("text") is not None:
        want, nth, seen = _norm_word(ref["text"]), int(ref.get("nth") or 1), 0
        for line in lines:
            for word in line["words"]:
                if want and want in _norm_word(word[0]):
                    seen += 1
                    if seen == nth:
                        return float(word[1])
        raise ToolError(f"at_word: слова «{ref['text']}» (вхождение {nth}) нет в субтитрах")
    try:
        line = lines[int(ref.get("line"))]
        return float(line["words"][int(ref.get("word", 0))][1])
    except (TypeError, ValueError, IndexError) as exc:
        raise ToolError("at_word: нет такой строки/слова (см. get_clip.subtitles.lines[i].words[j])") from exc


def _place_on_word(ctx: ToolContext, item: dict) -> dict:
    """at_word → start (+word_offset); конец — until_word (начало того слова), иначе duration,
    иначе прежняя end-start, иначе 1.2 с. Агент: «слой на слове держится до следующей реплики»
    — раньше конец приходилось считать руками."""
    if item.get("at_word") is None and item.get("until_word") is None:
        return item
    item = dict(item)
    until = item.pop("until_word", None)
    if item.get("at_word") is None:
        start = float(item.get("start") or 0)
    else:
        start = round(word_time(ctx, item.pop("at_word")) + float(item.pop("word_offset", 0) or 0), 3)
    if until is not None:
        end = word_time(ctx, until)
        if end <= start + 0.1:
            raise ToolError("until_word: это слово раньше начала слоя — выбери более позднее")
        item.pop("duration", None)
        item["start"], item["end"] = max(0.0, start), round(end, 3)
        return item
    if item.get("duration") is not None:
        length = float(item.pop("duration"))
    elif item.get("start") is not None and item.get("end") is not None:
        length = float(item["end"]) - float(item["start"])
    else:
        length = 1.2
    item["start"], item["end"] = max(0.0, start), round(max(0.0, start) + max(0.1, length), 3)
    return item


def _resolve_caption_marks(ctx: ToolContext, layer: dict) -> dict:
    """Подсветка слов субтитров словом (``{text, nth?, color}``) → номер и якорь
    (app/caption_marks.py): после set_subtitle_words она останется на том же слове."""
    props = layer.get("props")
    if layer.get("type") != "captions" or not isinstance(props, dict):
        return layer
    if not any(isinstance(props.get(k), list) and props.get(k) for k in ("highlightWords", "emoji")):
        return layer
    from app.caption_marks import comp_words, resolve_text_marks

    words = comp_words((ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or [])
    fixed, problems = resolve_text_marks(props, words)
    if problems:
        raise ToolError("подсветка слов не принята", problems)
    return {**layer, "props": fixed}


def h_add_layers(ctx: ToolContext, args: dict) -> dict:
    raw = args.get("layers")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ToolError("нужен непустой список layers")
    if len(raw) > MAX_LAYERS_PER_CALL:
        raise ToolError(f"не больше {MAX_LAYERS_PER_CALL} слоёв за вызов")
    duration = _clip_duration(ctx)
    render = _current_render(ctx)
    current = _layers_of(render)
    taken = {str(l.get("id")) for l in current}
    prepared = []
    for item in raw:
        if not isinstance(item, dict):
            raise ToolError("каждый слой — объект", ["layers[]: ожидается объект"])
        item = _resolve_caption_marks(ctx, _place_on_word(ctx, item))
        if not item.get("id") or str(item.get("id")) in taken:
            item["id"] = motion.new_layer_id(taken)
        taken.add(str(item["id"]))
        prepared.append(item)
    prepared, dropped_emoji = _no_emoji_layers(prepared)
    if not prepared:
        raise ToolError("эмодзи в монтаже запрещены владельцем — сделай акцент цветом, наездом, звуком или крупным словом")
    problems = covering_problems(prepared, _source_keyed(ctx)) + _validate_new_layers(ctx, prepared, duration)
    if problems:
        raise ToolError("слои не приняты — исправь и пришли все эти слои снова", problems)
    added = motion.normalize_layers(prepared)
    new_ids = {str(l.get("id")) for l in added}
    fresh = _save_render(ctx, {**render, "layers": current + added})
    layers = _layers_of(fresh)
    return {
        "ok": True,
        "added": [{"id": l.get("id"), "summary": _layer_summary(l)} for l in added],
        "layers_total": len(layers),
        "warnings": lint_layers(layers, duration, only_ids=new_ids, layout=fresh.get("layout"))
        + ([f"эмодзи-слоёв отброшено: {dropped_emoji} — владелец запретил эмодзи"] if dropped_emoji else []),
    }


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if key in ("props", "in", "out", "loop", "shadow") and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        elif value is None and key in out and key not in ("props",):
            out.pop(key)
        else:
            out[key] = value
    return out


def h_update_layer(ctx: ToolContext, args: dict) -> dict:
    layer_id = str(args.get("id") or "")
    patch = args.get("patch")
    if not layer_id or not isinstance(patch, dict) or not patch:
        raise ToolError("нужны id слоя и patch (объект с изменёнными полями)")
    duration = _clip_duration(ctx)
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    patch = {k: v for k, v in patch.items() if k != "id"}
    merged = _resolve_caption_marks(ctx, _strip_emoji(_deep_merge(layers[index], patch)))
    problems = covering_problems([merged], _source_keyed(ctx)) + _validate_new_layers(ctx, [merged], duration)
    if problems:
        raise ToolError("правка не принята — слой остался прежним", problems)
    normalized = motion.normalize_layers([merged])
    old_y = _num(layers[index].get("y"))
    layers[index] = normalized[0]
    new_y = _num(normalized[0].get("y"))
    if layer_id == "auto_caps" and old_y is not None and new_y is not None and abs(new_y - old_y) > 1e-6:
        # Главные субтитры подвинули — слой «других голосов» едет следом, сохраняя зазор.
        for i, other in enumerate(layers):
            if str(other.get("id")) == "auto_caps_voices":
                moved = dict(other)
                moved["y"] = round(min(0.85, max(0.12, (_num(other.get("y"), new_y) or new_y) + new_y - old_y)), 3)
                layers[i] = motion.normalize_layers([moved])[0]
    fresh = _save_render(ctx, {**render, "layers": layers})
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), normalized[0])
    return {"ok": True, "layer": saved,
            "warnings": lint_layers(_layers_of(fresh), duration, only_ids={layer_id}, layout=fresh.get("layout"))}


def h_remove_layers(ctx: ToolContext, args: dict) -> dict:
    ids = {str(i) for i in args.get("ids") or []}
    if not ids:
        raise ToolError("нужен список ids")
    render = _current_render(ctx)
    layers = _layers_of(render)
    keep = [l for l in layers if str(l.get("id")) not in ids]
    missing = ids - {str(l.get("id")) for l in layers}
    if len(keep) != len(layers):
        _save_render(ctx, {**render, "layers": keep})
    return {"ok": True, "removed": len(layers) - len(keep), "missing": sorted(missing), "layers_total": len(keep)}


def h_clear_layers(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    layers = _layers_of(render)
    camera = _camera_of(render)
    with_camera = bool(args.get("camera", False))
    if not layers and not (with_camera and camera):
        return {"ok": True, "removed": 0}
    update = {**render, "layers": []}
    if with_camera:
        update["camera"] = []
    _save_render(ctx, update)
    return {"ok": True, "removed": len(layers), "camera_cleared": with_camera}


def h_set_camera(ctx: ToolContext, args: dict) -> dict:
    keys = args.get("keys")
    if not isinstance(keys, list):
        raise ToolError("нужен список keys (пустой — убрать все наезды)")
    duration = _clip_duration(ctx)
    below = [k for k in keys if isinstance(k, dict) and (_num(k.get("zoom"), 1.0) or 1.0) < 1.0]
    if below:
        # Камера только приближает окно 9:16 — «отъехать» и показать больше исходника она не может.
        raise ToolError("zoom < 1 нельзя: камера только приближает кадр 9:16 (1..4)", [
            "двое по краям широкого плана не влезают в окно — set_piece_fit(piece, w 0.5–1): кусок шире окна, "
            "сверху и снизу фон; или фокус на говорящего со сменой на склейке/реплике (set_focus с cut:true)"])
    problems = motion.validate_camera(keys, duration)
    if problems:
        raise ToolError("камера не принята", problems)
    keys = _anchor_zoom_keys(ctx, keys)
    clean = motion.normalize_camera(keys)
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "camera": clean})
    camera = _camera_of(fresh)
    warnings = []
    for key in camera:
        if (_num(key.get("zoom"), 1.0) or 1.0) > 1.6:
            warnings.append(f"наезд {key.get('zoom')}× на {key.get('t')} с — сильнее 1.6× мылит картинку")
    out = {"ok": True, "camera": camera, "dropped": len(keys) - len(camera), "warnings": warnings}
    mode = (fresh.get("layout") or {}).get("mode")
    if camera and mode in ("pip_cam", "split"):
        # Компоновщик зумит только окно игры (app/stream_layout.camera_regions).
        out["note"] = ("раскладка " + mode + ": камера двигает только игру — окошко pip / полоса вебки стоят "
                       "на месте; x/y камеры — доли окна игры. Проверь view_frame на пике наезда")
    return out


def _anchor_zoom_keys(ctx: ToolContext, keys: list) -> list:
    """Наезд без своих x/y — в героя: край героя со стороны текста стоит на месте,
    и при zoom он не заезжает под субтитры (по умолчанию камера зумит в центр кадра)."""
    if not any(isinstance(k, dict) and "x" not in k and "y" not in k for k in keys):
        return keys
    try:
        from app.ai.subject_box import camera_center, subject_box, text_positions, zoom_anchor
        from app.auto_edit import caption_y_for

        box = subject_box(ctx.store, ctx.plan_id)
        render = _current_render(ctx)
        caption_y = next((float(l.get("y", 0.7)) for l in _layers_of(render) if l.get("type") == "captions"), None)
        if caption_y is None:
            caption_y = text_positions(box, caption_y_for(render))["captions_y"]
        anchor = zoom_anchor(box, caption_y)
    except Exception:  # noqa: BLE001 - без зрения — как просили
        return keys
    if not anchor:
        return keys
    out = []
    for key in keys:
        if isinstance(key, dict) and "x" not in key and "y" not in key:
            zoom = _num(key.get("zoom"), 1.0) or 1.0
            key = {**key, **camera_center(anchor, zoom)}
        out.append(key)
    return out


def h_get_motion_reference(_ctx: ToolContext, _args: dict) -> dict:
    return {"reference": motion_reference(), "spec": "docs/motion/spec.md"}


def _font_manifest() -> list[dict]:
    """Шрифты: из app.motion_assets, а без него — прямо из vendor/fonts/fonts.json."""
    try:
        from app import motion_assets

        if hasattr(motion_assets, "list_fonts"):
            fonts = motion_assets.list_fonts()
            return [f for f in (fonts.get("fonts") if isinstance(fonts, dict) else fonts) or [] if isinstance(f, dict)]
    except ImportError:
        pass
    from app.settings import settings

    path = settings.root_dir / "vendor" / "fonts" / "fonts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def h_list_fonts(_ctx: ToolContext, _args: dict) -> dict:
    """Шрифты, сгруппированные по семействам: модели нужны family + доступные weight."""
    families: dict[str, dict] = {}
    for font in _font_manifest():
        family = str(font.get("family") or "").strip()
        if not family:
            continue
        entry = families.setdefault(family, {"family": family, "weights": [], "cyrillic": True, "tags": [], "hint": ""})
        if font.get("weight") is not None and font["weight"] not in entry["weights"]:
            entry["weights"].append(font["weight"])
        entry["cyrillic"] = entry["cyrillic"] and bool(font.get("cyrillic", True))
        for tag in font.get("tags") or []:
            if tag not in entry["tags"]:
                entry["tags"].append(tag)
        if font.get("hint") and not entry["hint"]:
            entry["hint"] = str(font["hint"])[:120]
    for entry in families.values():
        entry["weights"].sort()
    return {"fonts": list(families.values())}


def h_search_emoji(_ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("q") or "").strip()
    if not query:
        raise ToolError("нужен запрос q (слово по-русски или по-английски)")
    limit = max(1, min(int(args.get("limit") or 12), 40))
    found = _motion_assets().search_emoji(query, limit=limit)
    return {
        "query": query,
        "total": found.get("total", 0),
        "results": [
            {"char": e.get("char"), "name": e.get("name"), "en": e.get("en"), "ref": e.get("ref")}
            for e in found.get("items") or []
        ][:limit],
        "hint": "слой emoji: props.char = символ; или image: props.src = ref",
    }


def h_list_packs(_ctx: ToolContext, args: dict) -> dict:
    kind = str(args.get("kind") or "").strip() or None
    return {
        "packs": [
            {k: p.get(k) for k in ("id", "name", "kind", "count", "license") if p.get(k) is not None}
            for p in _motion_assets().list_packs(kind)
        ],
        "hint": "sfx — звуки (audio asset_id), sticker/meme — картинки (image src), emoji — через search_emoji",
    }


def _layer_ref(ref: str) -> str:
    """Мем-паки слой берёт как ``sticker:`` (так их принимают и проверка, и рендер)."""
    return "sticker:" + ref[len("meme:"):] if ref.startswith("meme:") else ref


def h_search_pack(_ctx: ToolContext, args: dict) -> dict:
    assets = _motion_assets()
    pack = str(args.get("pack") or "").strip()
    query = str(args.get("q") or "").strip()
    kind = str(args.get("kind") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 60))
    if pack:
        packs = [pack]
    else:
        # Звуки без явного пака — только freesound: интерфейсные клики/тоны Kenney владелец
        # назвал дешёвыми (их можно взять, лишь назвав пак).
        packs = [p["id"] for p in assets.list_packs()
                 if p.get("kind") != "emoji" and (not kind or p.get("kind") == kind)
                 and (p.get("kind") != "sfx" or p["id"] == "freesound")]
    results = []
    for pack_id in packs:
        found = assets.pack_items(pack_id, query, limit=limit)
        if found is None:
            raise ToolError(f"пака {pack_id} нет (см. list_packs)")
        for item in found.get("items") or []:
            entry = {k: item.get(k) for k in ("name", "tags", "duration") if item.get(k)}
            entry.update(pack=pack_id, kind=found.get("kind"), ref=_layer_ref(str(item.get("ref") or "")))
            results.append(entry)
        if len(results) >= limit:
            break
    return {
        "query": query,
        "results": results[:limit],
        "hint": "sfx → слой audio props.asset_id = ref; sticker → слой image props.src = ref",
    }


def h_list_assets(ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("q") or "").strip().lower()
    out = []
    for asset in ctx.store.list_montage_assets():
        hay = " ".join(str(asset.get(k) or "") for k in ("label", "description", "tags")).lower()
        if query and not all(word in hay for word in query.split()):
            continue
        out.append(
            {
                "asset_id": asset["id"],
                "kind": asset["kind"],
                "label": asset["label"],
                "description": asset.get("description") or "",
                "tags": asset.get("tags") or "",
                "width": asset.get("width"),
                "height": asset.get("height"),
                "duration_sec": asset.get("duration_sec"),
            }
        )
        if len(out) >= 60:
            break
    return {"assets": out}


def h_search_web_images(_ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_search import search_images

    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolError("нужен запрос query")
    try:
        found = search_images(query, int(args.get("count") or 6), free_only=args.get("free_only", True) is not False)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "query": found.get("query"),
        "results": [
            {k: r.get(k) for k in ("url", "title", "source", "license", "width", "height") if r.get(k) is not None}
            for r in found.get("results") or []
        ],
        "hint": "понравившуюся картинку добавь в библиотеку через add_image_from_url, потом ставь слоем image с asset_id",
    }


def h_add_image_from_url(ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_generate import add_from_url

    url = str(args.get("url") or "").strip()
    try:
        asset = add_from_url(ctx.store, url, label=str(args.get("label") or "")[:120],
                             description=str(args.get("description") or "")[:500])
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать: {str(exc)[:200]}") from exc
    query = str(args.get("query") or "").strip()
    if query:
        from app.ai.asset_generate import image_matches

        if not image_matches(asset, query):
            # Поиск по названию приносит мимо (на «Олег Монгол» — фото БелАЗа).
            ctx.store.delete_montage_asset(asset["id"])
            raise ToolError(f"картинка не про «{query[:60]}» — возьми другого кандидата из search_web_images")
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height")}


MAX_UPLOAD_IMAGE_BYTES = 12 * 1024 * 1024
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def h_upload_image(ctx: ToolContext, args: dict) -> dict:
    """Своя картинка агента (скриншот сайта и т.п.) в библиотеку — base64 в аргументах.

    Агент-монтажёр снимал скрины vtubika.store у себя и собирал multipart-запрос
    к /api/montage-assets руками: по MCP загрузить файл было нечем."""
    import base64
    import binascii
    from uuid import uuid4

    from app.montage_assets import assets_dir, kind_for, probe_asset

    name = Path(str(args.get("filename") or "image.png")).name
    if Path(name).suffix.lower() not in _IMAGE_EXTS:
        raise ToolError("filename — картинка: .png, .jpg, .webp или .gif")
    raw = str(args.get("data_base64") or "")
    if "," in raw[:100] and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]  # data:image/png;base64,...
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("data_base64 — не base64") from exc
    if not data:
        raise ToolError("пустой файл")
    if len(data) > MAX_UPLOAD_IMAGE_BYTES:
        raise ToolError("картинка больше 12 МБ — уменьши или сожми")
    dest = assets_dir() / f"{uuid4().hex}-{name}"
    dest.write_bytes(data)
    meta = probe_asset(dest)
    if not meta.get("width"):
        dest.unlink(missing_ok=True)
        raise ToolError("это не картинка — файл не читается")
    asset = ctx.store.create_montage_asset(
        kind=kind_for(name, "") or "image",
        label=str(args.get("label") or Path(name).stem)[:120],
        description=str(args.get("description") or "")[:500],
        tags="",
        file_path=str(dest),
        original_filename=name,
        mime_type="",
        size_bytes=len(data),
        **meta,
    )
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height")}


def h_generate_image(ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_generate import generate_asset

    prompt = str(args.get("prompt") or "").strip()
    if len(prompt) < 3:
        raise ToolError("нужно описание картинки prompt")
    try:
        asset = generate_asset(ctx.store, prompt, label=str(args.get("label") or "")[:120])
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(f"не нарисовалось: {str(exc)[:200]}") from exc
    return {"ok": True, "asset_id": asset["id"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height")}


def h_list_subtitle_styles(ctx: ToolContext, _args: dict) -> dict:
    styles = []
    for profile in ctx.store.list_subtitle_profiles():
        styles.append({k: profile.get(k) for k in ("id", "name", "font_name", "font_size", "primary_color",
                                                     "highlight_color", "uppercase") if k in profile})
    return {"styles": styles}


def h_set_subtitles(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    update = dict(render)
    if "on" in args and args["on"] is not None:
        update["subs_on"] = bool(args["on"])
    if args.get("style_id") is not None:
        style_id = int(args["style_id"])
        try:
            ctx.store.get_subtitle_profile(style_id)
        except KeyError as exc:
            raise ToolError(f"стиля субтитров {style_id} нет (см. list_subtitle_styles)") from exc
        update["sub_id"] = style_id
    if args.get("position_pct") is not None:
        update["sub_pos_pct"] = float(args["position_pct"])
    fresh = _save_render(ctx, update)
    note = ""
    if fresh["subs_on"] and not (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("lines"):
        note = "текста субтитров ещё нет — рендер распознает речь сам (или владелец нажмёт «Сделать субтитры»)"
    return {"ok": True, "subs_on": fresh["subs_on"], "sub_id": fresh["sub_id"], "sub_pos_pct": fresh["sub_pos_pct"],
            "note": note}


CAM_CROP_WARN = 0.15  # срезается больше этой доли вебки — предупреждаем
CAM_BIG_W = 0.30
CAM_BIG_H = 0.45


def facecam_warnings(layout: dict, source: dict) -> list[str]:
    """Вебка в раскладке: не режется ли под пропорции полосы и не захвачен ли лишний оверлей.

    Агент стримов: сохранённая facecam (0.63/0.4/0.37/0.6) захватила вшитый чат, а полоса
    split шире вебки — компоновщик заполняет полосу и срезает верх и низ: аватар до лба."""
    from app.stream_layout import normalize_layout

    layout = normalize_layout(layout)
    cam = layout["cam"]
    out = []
    if cam["w"] > CAM_BIG_W or cam["h"] > CAM_BIG_H:
        out.append(f"вебка большая ({cam['w']:.2f}×{cam['h']:.2f} кадра исходника) — вебка стримера обычно меньше: "
                   "проверь source_contact_sheet / view_frame, не захвачен ли вшитый чат или оверлей; поправь cam")
    if layout["mode"] != "split":
        return out
    src_w, src_h = float(source.get("width") or 16), float(source.get("height") or 9)
    cam_aspect = (cam["w"] * src_w) / max(1e-6, cam["h"] * src_h)
    strip_aspect = 1080 / max(1.0, layout["ratio"] * 1920)
    fit_ratio = round(min(0.6, max(0.2, 1080 / cam_aspect / 1920)), 3)
    if cam_aspect < strip_aspect:
        loss = 1 - cam_aspect / strip_aspect
        if loss > CAM_CROP_WARN:
            out.append(f"полоса вебки шире самой вебки: срежется {loss:.0%} её высоты (сверху и снизу — лицо «до лба»). "
                       f"Под пропорции вебки — ratio {fit_ratio}, или шире cam (w) в исходнике")
    else:
        loss = 1 - strip_aspect / cam_aspect
        if loss > CAM_CROP_WARN:
            out.append(f"полоса вебки уже самой вебки: срежется {loss:.0%} её ширины по бокам. Под пропорции вебки — "
                       f"ratio {fit_ratio}, или выше cam (h) в исходнике")
    return out


def _focus_all(ctx: ToolContext, x: float, piece: int | None = None) -> list[str]:
    """Неподвижный фокус окна игры x на всех кусках (или на одном) — как manual_x в файле клипа."""
    from app.clip_spec import SpecError, apply_spec, export_spec, validate_spec

    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = []
    for i, item in enumerate(spec["pieces"]):
        item = dict(item)
        item.pop("fit", None)
        if piece is None or i == piece:
            item["focus"] = [{"t": 0.0, "x": round(float(x), 4)}]
        else:
            item.pop("focus", None)
        pieces.append(item)
    try:
        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("фокус окна игры не принят", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    return changes


def h_set_layout(ctx: ToolContext, args: dict) -> dict:
    """Раскладка кадра: single, split (вебка+игра), pip_cam, meme (видео сверху, фон снизу)."""
    from app.stream_layout import LAYOUT_MODES, normalize_layout

    if args.get("mode") is not None and args["mode"] not in LAYOUT_MODES:
        raise ToolError(f"mode: одно из {', '.join(LAYOUT_MODES)}")
    game_x = _num(args.get("game_x"), _num(args.get("manual_x")))
    if game_x is not None and not 0.0 <= game_x <= 1.0:
        raise ToolError("game_x — центр окна игры в долях ширины исходника, 0..1")
    piece = args.get("piece")
    if piece is not None:
        piece = _piece_index({"piece": piece}, len(clip_timeline(ctx.store, ctx.plan_id)["pieces"]))
    layout_args = {k: v for k, v in args.items() if v is not None and k not in ("game_x", "manual_x", "piece")}
    render = _current_render(ctx)
    source = ctx.store.get_source(int(ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)["source_id"]))
    took_facecam = False
    if (layout_args.get("mode") in ("split", "pip_cam") and "cam" not in layout_args
            and (render.get("layout") or {}).get("mode") not in ("split", "pip_cam") and source.get("facecam")):
        # Вебка, найденная/заданная у источника, — а не угол по умолчанию.
        layout_args["cam"] = source["facecam"]
        took_facecam = True
    merged = {**(render.get("layout") or {}), **layout_args}
    fresh = _save_render(ctx, {**render, "layout": normalize_layout(merged)})
    out: dict = {"ok": True, "layout": fresh["layout"]}
    if took_facecam:
        out["cam_from"] = "facecam источника (detect-facecam) — проверь view_frame"
    if game_x is not None:
        out["focus_changes"] = _focus_all(ctx, game_x, piece)
        out["game_x"] = round(game_x, 4)
    mode = fresh["layout"]["mode"]
    if mode == "meme":
        out["note"] = ("видео сверху во всю ширину; под ним свободный фон — клади туда картинки (image) и титры, "
                       "проверь view_frame")
    elif mode in ("split", "pip_cam"):
        out["note"] = ("камера (set_camera) двигает только игру — полоса/окошко вебки стоят на месте; что из игры в "
                       "кадре — focus кусков (game_x здесь — один x на все куски, set_focus — трек по куску). "
                       "Проверь view_frame: лицо в полосе целиком, хук и субтитры не на лице (positions в get_clip)")
        warnings = facecam_warnings(fresh["layout"], source)
        if warnings:
            out["warnings"] = warnings
    return out


def h_list_music(ctx: ToolContext, _args: dict) -> dict:
    """Фоновая музыка сервиса (пак CC0 + загруженная владельцем)."""
    tracks = [
        {"id": t["id"], "label": t.get("label") or "", "duration": round(float(t.get("duration_sec") or 0), 1)}
        for t in ctx.store.list_audio_tracks()
    ]
    return {"tracks": tracks, "note": "громкость фона выставляется сама — заметно тише голоса клипа и приглушается под речь"}


def h_mute_audio(ctx: ToolContext, args: dict) -> dict:
    """Тишина вместо звука исходника на отрезках (секунды клипа до вырезов), картинка остаётся."""
    from app.audio_mute import normalize_mute

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end} (секунды клипа до вырезов; [] — снять всё)")
    duration = _clip_duration(ctx)
    clean = [m for m in normalize_mute(ranges) if m["start"] < duration]
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "mute": clean})
    return {"ok": True, "mute": fresh["mute"],
            "note": "звук исходника там заглушён; звуки слоёв и музыка играют. Проверь listen."}


def h_add_music_from_url(ctx: ToolContext, args: dict) -> dict:
    """Музыка по ссылке (YouTube и т.п.) → новый трек фона."""
    from app.music_import import import_music_from_url

    try:
        track = import_music_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "track_id": track["id"], "label": track["label"],
            "duration": round(float(track.get("duration_sec") or 0), 1)}


def h_add_sound_from_url(ctx: ToolContext, args: dict) -> dict:
    """Звуковой эффект по ссылке владельца (страница freesound или видео) → asset_id для слоя audio."""
    from app.sound_import import MAX_SOUND_SEC, import_sound_from_url

    try:
        asset = import_sound_from_url(ctx.store, str(args.get("url") or ""), str(args.get("label") or ""),
                                      start=_num(args.get("start"), 0.0) or 0.0)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать звук: {str(exc)[:200]}") from exc
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "duration": round(float(asset.get("duration_sec") or 0), 2),
            "note": (f"обрезан до {MAX_SOUND_SEC:g} с; ставь слоем audio (props.asset_id) на слово (at_word), "
                     "громкость 0.3–0.6 — сервис сам держит эффект под голосом")}


def h_get_performance(ctx: ToolContext, args: dict) -> dict:
    """Что заходит: топ и антитоп опубликованных клипов по просмотрам с признаками монтажа."""
    from app.platform_stats import performance_report

    limit = int(_num(args.get("limit"), 5) or 5)
    return performance_report(ctx.store, limit)


def h_set_music(ctx: ToolContext, args: dict) -> dict:
    """Фоновая музыка клипа: on + track_id (list_music)."""
    render = _current_render(ctx)
    on = args.get("on") is not False
    track_id = args.get("track_id")
    if on:
        track_id = track_id or render.get("track_id")
        if not track_id:
            raise ToolError("нужен track_id — см. list_music")
        try:
            ctx.store.get_audio_track(int(track_id))
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolError(f"нет трека {track_id} — см. list_music") from exc
    fresh = _save_render(ctx, {**render, "music_on": on, "track_id": int(track_id) if on else render.get("track_id")})
    return {"ok": True, "music_on": fresh["music_on"], "track_id": fresh["track_id"]}


def h_set_backdrop(ctx: ToolContext, args: dict) -> dict:
    """Фон за вырезанным персонажем (app/backgrounds.py): анимированный kind в цветах
    colors; статичная заливка — только kind solid/gradient (или старые color/color2)."""
    from app.backgrounds import normalize_background
    from app.chroma_key import normalize_backdrop

    render = _current_render(ctx)
    current = normalize_background(render.get("backdrop"))
    fresh_args = {k: v for k, v in args.items() if v is not None}
    if any(k in fresh_args for k in ("kind", "colors", "speed", "intensity")):
        # Передано только то, что меняется: остальное — от текущего фона.
        merged = {**current, **{k: fresh_args[k] for k in ("kind", "colors", "speed", "intensity") if k in fresh_args}}
    elif fresh_args.get("color"):
        # Старый вызов color/color2 — явная заливка (градиент), её просили.
        merged = {"color": fresh_args["color"], "color2": fresh_args.get("color2") or ""}
        if str(merged["color"]).lower() == "#0b0d14" and not merged["color2"]:
            merged = {"kind": "solid", "colors": [merged["color"]]}
    else:
        merged = current
    fresh = _save_render(ctx, {**render, "backdrop": normalize_backdrop(merged)})
    out: dict = {"ok": True, "backdrop": fresh["backdrop"]}
    if not _source_keyed(ctx):
        out["note"] = "у источника нет хромакея — фон не виден (set_chroma_key on)"
    return out


def h_list_brands(ctx: ToolContext, _args: dict) -> dict:
    """Бренды владельца целиком (шрифт, цвета, логотип, призыв, музыка) — их задаёт он сам."""
    return {"brands": [{k: v for k, v in b.items() if k not in ("created_at", "updated_at")}
                       for b in ctx.store.list_brands()],
            "note": "бренд задаёт владелец (Настройки → Бренды); ты только применяешь: apply_brand"}


def h_apply_brand(ctx: ToolContext, args: dict) -> dict:
    """Оформить клип в стиле бренда (app/brand_kit.py): повтор не плодит дублей."""
    from app.brand_kit import apply_brand

    brand_id = args.get("brand_id")
    if not brand_id:
        raise ToolError("нужен brand_id — см. list_brands")
    try:
        ctx.store.get_brand(int(brand_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    try:
        return apply_brand(ctx.store, ctx.plan_id, int(brand_id), before_change=ctx.before_change)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def h_set_chroma_key(ctx: ToolContext, args: dict) -> dict:
    """Хромакей источника клипа: on — найти зелёнку (или взять color), off — убрать."""
    from app.chroma_key import detect_key, normalize_key

    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"]))
    if args.get("on") is False:
        ctx.store.update_source(source["id"], chroma_key=None)
        return {"ok": True, "chroma_key": None}
    current = source.get("chroma_key") or {}
    tweaks = {k: v for k, v in args.items() if k in ("color", "similarity", "blend") and v is not None}
    if args.get("color") or current:
        key = normalize_key({**current, **tweaks})
    else:
        try:
            key = detect_key(source)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        key = normalize_key({**key, **tweaks}) if key else None
    if not key:
        raise ToolError("зелёного/синего фона в источнике не нашлось — передай color (#rrggbb) сам, если он есть")
    saved = ctx.store.update_source(source["id"], chroma_key=key)
    return {"ok": True, "chroma_key": saved.get("chroma_key"),
            "note": "проверь view_frame: края персонажа без зелени; ореол — similarity выше (0.3), рваные края — ниже"}


def h_set_subtitle_words(ctx: ToolContext, args: dict) -> dict:
    """Заменить слова субтитров целиком: текст и тайминг каждого слова (секунды клипа до вырезов).

    Агент-монтажёр правил распознанные слова («2D модели» → «дорого») и сдвигал опоздавшее
    «Художник» через REST — инструмента не было. Строки пересобираются из слов."""
    from app.ai.speakers import lines_from_words
    from app.clip_subtitles import save_lines, tidy_words

    raw = args.get("words")
    if not isinstance(raw, list):
        raise ToolError("нужен список words [{word, start, end}] ([] — убрать субтитры клипа)")
    if not raw:
        # Пустой список — очистить: клип без речи (игра), распознались фантомные слова.
        ctx.before_change()
        save_lines(ctx.store, ctx.plan_id, [], [])
        return {"ok": True, "lines": 0, "words": 0, "note": "субтитры клипа убраны"}
    if len(raw) > 2000:
        raise ToolError("слишком много слов (больше 2000) — клип для шортса короче")
    words, problems = [], []
    for i, w in enumerate(raw):
        try:
            word = str(w["word"]).strip()
            start, end = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"words[{i}]: нужны word, start, end")
            continue
        if not word or end <= start:
            problems.append(f"words[{i}]: пустое слово или end ≤ start")
            continue
        item = {"word": word[:80], "start": round(start, 3), "end": round(end, 3)}
        speaker = str(w.get("s") or w.get("speaker") or "").strip().upper()[:1]
        if speaker:
            if speaker not in "ABCD":
                problems.append(f"words[{i}]: s — голос A, B, C или D")
                continue
            item["speaker"] = speaker
        words.append(item)
    if problems:
        raise ToolError("слова не приняты", problems)
    old_payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    # Время агента — шкала clip (как в get_clip: с наплывами переходов); хранятся слова
    # «кусками подряд». Раньше слово из get_clip, отданное обратно как есть, уезжало
    # на длину наплыва.
    words = words_clip_to_plain(_clock(ctx), words, old_payload.get("words") or [])
    # Дефисные хвосты Whisper («какой» «-то») склеиваются, дубли на стыке строк убираются.
    words = tidy_words(words)
    ctx.before_change()
    lines = lines_from_words(words)
    payload = save_lines(ctx.store, ctx.plan_id, lines, words)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, old_payload.get("words") or [], payload.get("words") or [])
    out = {"ok": True, "lines": len(payload.get("lines") or []), "words": len(payload.get("words") or []),
           "speakers": sorted({w["speaker"] for w in payload.get("words") or [] if w.get("speaker")}),
           "time": "clip",
           "note": "строки собраны из слов: новая строка на конце предложения (. ! ?), паузе > 0.6 с, смене голоса s "
                   "и после 7 слов"}
    if moved:
        out["highlights"] = moved
    return out


def words_clip_to_plain(clock: Any, words: list[dict], old_words: list[dict]) -> list[dict]:
    """Слова агента (шкала clip) → «куски подряд», как хранятся субтитры.

    В зоне наплыва момент clip принадлежит двум кускам сразу. Слово, которое уже было в
    субтитрах (тот же текст и то же время в get_clip), остаётся в своём куске; новое —
    в том, где оно не нарушает порядок слов (хвост куска A раньше головы куска B)."""
    if not any(clock.pre.eaten):
        return words  # без наплывов шкалы совпадают
    known: list[tuple[str, float, int]] = []
    for w in old_words:
        try:
            plain = float(w["start"])
        except (KeyError, TypeError, ValueError):
            continue
        known.append((_norm_word(w.get("word")), clock.plain_to_clip(plain), clock.plain_piece(plain)))
    out, last = [], -1e9
    for w in words:
        start, end = float(w["start"]), float(w["end"])
        text = _norm_word(w.get("word"))
        piece = next((k for t, c, k in known if t == text and abs(c - start) <= 0.015), None)
        if piece is None:
            options = [(clock.clip_to_plain(start, k), k) for k in clock.pieces_at_clip(start)]
            fit = [o for o in options if o[0] >= last - 0.05]
            piece = (min(fit) if fit else max(options))[1] if options else None
        a, b = clock.clip_range_to_plain(start, end, piece)
        last = a
        out.append({**w, "start": round(a, 3), "end": round(max(b, a + 0.02), 3)})
    return out


def h_edit_subtitle_lines(ctx: ToolContext, args: dict) -> dict:
    from app.clip_subtitles import save_lines

    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError("нужен список edits [{i, text}]")
    payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    lines = [dict(line) for line in payload.get("lines") or []]
    if not lines:
        raise ToolError("у клипа нет субтитров")
    problems = []
    for edit in edits:
        try:
            index = int(edit.get("i"))
        except (TypeError, ValueError, AttributeError):
            problems.append(f"{edit!r}: нужен номер строки i")
            continue
        if not 0 <= index < len(lines):
            problems.append(f"строки {index} нет (0..{len(lines) - 1})")
            continue
        text = " ".join(str(edit.get("text") or "").split())
        if not text:
            problems.append(f"строка {index}: пустой текст (чтобы убрать строку, оставь её как есть)")
            continue
        lines[index]["text"] = text
    if problems:
        raise ToolError("правка субтитров не принята", problems)
    ctx.before_change()
    saved = save_lines(ctx.store, ctx.plan_id, lines)
    from app.caption_marks import rebind_plan_marks

    moved = rebind_plan_marks(ctx.store, ctx.plan_id, payload.get("words") or [], saved.get("words") or [])
    return {"ok": True, "lines": len(saved.get("lines") or []), **({"highlights": moved} if moved else {})}


def h_set_transition(ctx: ToolContext, args: dict) -> dict:
    from app.transitions import AUDIO_MODES, SFX_KINDS, TRANSITIONS

    render = _current_render(ctx)
    transition = dict(render.get("transition") or {})
    problems = []
    if args.get("type") is not None:
        if args["type"] not in TRANSITIONS:
            problems.append(f"type: одно из {', '.join(TRANSITIONS)}")
        transition["type"] = args["type"]
    if args.get("sfx") is not None:
        if args["sfx"] not in SFX_KINDS:
            problems.append(f"sfx: одно из {', '.join(SFX_KINDS)}")
        transition["sfx"] = args["sfx"]
    if args.get("audio") is not None:
        if args["audio"] not in AUDIO_MODES:
            problems.append(f"audio: одно из {', '.join(AUDIO_MODES)}")
        transition["audio"] = args["audio"]
    for key in ("duration", "sfx_volume"):
        if args.get(key) is not None:
            transition[key] = args[key]
    if args.get("smart") is not None:
        transition["smart"] = bool(args["smart"])
    if problems:
        raise ToolError("переход не принят", problems)
    from app.transitions import MIN_DURATION, SNAP_DURATIONS, normalize_transition, speech_join_warnings, \
        speech_under_joins

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    segments = plan.get("segments") or []
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    notes = []
    if args.get("fit_speech"):
        # Наплыв не должен уводить слова под переход: длину — по самому тесному стыку.
        report = speech_under_joins(segments, transition, words)
        kind = normalize_transition(transition)["type"]
        if report["hit"] and kind not in SNAP_DURATIONS:
            if report["safe_duration"] >= MIN_DURATION:
                transition["duration"] = report["safe_duration"]
                notes.append(f"длина перехода {report['safe_duration']:.2f} с — наплыв не заходит на речь")
            else:
                notes.append("на каком-то стыке речь вплотную к склейке — переход любой длины заденет слова; "
                             "подрежь куски (см. warnings) или поставь type cut")
    fresh = _save_render(ctx, {**render, "transition": transition})
    out: dict = {"ok": True, "transition": fresh["transition"]}
    warnings = speech_join_warnings(speech_under_joins(segments, fresh["transition"], words), fresh["transition"])
    if warnings:
        out["warnings"] = warnings
    if notes:
        out["note"] = "; ".join(notes)
    return out


def ending_warning(store: Any, source_id: int, end_sec: float) -> str | None:
    """Совет, если клип кончается на полуслове или вплотную к последнему слову.

    Владелец: «только концовки обрываются». Конец последнего куска — время
    исходника; сверяем с расшифровкой: внутри фразы → договорить; ровно на
    её конце (меньше 0.3 с) → оставить хвост, Whisper ставит конец рано."""
    try:
        cues = store.get_source_transcript(source_id)
    except KeyError:
        return None
    for cue in cues:
        s, e = float(cue.get("start") or 0), float(cue.get("end") or 0)
        if s + 0.1 < end_sec < e - 0.05:
            text = " ".join(str(cue.get("text") or "").split())[-60:]
            return (f"клип обрывается на полуслове: фраза «…{text}» идёт до {e:.2f} с исходника — "
                    f"продли последний кусок до ≈{e + 0.5:.2f}")
        if e - 0.05 <= end_sec < e + 0.3:
            return f"конец вплотную к последнему слову ({e:.2f}) — оставь хвост 0.4–0.8 с (до ≈{e + 0.6:.2f})"
    return None


def h_trim_piece(ctx: ToolContext, args: dict) -> dict:
    from app.clip_spec import SpecError, apply_spec, export_spec

    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index куска (с 0)") from exc
    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = spec["pieces"]
    if not 0 <= index < len(pieces):
        raise ToolError(f"куска {index} нет (0..{len(pieces) - 1})")
    piece = dict(pieces[index])
    # source_start/source_end — прежние имена из get_clip (алиасы).
    start = _num(args.get("start_sec", args.get("source_start")), piece["start_sec"])
    end = _num(args.get("end_sec", args.get("source_end")), piece["end_sec"])
    piece.update(start_sec=start, end_sec=end)
    piece.pop("focus", None)  # трек кадра остаётся прежним
    pieces = [*pieces[:index], piece, *pieces[index + 1:]]
    try:
        from app.clip_spec import validate_spec

        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("подрезка не принята", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    out = {"ok": True, "changes": changes, "duration": timeline["duration"],
           "note": "слои, камера, вставки и субтитры сдвинуты вслед за куском; если кусок расширили на новую речь — generate_subtitles"}
    if index == len(pieces) - 1:
        warn = ending_warning(ctx.store, int(spec["source"]["id"]), float(end))
        if warn:
            out["warnings"] = [warn]
    return out


def _piece_index(args: dict, count: int) -> int:
    try:
        index = int(args.get("piece"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен piece — номер куска с 0 (get_clip.pieces[].index)") from exc
    if not 0 <= index < count:
        raise ToolError(f"куска {index} нет (0..{count - 1})")
    return index


def _apply_piece(ctx: ToolContext, index: int, **fields: Any) -> tuple[dict, list[str]]:
    """Поменять поля одного куска через файл клипа (та же проверка и запись, что PUT /spec)."""
    from app.clip_spec import SpecError, apply_spec, export_spec, validate_spec

    spec = export_spec(ctx.store, ctx.plan_id)
    pieces = []
    for i, piece in enumerate(spec["pieces"]):
        piece = dict(piece)
        # Чужие поля не переписываем: только таймкоды и то, что меняем у этого куска.
        piece.pop("focus", None)
        piece.pop("fit", None)
        if i == index:
            piece.update(fields)
        pieces.append(piece)
    try:
        validate_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    except SpecError as exc:
        raise ToolError("кусок не принят", exc.problems) from exc
    ctx.before_change()
    _fresh, changes = apply_spec(ctx.store, ctx.plan_id, {**spec, "pieces": pieces})
    return clip_timeline(ctx.store, ctx.plan_id)["pieces"][index], changes


def _shared_warning(ctx: ToolContext, piece: dict) -> list[str]:
    count = ctx.store.segment_plan_count(int(piece["segment_id"]))
    if count > 1:
        return [f"кусок общий ещё с {count - 1} клип(ами) — кадрирование поменялось и там"]
    return []


def _piece_words(words: list[dict], plain_start: float, length: float) -> list[dict]:
    """Слова субтитров куска во времени куска (``piece``). Субтитры лежат «кусками подряд»
    (plain, без наплывов) — от начала куска на этой шкале и считаем."""
    out = []
    for w in words or []:
        try:
            start = float(w.get("start", 0)) - plain_start
            end = float(w.get("end", w.get("start", 0))) - plain_start
        except (TypeError, ValueError):
            continue
        if -0.2 <= start <= length + 0.2 and str(w.get("word") or "").strip():
            item = {"word": str(w["word"]), "start": round(start, 3), "end": round(max(start, end), 3)}
            if w.get("speaker"):
                item["speaker"] = str(w["speaker"])
            out.append(item)
    return out


def h_auto_focus(ctx: ToolContext, args: dict) -> dict:
    """Кадрирование 9:16 сам: лица/движение по планам (app/autofocus), для мультфильмов —
    ещё и Gemini по кадру на план. Проверочный прогон: встроенный монтажёр 8 шагов
    подбирал x «на глаз» по листам кадров и всё равно промахнулся на стене."""
    from app.autofocus import compute_segment_focus
    from app.settings import settings

    timeline = clip_timeline(ctx.store, ctx.plan_id)
    pieces = timeline["pieces"]
    indexes = [_piece_index(args, len(pieces))] if args.get("piece") is not None else list(range(len(pieces)))
    resolver = None
    # По умолчанию с Gemini по кадру на план: на мультфильмах лица не находятся, а монтажёр
    # use_vlm сам не ставил (проверочный прогон: кадр на руке Рика, пока говорит другой).
    if args.get("use_vlm", True) is not False and settings.gemini_api_key:
        from app.ai.vlm_focus import shots_focus_x

        resolver = shots_focus_x
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    source = ctx.store.get_source(int(plan["source_id"]))
    words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
    done = []
    for index in indexes:
        piece = pieces[index]
        length = float(piece["source_end"]) - float(piece["source_start"])
        plain_start = sum(float(p["source_end"]) - float(p["source_start"]) for p in pieces[:index])
        # Слова куска: Gemini видит реплику плана, а длинный план с диалогом — по кадру на реплику.
        piece_words = _piece_words(words, plain_start, length) if resolver and words else []
        kwargs = {"speech_words": piece_words} if piece_words else {}
        points = compute_segment_focus(source, float(piece["source_start"]), float(piece["source_end"]),
                                       vlm_resolver=resolver, **kwargs)
        local = []
        for point in points:  # время точек — уже от начала куска
            item = {"t": round(float(point["t"]), 3), "x": round(float(point["x"]), 4)}
            if point.get("cut"):
                item["cut"] = True
            local.append(item)
        _apply_piece(ctx, index, focus=local)
        turns = sum(1 for a, b in zip(local, local[1:]) if not b.get("cut") and abs(b["x"] - a["x"]) >= 0.06)
        item = {"piece": index, "points": len(local), "cuts": sum(1 for q in local if q.get("cut"))}
        if turns and resolver:
            item["turns"] = turns  # повороты к ответившему внутри плана (по репликам)
        done.append(item)
    return {"ok": True, "pieces": done,
            "note": "проверь contact_sheet: говорящий в кадре? Промах на плане — поправь set_focus этого куска"}


def h_set_focus(ctx: ToolContext, args: dict) -> dict:
    """Кадрирование 9:16 куска: центр окна x(t), cut — жёсткая смена на склейке плана."""
    timeline = clip_timeline(ctx.store, ctx.plan_id)
    index = _piece_index(args, len(timeline["pieces"]))
    piece = timeline["pieces"][index]
    length = float(piece["source_end"]) - float(piece["source_start"])
    raw = args.get("points")
    if not isinstance(raw, list):
        raise ToolError("points — список {t, x, cut?} (t — секунды от начала куска, x 0..1); [] — по центру")
    points, problems = [], []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            problems.append(f"points[{i}]: объект {{t, x, cut?}}")
            continue
        t = _num(p.get("t"))
        if t is None and _num(p.get("source_t")) is not None:
            t = float(_num(p.get("source_t"))) - float(piece["source_start"])  # прямо из get_shots
        x = _num(p.get("x"))
        if t is None or x is None:
            problems.append(f"points[{i}]: нужны числа t (или source_t) и x")
            continue
        if not -0.05 <= t <= length + 0.05:
            problems.append(f"points[{i}]: t={t:.2f} вне куска (0..{length:.2f} с от его начала)")
            continue
        if not 0.0 <= x <= 1.0:
            problems.append(f"points[{i}]: x={x} — доля ширины исходника 0..1")
            continue
        point = {"t": round(min(max(t, 0.0), length), 3), "x": round(x, 4)}
        if p.get("cut"):
            point["cut"] = True
        points.append(point)
    if problems:
        raise ToolError("кадрирование не принято", problems)
    points.sort(key=lambda p: p["t"])
    if points and points[0]["t"] > 0.05:
        # До первой точки окно стоит на её x — явная точка в 0 убирает догадки.
        points.insert(0, {"t": 0.0, "x": points[0]["x"]})
    fresh, changes = _apply_piece(ctx, index, focus=points)
    warnings = _shared_warning(ctx, fresh)
    render = _current_render(ctx)
    mode = (render.get("layout") or {}).get("mode") or "single"
    if mode == "meme" and not fresh.get("fit"):
        warnings.append("раскладка meme показывает видео целиком — фокус в ней не виден")
    from app.render import effective_preset

    preset = effective_preset(ctx.store, render.get("preset_id")) or {}
    if preset and not preset.get("smart_reframe"):
        warnings.append("у пресета выключен smart_reframe — фокус не действует (кадр по центру)")
    out = {"ok": True, "piece": index, "focus": fresh.get("focus") or [], "changes": changes,
           "note": "проверь source_contact_sheet(x=…) или view_frame на точках и сразу после cut"}
    if warnings:
        out["warnings"] = warnings
    return out


MAX_FIT_PIECE_SEC = 12.0


def h_set_piece_fit(ctx: ToolContext, args: dict) -> dict:
    """Кусок шире окна 9:16: в кадре доля w ширины исходника, сверху и снизу фон."""
    from app.video_crop import FIT_MIN_W, normalize_fit, window_share

    timeline = clip_timeline(ctx.store, ctx.plan_id)
    index = _piece_index(args, len(timeline["pieces"]))
    w = args.get("w")
    if w is None or w is False or _num(w) == 0:
        fit = None
    else:
        value = _num(w)
        if value is None or not FIT_MIN_W <= value <= 1.0:
            raise ToolError(f"w — доля ширины исходника в кадре, {FIT_MIN_W}..1 (1 — весь кадр); null — обычный кадр")
        fit = normalize_fit({"w": value, "bg": args.get("bg") or "blur"})
        piece = timeline["pieces"][index]
        length = float(piece["source_end"]) - float(piece["source_start"])
        if length > MAX_FIT_PIECE_SEC:
            # Проверочный прогон Gemini: fit на весь 40-секундный клип — ролик превратился в
            # мелкую полоску на размытом фоне, как у дешёвых перезаливов.
            raise ToolError(
                f"кусок {length:.0f} с — «шире окна» на весь кусок делает ролик мелким видео на размытом фоне. "
                f"Сначала auto_focus (говорящий в кадре по планам). fit — только короткий кусок ≤ {MAX_FIT_PIECE_SEC:.0f} с, "
                "где двое по краям: разрежь кусок по склейке плана (trim_piece / create_clip)")
    fresh, changes = _apply_piece(ctx, index, fit=fit)
    out: dict = {"ok": True, "piece": index, "fit": fresh.get("fit"), "changes": changes}
    warnings = _shared_warning(ctx, fresh)
    source = ctx.store.get_source(int(timeline["plan"]["source_id"]))
    share = window_share(source)
    if fit and share and fit["w"] <= share + 0.02:
        warnings.append(f"w={fit['w']} не шире обычного окна ({share}) — смысла нет, бери больше")
    mode = (_current_render(ctx).get("layout") or {}).get("mode") or "single"
    if fit and mode in ("split", "pip_cam", "meme"):
        warnings.append(f"на этом куске раскладка {mode} не действует (вебки/окошка нет) — только он шире окна")
    if fit:
        bg = fit["bg"]
        bg_name = bg.get("kind") if isinstance(bg, dict) else bg
        out["note"] = ("кусок рендерится шире окна: центр — тот же focus куска; фон сверху и снизу — "
                       f"{bg_name}. Проверь view_frame на куске: субтитры и титры лягут на фон — это хорошо")
    if warnings:
        out["warnings"] = warnings
    return out


def h_add_insert(ctx: ToolContext, args: dict) -> dict:
    from app.montage_assets import MAX_INSERTS, MODES, normalize_inserts

    try:
        asset = ctx.store.get_montage_asset(int(args.get("asset_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого файла в библиотеке (см. list_assets)") from exc
    mode = args.get("mode") or ("sound" if asset["kind"] == "audio" else "pip")
    if mode not in MODES:
        raise ToolError(f"mode: одно из {', '.join(MODES)}")
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    if len(inserts) >= MAX_INSERTS:
        raise ToolError(f"вставок уже {MAX_INSERTS} — больше нельзя; для картинок используй слой image")
    if args.get("at_word") is not None:
        args = {**args, "at": round(word_time(ctx, args["at_word"]) + float(args.get("word_offset") or 0), 3)}
    if args.get("at") is None:
        raise ToolError("нужно at (секунды клипа) или at_word")
    entry = {k: args[k] for k in ("at", "duration", "x", "y", "scale", "volume", "duck", "reason") if args.get(k) is not None}
    entry.update(asset_id=asset["id"], mode=mode)
    note = _fit_pip_insert(render, entry, asset, _clock(ctx)) if mode == "pip" else None
    fresh = _save_render(ctx, {**render, "inserts": normalize_inserts([*inserts, entry])})
    out: dict = {"ok": True, "inserts": fresh["inserts"]}
    if note:
        out["warnings"] = [note]
    return out


def refit_inserts(store: Any, clip_plan_id: int) -> list[str]:
    """Подогнать все окошки-вставки клипа под текущие субтитры и хук (как add_insert).

    Вставки ставит и первый шаг ИИ-монтажа (склейка), когда субтитров ещё нет, —
    после моушн-прохода их надо развести с текстом. Возвращает пояснения."""
    from app.montage_assets import normalize_inserts

    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    render = dict(plan.get("render_settings") or {})
    notes, out = [], []
    for ins in render.get("inserts") or []:
        entry = dict(ins)
        if entry.get("mode") == "pip":
            try:
                note = _fit_pip_insert(render, entry, store.get_montage_asset(int(entry["asset_id"])),
                                       plan_clock(store, store.get_clip_plan(clip_plan_id)))
            except (KeyError, TypeError, ValueError):
                note = None
            if note:
                notes.append(note)
        out.append(entry)
    if notes:
        store.set_clip_plan_render_settings(clip_plan_id, {**render, "inserts": normalize_inserts(out)})
    return notes


CAPTION_HALF = 0.075  # половина высоты двух строк субтитров в долях кадра
HOOK_BOTTOM = 0.2


def _fit_pip_insert(render: dict, entry: dict, asset: dict, clock: Any = None) -> str | None:
    """Окошко мема не должно лечь на субтитры и хук: ставим его над субтитрами
    и, если не влезает, уменьшаем. Вставки ложатся поверх субтитров при рендере,
    а агент их в view_frame не видит — поэтому подгоняем здесь."""
    from app.montage_assets import MIN_INSERT_SCALE, PIP_WIDTH

    from app.cuts import PINNED_START_SEC, CutMap, normalize_cuts

    # Сравниваем во времени ГОТОВОГО клипа: вырезанное начало сдвигает мем к хуку.
    # clock — шкалы клипа (app/timebase.py) с наплывами; без него — только вырезы.
    mapper = CutMap(normalize_cuts(render.get("cuts")))
    if clock is not None and clock.has_cuts:
        to_final, from_final = clock.clip_to_final, clock.final_to_clip
    elif mapper:
        to_final, from_final = mapper.clamp, mapper.source_time
    else:
        to_final = from_final = (lambda t: t)
    at = to_final(float(entry.get("at") or 0.0))
    end = at + float(entry.get("duration") or 1.5)
    layers = [l for l in render.get("layers") or [] if not l.get("hidden")]

    def window(l: dict) -> tuple[float, float]:
        a, b = float(l.get("start") or 0), float(l.get("end") or 0)
        if a <= PINNED_START_SEC:  # хук остаётся в начале и после вырезов
            return 0.0, b - a
        return to_final(a), to_final(b)

    def active(l: dict) -> bool:
        a, b = window(l)
        return a < end and b > at

    caps = [l for l in layers if l.get("type") == "captions" and active(l)]
    if not caps:
        return None
    cap_top = float(caps[0].get("y", 0.68)) - CAPTION_HALF
    # Крупные титры (панчлайны) выше субтитров — тоже потолок для окошка мема.
    for l in layers:
        if l.get("type") != "text" or l.get("id") == "auto_hook" or not active(l):
            continue
        y = float(l.get("y", 0.5))
        if y < cap_top:
            size = float((l.get("props") or {}).get("size") or 96)
            cap_top = min(cap_top, y - size * 0.65 / 1920)
    moved = ""
    hook = next((l for l in layers if l.get("id") == "auto_hook" and active(l)), None)
    if hook and cap_top - 0.015 - HOOK_BOTTOM < 0.12:
        # Между хуком и субтитрами мему не уместиться — пусть выходит сразу после хука.
        at = round(window(hook)[1], 2)
        entry["at"] = round(from_final(at), 2)
        hook = None
        moved = f"сдвинул на {at} с (после хука), "
    top = HOOK_BOTTOM if hook else 0.07
    room = cap_top - 0.015 - top
    if room < 0.08:
        return None  # субтитры у самого верха — не трогаем, пусть решает агент
    aw, ah = float(asset.get("width") or 0), float(asset.get("height") or 0)
    ratio = ah / aw if aw > 0 and ah > 0 else 1.0  # высота / ширина
    scale = float(entry.get("scale") or PIP_WIDTH)
    height = scale * 1080 * ratio / 1920
    y = float(entry.get("y") if entry.get("y") is not None else 0.5)
    if y - height / 2 >= top and y + height / 2 <= cap_top - 0.015:
        return f"вставку {moved.rstrip(', ')}" if moved else None
    if height > room:
        scale = max(MIN_INSERT_SCALE, int(room * 1920 / (1080 * ratio) * 1000) / 1000)
        height = scale * 1080 * ratio / 1920
    entry["scale"] = scale
    entry["y"] = round(cap_top - 0.015 - height / 2, 3)
    entry["x"] = 0.5
    return (f"вставку {moved}подвинул/уменьшил, чтобы не закрыть субтитры и хук: y={entry['y']}, "
            f"scale={entry['scale']}")


def h_remove_insert(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    inserts = list(render.get("inserts") or [])
    try:
        index = int(args.get("index"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен index вставки") from exc
    if not 0 <= index < len(inserts):
        raise ToolError(f"вставки {index} нет (0..{len(inserts) - 1})")
    inserts.pop(index)
    fresh = _save_render(ctx, {**render, "inserts": inserts})
    return {"ok": True, "inserts": fresh["inserts"]}


def h_list_projects(ctx: ToolContext, _args: dict) -> dict:
    return {
        "projects": [
            {"id": s["id"], "name": s.get("original_filename") or "", "status": s.get("status"),
             "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count"),
             "audio_tracks": _audio_brief(s)}
            for s in ctx.store.list_sources()
        ]
    }


def _audio_brief(source: dict) -> dict:
    """Сколько звуковых дорожек у записи и какие выбраны (по метаданным загрузки, без ffprobe)."""
    from app.source_audio import audio_streams, normalize_tracks

    return {"total": len(audio_streams(source, probe=False)),
            "selected": normalize_tracks(source.get("audio_tracks")) or [0]}


def _project_id(args: dict) -> int:
    try:
        return int(args.get("project_id"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен project_id (см. list_projects)") from exc


def h_source_transcript(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import transcript_job

    try:
        # Whisper фоном: вызов не держит соединение дольше ~45 с (раньше HTTP рвался на 2.5 мин).
        out = transcript_job(ctx.store, _project_id(args))
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    if out.get("status") == "running":
        return {"project_id": out["source_id"], **{k: v for k, v in out.items() if k != "source_id"}}
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    cues = [c for c in out["cues"] if float(c["end"]) > lo and (hi is None or float(c["start"]) < hi)]
    result = {"project_id": out["source_id"], "status": "done", "time": "source",
              "duration_sec": out["duration_sec"],
              "lines": [[round(float(c["start"]), 2), round(float(c["end"]), 2), c["text"]] for c in cues]}
    if out.get("no_speech"):
        result.update(no_speech=True, note=out["note"])
    return result


def _project_or_clip_source(ctx: ToolContext, args: dict) -> int:
    """project_id из аргументов, а у встроенного монтажёра — источник его клипа."""
    if args.get("project_id") is None and ctx.clip_plan_id is not None:
        plan = ctx.store.get_clip_plan(int(ctx.clip_plan_id), include_segments=False)
        return int(plan["source_id"])
    return _project_id(args)


def h_set_source_audio(ctx: ToolContext, args: dict) -> dict:
    """Звуковые дорожки исходника: [0] — первая, [1] — вторая, [0, 1] — смешать (игра + микрофон)."""
    from app.source_audio import set_source_audio

    from app.source_audio import _KEEP

    project = _project_or_clip_source(ctx, args)
    if "tracks" not in args and "speech_tracks" not in args:
        raise ToolError("нужны tracks (дорожки для рендера) и/или speech_tracks (дорожки для распознавания речи)")
    tracks = args.get("tracks", _KEEP)
    if tracks is not _KEEP and tracks is not None and not isinstance(tracks, list):
        raise ToolError("tracks — список: номера дорожек с 0 ([0], [1], [0, 1]) или [{track, gain_db}] "
                        "(gain_db — дБ или \"auto\"); [] — по умолчанию (первая)")
    speech = args.get("speech_tracks", _KEEP)
    if speech is not _KEEP and speech is not None and not isinstance(speech, list):
        raise ToolError("speech_tracks — список номеров дорожек для распознавания речи, например [1]; [] — как tracks")
    try:
        return set_source_audio(ctx.store, project, tracks, speech)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def h_set_source_gain(ctx: ToolContext, args: dict) -> dict:
    """Громкость звука исходника на отрезках клипа (шкала clip), картинка не меняется."""
    from app.audio_mute import GAIN_MAX_DB, GAIN_MIN_DB, normalize_gain

    ranges = args.get("ranges")
    if not isinstance(ranges, list):
        raise ToolError("ranges — список {start, end, gain_db} (секунды клипа до вырезов; [] — снять всё)")
    problems = []
    for i, item in enumerate(ranges):
        if not isinstance(item, dict) or _num(item.get("gain_db")) is None:
            problems.append(f"ranges[{i}]: нужны start, end и gain_db (дБ, {GAIN_MIN_DB:g}..+{GAIN_MAX_DB:g})")
    if problems:
        raise ToolError("громкость не принята", problems)
    duration = _clip_duration(ctx)
    clean = [g for g in normalize_gain(ranges) if g["start"] < duration]
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "gain": clean})
    out: dict = {"ok": True, "gain": fresh["gain"], "time": "clip",
                 "note": ("громкость звука исходника на этих отрезках изменена (рампы 0.05 с по краям); звуки слоёв и "
                          "музыка не меняются, ролик потом выравнивается целиком до −14 LUFS. Проверь listen.")}
    if any(g["gain_db"] > 12 for g in clean):
        out["warnings"] = ["подъём больше 12 дБ поднимает и шум — если голос тонет в игре по всему ролику, лучше "
                           "громкость дорожки микрофона: set_source_audio tracks [{track, gain_db}]"]
    return out


def h_source_loudness(ctx: ToolContext, args: dict) -> dict:
    """Громкость исходника по времени: пики и громкие места — перестрелки и реакции без Gemini."""
    from app.source_loudness import source_loudness

    project = _project_or_clip_source(ctx, args)
    try:
        track = _num(args.get("track"))
        return source_loudness(ctx.store, project, _num(args.get("from_sec")), _num(args.get("to_sec")),
                               _num(args.get("step")), int(_num(args.get("top"), 10) or 10),
                               track=int(track) if track is not None else None)
    except ValueError as exc:
        raise ToolError(f"source_loudness: {exc}") from exc


CHAT_IMPORT_NOTE = ("чат этого проекта не импортирован. Импорт — запрос к Twitch (POST /api/sources/{id}/chat/import): "
                    "сам не импортируй — спроси владельца, он включит импорт, если разрешает")
MAX_CHAT_WINDOW_SEC = 900.0
MAX_CHAT_MESSAGES = 300


def _chat_ready(ctx: ToolContext, project: int) -> dict | None:
    """Статус чата проекта; None — сообщения есть, можно отвечать."""
    from app.twitch_chat import get_chat_status, has_chat

    if has_chat(ctx.store, project):
        return None
    status = get_chat_status(ctx.store, project)
    out = {"project_id": project, "has_chat": False, "status": status.get("status"), "time": "source"}
    if status.get("status") in ("queued", "running"):
        out["note"] = f"чат импортируется ({round(float(status.get('progress') or 0) * 100)}%) — повтори позже"
    else:
        out["note"] = CHAT_IMPORT_NOTE
    return out


def h_chat_activity(ctx: ToolContext, args: dict) -> dict:
    """Всплески чата Twitch по времени исходника: где зрители взорвались."""
    from app.twitch_chat import chat_activity, hype_windows_from_activity

    project = _project_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo = max(0.0, _num(args.get("from_sec"), 0.0) or 0.0)
    hi = _num(args.get("to_sec"))
    span = (hi - lo) if hi is not None else None
    bucket = _num(args.get("bucket"))
    if bucket is None:
        bucket = 10.0 if span is not None and span <= 900 else 60.0
    bucket = min(600.0, max(5.0, float(bucket)))
    activity = chat_activity(ctx.store, project, bucket)
    inside = [b for b in activity.get("buckets") or [] if b["t"] + bucket > lo and (hi is None or b["t"] < hi)]
    counts = sorted(b["count"] for b in inside if b["count"])
    windows = [w for w in hype_windows_from_activity(activity, top=int(_num(args.get("top"), 10) or 10))
               if w["end"] > lo and (hi is None or w["start"] < hi)]
    shown = inside[:240]
    return {
        "project_id": project, "has_chat": True, "time": "source", "bucket": bucket,
        "from_sec": round(lo, 2), "to_sec": round(hi, 2) if hi is not None else None,
        "messages": sum(b["count"] for b in inside),
        "median_per_bucket": counts[len(counts) // 2] if counts else 0,
        "windows": windows,
        "buckets": [[b["t"], b["count"], b["hype"], b["top"]] for b in shown],
        "note": ("секунды исходника. windows — всплески (hype 0..1, top — самая частая реакция); buckets — "
                 "[t, сообщений, hype, реакция] по bucket с. Зрители пишут с задержкой 5–20 с: событие обычно "
                 "чуть РАНЬШЕ всплеска — смотри get_source_transcript/source_contact_sheet перед ним. "
                 "Что писали — get_chat")
                + (f"; показаны первые {len(shown)} из {len(inside)} окон — сузь from_sec/to_sec" if len(inside) > len(shown) else ""),
    }


def h_get_chat(ctx: ToolContext, args: dict) -> dict:
    """Сообщения чата Twitch между from_sec и to_sec (время исходника)."""
    from app.twitch_chat import list_messages

    project = _project_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None or hi <= lo:
        raise ToolError("нужны from_sec < to_sec (секунды исходника); всплески — chat_activity")
    notes = []
    if hi - lo > MAX_CHAT_WINDOW_SEC:
        hi = lo + MAX_CHAT_WINDOW_SEC
        notes.append(f"окно урезано до {MAX_CHAT_WINDOW_SEC / 60:g} мин")
    limit = max(1, min(MAX_CHAT_MESSAGES, int(_num(args.get("limit"), MAX_CHAT_MESSAGES) or MAX_CHAT_MESSAGES)))
    messages = list_messages(ctx.store, project, lo, hi, limit + 1)
    if len(messages) > limit:
        notes.append(f"показаны первые {limit} сообщений — сузь окно")
    return {
        "project_id": project, "time": "source", "from_sec": round(lo, 2), "to_sec": round(hi, 2),
        "messages": [[round(m["t"], 1), m["user"], m["text"]] for m in messages[:limit]],
        "note": "[секунда исходника, кто, текст]" + ("; " + "; ".join(notes) if notes else ""),
    }


def h_source_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    """Лист кадров исходника с временем (до 24): что на экране в громких местах."""
    from app.motion_render import source_contact_sheet_jpeg

    project = _project_or_clip_source(ctx, args)
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None:
        raise ToolError("нужны from_sec и to_sec (секунды исходника)")
    x = _num(args.get("x"))
    if x is not None and not 0.0 <= x <= 1.0:
        raise ToolError("x — центр окна 9:16 в долях ширины, 0..1")
    try:
        data, times = source_contact_sheet_jpeg(ctx.store, project, lo, hi, _num(args.get("step")), x)
    except ValueError as exc:
        raise ToolError(f"source_contact_sheet: {exc}") from exc
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={"project_id": project, "time": "source", "times": times, "x": x,
                             "note": "подписи — время исходника; окна рабочего стола/уведомления на кадрах — "
                                     "такие места в клип не брать"})


def h_source_shots(ctx: ToolContext, args: dict) -> dict:
    """Склейки планов исходника (секунды исходника) — куда ставить смену кадра (cut)."""
    from app.store import _json_text, _parse_transcript

    project = _project_id(args)
    try:
        row = ctx.store.db.query_one("SELECT local_path, scene_cuts_json FROM sources WHERE id = ?", (project,))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(str(exc)) from exc
    if not row:
        raise ToolError("нет такого проекта (см. list_projects)")
    cuts = _parse_transcript(row["scene_cuts_json"])
    if not cuts:
        from app.scene_cuts import detect_scene_cuts

        cuts = detect_scene_cuts(row["local_path"])
        ctx.store.db.execute("UPDATE sources SET scene_cuts_json = ? WHERE id = ?", (_json_text(cuts), project))
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    shown = [round(float(t), 3) for t in cuts if float(t) >= lo and (hi is None or float(t) <= hi)]
    return {"project_id": project, "time": "source", "shot_changes": shown,
            "note": "секунды исходника; в кадрировании куска ставь точку {t: склейка − начало куска, x, cut: true}"}


def h_create_clip(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import create_manual_clip

    try:
        plan = create_manual_clip(ctx.store, _project_id(args), str(args.get("title") or ""),
                                  args.get("pieces") or [], str(args.get("description") or ""))
    except (KeyError, ValueError) as exc:
        raise ToolError(f"клип не создан: {exc}") from exc
    segs = plan.get("segments") or []
    return {"ok": True, "clip_id": plan["id"], "title": plan["title"],
            "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2)}


def h_list_clips(ctx: ToolContext, args: dict) -> dict:
    try:
        project = int(args.get("project_id"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен project_id (см. list_projects)") from exc
    favorites = bool(args.get("favorites_only"))
    out = []
    for plan in ctx.store.list_clip_plans(source_id=project):
        if favorites and not plan.get("favorite"):
            continue
        if plan.get("hidden") and not favorites:
            continue
        segs = plan.get("segments") or []
        settings = plan.get("render_settings") or {}
        out.append(
            {
                "clip_id": plan["id"],
                "title": plan.get("title"),
                "favorite": bool(plan.get("favorite")),
                "status": plan.get("status"),
                "pieces": len(segs),
                "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2),
                "layers": len(settings.get("layers") or []) if isinstance(settings, dict) else 0,
            }
        )
    return {"project_id": project, "clips": out}


# --------------------------------------------------------------------------- #
# Справка по слоям: из констант app.motion — для системного промпта и MCP
# --------------------------------------------------------------------------- #


def motion_enums() -> dict:
    return {
        "types": list(motion.LAYER_TYPES),
        "easings": list(motion.EASINGS),
        "anims": list(motion.ANIMATIONS),
        "loops": list(motion.LOOPS),
        "shapes": list(motion.SHAPE_KINDS),
        "charts": list(motion.CHART_KINDS),
        "callouts": list(motion.CALLOUT_STYLES),
    }


def motion_reference() -> str:
    """Компактная шпаргалка по слоям (полный контракт — docs/motion/spec.md)."""
    enums = motion_enums()
    fields = "; ".join(f"{kind}: {', '.join(props)}" for kind, props in motion.PROPS.items())
    return f"""\
КАДР 1080×1920 (опорные px). x,y — центр слоя в долях кадра (0..1). Время — секунды клипа.
Слой: {{id?, type, name?, start, end, track 0..7 (выше = поверх), behind (за персонажем, только при хромакее), x, y, scale, rotation, opacity,
  keys {{prop: [{{t (от start слоя), v, ease?}}]}} — prop: x,y,scale,rotation,opacity,progress,
  in {{type, duration}}, out {{type, duration}} (null или duration 0 — без анимации; у слоя до конца клипа out не играет), loop {{type, speed 0.1..5, amount 0..3}},
  shadow {{color, blur, x, y, opacity}}, blend normal|multiply|screen|overlay|add, props {{...}}}}
type: {', '.join(enums['types'])}
in/out: {', '.join(enums['anims'])} (typewriter/words — только text; draw — shape line/arrow/ellipse)
loop: {', '.join(enums['loops'])}
ease: {', '.join(enums['easings'])} или bezier(x1,y1,x2,y2)
props по типу:
  text: text (≤400, \\n), font, weight 100..900, size 12..400, color, gradient [c1,c2]|null, stroke {{color,width}},
        bg {{color,opacity,radius,padX,padY}}|null, align, maxWidth, lineHeight, letterSpacing, uppercase, italic,
        highlight {{words:[индексы слов], color, bg}}
  shape: kind {'|'.join(enums['shapes'])}, w, h, fill|null, stroke {{color,width}}, radius, points, dash
  image: asset_id (из библиотеки) ИЛИ src ("emoji:1f525", "sticker:<pack>/<file>"), w, radius, border, crop, flipX
  emoji: char ("🔥"), size
  counter: from, to, decimals, prefix, suffix, thousands + стили text; рост по progress (по умолч. первая половина)
  progress: w, h, value 0..1, track, fill (цвет или [c1,c2]), radius, label, font, labelColor
  chart: kind {'|'.join(enums['charts'])}, data [{{label,value,color?}}] ≤12, w, h, palette, font, labelColor, showValues, title
  callout: title, subtitle, accent, style {'|'.join(enums['callouts'])}, font, w
  audio: asset_id ИЛИ "sfx:<pack>/<file>", volume 0..2, fadeIn, fadeOut, offset, duck (не рисуется)
Все допустимые поля props (остальное отбрасывается): {fields}
Камера: [{{t (время клипа), zoom 1..4, x, y, ease?, shake? 0..1}}] — двигает только видео (в раскладках pip_cam/split — только игру, окошко вебки на месте).
  ease ключа — переход ОТ него к следующему ключу (по умолчанию inOutCubic); step — держать до следующего ключа и прыгнуть. zoom < 1 нет: шире окна — set_piece_fit.
Субтитры captions: в hormozi/mrbeast/karaoke звучащее слово жёлтое — highlightWords жёлтым там не видны, бери #FF3B30 или #22C55E.
Цвета: #rrggbb или #rrggbbaa."""


# --------------------------------------------------------------------------- #
# Подписи шагов (их видит владелец в чате: «Ставлю титр … на 3.2 с»)
# --------------------------------------------------------------------------- #


def _label_add_layers(args: dict) -> str:
    layers = args.get("layers")
    if isinstance(layers, dict):
        layers = [layers]
    layers = [l for l in layers or [] if isinstance(l, dict)]
    if not layers:
        return "Добавляю слои"
    parts = [_layer_summary(l) for l in layers[:3]]
    more = f" и ещё {len(layers) - 3}" if len(layers) > 3 else ""
    return "Ставлю " + "; ".join(parts) + more


def _label_camera(args: dict) -> str:
    keys = [k for k in args.get("keys") or [] if isinstance(k, dict)]
    if not keys:
        return "Убираю наезды камеры"
    peak = max(keys, key=lambda k: _num(k.get("zoom"), 1.0) or 1.0)
    return f"Наезд камеры до {(_num(peak.get('zoom'), 1.0) or 1.0):.2f}× на {(_num(peak.get('t'), 0.0) or 0.0):.1f} с"


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_AT_WORD_SCHEMA = {
    "type": "object",
    "description": "вместо времени: начало слова субтитров — {line, word} из get_clip или {text, nth}",
    "properties": {
        "line": {"type": "integer"}, "word": {"type": "integer"},
        "text": {"type": "string"}, "nth": {"type": "integer", "description": "какое по счёту вхождение, с 1"},
    },
}


def _layer_schema() -> dict:
    enums = motion_enums()
    anim = {**_obj({"type": {"type": "string", "enum": enums["anims"]}, "duration": {"type": "number"}}),
            "description": "null, type none или duration 0 — без анимации"}
    key_point = _obj({"t": {"type": "number"}, "v": {"type": "number"},
                      "ease": {"type": "string", "enum": enums["easings"]}}, ["t", "v"])
    return _obj(
        {
            "id": {"type": "string", "description": "необязательно; ^[A-Za-z0-9_-]{1,32}$"},
            "type": {"type": "string", "enum": enums["types"]},
            "name": {"type": "string"},
            "start": {"type": "number", "description": "секунды клипа"},
            "end": {"type": "number", "description": "секунды клипа, end - start ≥ 0.1"},
            "track": {"type": "integer", "description": "0..7, выше = поверх"},
            "behind": {"type": "boolean", "description": "за вырезанным персонажем (только источник с хромакеем)"},
            "x": {"type": "number", "description": "центр по ширине, доля кадра"},
            "y": {"type": "number", "description": "центр по высоте, доля кадра (безопасно 0.15..0.72 для центра текста; края текста — не выше 10% и не ниже 80% кадра)"},
            "scale": {"type": "number"},
            "rotation": {"type": "number"},
            "opacity": {"type": "number"},
            "keys": {
                "type": "object",
                "description": "ключевые кадры: {x|y|scale|rotation|opacity|progress: [{t, v, ease?}]}, t от start слоя",
                "additionalProperties": {"type": "array", "items": key_point},
            },
            "in": anim,
            "out": anim,
            "loop": _obj({"type": {"type": "string", "enum": enums["loops"]}, "speed": {"type": "number"},
                          "amount": {"type": "number"}}),
            "shadow": {"type": "object"},
            "blend": {"type": "string", "enum": ["normal", "multiply", "screen", "overlay", "add"]},
            "props": {"type": "object", "description": "свои поля у каждого типа — см. get_motion_reference"},
            "at_word": _AT_WORD_SCHEMA,
            "word_offset": {"type": "number", "description": "сдвиг от начала слова, с (−0.1 — чуть раньше)"},
            "duration": {"type": "number", "description": "с at_word: длина слоя, с (иначе end-start или 1.2)"},
            "until_word": {**_AT_WORD_SCHEMA,
                           "description": "вместо end: слой держится до начала этого слова ({line, word} или {text, nth})"},
        },
        ["type"],  # start/end или at_word — проверяет _validate_new_layers
    )


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


def _build_registry() -> list[Tool]:
    layer = _layer_schema()
    return [
        Tool(
            "get_clip", "Смотрю клип",
            "Весь клип: куски (start_sec/end_sec — время исходника, как в trim_piece и create_clip; "
            "clip_start/clip_end — шкала clip; у каждого куска его кадрирование focus, fit и рамка героя "
            "subject_box), framing (доля ширины исходника в окне 9:16), настройки (и каким пресетом "
            "отрендерится — render.effective_preset), слои, камера, вставки, вырезы, тишина и громкость "
            "исходника, субтитры с временем слов, места для текста (positions: хук с поправкой на число строк "
            "и полосу вебки split), звуковые дорожки исходника (source.audio) и предупреждения. Шкалы времени "
            "— time_scales: clip (до вырезов, с наплывами; почти всё), final (готовый ролик), source, piece. "
            "Зови первым. / Full clip state; times are in the 'clip' scale unless named otherwise.",
            _obj({}), h_get_clip,
        ),
        Tool(
            "get_transcript", "Читаю расшифровку исходника",
            "Реплики ИСХОДНИКА (время исходника, не клипа) между from_sec и to_sec — чтобы подрезать "
            "кусок по фразе. Без параметров — вокруг кусков клипа. / Source transcript window (source time).",
            _obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"}}), h_get_transcript,
            label=lambda a: "Читаю расшифровку исходника",
        ),
        Tool(
            "view_frame", "Смотрю кадр",
            "Картинка клипа в момент t — как будет в готовом видео, со слоями и камерой. t — шкала clip "
            "(до вырезов, как слои); final=true — t в секундах ГОТОВОГО ролика (как contact_sheet и listen). "
            "Проверяй так размещение, читаемость, что титр не закрыл лицо. / Render the clip frame at t.",
            _obj({"t": {"type": "number"}, "with_layers": {"type": "boolean"},
                  "final": {"type": "boolean", "description": "t — секунды готового ролика (после вырезов)"}},
                 ["t"]), h_view_frame,
            label=lambda a: f"Смотрю кадр на {(_num(a.get('t'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "find_noises", "Ищу посторонние звуки",
            "Громкие места звука клипа без речи — вшитые эффекты старого монтажа, музыка, шум, в игре — "
            "выстрелы и взрывы (распознавание их не видит). Отрезки уже слиты, шкала clip (до вырезов, как "
            "слои и mute_audio — годятся туда как есть): в паузе — вырежи apply_cuts или mute_audio, тише — "
            "set_source_gain. threshold_db — порог громкости, по умолчанию медиана звука клипа без речи + 6 дБ. "
            "Речь вычитается по субтитрам, без них — по дорожке речи (speech_tracks), если она своя. "
            "/ Loud non-speech spans in the clip audio.",
            _obj({"threshold_db": {"type": "number"}}), h_find_noises,
            label=lambda a: "Ищу посторонние звуки",
        ),
        Tool(
            "listen", "Слушаю отрезок",
            "Распознать, что РЕАЛЬНО звучит в готовом клипе между from_sec и to_sec (с вырезами и наплывами "
            "переходов, до 30 с): время — шкала final (готовый ролик, как contact_sheet); before_cuts=true — "
            "шкала clip (до вырезов, как слои и субтитры get_clip). Распознаёт дорожку речи (speech_tracks). "
            "Слова с временем и тишина после последнего слова, громкость отрезка (level, RMS дБ). Без "
            "параметров — последние 6 с: проверь, договорена ли развязка и есть ли хвост реакции. Речи нет "
            "(игра, музыка) — words [] и пометка. Для концовок, вырезов и «не торчит ли слово» — НЕ для "
            "исправления распознавания: это тот же Whisper и он повторит те же ошибки (неверное слово правь "
            "по смыслу/на слух владельца через set_subtitle_words). / Transcribe the finished clip audio in a range.",
            _obj({"from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "before_cuts": {"type": "boolean", "description": "from/to и слова — шкала clip (до вырезов)"}}),
            h_listen,
            label=lambda a: "Слушаю финал клипа" if a.get("from_sec") is None else
            f"Слушаю {(_num(a.get('from_sec'), 0.0) or 0.0):.1f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "contact_sheet", "Смотрю весь клип",
            "Сетка из n кадров равномерно по клипу (с временем) — весь монтаж одним взглядом: "
            "проверить ритм графики, не перегружен ли кадр, где пусто. times — свои моменты, до 16: по "
            "умолчанию секунды ГОТОВОГО ролика (после вырезов, как подписи на листе); before_cuts=true — "
            "секунды ДО вырезов, как у слоёв и камеры. Кадры исходника вне клипа — source_contact_sheet. / "
            "Contact sheet of n frames or of chosen times.",
            _obj({"n": {"type": "integer", "minimum": 2, "maximum": 16},
                  "times": {"type": "array", "items": {"type": "number"}},
                  "before_cuts": {"type": "boolean",
                                  "description": "times в секундах ДО вырезов — как у слоёв, камеры и view_frame"}}),
            h_contact_sheet,
            label=lambda a: "Смотрю весь клип сеткой кадров",
        ),
        Tool(
            "source_contact_sheet", "Смотрю кадры исходника",
            "Сетка кадров ИСХОДНИКА между from_sec и to_sec (секунды исходника, подписи — время исходника): "
            "step — шаг в секундах (от 0.04 — покадрово на коротком окне; до 24 кадров), без него 12 "
            "равномерно; x 0..1 — показать окно 9:16 с "
            "центром x, как оно встанет в вертикальный кадр. Выбирать моменты без Gemini: громкие места "
            "source_loudness → что там на экране. Окна рабочего стола, Alt-Tab, уведомления — такие места не "
            "брать. project_id — проект (у встроенного монтажёра — источник клипа). / Contact sheet of the source.",
            _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "x": {"type": "number"}}, ["from_sec", "to_sec"]),
            h_source_contact_sheet, scope="global",
            label=lambda a: f"Смотрю кадры исходника {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–"
                            f"{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "source_loudness", "Слушаю громкость исходника",
            "Профиль громкости ИСХОДНИКА: RMS дБ по шагу step (≥ 0.5 с, по умолчанию 1) в окне from_sec..to_sec "
            "(не больше 10 мин за вызов, по умолчанию 10 мин от from_sec), пики (top, по умолчанию 10) и "
            "громкие отрезки, сводка: медиана и порог «громко» (медиана + 6 дБ). Для игр и стримов без речи — "
            "так ищут перестрелки, взрывы, реакции; потом source_contact_sheet на пиках. Звук — смесь рендера "
            "(set_source_audio); track — одна дорожка записи (сравнить громкость игры и микрофона перед "
            "gain_db). / Loudness profile of the source over time.",
            _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "top": {"type": "integer"},
                  "track": {"type": "integer", "description": "одна звуковая дорожка вместо смеси"}}),
            h_source_loudness, scope="global",
            label=lambda a: f"Слушаю громкость исходника с {(_num(a.get('from_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "chat_activity", "Активность чата",
            "Всплески чата Twitch (время исходника) между from_sec и to_sec: windows — самые горячие окна "
            "(hype 0..1, top — частая реакция), buckets — [t, сообщений, hype, реакция] по bucket секунд "
            "(по умолчанию 60, на окне до 15 мин — 10). Зрители пишут с задержкой 5–20 с — событие чуть раньше "
            "всплеска. Чата нет — has_chat false: импорт — запрос к Twitch, только с разрешения владельца, сам не "
            "импортируй. / Twitch chat activity bursts.",
            _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "bucket": {"type": "number"}, "top": {"type": "integer"}}),
            h_chat_activity, scope="global",
            label=lambda a: "Смотрю активность чата",
        ),
        Tool(
            "get_chat", "Читаю чат",
            "Сообщения чата Twitch между from_sec и to_sec (время исходника, окно до 15 мин, до 300 сообщений): "
            "[секунда, кто, текст] — что зрители писали на моменте (реакции, мемы, цитаты для хука). Чата нет — "
            "has_chat false (импорт — только с разрешения владельца). / Twitch chat messages in a source window.",
            _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "limit": {"type": "integer"}}, ["from_sec", "to_sec"]),
            h_get_chat, scope="global",
            label=lambda a: f"Читаю чат {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "set_source_audio", "Выбираю звуковую дорожку",
            "Звуковые дорожки исходника (запись NVIDIA/OBS: часто 0 — игра, 1 — микрофон). tracks — смесь для "
            "рендера: [0], [1], [0, 1] или со своей громкостью [{track, gain_db}] (gain_db — дБ −30..+30; "
            "\"auto\" — замерить и поставить голос на 6 дБ над игрой): тихий микрофон в смеси поровну тонет. "
            "speech_tracks — дорожки для РАСПОЗНАВАНИЯ речи (расшифровка исходника, субтитры, listen, сверка "
            "субтитров в QC), например [1] — один микрофон: на смеси с игрой Whisper выдумывает; [] — как tracks. "
            "Сменился звук для распознавания — расшифровка исходника сбрасывается. Сколько дорожек — "
            "get_clip.source.audio / list_projects; громкость каждой — source_loudness(track). project_id — "
            "проект (у встроенного монтажёра — источник клипа). / Choose, mix and level the source audio tracks.",
            _obj({"project_id": {"type": "integer"},
                  "tracks": {"type": "array", "items": {
                      "anyOf": [{"type": "integer"},
                                _obj({"track": {"type": "integer"},
                                      "gain_db": {"type": ["number", "string"],
                                                  "description": "дБ −30..+30 или \"auto\""}}, ["track"])]}},
                  "speech_tracks": {"type": "array", "items": {"type": "integer"},
                                    "description": "дорожки для распознавания речи; [] — как tracks"}}),
            h_set_source_audio, mutating=True, scope="global",
            label=lambda a: f"Звук исходника: дорожки {a.get('tracks')}"
            + (f", речь {a.get('speech_tracks')}" if a.get("speech_tracks") is not None else ""),
        ),
        Tool(
            "get_motion_reference", "Справка по слоям",
            "Шпаргалка по моушн-слоям: типы, props, анимации, easing, камера. / Motion layer cheat sheet.",
            _obj({}), h_get_motion_reference, scope="global",
        ),
        Tool(
            "add_layers", "Добавляю слои",
            "Добавить моушн-слои (титры, картинки, фигуры, счётчики, графики, плашки, звуки). "
            "Звук или титр ровно на слово — at_word {line, word} или {text: \"Артас\"} вместо start, "
            "подсветка слова в субтитрах (captions props.highlightWords) — {text, nth?, color} вместо номера i: "
            "она переживёт set_subtitle_words; "
            "until_word — держать до начала другого слова. Не больше 30 слоёв за вызов. "
            "Слой image рисует картинку статично (у GIF — первый кадр): анимированные гифки — add_insert. "
            "Звук (audio) сервис выравнивает сам: при речи — на 10 дБ тише голоса, в клипе без речи (игра) — "
            "на 4 дБ ниже его громких мест, не громче; volume 0.3–0.6, выше 1 срезается. "
            "Слой до самого конца клипа держится на финальном стоп-кадре — out у него не играет; "
            "out null или {duration: 0} — без анимации. "
            "Проверяется как правка человека; при ошибке вернёт problems — исправь и повтори. "
            "Возвращает id и предупреждения (безопасные зоны, перегруз). / Add motion layers.",
            _obj({"layers": {"type": "array", "items": layer}}, ["layers"]), h_add_layers,
            mutating=True, label=_label_add_layers,
        ),
        Tool(
            "update_layer", "Правлю слой",
            "Изменить слой по id: patch — только изменённые поля (props/in/out/loop сливаются, keys "
            "заменяются целиком). / Patch one layer.",
            _obj({"id": {"type": "string"}, "patch": {"type": "object"}}, ["id", "patch"]), h_update_layer,
            mutating=True, label=lambda a: f"Правлю слой {a.get('id')}",
        ),
        Tool(
            "remove_layers", "Убираю слои",
            "Удалить слои по id. / Remove layers by id.",
            _obj({"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"]), h_remove_layers,
            mutating=True, label=lambda a: f"Убираю слои: {', '.join(map(str, a.get('ids') or []))[:80]}",
        ),
        Tool(
            "clear_layers", "Убираю все слои",
            "Удалить все слои (camera=true — и наезды камеры). / Remove all layers.",
            _obj({"camera": {"type": "boolean"}}), h_clear_layers,
            mutating=True, label=lambda a: "Убираю все слои" + (" и наезды" if a.get("camera") else ""),
        ),
        Tool(
            "set_camera", "Наезды камеры",
            "Заменить ВСЕ ключи камеры: [{t, zoom 1..4, x, y, ease?, shake?}]. Пустой список — без наездов. "
            "Между ключами значения плавно переходят; ease ключа A задаёт переход A → следующий ключ (по "
            "умолчанию inOutCubic); ease \"step\" — держать A до времени следующего ключа и прыгнуть "
            "(жёсткий кадр, без наезда). До первого ключа и после последнего — их значения. "
            "Панч-ин на панчлайн: 1.0 → 1.15–1.35 за 0.15–0.3 с, держать, вернуть. zoom < 1 нельзя (камера "
            "только приближает) — шире окна: set_piece_fit. В раскладках pip_cam/split "
            "камера двигает только игру: окошко вебки/килфида и полоса вебки стоят на месте. / Replace camera keys.",
            _obj({"keys": {"type": "array", "items": _obj({
                "t": {"type": "number"}, "zoom": {"type": "number"}, "x": {"type": "number"},
                "y": {"type": "number"}, "ease": {"type": "string", "enum": motion_enums()["easings"]},
                "shake": {"type": "number"}}, ["t", "zoom"])}}, ["keys"]),
            h_set_camera, mutating=True, label=_label_camera,
        ),
        Tool(
            "list_fonts", "Смотрю шрифты",
            "Шрифты для слоёв (family, weight, cyrillic). Для русского текста бери cyrillic=true. / Fonts.",
            _obj({}), h_list_fonts, scope="global",
        ),
        Tool(
            "search_emoji", "Ищу эмодзи",
            "Поиск эмодзи по слову (рус/англ): вернёт символ и код для слоя emoji или image src emoji:<код>. "
            "/ Emoji search.",
            _obj({"q": {"type": "string"}, "limit": {"type": "integer"}}, ["q"]), h_search_emoji, scope="global",
            label=lambda a: f"Ищу эмодзи «{_short(a.get('q'), 24)}»",
        ),
        Tool(
            "list_packs", "Смотрю паки",
            "Паки стикеров, звуков (sfx) и мемов, встроенные в сервис. / Built-in sticker/sfx/meme packs.",
            _obj({"kind": {"type": "string", "enum": ["sticker", "meme", "sfx", "emoji"]}}), h_list_packs,
            scope="global",
        ),
        Tool(
            "search_pack", "Ищу в паке",
            "Поиск звуков/стикеров/мемов по словам (рус/англ): pack — конкретный пак, или kind "
            "(sfx/sticker/meme) — по всем пакам вида. Вернёт ref: sfx:<pack>/<file> для audio asset_id, "
            "sticker:<pack>/<file> для image src. / Search sfx/sticker/meme packs.",
            _obj({"q": {"type": "string"}, "pack": {"type": "string"},
                  "kind": {"type": "string", "enum": ["sfx", "sticker", "meme"]}, "limit": {"type": "integer"}}),
            h_search_pack, scope="global",
            label=lambda a: f"Ищу «{_short(a.get('q'), 24)}» в " + (f"паке {a['pack']}" if a.get("pack")
                                                                  else f"паках {a.get('kind') or ''}".strip()),
        ),
        Tool(
            "list_assets", "Смотрю библиотеку",
            "«Файлы для монтажа» владельца (мемы, реакции, звуки) — asset_id для слоёв image/audio и "
            "вставок. q — фильтр по словам. / Owner's montage library.",
            _obj({"q": {"type": "string"}}), h_list_assets, scope="global",
        ),
        Tool(
            "search_web_images", "Ищу картинку в интернете",
            "Найти картинку/мем в интернете (Gemini + Google; без Gemini — Imgflip-мемы, Wikimedia Commons, "
            "Giphy при GIPHY_API_KEY — запрос лучше по-английски). Ничего не скачивает — только кандидаты. "
            "free_only=true — только свободные лицензии. / Web image search.",
            _obj({"query": {"type": "string"}, "free_only": {"type": "boolean"}, "count": {"type": "integer"}},
                 ["query"]),
            h_search_web_images, scope="global", label=lambda a: f"Ищу в интернете «{_short(a.get('query'), 30)}»",
        ),
        Tool(
            "add_image_from_url", "Скачиваю картинку",
            "Скачать картинку по прямой ссылке в библиотеку; вернёт asset_id для слоя image. query — что "
            "искал (кого/что должна показывать картинка): Gemini проверит, что она про это, иначе ошибка и "
            "бери следующего кандидата. / Download to library.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}, "description": {"type": "string"},
                  "query": {"type": "string"}}, ["url"]),
            h_add_image_from_url, scope="global", label=lambda a: f"Скачиваю картинку {(a.get('label') or '')[:40]}",
        ),
        Tool(
            "upload_image", "Загружаю картинку",
            "Своя картинка (скриншот сайта, кадр) в библиотеку: data_base64 — содержимое файла в base64 "
            "(можно data:-URL), filename с расширением .png/.jpg/.webp/.gif, label. До 12 МБ. Вернёт asset_id "
            "для слоя image. / Upload a local image as base64.",
            _obj({"data_base64": {"type": "string"}, "filename": {"type": "string"}, "label": {"type": "string"},
                  "description": {"type": "string"}}, ["data_base64", "filename"]),
            h_upload_image, scope="global", label=lambda a: f"Загружаю {(a.get('label') or a.get('filename') or '')[:40]}",
        ),
        Tool(
            "generate_image", "Рисую картинку",
            "Нарисовать оригинальную картинку/стикер (ИИ) в библиотеку; вернёт asset_id. / Generate an image.",
            _obj({"prompt": {"type": "string"}, "label": {"type": "string"}}, ["prompt"]), h_generate_image,
            scope="global", label=lambda a: f"Рисую «{_short(a.get('prompt'), 40)}»",
        ),
        Tool(
            "list_subtitle_styles", "Смотрю стили субтитров",
            "Стили субтитров (id, шрифт, цвета). / Subtitle styles.",
            _obj({}), h_list_subtitle_styles, scope="global",
        ),
        Tool(
            "set_subtitles", "Настраиваю субтитры",
            "Субтитры: on (вкл/выкл), style_id (см. list_subtitle_styles), position_pct (отступ снизу, 2..85 % "
            "высоты; 12–22 — над интерфейсом площадки; 60–80 — над видео в раскладке meme). / Subtitle settings.",
            _obj({"on": {"type": "boolean"}, "style_id": {"type": "integer"}, "position_pct": {"type": "number"}}),
            h_set_subtitles, mutating=True,
            label=lambda a: "Субтитры " + ("выкл" if a.get("on") is False else "вкл")
            + (f", стиль {a['style_id']}" if a.get("style_id") else "")
            + (f", {a['position_pct']}% снизу" if a.get("position_pct") is not None else ""),
        ),
        Tool(
            "set_layout", "Меняю раскладку кадра",
            "Раскладка кадра 9:16: mode single (обычный кадр) | split (вебка сверху/снизу + игра; cam {x,y,w,h} "
            "в долях исходника, ratio, cam_pos) | pip_cam (окошко вебки pip {x,y,w}) | meme (горизонтальное видео "
            "целиком сверху во всю ширину, ниже фон bg под картинки и титры — анимированный объект {kind, colors, "
            "speed, intensity} как у set_backdrop или имя стиля строкой (\"grid\"); не задан — аврора в цветах "
            "ролика; #rrggbb (заливка) — только если владелец просил; video_y — верх видео в долях "
            "высоты; video_scale 1..4 — ширина видео в ширинах кадра, края срезаются: для персонажа на зелёнке "
            "2–3, чтобы он был крупным). Передавай только то, что меняешь. В split/pip_cam камера (set_camera) "
            "двигает только игру; что из игры в окне — game_x (центр окна игры 0..1 на всех кусках, или на куске "
            "piece; трек по куску — set_focus). cam не задан — берётся вебка источника (detect-facecam); ответ "
            "предупредит, если полоса режет вебку (не те пропорции — ratio) или cam великоват (захвачен чат). "
            "/ Frame layout.",
            _obj({"mode": {"type": "string", "enum": ["single", "split", "pip_cam", "meme"]},
                  "cam": {"type": "object"}, "ratio": {"type": "number"}, "cam_pos": {"type": "string"},
                  "pip": {"type": "object"},
                  "bg": {"type": ["object", "string"], "description": "meme: фон {kind, colors, speed, intensity} или имя стиля"},
                  "video_y": {"type": "number"},
                  "video_scale": {"type": "number"},
                  "game_x": {"type": "number", "description": "центр окна игры, доля ширины исходника 0..1"},
                  "piece": {"type": "integer", "description": "game_x только для этого куска"}}),
            h_set_layout, mutating=True, label=lambda a: f"Раскладка: {a.get('mode') or 'правка'}",
        ),
        Tool(
            "set_chroma_key", "Вырезаю зелёный фон",
            "Хромакей источника (персонаж на зелёнке): on true — найти фон сам (или color #rrggbb), similarity "
            "0.01..0.6 (порог, по умолчанию 0.25), blend 0..0.5 (мягкость края); on false — выключить. Фон за "
            "персонажем — set_backdrop, слои за ним — behind:true. / Chroma key of the clip's source.",
            _obj({"on": {"type": "boolean"}, "color": {"type": "string"}, "similarity": {"type": "number"},
                  "blend": {"type": "number"}}),
            h_set_chroma_key, mutating=True,
            label=lambda a: "Хромакей " + ("выкл" if a.get("on") is False else "вкл"),
        ),
        Tool(
            "list_music", "Смотрю фоновую музыку",
            "Треки фоновой музыки (id, название, длина). / Background music tracks.",
            _obj({}), h_list_music, scope="global",
        ),
        Tool(
            "add_sound_from_url", "Скачиваю звук",
            "Звуковой эффект по ссылке ВЛАДЕЛЬЦА: страница звука freesound.org или любое видео yt-dlp (только "
            "звук; start — с какой секунды взять). Режет тишину в начале, обрезает до 6 с → asset_id для слоя "
            "audio. Сам звуки в интернете не ищи — только ссылка владельца; иначе пак freesound (search_pack). / "
            "Sound effect from the owner's link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}, "start": {"type": "number"}}, ["url"]),
            h_add_sound_from_url, scope="global",
            label=lambda a: f"Скачиваю звук {(a.get('label') or '')[:40]}".strip(),
        ),
        Tool(
            "get_performance", "Смотрю, что заходит",
            "Статистика опубликованных клипов: топ и антитоп по просмотрам (limit, по умолчанию 5) с признаками "
            "монтажа — длина, хук-титр, субтитры, музыка, слои, звуки, вырезы, раскладка, хромакей — и сравнение "
            "«с признаком / без». Зови перед монтажом нового клипа, чтобы повторять то, что заходит. Пусто "
            "(hint) — публикаций с числами ещё нет: это нормально, монтируй по правилам. / Top and "
            "bottom published clips by views with their montage features.",
            _obj({"limit": {"type": "integer"}}), h_get_performance, scope="global",
        ),
        Tool(
            "mute_audio", "Глушу звук исходника",
            "Тишина вместо звука исходника на отрезках ranges [{start, end}] (секунды клипа до вырезов), "
            "картинка остаётся — для вшитых эффектов старого монтажа, шума, мата (find_noises покажет где). "
            "Заменяет прежний список; [] — снять. / Mute source audio in ranges.",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"}},
                                                            ["start", "end"])}}, ["ranges"]),
            h_mute_audio, mutating=True, label=lambda a: f"Глушу звук ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "set_source_gain", "Громкость исходника на отрезках",
            "Громкость звука исходника на отрезках ranges [{start, end, gain_db}] (шкала clip — секунды клипа до "
            "вырезов, как mute_audio и find_noises): поднять тихий кусок (+6..+12 дБ) или приглушить громкий "
            "(−6..−20 дБ), картинка та же; звуки слоёв и музыка не меняются, ролик потом выравнивается целиком. "
            "Заменяет прежний список; [] — снять. Тихий микрофон по всему ролику — громкость дорожки "
            "(set_source_audio tracks [{track, gain_db}]), а не отрезки. / Source audio gain in ranges.",
            _obj({"ranges": {"type": "array", "items": _obj({"start": {"type": "number"}, "end": {"type": "number"},
                                                            "gain_db": {"type": "number"}},
                                                           ["start", "end", "gain_db"])}}, ["ranges"]),
            h_set_source_gain, mutating=True,
            label=lambda a: f"Громкость исходника ({len(a.get('ranges') or [])} отр.)",
        ),
        Tool(
            "add_music_from_url", "Скачиваю музыку",
            "Трек фоновой музыки по ссылке (YouTube и всё, что умеет yt-dlp; до 15 мин) → track_id для "
            "set_music. Только когда владелец дал ссылку. / Background music from a link.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}}, ["url"]),
            h_add_music_from_url, scope="global", label=lambda a: "Скачиваю музыку по ссылке",
        ),
        Tool(
            "set_music", "Подкладываю музыку",
            "Фоновая музыка клипа: on, track_id (list_music). Громкость сама: заметно тише голоса и "
            "приглушается под речь; эффекты при музыке — реже. / Background music for the clip.",
            _obj({"on": {"type": "boolean"}, "track_id": {"type": "integer"}}),
            h_set_music, mutating=True,
            label=lambda a: "Музыка " + ("выкл" if a.get("on") is False else f"трек {a.get('track_id') or ''}".strip()),
        ),
        Tool(
            "set_backdrop", "Ставлю фон за персонажем",
            "Фон за вырезанным персонажем (нужен хромакей) — анимированный зацикленный, рисуется кодом: kind "
            f"{BG_KINDS_HINT}; colors — до 3 #rrggbb [тёмная основа, акцент, второй акцент], [] — в цветах ролика "
            "(тёмный + акцент субтитров); speed 0.1..4 (1 — цикл 10 с); intensity 0..1 (яркость свечения, 0.7). "
            "Передавай только то, что меняешь. Статичная заливка (kind solid/gradient или color/color2) — только "
            "если владелец попросил. Картинки во весь фон — слоем image с behind:true. / Animated backdrop behind "
            "a keyed subject.",
            _obj({"kind": {"type": "string", "enum": list(BG_KINDS)}, "colors": {"type": "array", "items": {"type": "string"}},
                  "speed": {"type": "number"}, "intensity": {"type": "number"},
                  "color": {"type": "string", "description": "устар.: заливка #rrggbb"},
                  "color2": {"type": "string", "description": "устар.: низ градиента"}}),
            h_set_backdrop, mutating=True, label=lambda a: "Фон за персонажем",
        ),
        Tool(
            "list_brands", "Смотрю бренды",
            "Бренды владельца (повторяющиеся заказчики, например магазин): шрифт, цвета, логотип, призыв в "
            "конце (cta), музыка, фон, политика эффектов. Создаёт и правит их только владелец. / Brand kits.",
            _obj({}), h_list_brands, scope="global",
        ),
        Tool(
            "apply_brand", "Оформляю в стиле бренда",
            "Ролик для бренда (реклама повторяющегося заказчика) — в его стиле одной командой: перекрашивает "
            "субтитры-слои и хук, ставит маленький логотип в углу, плашку-призыв на последние секунды, музыку "
            "и фон бренда, снимает лишние эффекты. Зови ПОСЛЕ монтажа (auto_edit, хук, вырезы): повтор "
            "ничего не дублирует (слои brand_*). Потом view_frame на концовке — призыв не на лице. "
            "/ Apply a brand kit to the clip.",
            _obj({"brand_id": {"type": "integer"}}, ["brand_id"]),
            h_apply_brand, mutating=True, label=lambda a: f"Бренд #{a.get('brand_id')}",
        ),
        Tool(
            "set_subtitle_words", "Задаю слова субтитров",
            "Заменить слова субтитров целиком: words [{word, start, end, s?}] — текст и время каждого слова "
            "(шкала clip: до вырезов, с наплывами — ровно как в get_clip, слова оттуда можно вернуть как есть), "
            "s (или speaker) — голос (A главный, B/C/D — второй голос, TTS-донат, ролик): так помечают голоса "
            "без Gemini. Подсветка слов (highlightWords) остаётся на тех же словах. Для исправления "
            "ошибок распознавания и опоздавших слов. Строки соберутся из слов сами: новая строка на конце "
            "предложения (. ! ? …), паузе > 0.6 с, смене голоса и после 7 слов; «какой» «-то» склеится в "
            "«какой-то». Пустой список [] — убрать субтитры клипа (клип без речи). "
            "/ Replace subtitle words with exact timings; [] clears them.",
            _obj({"words": {"type": "array", "items": _obj({"word": {"type": "string"}, "start": {"type": "number"},
                                                              "end": {"type": "number"},
                                                              "s": {"type": "string", "description": "голос: A, B, C, D"},
                                                              "speaker": {"type": "string",
                                                                          "description": "то же, что s"}},
                                                             ["word", "start", "end"])}},
                 ["words"]),
            h_set_subtitle_words, mutating=True, label=lambda a: "Задаю слова субтитров",
        ),
        Tool(
            "edit_subtitle_lines", "Правлю субтитры",
            "Исправить текст строк субтитров: edits [{i, text}] (i — номер из get_clip). Время слов "
            "сохранится, где текст не менялся. / Fix subtitle line text.",
            _obj({"edits": {"type": "array", "items": _obj({"i": {"type": "integer"}, "text": {"type": "string"}},
                                                           ["i", "text"])}}, ["edits"]),
            h_edit_subtitle_lines, mutating=True, label=lambda a: f"Правлю субтитры ({len(a.get('edits') or [])} стр.)",
        ),
        Tool(
            "separate_voices", "Разбираю голоса",
            "Послушать звук клипа и расшифровать каждый голос отдельно (стример A, гость/донат/ролик — B, C): "
            "возвращает потерянные под чужим голосом фразы, метит слова, строки не смешивают людей. "
            "auto_edit делает это сам; вызывай, если перераспознал речь после auto_edit. / Separate voices.",
            _obj({}, []),
            h_separate_voices, mutating=True, label=lambda a: "Разбираю голоса",
        ),
        Tool(
            "generate_subtitles", "Распознаю речь заново",
            "Заново распознать речь всего клипа и заменить субтитры (ручные правки текста пропадут). "
            "Нужно, когда get_clip предупреждает, что на кусках с речью субтитров нет. После — auto_edit. "
            "/ Re-transcribe the clip.",
            _obj({}, []),
            h_generate_subtitles, mutating=True, label=lambda a: "Распознаю речь клипа заново",
        ),
        Tool(
            "suggest_cuts", "Ищу паузы и паразиты",
            "Найти паузы и слова-паразиты по словам клипа (ничего не меняет). mode: pauses|fillers|both. "
            "Длинные паузы помечены risky — в реакциях это часто сам момент, не вырезай их вслепую. Шкала clip: "
            "cuts можно передать в apply_cuts как есть. / Suggest jump cuts.",
            _obj({"mode": {"type": "string"}, "min_pause": {"type": "number"}, "keep_pad": {"type": "number"}}),
            h_suggest_cuts, label=lambda a: "Ищу паузы и слова-паразиты",
        ),
        Tool(
            "apply_cuts", "Вырезаю паузы",
            "Сохранить вырезы [{start,end}] — шкала clip (секунды клипа ДО вырезов, с наплывами, как слои, "
            "субтитры и cuts в get_clip); [] — вернуть всё. piece — номер куска, если вырез в зоне наплыва "
            "перехода (там время принадлежит двум кускам; get_clip и suggest_cuts его дают). / Save jump cuts.",
            _obj({"cuts": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "piece": {"type": "integer", "description": "чей вырез в зоне наплыва"}}}}}, ["cuts"]),
            h_apply_cuts, mutating=True, label=lambda a: f"Вырезаю {len(a.get('cuts') or [])} кусочков",
        ),
        Tool(
            "auto_edit", "Автомонтаж",
            "Без ИИ за секунду: хук-титр, анимированные субтитры с эмодзи, наезды и удары по речи. "
            "Хорошая основа — дальше правь слои. style — стиль субтитров. / Rule-based auto edit.",
            _obj({"style": {"type": "string"}, "keep_layers": {"type": "boolean"}}),
            h_auto_edit, mutating=True, label=lambda a: "Делаю автомонтаж",
        ),
        Tool(
            "compose_cover", "Рисую обложку",
            "Обложка: кадр клипа в момент t + крупный заголовок (2–4 слова), style yellow|fire|white|plate; "
            "включается сразу и вшивается в первые кадры. t — шкала clip (как view_frame); final=true — секунды "
            "готового ролика. / Compose a cover with a title.",
            _obj({"t": {"type": "number"}, "title": {"type": "string"}, "emoji": {"type": "string"},
                  "style": {"type": "string"}, "y": {"type": "number"},
                  "final": {"type": "boolean", "description": "t — секунды готового ролика"}}, ["t", "title"]),
            h_compose_cover, mutating=True, label=lambda a: f"Обложка «{(a.get('title') or '')[:30]}»",
        ),
        Tool(
            "make_compilation", "Собираю компиляцию",
            "Новый клип «ТОП-N» из клипов одного проекта: номера обратным отсчётом, названия, субтитры, whoosh. "
            "clip_ids — от худшего к лучшему не важно, countdown ставит лучший (первый в списке) последним. / Compilation.",
            _obj({"clip_ids": {"type": "array", "items": {"type": "integer"}}, "title": {"type": "string"},
                  "countdown": {"type": "boolean"}}, ["clip_ids"]),
            h_make_compilation, mutating=True, scope="global", label=lambda a: "Собираю компиляцию",
        ),
        Tool(
            "set_transition", "Ставлю переход",
            "Переход между кусками: type (cut, whip, zoompunch, glitch, flash, fade, fadeblack, …), duration, "
            "sfx (none/whoosh/click/pop/boom), audio (smooth/hard), smart. Наплыв занимает последние duration "
            "секунд куска и первые следующего: если там речь — warnings скажут, какие слова уйдут под переход "
            "(fadeblack — речь на чёрном) и как поправить; fit_speech=true — сам укоротит переход, чтобы он не "
            "заходил на речь. Наплыв сдвигает шкалу clip — слои, камера, вставки переезжают сами. "
            "/ Transition between pieces.",
            _obj({"type": {"type": "string"}, "duration": {"type": "number"}, "sfx": {"type": "string"},
                  "sfx_volume": {"type": "number"}, "audio": {"type": "string"}, "smart": {"type": "boolean"},
                  "fit_speech": {"type": "boolean", "description": "укоротить переход, чтобы не задевал слова"}}),
            h_set_transition, mutating=True, label=lambda a: f"Переход {a.get('type') or ''} {a.get('sfx') or ''}".strip(),
        ),
        Tool(
            "trim_piece", "Подрезаю кусок",
            "Подрезать кусок index: новые start_sec/end_sec во времени ИСХОДНИКА (5..180 с; те же имена, что "
            "get_clip.pieces[].start_sec/end_sec). Слои, камера, вставки, вырезы, тишина и субтитры едут за "
            "куском сами. / Trim a piece (source seconds).",
            _obj({"index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"}},
                 ["index"]),
            h_trim_piece, mutating=True, label=lambda a: f"Подрезаю кусок {a.get('index')}",
        ),
        Tool(
            "auto_focus", "Ищу говорящего в кадре",
            "Кадрирование 9:16 автоматически: где лица/движение на каждом плане куска (piece — один кусок, "
            "без него — все), с жёсткой сменой на склейках. По умолчанию Gemini смотрит кадр каждого плана "
            "(use_vlm:false — только лица/движение, быстрее). Длинный план с диалогом делит по репликам субтитров "
            "(пауза или смена голоса): кадр на реплику, плавный поворот окна к ответившему (turns в ответе) — "
            "поэтому сначала субтитры. Быстрее, чем подбирать x вручную; потом проверь contact_sheet и "
            "поправь промахи set_focus. / Automatic 9:16 framing, per reply inside long dialogue shots.",
            _obj({"piece": {"type": "integer"}, "use_vlm": {"type": "boolean"}}),
            h_auto_focus, mutating=True, label=lambda a: "Ищу говорящего в кадре",
        ),
        Tool(
            "set_focus", "Кадрирую 9:16",
            "Кадрирование куска piece в 9:16: points [{t, x, cut?}] — центр окна x(t) внутри куска (t — секунды "
            "от начала куска, или source_t — секунды исходника прямо из get_shots; x 0..1 — доля ширины "
            "исходника; cut:true — жёсткий переход в этой точке, ставь ровно на склейку плана). Между точками "
            "без cut окно плавно ведётся; одна точка — неподвижный кадр; [] — по центру. Заменяет трек куска "
            "целиком. Текущий трек — get_clip.pieces[].focus, доля ширины окна — get_clip.framing.window_w. "
            "Говорящий в кадре: get_shots → source_contact_sheet(x=…) по планам → set_focus с cut на склейках. "
            "/ 9:16 reframe track of a piece.",
            _obj({"piece": {"type": "integer", "description": "номер куска с 0"},
                  "points": {"type": "array", "items": _obj({
                      "t": {"type": "number", "description": "секунды от начала куска"},
                      "source_t": {"type": "number", "description": "вместо t: секунды исходника"},
                      "x": {"type": "number", "description": "центр окна, доля ширины исходника 0..1"},
                      "cut": {"type": "boolean", "description": "жёсткая смена кадра в этой точке (склейка)"}},
                      ["x"])}}, ["piece", "points"]),
            h_set_focus, mutating=True,
            label=lambda a: f"Кадрирую кусок {a.get('piece')}: {len(a.get('points') or [])} точ.",
        ),
        Tool(
            "set_piece_fit", "Кадр шире окна",
            "Кусок piece шире окна 9:16 — когда двое по краям широкого плана не влезают в окно (камера только "
            "приближает, zoom ≥ 1): w — доля ширины исходника в кадре (0.35..1; 1 — весь кадр, ≈0.6 — двое "
            "крупнее), bg — фон сверху и снизу: blur (размытый тот же кадр, по умолчанию) или анимированный фон "
            "(имя стиля aurora|grid|bokeh|waves или объект {kind, colors, speed, intensity} как у set_backdrop); "
            "black/#rrggbb (заливка) — только если владелец просил. "
            "Центр — тот же focus куска. w null — обычный кадр. Раскладка клипа (split/pip/meme) на этот кусок "
            "не действует. Часто лучше фокус на говорящего с cut на склейках (set_focus) — fit для общего плана. "
            "/ Show a piece wider than the 9:16 window.",
            _obj({"piece": {"type": "integer"}, "w": {"type": ["number", "null"]},
                  "bg": {"type": ["string", "object"],
                         "description": "blur | aurora | grid | bokeh | waves | {kind, colors, speed, intensity} | black | #rrggbb"}},
                 ["piece"]),
            h_set_piece_fit, mutating=True,
            label=lambda a: f"Кусок {a.get('piece')} шире окна" if a.get("w") else f"Кусок {a.get('piece')}: обычный кадр",
        ),
        Tool(
            "add_insert", "Вставляю мем",
            "Вставка из библиотеки поверх клипа (видео-мем/картинка/звук): asset_id, at (с клипа), duration, "
            "mode pip|full|sound, x, y, scale, volume. Для статичных картинок лучше слой image. Вставки "
            "видны в view_frame и contact_sheet (первые кадры гифки). Окошко само встаёт между хуком и "
            "субтитрами. / Insert.",
            _obj({"asset_id": {"type": "integer"}, "at": {"type": "number"}, "duration": {"type": "number"},
                  "mode": {"type": "string", "enum": ["pip", "full", "sound"]}, "x": {"type": "number"},
                  "y": {"type": "number"}, "scale": {"type": "number"}, "volume": {"type": "number"},
                  "reason": {"type": "string"},
                  "at_word": _AT_WORD_SCHEMA,
                  "word_offset": {"type": "number"}}, ["asset_id"]),
            h_add_insert, mutating=True,
            label=lambda a: (f"Вставляю мем #{a.get('asset_id')} на слово" if a.get("at_word") is not None
                             else f"Вставляю мем #{a.get('asset_id')} на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "remove_insert", "Убираю вставку",
            "Убрать вставку по index (из get_clip). / Remove insert.",
            _obj({"index": {"type": "integer"}}, ["index"]), h_remove_insert, mutating=True,
            label=lambda a: f"Убираю вставку {a.get('index')}",
        ),
    ]


_REGISTRY: dict[str, Tool] | None = None

# Только для внешних агентов (MCP): найти проект и клип.
DISCOVERY_TOOLS = [
    Tool(
        "list_projects", "Проекты",
        "Проекты (исходные видео) с числом клипов. / Projects (source videos).",
        _obj({}), h_list_projects, scope="global",
    ),
    Tool(
        "list_clips", "Клипы проекта",
        "Клипы проекта: clip_id, название, ★, длина, число слоёв. / Clips of a project.",
        _obj({"project_id": {"type": "integer"}, "favorites_only": {"type": "boolean"}}, ["project_id"]),
        h_list_clips, scope="global",
    ),
    Tool(
        "get_source_transcript", "Расшифровка проекта",
        "Реплики ВСЕГО исходника проекта [{start, end, text}] (время исходника) — выбрать моменты "
        "для клипов без анализа Gemini. Первый раз распознаёт локальный Whisper ФОНОМ (минуты): вызов "
        "ждёт не дольше ~45 с и, если не готово, отвечает status \"running\" — «распознаю: готово N из M "
        "мин, повтори через ~K с» (retry_after_sec); зови тот же вызов ещё раз, второе распознавание не "
        "запустится. Готово — status \"done\" и реплики из кэша. "
        "Звук без речи (игра) — пустой список и no_speech: ищи моменты source_loudness. "
        "/ Whole-source transcript.",
        _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}},
             ["project_id"]),
        h_source_transcript, scope="global",
    ),
    Tool(
        "get_shots", "Склейки планов",
        "Моменты смены плана в исходнике (секунды исходника) между from_sec и to_sec — чтобы ставить "
        "жёсткую смену кадрирования ровно на склейку. Первый вызов — ffmpeg по всему видео (минута-две), "
        "потом кэш. / Shot boundaries of the source.",
        _obj({"project_id": {"type": "integer"}, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}},
             ["project_id"]),
        h_source_shots, scope="global",
    ),
    Tool(
        "create_clip", "Создаю клип",
        "Новый клип проекта из кусков исходника: title, pieces [{start_sec, end_sec, title?}] "
        "(каждый 5–180 с, до 12; source_start/source_end — тоже можно). Клип сразу с пресетом рендера по "
        "умолчанию (preset_id числом). Вернёт clip_id — дальше обычные инструменты клипа. / Create a clip from timecodes.",
        _obj({"project_id": {"type": "integer"}, "title": {"type": "string"}, "description": {"type": "string"},
              "pieces": {"type": "array", "items": _obj({"start_sec": {"type": "number"}, "end_sec": {"type": "number"},
                                                          "title": {"type": "string"}}, ["start_sec", "end_sec"])}},
             ["project_id", "title", "pieces"]),
        h_create_clip, scope="global",
    ),
]


def registry() -> dict[str, Tool]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = {tool.name: tool for tool in _build_registry()}
    return _REGISTRY


def list_tools(include_discovery: bool = False) -> list[Tool]:
    tools = list(registry().values())
    return (DISCOVERY_TOOLS + tools) if include_discovery else tools


def get_tool(name: str) -> Tool:
    tool = registry().get(name) or next((t for t in DISCOVERY_TOOLS if t.name == name), None)
    if tool is None:
        raise KeyError(name)
    return tool


def run_tool(ctx: ToolContext, name: str, args: dict | None) -> tuple[Any, bool]:
    """Выполнить инструмент. Возвращает (результат, ok). Никогда не бросает:
    ошибка — это результат ``{ok: false, error, problems}``, который увидит модель."""
    try:
        tool = get_tool(name)
    except KeyError:
        return {"ok": False, "error": f"нет инструмента {name}"}, False
    args = dict(args or {})
    if "project_id" not in args and getattr(ctx, "clip_plan_id", None) and _takes_project(tool):
        # Монтажёр работает внутри клипа: проект — источник этого клипа (Gemini спотыкался
        # о «нужен project_id» на get_shots).
        try:
            args["project_id"] = int(ctx.store.get_clip_plan(ctx.clip_plan_id, include_segments=False)["source_id"])
        except (KeyError, TypeError, ValueError):
            pass
    try:
        result = tool.handler(ctx, args)
    except ToolError as exc:
        return {"ok": False, "error": str(exc), "problems": exc.problems}, False
    except KeyError as exc:
        return {"ok": False, "error": f"не найдено: {exc}"}, False
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)[:300]}, False
    except Exception as exc:  # noqa: BLE001 - инструмент не роняет агента
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}, False
    if isinstance(result, dict) and result.get("ok") is False:
        return result, False
    return result, True


def _takes_project(tool: Tool) -> bool:
    return "project_id" in ((tool.parameters or {}).get("properties") or {})


def result_for_json(result: Any) -> Any:
    """Результат без байтов картинки — для журнала шагов и текстовых протоколов."""
    if isinstance(result, ImageResult):
        return {"ok": True, "image": result.mime_type, "bytes": len(result.data), **result.info}
    return result


def image_b64(result: ImageResult) -> str:
    return base64.b64encode(result.data).decode("ascii")


def dumps(value: Any) -> str:
    return json.dumps(result_for_json(value), ensure_ascii=False, default=str)
