"""Карточки стилей ролика: «стиль» — одна карточка, по которой ролик собирается в одном виде.

Схема карточки — идея из разбора lemo-opuscar (docs/research/video-repos-2026-11-17.md); их тексты стилей под
CC BY-NC, поэтому формулировки и сами стили здесь свои. Разделы карточки (``card``): суть и чем стиль НЕ является,
материалы (палитра, шрифты, фактура), движение и камера, звуковая палитра (только записи пака freesound —
``sfx:freesound/…``), родные приёмы (наши шаблоны, переходы и входы — с тем, к какому содержанию они подходят), грабли,
что можно варьировать.

Применение (``apply``) — то, что карточка ставит клипу одним вызовом (``apply_style``, кнопка в редакторе):
образ рендера и стиль субтитров дорожкой (из стартового пака, ``app/default_pack.py``), вид слоёв субтитров, переход
на склейках, фон холста или за персонажем (анимированный, свой — app/backgrounds.py), эффект на весь ролик (две
краски, растр — слой ``style_fx``) и предпочтения: входы новых текстов (``add_layers`` берёт их, если вход не задан),
шаблоны и тема плоских шаблонов. Отметка ``render_settings.style`` хранит id и версию карточки. Повтор не плодит
дублей. Проект: ``project_default`` — стиль проекта по умолчанию (подсказка агентам и новым клипам).

Данные — ``app/style_cards.json`` (его же читает ``tools/style_samples.mjs`` для образцов).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

CARDS_PATH = Path(__file__).with_name("style_cards.json")
FX_LAYER_ID = "style_fx"
PROJECT_KEY = "project_style:{pid}"
CARD_SECTIONS = ("essence", "not", "materials", "motion", "camera", "sound", "native", "pitfalls", "vary")


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))


def styles() -> list[dict]:
    return list(_load()["styles"])


def get_style(style_id: str) -> dict:
    key = str(style_id or "").strip().lower()
    for card in styles():
        if card["id"] == key or card["name"].lower() == key or card["short"].lower() == key:
            return card
    raise KeyError(f"стиля «{style_id}» нет; есть: {', '.join(c['id'] for c in styles())}")


def brief(card: dict) -> dict:
    return {"id": card["id"], "name": card["name"], "short": card["short"], "version": card["version"],
            "essence": card["card"]["essence"]}


def plan_project(store: Any, clip_plan_id: int) -> int | None:
    from app.projects import project_of_plan

    try:
        return project_of_plan(store, int(clip_plan_id))
    except Exception:  # noqa: BLE001 - без проекта — без стиля проекта
        return None


def project_style(store: Any, project_id: int | None) -> str | None:
    if not project_id:
        return None
    try:
        value = store.get_app_setting_value(PROJECT_KEY.format(pid=int(project_id)))
    except Exception:  # noqa: BLE001 - нет настройки — нет стиля
        return None
    return str(value) if value else None


def set_project_style(store: Any, project_id: int, style_id: str | None) -> None:
    if style_id:
        get_style(style_id)
    store.set_app_setting(PROJECT_KEY.format(pid=int(project_id)), style_id or "")


def _label_id(items: list[dict], label: str) -> int | None:
    found = next((i for i in items if i.get("label") == label), None)
    return int(found["id"]) if found else None


def _entrance_kind(layer: dict) -> str | None:
    if layer.get("type") == "text":
        size = float((layer.get("props") or {}).get("size") or 0)
        return "title" if size >= 110 else "text"
    if layer.get("type") == "shape":
        return "shape"
    return None


def styled_entrance(render: dict, layer: dict) -> dict:
    """Новый слой без своего входа — вход по стилю клипа (заголовок, текст, фигура); остальное как прислано."""
    style = render.get("style") if isinstance(render.get("style"), dict) else None
    if not style or layer.get("in") not in (None, {}):
        return layer
    kind = _entrance_kind(layer)
    entrance = (style.get("entrances") or {}).get(kind) if kind else None
    if not entrance:
        return layer
    return {**layer, "in": {"type": entrance, "duration": 0.35 if kind == "title" else 0.3}}


def normalize_style_mark(raw: Any) -> dict | None:
    """Отметка стиля в настройках клипа: ``{id, version, entrances, templates, theme}`` или None."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    try:
        card = get_style(raw["id"])
    except KeyError:
        return None
    try:
        version = int(raw.get("version") or card["version"])
    except (TypeError, ValueError):
        version = int(card["version"])
    ent = raw.get("entrances") if isinstance(raw.get("entrances"), dict) else card["apply"].get("entrances") or {}
    return {"id": card["id"], "version": version,
            "entrances": {k: str(v) for k, v in ent.items() if k in ("title", "text", "shape") and isinstance(v, str)},
            "templates": [str(t) for t in (raw.get("templates") or card["apply"].get("templates") or [])][:8],
            "theme": str(raw.get("theme") or card["apply"].get("theme") or "")[:12]}


def _caption_props(props: dict, style: dict) -> dict:
    """Перекрасить слой субтитров: вид, шрифт, цвета, обводка, плашка; слова, позиция и голоса — как были."""
    out = {k: v for k, v in (props or {}).items() if k not in ("gradient", "glow", "dimColor", "activeBg", "bg",
                                                               "stroke", "tilt")}
    for key, value in style.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def apply_style(store: Any, clip_plan_id: int, style_id: str, *, before_change: Callable[[], None] | None = None,
                project_default: bool = False) -> dict:
    """Оформить клип по карточке стиля. Повтор даёт тот же результат (без дублей). Ошибки — KeyError/ValueError."""
    from app import motion
    from app.chroma_key import normalize_backdrop
    from app.tool_core import clip_timeline
    from app.transitions import normalize_transition

    card = get_style(style_id)
    spec = card["apply"]
    timeline = clip_timeline(store, clip_plan_id)
    plan, render, duration = timeline["plan"], dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    changes: list[str] = []
    warnings: list[str] = []

    look = _label_id(store.list_ffmpeg_presets(), spec["render_look"])
    if look:
        render["preset_id"] = look
        changes.append(f"образ «{spec['render_look']}»")
    else:
        warnings.append(f"образа «{spec['render_look']}» нет в библиотеке (стартовый пак) — образ не тронут")
    sub = _label_id(store.list_subtitle_profiles(), spec["subtitle_profile"])
    if sub:
        render["sub_id"] = sub
        changes.append(f"стиль субтитров дорожкой «{spec['subtitle_profile']}»")

    layers = [dict(l) for l in render.get("layers") or [] if l.get("id") != FX_LAYER_ID]
    caps = [l for l in layers if l.get("type") == "captions"]
    for layer in caps:
        layer["props"] = _caption_props(layer.get("props") or {}, spec["captions"])
        if spec.get("caption_track") is not None:
            layer["track"] = int(spec["caption_track"])
    if caps:
        changes.append(f"субтитры-слой: {spec['captions'].get('font')}, {spec['captions'].get('style')}")

    fx = spec.get("fx")
    if fx:
        layer = {"id": FX_LAYER_ID, "type": "fx", "name": f"Стиль: {card['short']}", "start": 0.0,
                 "end": round(duration, 3), "track": int(fx.get("track", 7)),
                 "props": {k: v for k, v in fx.items() if k != "track"}}
        problems = motion.validate_layers([layer], duration)
        if problems:
            raise ValueError("эффект стиля не прошёл проверку: " + "; ".join(problems))
        layers.append(layer)
        changes.append(f"эффект на весь ролик: {fx['kind']}")

    transition = normalize_transition({**(render.get("transition") or {}), **spec["transition"]})
    render["transition"] = transition
    changes.append(f"переход на склейках: {transition['type']}")

    source = store.get_source(int(plan["source_id"]))
    keyed = bool(source.get("chroma_key"))
    if spec.get("backdrop"):
        render["backdrop"] = normalize_backdrop(spec["backdrop"])
        if keyed:
            changes.append(f"фон: {render['backdrop']['kind']}")
        layout = render.get("layout")
        if isinstance(layout, dict) and layout.get("mode") == "meme":
            render["layout"] = {**layout, "bg": render["backdrop"]}
            changes.append("фон под видео")

    render["layers"] = layers
    render["style"] = normalize_style_mark({"id": card["id"], "version": card["version"]})
    if before_change is not None:
        before_change()
    else:
        from app.clip_history import snapshot

        snapshot(store, clip_plan_id, f"стиль «{card['short']}»")
    store.set_clip_plan_render_settings(clip_plan_id, render)
    out: dict = {"ok": True, "style": brief(card), "changes": changes, "warnings": warnings,
                 "prefer": {"entrances": spec.get("entrances"), "templates": spec.get("templates"),
                            "theme": spec.get("theme"), "native": card["card"]["native"],
                            "sound": card["card"]["sound"]},
                 "next": "новые тексты без in получат вход стиля; шаблоны — из prefer.templates (theme "
                         f"«{spec.get('theme')}»); проверь кадр: view_frame / contact_sheet"}
    if project_default:
        project_id = plan_project(store, clip_plan_id)
        if project_id:
            set_project_style(store, int(project_id), card["id"])
            out["project_default"] = int(project_id)
        else:
            out["warnings"].append("клип вне проекта — стиль проекта не записан")
    return out


# --------------------------------------------------------------------------- #
# Инструменты агента
# --------------------------------------------------------------------------- #


def tools() -> list:
    from app.tool_core import _obj, Tool, ToolError

    def h_list(ctx: Any, args: dict) -> dict:
        sid = str(args.get("id") or "").strip()
        if sid:
            try:
                card = get_style(sid)
            except KeyError as exc:
                raise ToolError(str(exc)) from exc
            return {"style": {**brief(card), "card": card["card"], "apply": card["apply"]}}
        out: dict = {"styles": [brief(c) for c in styles()],
                     "next": "list_styles(id) — карточка целиком; apply_style(clip_id, style) — оформить клип"}
        clip_id = getattr(ctx, "clip_plan_id", None) or args.get("clip_id")
        if clip_id:
            try:
                plan = ctx.store.get_clip_plan(int(clip_id), include_segments=False)
                out["clip_style"] = (plan.get("render_settings") or {}).get("style")
                out["project_style"] = project_style(ctx.store, plan_project(ctx.store, int(clip_id)))
            except (KeyError, TypeError, ValueError):
                pass
        return out

    def h_apply(ctx: Any, args: dict) -> dict:
        sid = str(args.get("style") or "").strip()
        if not sid:
            sid = project_style(ctx.store, plan_project(ctx.store, ctx.plan_id)) or ""
        if not sid:
            raise ToolError("нужен style — id из list_styles (у проекта клипа своего стиля нет)")
        try:
            return apply_style(ctx.store, ctx.plan_id, sid, before_change=ctx.before_change,
                               project_default=bool(args.get("project_default")))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return [
        Tool(
            "list_styles", "Смотрю стили ролика",
            "Карточки стилей ролика (одна карточка — один вид): «новостные крупные титры поверх видео», «маркер на "
            "бумаге», «рисограф», «1-бит», «чертёж». Без id — список с сутью (и стиль клипа/проекта, если дан "
            "clip_id); id — карточка целиком: суть и чем НЕ является, материалы, движение и камера, звуки пака, "
            "родные приёмы (шаблоны, переходы, входы — к какому содержанию), грабли, что варьировать, и что ставит "
            "apply_style. / List video style cards.",
            _obj({"id": {"type": "string", "description": "id стиля: news, marker, riso, onebit, blueprint"},
                  "clip_id": {"type": "integer"}}),
            h_list, scope="global",
            label=lambda a: f"Смотрю стиль {a.get('id')}" if a.get("id") else "Смотрю стили ролика",
        ),
        Tool(
            "apply_style", "Оформляю клип стилем",
            "Оформить клип по карточке стиля одним вызовом: образ рендера, стиль субтитров (дорожкой и слоем), переход "
            "на склейках, анимированный фон холста/за персонажем, эффект на весь ролик (две краски, растр), входы "
            "новых текстов и шаблоны-предпочтения (ответ prefer). Повтор не плодит дублей, Ctrl+Z откатит. "
            "style — id из list_styles (нет — стиль проекта клипа); project_default — сделать стилем проекта. "
            "Делай в начале монтажа, потом проверь кадр. / Apply a video style card to the clip.",
            _obj({"style": {"type": "string", "description": "id стиля из list_styles"},
                  "project_default": {"type": "boolean", "description": "запомнить стилем проекта клипа"}}),
            h_apply, mutating=True,
            label=lambda a: f"Оформляю клип стилем {a.get('style') or 'проекта'}",
        ),
    ]
