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
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app import motion
from app.clip_settings import normalize_render_settings

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
                "source_start": round(src_start, 3),
                "source_end": round(src_end, 3),
                "clip_start": round(start, 3),
                "clip_end": round(start + (src_end - src_start), 3),
                "focus": seg.get("focus") or [],
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


def _layer_box(layer: dict) -> tuple[float, float]:
    """Примерная половина высоты слоя в долях кадра — для проверки безопасных зон."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    scale = _num(layer.get("scale"), 1.0) or 1.0
    kind = layer.get("type")
    height = 0.0
    if kind in ("text", "counter"):
        size = _num(props.get("size"), 80.0) or 80.0
        lines = max(1, str(props.get("text") or "").count("\n") + 1)
        height = size * 1.15 * lines
    elif kind == "emoji":
        height = _num(props.get("size"), 160.0) or 160.0
    elif kind in ("shape", "progress", "chart"):
        height = _num(props.get("h"), 120.0) or 120.0
    elif kind == "callout":
        height = 180.0
    elif kind == "image":
        height = _num(props.get("w"), 400.0) or 400.0
    return height * scale / 1920 / 2, 0.0


def lint_layers(layers: list[dict], duration: float | None = None, only_ids: set[str] | None = None) -> list[str]:
    """Предупреждения ремесла (не ошибки): безопасные зоны, перегруз, мелкий текст.

    Не блокируют правку — модель (или человек) решает сама, но видит, где
    интерфейс площадки закроет титр или экран превратился в кашу.
    """
    warnings: list[str] = []
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
            warnings.append(f"{lid}: текст в верхних 10% кадра (y={y:.2f}) — там шапка площадки")
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        if layer.get("type") == "text":
            size = (_num(props.get("size"), 80.0) or 80.0) * (_num(layer.get("scale"), 1.0) or 1.0)
            if size < 44:
                warnings.append(f"{lid}: шрифт {size:.0f}px мелковат для телефона (лучше ≥ 56)")
            if len(str(props.get("text") or "")) > 60:
                warnings.append(f"{lid}: длинный титр — на экране читают 3–6 слов")
        start, end = _num(layer.get("start"), 0.0) or 0.0, _num(layer.get("end"), 0.0) or 0.0
        if duration and end > duration + 0.05:
            warnings.append(f"{lid}: заканчивается на {end:.2f} с, а клип длится {duration:.2f} с")
        if textual and end - start < 0.6:
            warnings.append(f"{lid}: титр виден {end - start:.2f} с — не успеют прочитать")
    # Перегруз: сколько видимых слоёв одновременно в момент начала каждого слоя.
    for layer in visible:
        lid = str(layer.get("id") or "?")
        if only_ids is not None and lid not in only_ids:
            continue
        t = (_num(layer.get("start"), 0.0) or 0.0) + 0.05
        together = [
            o for o in visible
            if (_num(o.get("start"), 0.0) or 0.0) <= t < (_num(o.get("end"), 0.0) or 0.0)
            and o.get("type") not in ("shape",)
        ]
        if len(together) > MAX_SIMULTANEOUS:
            warnings.append(f"{lid}: на {t:.1f} с на экране {len(together)} слоя графики — перегруз (≤ {MAX_SIMULTANEOUS})")
    return list(dict.fromkeys(warnings))


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
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    shift = (lambda t: _to_clip_time(pieces, t)) if pieces else (lambda t: round(t, 3))
    words = payload.get("words") or []
    lines = []
    for index, line in enumerate(payload.get("lines") or []):
        start, end = float(line["start"]), float(line["end"])
        inside = [
            [w["word"], round(shift(float(w["start"])), 2), round(shift(float(w["end"])), 2)]
            for w in words
            if float(w["start"]) < end - 0.001 and float(w["end"]) > start + 0.001
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
    return {
        "clip_id": plan["id"],
        "title": plan["title"],
        "description": plan.get("description") or "",
        "source": {"id": source["id"], "name": source.get("original_filename") or "",
                   "width": source.get("width"), "height": source.get("height")},
        "duration": timeline["duration"],
        "time_note": "все времена (слои, камера, субтитры, clip_start) — секунды готового клипа",
        "frame": {"width": 1080, "height": 1920, "safe_zone": {"top": SAFE_TOP, "bottom": SAFE_BOTTOM}},
        "pieces": timeline["pieces"],
        "render": {
            "subs_on": render.get("subs_on"),
            "sub_id": render.get("sub_id"),
            "sub_pos_pct": render.get("sub_pos_pct"),
            "subtitle_zone": _subtitle_zone(render),
            "banner_on": render.get("banner_on"),
            "music_on": render.get("music_on"),
            "mirror": render.get("mirror"),
            "transition": render.get("transition"),
            "cover": (render.get("cover") or {}).get("mode"),
        },
        # Готовые места на кадре — как монтирует владелец: заголовок сверху по центру,
        # субтитры строго по центру под лицом (с учётом раскладки «вебка снизу»).
        "positions": _recommended_positions(ctx, render),
        "layers": layers,
        "camera": _camera_of(render),
        "inserts": inserts,
        "subtitles": _subtitles_view(ctx.store, ctx.plan_id, timeline["pieces"]),
        "warnings": _clip_warnings(ctx, plan, timeline, layers),
    }


def _clip_warnings(ctx: ToolContext, plan: dict, timeline: dict, layers: list) -> list[str]:
    warnings = lint_layers(layers, timeline["duration"]) if layers else []
    gaps = subtitle_gaps(ctx.store, plan, timeline["pieces"])
    if gaps:
        warnings.insert(0, (
            f"На кусках {', '.join(map(str, gaps))} речь есть, а субтитров нет — субтитры устарели. "
            "Сначала generate_subtitles (распознать заново), потом auto_edit."
        ))
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
    # Точная модель: small на стримах выдумывает фразы под чужими голосами и музыкой.
    payload = generate_for_plan(ctx.store, ctx.plan_id, provider="whisper", model=QUALITY_WHISPER_MODEL)
    return {"ok": True, "lines": len(payload.get("lines") or []), "words": len(payload.get("words") or [])}


def _recommended_positions(ctx: ToolContext, render: dict) -> dict:
    """Места для текста с учётом того, где на кадре герой (рамку даёт Gemini-зрение)."""
    from app.auto_edit import HOOK_Y, caption_y_for

    layout = (render.get("layout") or {}).get("mode") or "single"
    places = {"captions_y": caption_y_for(render), "punchline_y": 0.3, "subject": None}
    try:
        from app.ai.subject_box import subject_box, text_positions

        box = subject_box(ctx.store, ctx.plan_id)
        if box:
            places = text_positions(box, caption_y_for(render))
    except Exception:  # noqa: BLE001 - без зрения — привычные места
        pass
    return {
        "hook": {"x": 0.5, "y": HOOK_Y, "note": "заголовок-хук: строго по центру сверху"},
        "captions": {"x": 0.5, "y": places["captions_y"], "note": "субтитры: строго x=0.5 и этот y — свободное от героя место"},
        "punchline_text": {"x": 0.5, "y": places["punchline_y"], "note": "крупное слово-панчлайн: по центру, не на герое"},
        "subject_box": places["subject"],
        "layout": layout,
    }


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


def h_view_frame(ctx: ToolContext, args: dict) -> ImageResult:
    t = _num(args.get("t"), 0.0) or 0.0
    with_layers = args.get("with_layers", True) is not False
    data, mime, info = render_clip_frame(ctx.store, ctx.plan_id, t, with_layers=with_layers)
    return ImageResult(data=data, mime_type=mime, info={"t": round(t, 3), "with_layers": with_layers, **info})


def h_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    from app.motion_render import contact_sheet_jpeg

    n = int(_num(args.get("n"), 8) or 8)
    data = contact_sheet_jpeg(ctx.store, ctx.plan_id, n=n, width=220)
    return ImageResult(data=data, mime_type="image/jpeg", info={"n": n})



# ---------------------------------------------------------------- монтаж «кнопками»


def h_suggest_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import suggest_for_plan

    mode = args.get("mode") if args.get("mode") in ("pauses", "fillers", "both") else "both"
    # Явная проверка на None: keep_pad=0 («тишину у слов не оставлять») — законное значение.
    min_pause = _num(args.get("min_pause"))
    keep_pad = _num(args.get("keep_pad"))
    return suggest_for_plan(ctx.store, ctx.plan_id, mode, 0.45 if min_pause is None else float(min_pause),
                            0.12 if keep_pad is None else float(keep_pad))


def h_apply_cuts(ctx: ToolContext, args: dict) -> dict:
    from app.cuts import check_cuts_for_plan, cuts_total

    plan = ctx.store.get_clip_plan(ctx.plan_id)
    cuts, problems = check_cuts_for_plan(plan, args.get("cuts") or [])
    if problems:
        raise ToolError("вырезы не приняты", problems)
    ctx.before_change()
    settings = dict(_current_render(ctx))
    settings["cuts"] = cuts
    ctx.store.set_clip_plan_render_settings(ctx.plan_id, settings)
    return {"cuts": len(cuts), "saved_sec": round(cuts_total(cuts), 2)}


def h_auto_edit(ctx: ToolContext, args: dict) -> dict:
    from app.auto_edit import apply_auto_edit

    ctx.before_change()
    return apply_auto_edit(ctx.store, ctx.plan_id, style=str(args.get("style") or "hormozi"),
                           keep_layers=args.get("keep_layers", True) is not False)


def h_compose_cover(ctx: ToolContext, args: dict) -> dict:
    from app.cover_compose import compose_cover

    ctx.before_change()
    return compose_cover(ctx.store, ctx.plan_id, float(_num(args.get("t"), 0.0) or 0.0), str(args.get("title") or ""),
                         emoji=str(args.get("emoji") or ""), style=str(args.get("style") or "yellow"),
                         y=float(_num(args.get("y"), 0.2) or 0.2))


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
        if isinstance(props.get(key), str):
            props[key] = _EMOJI_RE.sub("", props[key]).strip()
    if layer.get("type") == "captions":
        props["emoji"] = []
    return {**layer, "props": props}


def _no_emoji_layers(items: list[dict]) -> tuple[list[dict], int]:
    kept = [_strip_emoji(l) for l in items if l.get("type") != "emoji"]
    return kept, len(items) - len(kept)


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
        item = dict(item)
        if not item.get("id") or str(item.get("id")) in taken:
            item["id"] = motion.new_layer_id(taken)
        taken.add(str(item["id"]))
        prepared.append(item)
    prepared, dropped_emoji = _no_emoji_layers(prepared)
    if not prepared:
        raise ToolError("эмодзи в монтаже запрещены владельцем — сделай акцент цветом, наездом, звуком или крупным словом")
    problems = _validate_new_layers(ctx, prepared, duration)
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
        "warnings": lint_layers(layers, duration, only_ids=new_ids)
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
    merged = _strip_emoji(_deep_merge(layers[index], patch))
    problems = _validate_new_layers(ctx, [merged], duration)
    if problems:
        raise ToolError("правка не принята — слой остался прежним", problems)
    normalized = motion.normalize_layers([merged])
    layers[index] = normalized[0]
    fresh = _save_render(ctx, {**render, "layers": layers})
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), normalized[0])
    return {"ok": True, "layer": saved, "warnings": lint_layers(_layers_of(fresh), duration, only_ids={layer_id})}


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
    return {"ok": True, "camera": camera, "dropped": len(keys) - len(camera), "warnings": warnings}


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
        packs = [p["id"] for p in assets.list_packs() if p.get("kind") != "emoji" and (not kind or p.get("kind") == kind)]
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
    return {"ok": True, "lines": len(saved.get("lines") or [])}


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
    fresh = _save_render(ctx, {**render, "transition": transition})
    return {"ok": True, "transition": fresh["transition"]}


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
    start = _num(args.get("start_sec"), piece["start_sec"])
    end = _num(args.get("end_sec"), piece["end_sec"])
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
    return {"ok": True, "changes": changes, "duration": timeline["duration"],
            "note": "время клипа сдвинулось — проверь start/end слоёв после этого куска"}


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
    entry = {k: args[k] for k in ("at", "duration", "x", "y", "scale", "volume", "duck", "reason") if args.get(k) is not None}
    entry.update(asset_id=asset["id"], mode=mode)
    note = _fit_pip_insert(render, entry, asset) if mode == "pip" else None
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
                note = _fit_pip_insert(render, entry, store.get_montage_asset(int(entry["asset_id"])))
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


def _fit_pip_insert(render: dict, entry: dict, asset: dict) -> str | None:
    """Окошко мема не должно лечь на субтитры и хук: ставим его над субтитрами
    и, если не влезает, уменьшаем. Вставки ложатся поверх субтитров при рендере,
    а агент их в view_frame не видит — поэтому подгоняем здесь."""
    from app.montage_assets import MIN_INSERT_SCALE, PIP_WIDTH

    from app.cuts import PINNED_START_SEC, CutMap, normalize_cuts

    # Сравниваем во времени ГОТОВОГО клипа: вырезанное начало сдвигает мем к хуку.
    mapper = CutMap(normalize_cuts(render.get("cuts")))
    to_final = mapper.clamp if mapper else (lambda t: t)
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
        entry["at"] = round(mapper.source_time(at), 2) if mapper else at
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
             "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count")}
            for s in ctx.store.list_sources()
        ]
    }


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
Слой: {{id?, type, name?, start, end, track 0..7 (выше = поверх), x, y, scale, rotation, opacity,
  keys {{prop: [{{t (от start слоя), v, ease?}}]}} — prop: x,y,scale,rotation,opacity,progress,
  in {{type, duration}}, out {{type, duration}}, loop {{type, speed 0.1..5, amount 0..3}},
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
Камера: [{{t (время клипа), zoom 1..4, x, y, ease?, shake? 0..1}}] — двигает только видео.
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


def _layer_schema() -> dict:
    enums = motion_enums()
    anim = _obj({"type": {"type": "string", "enum": enums["anims"]}, "duration": {"type": "number"}})
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
            "x": {"type": "number", "description": "центр по ширине, доля кадра"},
            "y": {"type": "number", "description": "центр по высоте, доля кадра (безопасно 0.12..0.75)"},
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
        },
        ["type", "start", "end"],
    )


# --------------------------------------------------------------------------- #
# Реестр
# --------------------------------------------------------------------------- #


def _build_registry() -> list[Tool]:
    layer = _layer_schema()
    return [
        Tool(
            "get_clip", "Смотрю клип",
            "Весь клип: куски (время исходника и клипа), настройки, слои, камера, вставки, субтитры с "
            "временем слов (в секундах клипа) и предупреждения. Зови первым. / Full clip state incl. "
            "subtitle word timings in clip seconds.",
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
            "Картинка клипа в момент t (секунды клипа) — как будет в готовом видео, со слоями и камерой. "
            "Проверяй так размещение, читаемость, что титр не закрыл лицо. / Render the clip frame at t.",
            _obj({"t": {"type": "number"}, "with_layers": {"type": "boolean"}}, ["t"]), h_view_frame,
            label=lambda a: f"Смотрю кадр на {(_num(a.get('t'), 0.0) or 0.0):.1f} с",
        ),
        Tool(
            "contact_sheet", "Смотрю весь клип",
            "Сетка из n кадров равномерно по клипу (с временем) — весь монтаж одним взглядом: "
            "проверить ритм графики, не перегружен ли кадр, где пусто. / Contact sheet of n frames.",
            _obj({"n": {"type": "integer", "minimum": 2, "maximum": 16}}), h_contact_sheet,
            label=lambda a: "Смотрю весь клип сеткой кадров",
        ),
        Tool(
            "get_motion_reference", "Справка по слоям",
            "Шпаргалка по моушн-слоям: типы, props, анимации, easing, камера. / Motion layer cheat sheet.",
            _obj({}), h_get_motion_reference, scope="global",
        ),
        Tool(
            "add_layers", "Добавляю слои",
            "Добавить моушн-слои (титры, эмодзи, картинки, фигуры, счётчики, графики, плашки, звуки). "
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
            "Панч-ин на панчлайн: 1.0 → 1.15–1.35 за 0.15–0.3 с, держать, вернуть. / Replace camera keys.",
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
            "Найти картинку в интернете (Google). Ничего не скачивает — только кандидаты. free_only=true — "
            "только свободные лицензии. / Web image search.",
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
            "Субтитры: on (вкл/выкл), style_id (см. list_subtitle_styles), position_pct (отступ снизу, 2..40 % "
            "высоты; 12–22 — над интерфейсом площадки). / Subtitle settings.",
            _obj({"on": {"type": "boolean"}, "style_id": {"type": "integer"}, "position_pct": {"type": "number"}}),
            h_set_subtitles, mutating=True,
            label=lambda a: "Субтитры " + ("выкл" if a.get("on") is False else "вкл")
            + (f", стиль {a['style_id']}" if a.get("style_id") else "")
            + (f", {a['position_pct']}% снизу" if a.get("position_pct") is not None else ""),
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
            "Длинные паузы помечены risky — в реакциях это часто сам момент, не вырезай их вслепую. / Suggest jump cuts.",
            _obj({"mode": {"type": "string"}, "min_pause": {"type": "number"}, "keep_pad": {"type": "number"}}),
            h_suggest_cuts, label=lambda a: "Ищу паузы и слова-паразиты",
        ),
        Tool(
            "apply_cuts", "Вырезаю паузы",
            "Сохранить вырезы [{start,end}] в секундах клипа ДО вырезов; [] — вернуть всё. / Save jump cuts.",
            _obj({"cuts": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"}}}}}, ["cuts"]),
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
            "включается сразу и вшивается в первые кадры. / Compose a cover with a title.",
            _obj({"t": {"type": "number"}, "title": {"type": "string"}, "emoji": {"type": "string"},
                  "style": {"type": "string"}, "y": {"type": "number"}}, ["t", "title"]),
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
            "sfx (none/whoosh/click/pop/boom), audio (smooth/hard), smart. / Transition between pieces.",
            _obj({"type": {"type": "string"}, "duration": {"type": "number"}, "sfx": {"type": "string"},
                  "sfx_volume": {"type": "number"}, "audio": {"type": "string"}, "smart": {"type": "boolean"}}),
            h_set_transition, mutating=True, label=lambda a: f"Переход {a.get('type') or ''} {a.get('sfx') or ''}".strip(),
        ),
        Tool(
            "trim_piece", "Подрезаю кусок",
            "Подрезать кусок index: новые start_sec/end_sec во времени ИСХОДНИКА (5..180 с). Сдвигает время "
            "клипа — после этого поправь слои. / Trim a piece (source seconds).",
            _obj({"index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"}},
                 ["index"]),
            h_trim_piece, mutating=True, label=lambda a: f"Подрезаю кусок {a.get('index')}",
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
                  "reason": {"type": "string"}}, ["asset_id", "at"]),
            h_add_insert, mutating=True,
            label=lambda a: f"Вставляю мем #{a.get('asset_id')} на {(_num(a.get('at'), 0.0) or 0.0):.1f} с",
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
    try:
        result = tool.handler(ctx, dict(args or {}))
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


def result_for_json(result: Any) -> Any:
    """Результат без байтов картинки — для журнала шагов и текстовых протоколов."""
    if isinstance(result, ImageResult):
        return {"ok": True, "image": result.mime_type, "bytes": len(result.data), **result.info}
    return result


def image_b64(result: ImageResult) -> str:
    return base64.b64encode(result.data).decode("ascii")


def dumps(value: Any) -> str:
    return json.dumps(result_for_json(value), ensure_ascii=False, default=str)
