"""Брендовый шаблон (brand kit): ролики одного заказчика — в одном стиле одной командой.

Реклама магазина (например, VTuber-моделей) идёт сериями: каждый ролик монтируется
заново, но шрифт, цвета, логотип, концовка-призыв и музыка у бренда одни. Держать
это в голове монтажёра (или в промпте ИИ) — значит каждый раз чуть по-разному.
Поэтому бренд хранится один раз (таблица ``brands``), а ``apply_brand`` раскладывает
его на клип поверх уже сделанного монтажа:

* субтитры-слои и хук перекрашиваются (текст и тайминг не трогаем);
* логотип — маленький, в верхнем углу, после хука и до концовки (видео не закрывает);
* концовка — плашка-призыв на последние ``cta.duration_sec`` готового ролика, в
  свободной от субтитров (и героя, если его рамка известна) зоне; у источника на
  зелёнке плашка лежит ЗА персонажем (``behind``), текст — поверх;
* музыка бренда, фон за персонажем, политика звуковых эффектов;
* плашка канала (``nameplate``: значок площадки + ник) — весь ролик, под разделителем
  раскладки «игра + вебка» или в безопасном верхнем углу (``nameplate_place``); её же
  ставит отдельно ``set_nameplate``.

Слои бренда помечены id с префиксом ``brand_`` — повторное применение (или другой
бренд) снимает прежние и ставит заново, дублей нет. Снимок для Ctrl+Z делает
вызывающий (MCP — ``ctx.before_change``), иначе — сам ``apply_brand``.

Где что лежит. Здесь — наложение ``apply_brand`` и ``_subject_box`` (его подменяют тесты). Вынесено: нормализация ``app/brand_kit_normalize.py``, свойства титров и конец
``app/brand_kit_props.py``, плашка с именем ``app/brand_kit_nameplate.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any, Callable

from app import motion

from app.brand_kit_normalize import (  # noqa: F401
    SFX_POLICIES, LOGO_CORNERS, CTA_SEC_DEFAULT, CTA_SEC_MIN, CTA_SEC_MAX, DEFAULT_COLORS, NAMEPLATE_PLATFORMS,
    NAMEPLATE_STYLES, NAMEPLATE_POSITIONS, NAMEPLATE_OUTROS, NAMEPLATE_SIZE_MIN, NAMEPLATE_SIZE_MAX,
    DEFAULT_HOOK_STROKE, _color, _text, _num, _opt_id, _stroke, _plate_bg, _normalize_caption_style,
    _normalize_hook_style, CTA_STYLES, _normalize_cta, normalize_nameplate, normalize_brand, _brand_palette,
    brand_backdrop, resolved_backdrop, merge_brand, brand_problems, font_families,
)
from app.brand_kit_props import (  # noqa: F401
    BRAND_PREFIX, LOGO_ID, CTA_ID, CTA_PLATE_ID, MINIMAL_SFX, LOGO_PX, LOGO_MARGIN_PX, CTA_SIZE, CTA_MAX_WIDTH,
    CTA_PAD_Y, HOOK_WINDOW_SEC, _brand_owned, caption_props, HIGHLIGHT_FALLBACKS, highlight_color, hook_props,
    find_hook, _is_sfx, apply_sfx_policy, tail_start, _cta_lines, _free_zones, cta_y, build_cta, build_logo,
)
from app.brand_kit_nameplate import (  # noqa: F401
    NAMEPLATE_ID, CAPTION_HALF, NAMEPLATE_CORNER_SIZE, NAMEPLATE_GAP_PX, NAMEPLATE_MARGIN_PX, NAMEPLATE_TOP_PX,
    NAMEPLATE_CLEAR_PX, NAMEPLATE_SHADOW, TIKTOK_BUTTONS, nameplate_enabled, _nameplate_width, _split_line,
    _caption_bands, nameplate_place, _overlaps_time, build_nameplate, _logo_corner_of, set_nameplate,
)

CAPS_ID = "brand_caps"


# Цвет площадки — акцент плашки по умолчанию (зеркало PLATFORM_COLORS в layers/nameplate.ts).
PLATFORM_COLORS = {
    "twitch": "#9146ff", "youtube": "#ff0033", "tiktok": "#fe2c55", "telegram": "#229ed9", "kick": "#53fc18",
}


def _subject_box(store: Any, clip_plan_id: int) -> dict | None:
    """Где герой (Gemini-зрение, кэш) — чтобы призыв не лёг на лицо. Без Gemini — None."""
    try:
        from app.ai.subject_box import subject_box

        return subject_box(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - без зрения — место по субтитрам
        return None


def apply_brand(
    store: Any, clip_plan_id: int, brand_id: int, *, before_change: Callable[[], None] | None = None,
    nameplate: Any = None, run: Callable[[list[str], float], str] | None = None,
) -> dict:
    """Оформить клип в стиле бренда. Повторный вызов даёт тот же результат (без дублей).

    ``before_change`` — снимок истории от вызывающего (MCP); без него снимок делается здесь.
    ``nameplate`` — плашка канала вместо бренда: dict — поля поверх плашки бренда, False — снять;
    None — как в бренде (у бренда её нет — уже стоящая плашка, например от set_nameplate, остаётся).
    Ошибки — ValueError (слои не прошли проверку), KeyError (нет клипа/бренда)."""
    from app.agent_tools import clip_timeline, covering_problems, lint_layers
    from app.asset_scope import new_foreign_assets
    from app.auto_edit import caption_y_for
    from app.chroma_key import normalize_backdrop
    from app.formats import ref_size, settings_layers_format

    brand = store.get_brand(int(brand_id))
    timeline = clip_timeline(store, clip_plan_id)
    plan, render, duration = timeline["plan"], dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    keyed = bool(store.get_source(int(plan["source_id"])).get("chroma_key"))
    # Слои бренда — в опорном кадре формата разметки слоёв клипа (9:16 — 1080×1920, как всегда).
    fmt = settings_layers_format(render)
    ref_h = ref_size(fmt)[1]
    cap_half = CAPTION_HALF if ref_h == 1920 else CAPTION_HALF * 1920 / ref_h
    changes: list[str] = []
    warnings: list[str] = []
    old_plate = next((dict(l) for l in render.get("layers") or [] if l.get("id") == NAMEPLATE_ID), None)
    layers = [dict(l) for l in render.get("layers") or [] if not _brand_owned(l)]

    # 1. Субтитры: перекрасить все слои captions; нет ни одного — поставить свой.
    caps = [l for l in layers if l.get("type") == "captions"]
    for layer in caps:
        layer["props"] = caption_props(layer.get("props") or {}, brand)
    if caps:
        changes.append(f"субтитры в стиле бренда ({len(caps)} слоя)" if len(caps) > 1 else "субтитры в стиле бренда")
    else:
        words = [w for w in (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
                 if str(w.get("word", "")).strip()]
        if words and not render.get("subs_on"):
            layers.append({
                "id": CAPS_ID, "type": "captions", "name": "Субтитры (бренд)", "start": 0.0, "end": duration,
                "track": 0, "x": 0.5, "y": caption_y_for(render), "props": caption_props({"maxWords": 3}, brand),
            })
            changes.append("субтитры-слой в стиле бренда")
        elif words:
            warnings.append("субтитры идут обычной дорожкой (стиль субтитров) — бренд их не красит; "
                            "⚡ автомонтаж сделает слой субтитров, затем примени бренд снова")
        else:
            warnings.append("в клипе нет распознанной речи — субтитров нет")

    # 2. Хук.
    hook = find_hook(layers)
    if hook is not None:
        hook["props"] = hook_props(hook.get("props") or {}, brand)
        changes.append("хук в стиле бренда")

    # 3. Звуковые эффекты по политике бренда.
    layers, dropped = apply_sfx_policy(layers, brand["sfx_policy"])
    if dropped:
        changes.append(f"снято звуковых эффектов: {dropped} (политика бренда «{brand['sfx_policy']}»)")
    transition = dict(render.get("transition") or {})
    if brand["sfx_policy"] == "none" and transition.get("sfx", "none") != "none":
        transition["sfx"] = "none"
        changes.append("без звука на склейках")

    # 3b. Плашка канала — весь ролик; место — до призыва, чтобы призыв её обошёл.
    added: list[dict] = []
    plate: dict | None = None
    plate_band: tuple[float, float] | None = None
    np = dict(brand["nameplate"])
    if isinstance(nameplate, dict):
        np = normalize_nameplate({**np, **{k: v for k, v in nameplate.items() if v is not None}})
    if nameplate is False:
        if old_plate is not None:
            changes.append("плашка канала снята")
    elif nameplate_enabled(np):
        if not np["accent"] and np["platform"] == "none":
            np["accent"] = brand["colors"]["accent"]
        place = nameplate_place(render, layers, np, logo_corner=brand["logo_corner"] if brand.get("logo_asset_id") else None,
                                fmt=fmt)
        plate = build_nameplate(np, place, duration, brand["font"])
        added.append(plate)
        # Плашка, которая сворачивается перед призывом, ему не мешает; остающаяся — призыв обходит.
        if np["outro"] == "none" or not (brand["cta"]["text"] or brand["cta"]["url_text"]):
            plate_band = (plate["y"] - np["size"] / ref_h / 2, plate["y"] + np["size"] / ref_h / 2)
        changes.append(f"плашка канала «{np['handle'] or np['platform']}» {place['note']}")
        if not place["fits"] or np["position"] not in ("auto", place["position"]):
            warnings.append(place["note"])
    elif old_plate is not None:
        layers.append(old_plate)  # у бренда плашки нет — поставленная руками/set_nameplate остаётся

    # 4. Концовка-призыв на последние cta.duration_sec готового ролика: плашкой или (style banner)
    # концовкой-баннером — видео в размытие, поверх анимированный баннер (app/end_screen.py).
    cta = brand["cta"]
    cta_start: float | None = None
    banner_built = None
    if cta.get("style") == "banner" and (cta["text"] or cta["url_text"] or brand["nameplate"].get("handle")):
        from app.end_screen import END_LAYER_ID, brand_fields, build_end_screen
        from app.svg_webm import SvgWebmError

        try:
            banner_built = build_end_screen(store, clip_plan_id, render, duration, brand_fields(brand), run=run)
        except (SvgWebmError, ValueError) as exc:
            warnings.append(f"концовка-баннер не собралась ({exc}) — призыв плашкой")
        if banner_built is not None:
            layers = [l for l in layers if l.get("id") != END_LAYER_ID]
            added.append(banner_built["layer"])
            render["end_screen"] = banner_built["end_screen"]
            cta_start = banner_built["start"]
            warnings.extend(banner_built["warnings"])
            changes.append(f"концовка-баннер «{cta['template']}» с {cta_start:.1f} с до конца: видео уходит в размытие")
    if banner_built is None and (cta["text"] or cta["url_text"]):
        want = min(cta["duration_sec"], max(1.0, duration * 0.4))
        cta_start = tail_start(render.get("cuts") or [], duration, want)
        blocked = [
            (float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) - cap_half, float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) + cap_half)
            for l in layers
            if l.get("type") == "captions" and not l.get("hidden")
            and float(l.get("start") or 0) < duration and float(l.get("end") or 0) > cta_start
        ]
        box = _subject_box(store, clip_plan_id)
        if box:
            blocked.append((float(box["y0"]), float(box["y1"])))
        if plate_band:
            blocked.append(plate_band)
        text = "\n".join(t for t in (cta["text"], cta["url_text"]) if t)
        height = (_cta_lines(text, CTA_SIZE) * CTA_SIZE * 1.15 + 2 * CTA_PAD_Y) / ref_h
        y, fits = cta_y(height, blocked, fmt)
        if not fits:
            warnings.append("призыву не нашлось свободного места между субтитрами и героем — проверь view_frame, "
                            "при нужде подвинь brand_cta (update_layer y)")
        added += build_cta(brand, cta_start, duration, y, keyed, fmt)
        changes.append(f"призыв «{text.replace(chr(10), ' / ')}» с {cta_start:.1f} с до конца")
        if plate is not None and np["outro"] == "auto":
            # Плашка сворачивается в значок, когда выходит призыв.
            added[added.index(plate)] = plate = build_nameplate(np, place, duration, brand["font"], cta_start)
            changes.append(f"плашка канала сворачивается к призыву ({cta_start:.1f} с)")

    # 5. Логотип: после хука (он тоже наверху) и до призыва.
    if brand.get("logo_asset_id"):
        try:
            asset = store.get_montage_asset(int(brand["logo_asset_id"]))
        except KeyError:
            asset = None
            warnings.append(f"логотипа #{brand['logo_asset_id']} больше нет в библиотеке — без логотипа")
        if asset is not None and asset.get("kind") not in ("image", "gif"):
            warnings.append(f"логотип #{asset['id']} — не картинка, пропущен")
            asset = None
        if asset is not None and new_foreign_assets(store, clip_plan_id, [asset["id"]], render):
            warnings.append(f"логотип #{asset['id']} лежит в другом проекте, клипу его брать нельзя — без логотипа "
                            "(логотип бренда держи в общих файлах)")
            asset = None
        if asset is not None:
            start = 0.0
            if hook is not None and float(hook.get("y", 0.5)) < 0.35:
                start = float(hook.get("end") or 0.0)
            end = cta_start if cta_start is not None else duration
            if end - start >= 1.0:
                added.append(build_logo(brand, asset, round(start, 3), round(end, 3), fmt))
                changes.append("логотип в углу")
            else:
                warnings.append("клип слишком короткий для логотипа между хуком и призывом")

    def asset_exists(asset_id: int) -> bool:
        try:
            store.get_montage_asset(int(asset_id))
            return True
        except (KeyError, TypeError, ValueError):
            return False

    problems = covering_problems(added, keyed, fmt) + motion.validate_layers(added, duration, asset_exists=asset_exists)
    if problems:
        raise ValueError("слои бренда не прошли проверку: " + "; ".join(problems))

    # 6. Музыка и фон за персонажем.
    if brand.get("music_track_id"):
        try:
            store.get_audio_track(int(brand["music_track_id"]))
            render["music_on"], render["track_id"] = True, int(brand["music_track_id"])
            changes.append("музыка бренда")
        except KeyError:
            warnings.append(f"трека #{brand['music_track_id']} больше нет — музыка не тронута")
    render["backdrop"] = normalize_backdrop(resolved_backdrop(brand))
    if keyed:
        changes.append("фон за персонажем")
    layout = render.get("layout")
    if isinstance(layout, dict) and layout.get("mode") == "meme":
        # Фон под видео meme — тот же фон бренда (анимированный), а не заливка.
        render["layout"] = {**layout, "bg": render["backdrop"]}
        changes.append("фон под видео")

    render["layers"] = layers + added
    render["transition"] = transition
    if before_change is not None:
        before_change()
    else:
        from app.clip_history import snapshot

        snapshot(store, clip_plan_id, f"бренд «{brand['name']}»")
    fresh = store.set_clip_plan_render_settings(clip_plan_id, render)
    fresh_layers = (fresh.get("render_settings") or {}).get("layers") or []
    brand_ids = {str(l["id"]) for l in added}
    out: dict = {
        "ok": True,
        "brand": {"id": brand["id"], "name": brand["name"]},
        "changes": changes,
        "warnings": warnings + lint_layers(fresh_layers, duration, only_ids=brand_ids, fmt=fmt),
    }
    if cta_start is not None:
        out["check"] = (f"посмотри концовку: view_frame t={min(duration, cta_start + 0.8):.1f} — призыв читается "
                        "и не лежит на лице")
    return out
