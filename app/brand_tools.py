"""Инструменты оформления: бренд-киты, плашка канала, концовка, плейбуки и их значения по умолчанию, обложка,
заготовка промо приложения.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

import re

from app import motion
from app.layer_lint import _lint_scene, lint_layers
from app.layer_tools import _validate_new_layers
from app.sound_tools import _music_warning
from app.tool_core import (
    _apply_piece, _camera_of, _clip_duration, _clip_time_arg, _current_render, _foreign_asset_problem, _layers_of,
    _num, _obj, _save_render, _source_canvas, clip_timeline, Tool, ToolContext, ToolError,
)


def h_compose_cover(ctx: ToolContext, args: dict) -> dict:
    from app.cover_compose import compose_cover

    t, scale = _clip_time_arg(ctx, args)
    ctx.before_change()
    out = compose_cover(ctx.store, ctx.plan_id, t, str(args.get("title") or ""),
                        emoji=str(args.get("emoji") or ""), style=str(args.get("style") or "yellow"),
                        y=float(_num(args.get("y"), 0.2) or 0.2))
    return {**out, "t": round(t, 3), **scale}


_END_HOLD_ASK_RE = re.compile(r"стоп[- ]?кадр|замира|замри|end[_ ]?hold|freeze", re.IGNORECASE)


def nameplate_after_hook(ctx: ToolContext) -> bool:
    """Плашка канала в углу выходит, когда уходит хук. ``set_nameplate`` так и ставит, но если хук добавили
    ПОСЛЕ плашки, она остаётся с 0 с и выглядывает из-под хука (QA 8, клип 901). → поменяли ли что-то."""
    from app.brand_kit import NAMEPLATE_ID, find_hook

    render = _current_render(ctx)
    layers = [dict(l) for l in render.get("layers") or []]
    plate = next((l for l in layers if l.get("id") == NAMEPLATE_ID and not l.get("hidden")), None)
    hook = find_hook([l for l in layers if l.get("id") != NAMEPLATE_ID])
    if plate is None or hook is None or hook.get("hidden"):
        return False
    position = str((plate.get("props") or {}).get("position") or plate.get("position") or "")
    corner = position.startswith("top-") or float(plate.get("y", 0.5)) < 0.2
    start, hook_start, hook_end = (float(plate.get("start") or 0.0), float(hook.get("start") or 0.0),
                                   float(hook.get("end") or 0.0))
    end = float(plate.get("end") or 0.0)
    if not corner or not (hook_start <= start < hook_end - 0.05) or hook_end >= end - 0.5:
        return False
    plate["start"] = round(hook_end, 3)
    _save_render(ctx, {**render, "layers": [plate if l.get("id") == NAMEPLATE_ID else l for l in layers]})
    return True


def apply_playbook_defaults(ctx: ToolContext) -> bool:
    """Умолчания активного плейбука, которых нет у клипа: стоп-кадр в конце (``end_hold_sec``) у vtuber-story — 0.

    QA 5 (клип 897): в конце ролика стоп-кадр 0.5 с (общее умолчание RENDER_END_HOLD_SEC) — на повторе ролик
    «замирает»; в прошлый раз агент сам ставил set_end_hold 0, в этот — нет. Стоп-кадр владелец просил в запросе
    или агент задал явно — не трогаем. → поменяли ли что-то."""
    from app.auto_edit import playbook_rules

    rules = playbook_rules(ctx.playbook) or {}
    if "end_hold_sec" not in rules:
        return False
    if ctx.request and _END_HOLD_ASK_RE.search(ctx.request):
        return False
    render = _current_render(ctx)
    if render.get("end_hold_sec") is not None:
        return False
    _save_render(ctx, {**render, "end_hold_sec": float(rules["end_hold_sec"])})
    return True


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
    plate = False if args.get("remove_nameplate") is True else args.get("nameplate")
    if plate is not None and not isinstance(plate, (dict, bool)):
        raise ToolError("nameplate — объект полей плашки канала или false (снять)")
    try:
        return apply_brand(ctx.store, ctx.plan_id, int(brand_id), before_change=ctx.before_change,
                           nameplate=None if plate is True else plate)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


NAMEPLATE_FIELDS = ("platform", "handle", "style", "accent", "position", "size", "fade", "anim", "live", "outro")


def h_set_nameplate(ctx: ToolContext, args: dict) -> dict:
    """Плашка канала (значок площадки + ник) на весь ролик без остального бренда (app/brand_kit.py)."""
    from app.brand_kit import set_nameplate

    brand_id = args.get("brand_id")
    if brand_id:
        try:
            ctx.store.get_brand(int(brand_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    fields = {k: args[k] for k in NAMEPLATE_FIELDS if k in args}
    try:
        return set_nameplate(ctx.store, ctx.plan_id, fields, brand_id=int(brand_id) if brand_id else None,
                             remove=args.get("remove") is True, before_change=ctx.before_change,
                             corner=args.get("corner") or None, start=_num(args.get("start")),
                             end=_num(args.get("end")))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


END_SCREEN_FIELDS = ("template", "svg_asset_id", "text", "handle", "platform", "accent", "days", "time", "font",
                     "duration", "blur", "dim", "ramp", "y", "w")


def h_set_end_screen(ctx: ToolContext, args: dict) -> dict:
    """Концовка-баннер: размытие основы на хвосте + анимированный баннер из SVG (app/end_screen.py)."""
    from app.end_screen import set_end_screen
    from app.svg_webm import SvgWebmError

    brand_id = args.get("brand_id")
    if brand_id:
        try:
            ctx.store.get_brand(int(brand_id))
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolError(f"бренда {brand_id} нет — см. list_brands") from exc
    fields = {k: args[k] for k in END_SCREEN_FIELDS if k in args}
    if fields.get("svg_asset_id"):
        foreign = _foreign_asset_problem(ctx, fields["svg_asset_id"])
        if foreign:
            raise ToolError(f"svg_asset_id: {foreign}")
    try:
        return set_end_screen(ctx.store, ctx.plan_id, fields, brand_id=int(brand_id) if brand_id else None,
                              remove=args.get("remove") is True, before_change=ctx.before_change)
    except (ValueError, SvgWebmError) as exc:
        raise ToolError(str(exc)) from exc


def h_get_playbook(_ctx: ToolContext, args: dict) -> dict:
    """Плейбук — пошаговый рецепт заказа (docs/playbooks/<name>.md); без name — список."""
    from app import playbooks

    name = str(args.get("name") or "").strip()
    items = playbooks.list_playbooks()
    if not name:
        return {"playbooks": items, "note": "get_playbook(name) — текст рецепта: шаги, инструменты, чек-лист"}
    try:
        return playbooks.get_playbook(name)
    except KeyError as exc:
        raise ToolError(f"плейбука «{name}» нет; есть: {', '.join(p['name'] for p in items) or '—'}") from exc


def _scaffold_assets(ctx: ToolContext, spec: dict) -> tuple[dict, list[str]]:
    """Размеры и длины записей/логотипа заготовки из библиотеки; чужой вид файла — проблема."""
    assets: dict = {}
    problems: list[str] = []
    wanted = [(f"scenes[{i}].asset_id", s["asset_id"], ("video", "gif")) for i, s in enumerate(spec["scenes"])]
    if spec["end"]["logo_asset_id"] is not None:
        wanted.append(("end.logo_asset_id", spec["end"]["logo_asset_id"], ("image",)))
    for where, ref, kinds in wanted:
        try:
            asset = ctx.store.get_montage_asset(int(ref))
        except (KeyError, TypeError, ValueError):
            problems.append(f"{where}: файла {ref} нет в библиотеке — id из list_assets")
            continue
        foreign = _foreign_asset_problem(ctx, asset["id"])
        if foreign:
            problems.append(f"{where}: {foreign}")
            continue
        if asset.get("kind") not in kinds:
            problems.append(f"{where}: {ref} — {asset.get('kind')}, нужен {' или '.join(kinds)}"
                            + (" (запись экрана: upload_video / add_video_from_source)" if "video" in kinds else ""))
            continue
        assets[str(asset["id"])] = {"kind": asset.get("kind"), "width": asset.get("width"),
                                    "height": asset.get("height"), "duration": asset.get("duration_sec")}
    return assets, problems


SCAFFOLD_POSES = {"center": "по центру крупно", "left": "слева с наклоном", "right": "справа с наклоном"}


def h_build_app_promo(ctx: ToolContext, args: dict) -> dict:
    """Заготовка промо приложения на холсте (app/promo_scaffold.py): spec → слои, камера, музыка."""
    from app import promo_scaffold
    from app.music_energy import ensure_energy

    raw = args.get("spec") if isinstance(args.get("spec"), dict) else {
        k: v for k, v in args.items() if k not in ("spec", "dry_run")}
    dry = bool(args.get("dry_run"))
    if not _source_canvas(ctx):
        raise ToolError("build_app_promo — только на холсте: create_canvas_clip, потом build_app_promo с его clip_id")
    from app.formats import settings_layers_format, settings_render_format

    # Формат клипа: 9:16 / 1:1 / 4:5 — заготовка в опорном кадре 9:16 (в 1:1 и 4:5 переносится
    # при рендере); 16:9 — понятный отказ (app/promo_scaffold.py, «Форматы»).
    out_fmt = settings_render_format(_current_render(ctx))
    try:
        promo_scaffold.check_format(out_fmt)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    try:
        spec = promo_scaffold.normalize_spec(raw)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    assets, problems = _scaffold_assets(ctx, spec)
    raw = dict(raw)
    if spec["music"]:
        try:
            [track] = ensure_energy(ctx.store, [ctx.store.get_audio_track(int(spec["music"]["track_id"]))])
        except (KeyError, TypeError, ValueError):
            problems.append(f"music.track_id: трека {spec['music']['track_id']} нет — list_music")
        else:
            raw["music"] = {**spec["music"], "bpm": track.get("bpm"), "energy_start_sec": track.get("energy_start_sec"),
                            "drop_sec": track.get("drop_sec"), "duration": track.get("duration_sec")}
    if problems:
        raise ToolError("spec заготовки не принят", problems)
    try:
        built = promo_scaffold.build_app_promo(raw, assets)
    except promo_scaffold.ScaffoldError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    duration = float(built["duration"])
    render = _current_render(ctx)
    kept = [l for l in _layers_of(render) if not promo_scaffold.is_scaffold_layer(l)]
    replaced = len(_layers_of(render)) - len(kept)
    layers_fmt = settings_layers_format(render)
    if layers_fmt != promo_scaffold.SCAFFOLD_FORMAT and kept:
        raise ToolError(f"свои слои клипа размечены в {layers_fmt}, а заготовка — в {promo_scaffold.SCAFFOLD_FORMAT}",
                        [f"убери свои слои (remove_layers) или собери промо на новом холсте; формат вывода "
                         f"{out_fmt} останется — слои 9:16 перенесутся в него при рендере"])
    if len(kept) + len(built["layers"]) > motion.MAX_LAYERS:
        raise ToolError(f"заготовка не влезает: своих слоёв {len(kept)} + заготовка {len(built['layers'])} > "
                        f"{motion.MAX_LAYERS}", ["убери лишние свои слои (remove_layers) и повтори"])
    taken = {str(l.get("id")) for l in kept}
    new_layers = []
    for layer in built["layers"]:
        layer = dict(layer)
        if str(layer["id"]) in taken:
            layer["id"] = motion.new_layer_id(taken)
        taken.add(str(layer["id"]))
        new_layers.append(layer)
    # Звук, которого нет в паке этой установки, не валит заготовку — слой пропускается с пометкой.
    problems, skipped_sfx = list(motion.validate_camera(built["camera"], duration)), []
    checked = []
    for layer in new_layers:
        bad = _validate_new_layers(ctx, [layer], duration)
        if bad and layer.get("type") == "audio":
            skipped_sfx.append(str((layer.get("props") or {}).get("asset_id")))
            continue
        problems += [re.sub(r"^layers\[0\]", f"{layer['id']} ({layer.get('name')})", p) for p in bad]
        checked.append(layer)
    if problems:
        raise ToolError("заготовка не прошла проверку слоёв", problems)
    new_layers = checked

    plan = built["plan"]
    scenes = []
    for s in plan["scenes"]:
        extra = []
        if s.get("zoom"):
            extra.append(f"наезд {s['zoom']['zoom']:g}× на {s['zoom']['t']:.1f} с")
        if s.get("window"):
            extra.append(f"окно записи с {s['window']['t']:.1f} с")
        scenes.append(f"{s['scene']}. {s['start']:.1f}–{s['end']:.1f} с «{s['title']}» — телефон "
                      f"{SCAFFOLD_POSES.get(s['pose'], s['pose'])}" + (", " + ", ".join(extra) if extra else ""))
    climax = plan["climax"]
    out: dict = {
        "ok": True,
        "layers": len(new_layers),
        "layers_total": len(kept) + len(new_layers),
        "camera_keys": len(built["camera"]),
        "duration": round(duration, 2),
        "hook": f"0–{plan['hook'][1]:.1f} с",
        "scenes": scenes,
        "climax": f"{climax['t']:.1f} с (сцена {climax['scene']}, первая доля такта): вспышка, кольцо, частицы, бас-дроп",
        "final": f"{plan['final'][0]:.1f}–{plan['final'][1]:.1f} с: логотип, строки, плашка CTA",
        "music": built["music"],
        "notes": built["notes"],
    }
    if skipped_sfx:
        out["sounds_skipped"] = (f"нет в паке звуков: {', '.join(sorted(set(skipped_sfx)))} — без них; свои — "
                                 "search_pack freesound")
    if out_fmt != promo_scaffold.SCAFFOLD_FORMAT:
        out["format"] = (f"клип {out_fmt}: заготовка размечена в {promo_scaffold.SCAFFOLD_FORMAT} (layers_format) и "
                         f"переносится в кадр {out_fmt} одним масштабом — титры мельче, чем в вертикали; проверь "
                         "view_frame (он уже в формате клипа)")
    if dry:
        scene = {**_lint_scene(ctx, {**render, "layers": kept + new_layers, "camera": built["camera"],
                                     "layers_format": promo_scaffold.SCAFFOLD_FORMAT})}
        out.update(dry_run=True, warnings=lint_layers(kept + new_layers, duration, **scene),
                   note="ничего не применено; dry_run:false — применить")
        return out

    current = _clip_duration(ctx)
    if abs(current - duration) > 0.01:
        timeline = clip_timeline(ctx.store, ctx.plan_id)
        start = float(timeline["pieces"][0]["start_sec"])
        _apply_piece(ctx, 0, end_sec=round(start + duration, 3))
        render = _current_render(ctx)
        kept = [l for l in _layers_of(render) if not promo_scaffold.is_scaffold_layer(l)]
        out["resized"] = f"длина холста {current:.2f} → {duration:.2f} с (по сетке долей)"
    update = {**render, "layers": kept + new_layers, "camera": built["camera"]}
    if layers_fmt != promo_scaffold.SCAFFOLD_FORMAT:
        update["layers_format"] = promo_scaffold.SCAFFOLD_FORMAT
    if built["music"]:
        update.update(music_on=True, track_id=int(built["music"]["track_id"]),
                      music_start_sec=float(built["music"]["start_sec"] or 0.0))
    if spec["background"] is not None:
        from app.backgrounds import normalize_background
        from app.chroma_key import normalize_backdrop

        update["backdrop"] = normalize_backdrop(normalize_background(built["backdrop"]))
    fresh = _save_render(ctx, update)
    if replaced:
        out["replaced"] = f"прежняя заготовка убрана: {replaced} слоёв «promo:»; свои слои ({len(kept)}) на месте"
    if _camera_of(render):
        out["camera_note"] = "ключи камеры заменены наездами заготовки (свои наезды — set_camera вместе с ними)"
    out["warnings"] = lint_layers(_layers_of(fresh), duration, **_lint_scene(ctx, fresh))
    music = _music_warning(ctx, fresh)
    if music:
        out["warnings"].append(music)
    times = [round(float(plan["hook"][1]) - 0.4, 1)]
    times += [round(s["zoom"]["t"] + 0.4, 1) for s in plan["scenes"] if s.get("zoom")][:2]
    times += [round(float(climax["t"]) + 0.4, 1), round(float(plan["final"][1]) - 0.6, 1)]
    out["next"] = (f"дальше подстрой: view_frame на {', '.join(f'{t:g}' for t in times)} с — кольцо касания на кнопке "
                   "(иначе поправь focus сцены и вызови build_app_promo снова), окно кульминации показывает главное "
                   "(иначе window), титры по смыслу; точечно — update_layer по id (слои «promo: …»); повторный вызов "
                   "заменит только слои «promo:»")
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "build_app_promo", "Собираю заготовку промо",
            "ЗАГОТОВКА ПРОМО ПРИЛОЖЕНИЯ на холсте (create_canvas_clip) одним вызовом — сильная основа по приёмам "
            "эталонных промо: сетка долей трека, хук крупно по словам над телефоном, сцены с записями экрана в "
            "телефоне (позы чередуются: по центру крупно / слева / справа с наклоном, свайпы), наезд камеры на "
            "focus на сильной доле и касание, окно записи (window) на ключевой момент, кульминация на первой доле "
            "такта (вспышка, кольцо, частицы, бас-дроп), титры над телефоном с затемнением, финал ≥ 3 с (логотип, "
            "строки, плашка CTA), тихие звуки, музыка со start_sec (дроп — на кульминацию). spec: {duration? 20–40 "
            "(нет — по сценам; холст подгоняется), brand {accent #rrggbb, text?, dark?, font?, weight?}, hook [1–3 "
            "удара по 1–3 слова], scenes [1–6: {asset_id записи, from, to (секунды записи), title 2–6 слов (« — » "
            "делит на строку и акцент), focus? {x, y доли экрана записи — кнопка; t? — секунда нажатия}, window? "
            "{x, y, w, h доли записи}, climax?}], end {logo_asset_id, lines [дата/оффер], cta (ссылка), sub?}, "
            "music {track_id, start_sec?} | null, background? (как set_backdrop), variant? (другая раскладка)}. "
            "Повторный вызов заменяет только прежние слои «promo: …» (свои слои остаются), камеру — целиком. "
            "dry_run:true — план без применения. / One-call app promo scaffold on a canvas clip.",
            _obj({
                "spec": _obj({
                    "duration": {"type": "number", "description": "20–40 с; не задано — по сценам"},
                    "brand": _obj({"accent": {"type": "string"}, "text": {"type": "string"}, "dark": {"type": "string"},
                                   "font": {"type": "string"}, "weight": {"type": "integer"}}, ["accent"]),
                    "hook": {"type": "array", "items": {"type": "string"}},
                    "scenes": {"type": "array", "items": _obj({
                        "asset_id": {"type": "integer"}, "from": {"type": "number"}, "to": {"type": "number"},
                        "title": {"type": "string"},
                        "focus": _obj({"x": {"type": "number"}, "y": {"type": "number"}, "t": {"type": "number"}}),
                        "window": _obj({"x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                                        "h": {"type": "number"}}),
                        "climax": {"type": "boolean"}}, ["asset_id", "from", "to", "title"])},
                    "end": _obj({"logo_asset_id": {"type": "integer"}, "lines": {"type": "array", "items": {"type": "string"}},
                                 "cta": {"type": "string"}, "sub": {"type": "string"}}, ["cta"]),
                    "music": _obj({"track_id": {"type": "integer"}, "start_sec": {"type": "number"}}, ["track_id"]),
                    "background": {"type": "object"},
                    "variant": {"type": "integer"},
                }, ["brand", "hook", "scenes", "end"]),
                "dry_run": {"type": "boolean", "description": "true — только план, клип не меняется"},
            }, ["spec"]),
            h_build_app_promo, mutating=True,
            label=lambda a: (("План заготовки промо" if a.get("dry_run") else "Собираю заготовку промо")
                             + f" ({len(((a.get('spec') or {}).get('scenes') or []))} сцен)"),
        ),
        Tool(
            "list_brands", "Смотрю бренды",
            "Бренды владельца (повторяющиеся заказчики, например магазин): шрифт, цвета, логотип, призыв в "
            "конце (cta), плашка канала (nameplate), музыка, фон, политика эффектов. Создаёт и правит их только "
            "владелец. / Brand kits.",
            _obj({}), h_list_brands, scope="global",
        ),
        Tool(
            "apply_brand", "Оформляю в стиле бренда",
            "Ролик для бренда (реклама повторяющегося заказчика) — в его стиле одной командой: перекрашивает "
            "субтитры-слои и хук, ставит маленький логотип в углу, плашку-призыв на последние секунды, плашку "
            "канала (если она есть в бренде), музыку и фон бренда, снимает лишние эффекты. Зови ПОСЛЕ монтажа "
            "(auto_edit, хук, вырезы): повтор ничего не дублирует (слои brand_*). nameplate — плашка канала "
            "на этот ролик: поля как у set_nameplate поверх плашки бренда; remove_nameplate:true — снять. "
            "Потом view_frame на концовке — призыв не на лице. / Apply a brand kit to the clip.",
            _obj({"brand_id": {"type": "integer"},
                  "nameplate": {"type": "object", "description": "поля плашки канала, как у set_nameplate"},
                  "remove_nameplate": {"type": "boolean"}},
                 ["brand_id"]),
            h_apply_brand, mutating=True, label=lambda a: f"Бренд #{a.get('brand_id')}",
        ),
        Tool(
            "set_nameplate", "Ставлю плашку канала",
            "Плашка канала на весь ролик: значок площадки (свой векторный глиф) + ник в пилюле — как на "
            "нарезках стримов. platform twitch|youtube|tiktok|telegram|kick|none, handle (ник как есть), style "
            "glass (тёмное стекло + обводка цветом площадки, по умолчанию) | solid (заливка), accent #rrggbb "
            "(нет — цвет площадки), size (высота пилюли, px кадра 1080×1920, 74). anim — своя анимация: subtle (по "
            "умолчанию: вход от значка ~0.7 с — значок выпрыгивает, пилюля вытягивается, ник по буквам; дальше раз в "
            "7 с блик по пилюле и тик значка), lively (ярче, тик каждые 3.5 с), none (стоит; fade — мягкое "
            "появление). live:true — красная пульсирующая точка LIVE. outro: auto (по умолчанию — свернуться в "
            "значок, когда выходит призыв бренда brand_cta) | collapse (свернуться в конце ролика) | none. position: auto (по умолчанию: в раскладке «игра + вебка» — на 72 px ниже "
            "разделителя и ниже субтитров, слева; иначе — безопасный верхний угол, напротив логотипа бренда) | "
            "under-split-left | under-split-center | top-left | top-right. Место считается от зон площадок "
            "(вкладки сверху, подпись снизу, кнопки TikTok справа), субтитров, хука и логотипа. brand_id — "
            "взять плашку бренда за основу. corner: top-right|top-left — пресет «маленькая в верхнем углу» (кегль "
            "58, угол безопасной зоны; есть хук — выходит, когда он уходит). start/end — секунды клипа, когда плашка "
            "на экране (хук, ушедший до start, ей не мешает). remove:true — снять. Повтор заменяет прежнюю (слой brand_nameplate). "
            "Потом view_frame. / Channel nameplate (platform icon + handle) for the whole clip.",
            _obj({"platform": {"type": "string", "enum": ["twitch", "youtube", "tiktok", "telegram", "kick", "none"]},
                  "handle": {"type": "string"},
                  "style": {"type": "string", "enum": ["glass", "solid"]},
                  "accent": {"type": "string"},
                  "position": {"type": "string", "enum": ["auto", "top-left", "top-right", "under-split-left",
                                                          "under-split-center"]},
                  "corner": {"type": "string", "enum": ["top-right", "top-left"]},
                  "start": {"type": "number", "minimum": 0},
                  "end": {"type": "number", "minimum": 0},
                  "size": {"type": "number", "minimum": 40, "maximum": 160},
                  "fade": {"type": "boolean"},
                  "anim": {"type": "string", "enum": ["none", "subtle", "lively"]},
                  "live": {"type": "boolean"},
                  "outro": {"type": "string", "enum": ["auto", "collapse", "none"]},
                  "brand_id": {"type": "integer"},
                  "remove": {"type": "boolean"}}),
            h_set_nameplate, mutating=True,
            label=lambda a: "Снимаю плашку канала" if a.get("remove") else f"Плашка канала {a.get('handle') or ''}".strip(),
        ),
        Tool(
            "set_end_screen", "Ставлю концовку-баннер",
            "Концовка шортса отдельным решением: на последних duration с (1.5–8, по умолчанию 3; не больше половины "
            "ролика) видео плавно уходит в размытие (blur 0–60, 18) и темнеет (dim 0–0.8, 0.35) за ramp с (0.6), "
            "поверх — анимированный баннер (SVG → webm с альфой, слой end_banner). template: live (стеклянная "
            "карточка, пульсирующая точка LIVE, фраза крупно — после « — » акцентом, ник со значком площадки) | "
            "schedule (дни недели чипами, время крупно) | neon (ник светящейся обводкой, адрес канала); или "
            "svg_asset_id — свой SVG из библиотеки. text («Стримлю с 8 утра — заходи»), handle, platform "
            "twitch|youtube|tiktok|telegram|kick|none, accent #rrggbb (нет — цвет площадки), days («пн–пт», "
            "«будни»), time («8:00 МСК»), font; y — центр баннера (0.44), w — ширина (960 px кадра 1080). "
            "brand_id — тексты, шрифт и акцент из бренда. Когда: стример — анонс расписания; реклама — призыв "
            "бренда; НЕ на каждый мем и не поверх развязки. remove:true — снять. Повтор заменяет. Потом "
            "view_frame на концовке. / Blurred end screen with an animated banner.",
            _obj({"template": {"type": "string", "enum": ["live", "schedule", "neon"]},
                  "svg_asset_id": {"type": "integer"},
                  "text": {"type": "string"}, "handle": {"type": "string"},
                  "platform": {"type": "string", "enum": ["twitch", "youtube", "tiktok", "telegram", "kick", "none"]},
                  "accent": {"type": "string"}, "days": {"type": "string"}, "time": {"type": "string"},
                  "font": {"type": "string"},
                  "duration": {"type": "number", "minimum": 1.5, "maximum": 8},
                  "blur": {"type": "number", "minimum": 0, "maximum": 60},
                  "dim": {"type": "number", "minimum": 0, "maximum": 0.8},
                  "ramp": {"type": "number", "minimum": 0.1, "maximum": 2},
                  "y": {"type": "number"}, "w": {"type": "number"},
                  "brand_id": {"type": "integer"}, "remove": {"type": "boolean"}}),
            h_set_end_screen, mutating=True,
            label=lambda a: "Снимаю концовку" if a.get("remove") else f"Концовка {a.get('template') or ''}".strip(),
        ),
        Tool(
            "get_playbook", "Читаю плейбук",
            "Плейбук — пошаговый рецепт типового заказа по опыту реальных работ: шаги, какие инструменты, "
            "чек-лист перед сдачей. Без name — список (stream-shorts — нарезка стрима в вертикальные шортсы, "
            "stream-horizontal — горизонтальная нарезка стрима 10–15 мин, meme-insert — мем/вставка поверх "
            "видео, app-promo — промо приложения, autopilot — автопилот канала: от VOD до одобрения, расписания "
            "и сводки, vtuber-story — история-«говорящая голова» витубера: только аватар крупно, заголовок, "
            "сабы по 1–2 слова, вставки под слова). Прочитай подходящий ДО монтажа. / Step-by-step playbook "
            "for a typical order; no name — list.",
            _obj({"name": {"type": "string"}}), h_get_playbook, scope="global",
            label=lambda a: f"Плейбук {a.get('name') or ''}".strip(),
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
    ]
