"""Общее ядро инструментов ИИ-монтажёра: типы (Tool, ToolContext, ToolError, ImageResult) и помощники,
которыми пользуются все домены: шкала клипа, настройки рендера, кто владелец файла, разбор source_id.

Импорт только в эту сторону: домены (``app/*_tools.py``) берут отсюда, отсюда — никого из них. Реестр и общие правила
инструментов — ``app/agent_tools.py``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app import motion
from app.canvas import is_canvas
from app.clip_settings import normalize_render_settings


class ToolError(Exception):
    """Понятная модели ошибка: сообщение + список конкретных проблем."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


# Имена инструментов, которые принимает ``agent_tools.run_tool`` (реестр, поиск, проектные): реестр ставит
# app/agent_tools при импорте — домены его не импортируют (list_modules сверяет с этим свои списки).
_callable_names: Callable[[], set[str]] | None = None


def set_callable_names(fn: Callable[[], set[str]]) -> None:
    global _callable_names
    _callable_names = fn


def callable_tool_names() -> set[str]:
    """Вызываемые инструменты; пусто — реестр ещё не собран."""
    return set(_callable_names()) if _callable_names is not None else set()


# Инструмент по имени (``agent_tools.get_tool``) — тоже ставит реестр: describe_tool живёт в домене источника.
_tool_lookup: Callable[[str], Any] | None = None


def set_tool_lookup(fn: Callable[[str], Any]) -> None:
    global _tool_lookup
    _tool_lookup = fn


def lookup_tool(name: str) -> Any:
    """Инструмент по имени; нет такого (или реестр не собран) — KeyError."""
    if _tool_lookup is None:
        raise KeyError(name)
    return _tool_lookup(name)


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
    # Запрос владельца этого хода (ИИ-монтажёр) и активный плейбук: инструменты сверяются с ними —
    # слова владельца в кавычках цензура не глушит, автомонтаж при плейбуке не ставит своё (QA 4).
    request: str = ""
    playbook: str | None = None
    # Попытки кадрирования аватара (QA 6, клип 898: 12 set_layout руками без прогресса): неудачи подряд и рамки
    # руками за ход; на пределе — ``framing_stop`` (текст указания), дальше рамки не принимаются.
    framing_fails: int = 0
    framing_manual: int = 0
    framing_stop: str = ""
    # «Файлы для монтажа» (app/asset_scope.py). project_hint — проект без клипа (чат проекта); trusted_assets —
    # сборка сервиса (объяснялка): ставит файлы своего ролика, чужие она не выбирает.
    project_hint: int | None = None
    trusted_assets: bool = False

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
    # Чем дорог вызов (как у модулей, app/modules.COST_LABELS: cpu | ram | gpu | network | paid); пусто — дёшево.
    # В MCP tools/list — ``_meta["vvf/cost"]``.
    cost: tuple[str, ...] = ()

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
            **({"cost": list(self.cost)} if self.cost else {}),
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
    from app.piece_speed import describe, is_reversed, seg_length, seg_speed, spans as speed_spans

    spans = speed_spans(segments)
    starts, total = motion.clip_timeline(spans, render.get("transition"))
    pieces = []
    for index, (seg, (src_start, src_end, _speed), start) in enumerate(zip(segments, spans, starts)):
        retimed = {}
        if seg_speed(seg) != 1.0 or is_reversed(seg):
            # Скорость/реверс куска (set_segment_speed): длина на шкале clip — length.
            retimed = {"speed": seg_speed(seg), "reverse": is_reversed(seg), "speed_audio": seg.get("speed_audio"),
                       "speed_note": describe(seg)}
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
                "clip_end": round(start + seg_length(seg), 3),
                "length": round(seg_length(seg), 3),
                "focus": seg.get("focus") or [],
                "fit": seg.get("fit"),
                **retimed,
                # Цветокоррекция куска (set_piece_color / match_piece_colors, app/piece_color.py).
                **({"color_fix": seg["color_fix"]} if seg.get("color_fix") else {}),
            }
        )
    return {"plan": plan, "render": render, "pieces": pieces, "duration": round(total, 3),
            "plain_duration": round(sum(seg_length(s) for s in segments), 3)}


def _to_clip_time(pieces: list[dict], plain_t: float) -> float:
    """Время «куски встык» (так хранятся субтитры) → время готового клипа."""
    cursor = 0.0
    for piece in pieces:
        length = _piece_len(piece)
        if plain_t < cursor + length or piece is pieces[-1]:
            return round(piece["clip_start"] + (plain_t - cursor), 3)
        cursor += length
    return round(plain_t, 3)


def _piece_len(piece: dict) -> float:
    """Длина куска clip_timeline на шкале клипа (со скоростью куска)."""
    if piece.get("length") is not None:
        return float(piece["length"])
    return float(piece["source_end"]) - float(piece["source_start"])


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


def _source_canvas(ctx: ToolContext) -> bool:
    """Клип — пустой холст (app/canvas.py): видео нет, весь кадр — фон."""
    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    return is_canvas(ctx.store.get_source(int(plan["source_id"])))


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


def _assets_project(ctx: ToolContext, args: dict | None = None) -> int | None:
    """Чьи файлы видит вызов и куда кладёт новые: проект клипа; без клипа — clip_id или project_id из аргументов,
    проект чата. None — только общие (app/asset_scope.py)."""
    from app.asset_scope import clip_project

    args = args or {}
    clip_id = ctx.clip_plan_id if ctx.clip_plan_id is not None else args.get("clip_id")
    if clip_id not in (None, ""):
        try:
            return clip_project(ctx.store, int(clip_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"клипа {clip_id} нет — см. list_clips") from exc
    if args.get("project_id") not in (None, ""):
        try:
            return int(ctx.store.db.query_one("SELECT id FROM projects WHERE id = ?", (int(args["project_id"]),))["id"])
        except (TypeError, ValueError) as exc:
            raise ToolError(f"проекта-серии {args['project_id']} нет — см. list_channel_projects") from exc
    return ctx.project_hint


def _foreign_asset_problem(ctx: ToolContext, asset_id: Any) -> str | None:
    """Новый файл чужого проекта в этом клипе — текст отказа (что уже стоит в клипе, не проверяем)."""
    from app.asset_scope import new_foreign_assets, refusal

    if ctx.trusted_assets:
        return None
    foreign = new_foreign_assets(ctx.store, ctx.plan_id, [asset_id])
    return refusal(ctx.store, foreign[0], _assets_project(ctx)) if foreign else None


def _short(text: Any, limit: int = 32) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _clip_time_arg(ctx: ToolContext, args: dict, key: str = "t") -> tuple[float, dict]:
    """Время кадра: шкала clip; с ``final: true`` — секунды готового ролика (переводим в clip)."""
    t = _num(args.get(key), 0.0) or 0.0
    if not args.get("final"):
        return t, {"time": "clip"}
    clip_t = _clock(ctx).final_to_clip(t)
    return clip_t, {"time": "clip", "final_t": round(t, 3)}


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


# Инструменты, где project_id исторически значит ИСТОЧНИК (видео из list_projects), а не проект-серию из таблицы projects
# (app/projects.py). Правильное имя у них source_id, project_id оставлен как старое имя для прежних вызовов.
# list_clips отсюда ушёл (12.11): там project_id — проект-серия (холсты лежат в общем скрытом источнике, найти их можно
# только по проекту); id, которого нет среди проектов, но есть среди источников, он по-прежнему читает как источник.
SOURCE_ID_TOOLS = frozenset({
    "get_source_transcript", "get_shots", "create_clip", "source_contact_sheet", "source_loudness",
    "chat_activity", "get_chat", "set_source_audio", "audio_map",
})
SOURCE_ARGS = {
    "source_id": {"type": "integer", "description": "id видео-источника (поле id в list_projects)"},
    "project_id": {"type": "integer", "description": "старое имя source_id: здесь это тоже id видео-источника, "
                                                     "не проект-серия из list_channel_projects"},
}


def _source_arg(args: dict) -> int | None:
    """source_id и его старое имя project_id; оба и разные — отказ: непонятно, о каком видео речь."""
    found = {}
    for key in ("source_id", "project_id"):
        if args.get(key) not in (None, ""):
            try:
                found[key] = int(args[key])
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{key} — число: id видео-источника (см. list_projects)") from exc
    if len(set(found.values())) > 1:
        raise ToolError(f"source_id {found['source_id']} и project_id {found['project_id']} расходятся: здесь project_id — "
                        "старое имя source_id. Дай только source_id (см. list_projects)")
    return next(iter(found.values()), None)


def _source_id(args: dict) -> int:
    source = _source_arg(args)
    if source is None:
        raise ToolError("нужен source_id: id видео-источника (см. list_projects)")
    return source


def _source_or_clip_source(ctx: ToolContext, args: dict) -> int:
    """source_id из аргументов, а у встроенного монтажёра — источник его клипа."""
    if _source_arg(args) is None and ctx.clip_plan_id is not None:
        plan = ctx.store.get_clip_plan(int(ctx.clip_plan_id), include_segments=False)
        return int(plan["source_id"])
    return _source_id(args)


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


# Чей файл (app/asset_scope.py) у глобальных инструментов «Файлов для монтажа»: без клипа в контексте — эти поля;
# нет и их — файл общий.
ASSET_OWNER_PARAMS = {
    "clip_id": {"type": "integer", "description": "клип: файл его проекта"},
    "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects (не источник из list_projects): файл этого проекта"},
}
