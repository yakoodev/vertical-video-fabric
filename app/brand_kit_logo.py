"""Заставка и концовка логотипом бренда (из ``app/brand_kit.py``): логотип пишется кистью.

Настройки бренда ``logo_intro`` / ``logo_outro`` (по умолчанию выключены). Включены — ``apply_brand``
вставляет шаблоны галереи «Логотип: заставка» (``logo-intro``) в начало ролика и «Логотип: концовка»
(``logo-outro``) так, что она заканчивается ровно на последнем кадре. Слои собирает движок шаблонов
(frontend/src/motion/templates/logo.ts через компоновщик, app/motion_template_tools.build_template) —
тот же вид и звук, что у галереи редактора и ``add_motion_template``: эскиз контуром, рывок царапинами,
живой логотип; записи пака freesound без треска (взмах, хлыст, удар, вжух).

Правило владельца для заставки yakoodev: только на длинных версиях (не на частях TikTok) — поэтому
``min_clip_sec`` (по умолчанию 60 с готового ролика): ролик короче — заставки нет, в ответе предупреждение;
``apply_brand(logo_intro: true)`` ставит её на этот ролик всё равно.
"""

from __future__ import annotations

from typing import Any, Callable

from app.brand_kit_normalize import _color, _num

INTRO_ID = "brand_intro"
OUTRO_ID = "brand_outro"
INTRO_TEMPLATE = "logo-intro"
OUTRO_TEMPLATE = "logo-outro"

BUMPER_SEC_DEFAULT, BUMPER_SEC_MIN, BUMPER_SEC_MAX = 3.5, 1.5, 10.0
BUMPER_SIZE_DEFAULT, BUMPER_SIZE_MIN, BUMPER_SIZE_MAX = 78.0, 20.0, 100.0
MIN_CLIP_SEC_DEFAULT, MIN_CLIP_SEC_MAX = 60.0, 3600.0
# Места по высоте — как LOGO_POSITIONS в templates/logo.ts.
BUMPER_POSITIONS = ("верх", "центр", "низ")
DEFAULT_POSITION = {"intro": "центр", "outro": "низ"}


def _flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return value is True or value in (1, "1", "true", "on")


def normalize_logo_bumper(raw: Any, kind: str) -> dict:
    """Заставка (``kind`` intro) или концовка (outro) бренда в полной форме. ``true``/``false`` — только вкл/выкл."""
    src = raw if isinstance(raw, dict) else ({"on": raw} if isinstance(raw, bool) else {})
    position = src.get("position")
    return {
        "on": _flag(src.get("on"), False),
        "duration": _num(src.get("duration"), BUMPER_SEC_DEFAULT, BUMPER_SEC_MIN, BUMPER_SEC_MAX),
        "color": _color(src.get("color")) or "#ffffff",
        "position": position if position in BUMPER_POSITIONS else DEFAULT_POSITION[kind],
        "size": _num(src.get("size"), BUMPER_SIZE_DEFAULT, BUMPER_SIZE_MIN, BUMPER_SIZE_MAX),
        "sound": _flag(src.get("sound"), True),
        # Ролик (готовый, после вырезов) короче — без заставки: правило «только на длинных версиях».
        "min_clip_sec": _num(src.get("min_clip_sec"), MIN_CLIP_SEC_DEFAULT, 0.0, MIN_CLIP_SEC_MAX),
    }


def bumper_params(cfg: dict, asset_id: int, sound: bool) -> dict:
    """Параметры шаблона logo-intro/logo-outro из настройки бренда."""
    return {"logo": str(int(asset_id)), "color": cfg["color"], "duration": cfg["duration"],
            "position": cfg["position"], "size": cfg["size"], "sound": bool(cfg["sound"] and sound)}


def _rename(layers: list[dict], base_id: str, label: str) -> list[dict]:
    """id шаблона → brand_intro / brand_intro_s1…: повтор apply_brand снимает их и ставит заново."""
    out: list[dict] = []
    n = 0
    for layer in layers:
        layer = dict(layer)
        if layer.get("type") == "audio":
            n += 1
            layer["id"] = f"{base_id}_s{n}"
        else:
            layer["id"] = base_id
            layer["name"] = f"Бренд: {label}"
        out.append(layer)
    return out


def build_logo_bumpers(
    brand: dict, asset_id: int, duration: float, finished: float, cuts: list[dict], fmt: Any,
    *, intro: bool | None = None, outro: bool | None = None,
    build: Callable[[str, dict, float, Any], dict] | None = None,
) -> dict:
    """Слои заставки и концовки бренда: ``{layers, intro: (start, end) | None, outro: …, changes, warnings}``.

    ``duration`` — длина клипа на шкале слоёв, ``finished`` — готового ролика (после вырезов), ``cuts`` —
    вырезы (концовка начинается раньше на вырезанное, чтобы в готовом ролике шла свои секунды и кончалась
    на последнем кадре). ``intro``/``outro`` — решение на этот ролик поверх бренда (None — как в бренде).
    ``build`` — сборщик шаблона (тесты подменяют); по умолчанию — компоновщик."""
    from app.brand_kit_props import tail_start

    if build is None:
        from app.motion_template_tools import build_template as build
    out: dict = {"layers": [], "intro": None, "outro": None, "changes": [], "warnings": []}
    sound = brand.get("sfx_policy") != "none"
    plan = (("intro", INTRO_ID, INTRO_TEMPLATE, "заставка логотипом", intro),
            ("outro", OUTRO_ID, OUTRO_TEMPLATE, "концовка логотипом", outro))
    for kind, base_id, template, label, override in plan:
        cfg = normalize_logo_bumper(brand.get(f"logo_{kind}"), kind)
        if override is False or (override is None and not cfg["on"]):
            continue
        if override is None and finished < cfg["min_clip_sec"]:
            out["warnings"].append(f"{label} не поставлена: ролик {finished:.0f} с короче {cfg['min_clip_sec']:.0f} с "
                                   f"(правило бренда — только длинные версии; на этот ролик — apply_brand "
                                   f"logo_{kind}: true)")
            continue
        length = min(cfg["duration"], duration)
        if length < BUMPER_SEC_MIN:
            out["warnings"].append(f"{label}: клип короче {BUMPER_SEC_MIN:g} с — не поставлена")
            continue
        at = 0.0 if kind == "intro" else tail_start(cuts, duration, length)
        params = {**bumper_params(cfg, asset_id, sound), "duration": round(duration - at if kind == "outro" else length, 3)}
        try:
            built = build(template, params, at, fmt)
        except Exception as exc:  # noqa: BLE001 - нет компоновщика — бренд без заставки, но с остальным
            out["warnings"].append(f"{label} не собралась ({exc})")
            continue
        layers = [l for l in built.get("layers") or [] if isinstance(l, dict)]
        if not layers:
            out["warnings"].append(f"{label}: шаблон {template} не дал слоёв")
            continue
        layers = _rename(layers, base_id, label)
        if kind == "outro":
            # Концовка — до последнего кадра: конец слоя логотипа ровно на конце клипа.
            for layer in layers:
                if layer["id"] == base_id:
                    layer["end"] = round(duration, 3)
        out["layers"] += layers
        main = next(l for l in layers if l["id"] == base_id)
        span = (float(main["start"]), float(main["end"]))
        out[kind] = span
        sfx = sum(1 for l in layers if l.get("type") == "audio")
        out["changes"].append(f"{label} {span[0]:.1f}–{span[1]:.1f} с" + (f" со звуком ({sfx})" if sfx else " без звука"))
    return out
