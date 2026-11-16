"""Шаблоны 3D-серий: готовые истории как стартовая точка новой серии в проекте пользователя.

Шаблон — готовая история: VRM-рекламы Витубики (``tools/vtubika_plans/<файл>.json``, переносятся
``app.s3d_import.vtubika_to_plan``) и мем-пилот с роботом Клодом (``tools/meme3d_pilot.py``, ``meme_pilot_plan``).
Файлы-источники лежат в образе (Dockerfile копирует их в ``/app/tools``), миниатюры — кадры готовых роликов,
``assets/series3d_templates/<slug>.jpg``.

«Из шаблона» создаёт обычную серию-черновик (план версии 1) в проекте: недостающие готовые персонажи и локация
добавляются в библиотеку, реплики и состав потом правятся как у любой серии. Ничего не рендерится и не тратится:
голос серии — голос рассказчика проекта (бесплатный по умолчанию), платный — только через карточку «Разрешить»
при «Рендере». Голос оригинала (ElevenLabs через Polza) в план не переносится.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import s3d_import as I
from app import s3d_plan as SP
from app import s3d_store as D

ROOT = Path(__file__).resolve().parent.parent
PLANS_DIR = ROOT / "tools" / "vtubika_plans"
MEME_FILE = ROOT / "tools" / "meme3d_pilot.py"
THUMBS_DIR = ROOT / "assets" / "series3d_templates"
THUMB_URL = "/api/series3d/templates/{slug}/thumb"

VTUBIKA_NOTE = "Реклама Витубики: поменяйте реплики под свою историю."

# Порядок — как выходили серии. cast — свой состав вместо героини из плана (история «Без лица» была воксельной —
# в 3D её играет Лёша, герой-парень). series — номер серии в рекламе Витубики.
TEMPLATES: tuple[dict[str, Any], ...] = (
    {"slug": "fox", "kind": "vtubika", "file": "fox.json", "series": 1,
     "description": "Алине 19, её аватар — две картинки «рот закрыт / рот открыт». Она копит донаты на живую "
                    "Live2D-модель. Тёплая история «до и после»: рассказчик за кадром, чат и донаты на экране."},
    {"slug": "tema", "kind": "vtubika", "file": "tema.json", "series": 2, "cast": ["lyosha"],
     "description": "Тёма ночью таскает коробки на складе, а в выходные стримит без камеры — чат просит показать "
                    "лицо. В оригинале — воксельная история; в шаблоне героя играет Лёша."},
    {"slug": "trial", "kind": "vtubika", "file": "trial.json", "series": 3,
     "description": "Художница Вера примеряет готовую 3D-модель перед дебютом: трекинг, кот на столе и «тестовый "
                    "эфир». На мониторе — пробная модель и персонаж Веры."},
    {"slug": "sticker", "kind": "vtubika", "file": "sticker.json", "series": 4,
     "description": "Ника стримит с картинкой, которая умеет только моргать, — чат зовёт её стикером. Она находит "
                    "модель, которая двигается. Кира — аватар на мониторе."},
    {"slug": "channel2014", "kind": "vtubika", "file": "channel2014.json", "series": 5,
     "description": "Женя стримит с 2014-го: рамка вебки в огне и двенадцать зрителей с первого эфира. Новое "
                    "оформление — и новенькие наконец остаются."},
    {"slug": "faces", "kind": "vtubika", "file": "faces.json", "series": 6,
     "description": "Соня стримит хорроры, а бесплатная модель улыбается даже на скримерах. Модель с выражениями "
                    "на горячих клавишах меняет весь эфир."},
    {"slug": "mom", "kind": "vtubika", "file": "mom.json", "series": 7,
     "description": "Лёша три года стримит с чёрным квадратом вместо камеры: за спиной бардак, мама заходит без "
                    "стука. Первый герой-парень."},
    {"slug": "collab", "kind": "vtubika", "file": "collab.json", "series": 8,
     "description": "Миру зовут на коллаб к большой стримерше — три часа, чтобы подтянуть оформление. Два аватара "
                    "на мониторе, чат и немые паузы под действие."},
    {"slug": "karaoke", "kind": "vtubika", "file": "karaoke.json", "series": 9,
     "description": "Лиля шепчет хорроры при свечах, а Варя зовёт её на караоке. Сцена, сайлиумы, чат и длинные "
                    "паузы под песню."},
    {"slug": "tracking", "kind": "vtubika", "file": "tracking.json", "series": 10,
     "description": "У Милы посреди смешной истории отваливается трекинг, модель замирает с гримасой — и это "
                    "лицо становится мемом канала."},
    {"slug": "meme_pilot", "kind": "meme", "file": "meme3d_pilot.py",
     "description": "Робот Клод ворчит на своего пользователя: у других просят крутые ролики, а у него — «сколько "
                    "будет 2 + 2». Мультяшная 3D-комната; рендер этого движка в сервисе — следующий шаг, план "
                    "можно собрать и править уже сейчас."},
)
BY_SLUG = {t["slug"]: t for t in TEMPLATES}


def _source_path(tpl: dict) -> Path:
    return MEME_FILE if tpl["kind"] == "meme" else PLANS_DIR / tpl["file"]


@lru_cache(maxsize=None)
def _plan_json(slug: str) -> str:
    tpl = BY_SLUG[slug]
    path = _source_path(tpl)
    if tpl["kind"] == "meme":
        plan = I.meme_pilot_plan(path)
    else:
        plan = I.vtubika_to_plan(json.loads(path.read_text("utf-8")), path.name)
        plan["source"].pop("voice", None)  # голос оригинала (платный) не переносим: голос — рассказчика проекта
    if tpl.get("cast"):
        plan["cast"] = list(tpl["cast"])
    src = plan.get("source") or {}
    plan["source"] = {**src, "kind": "template", "template": slug, "origin": src.get("kind"), "file": tpl["file"]}
    return json.dumps(plan, ensure_ascii=False)


def template_plan(slug: str) -> dict:
    """План шаблона (ещё не проверенный под библиотеку проекта). Нет шаблона — KeyError."""
    if slug not in BY_SLUG:
        raise KeyError(f"нет шаблона «{slug}» (есть: {', '.join(BY_SLUG)})")
    return json.loads(_plan_json(slug))


def _names() -> dict[str, str]:
    names = {slug: name for _kind, slug, name, _d in D.builtin_items()}
    names["meme_desk"] = I.MEME_DESK_NAME
    return names


def available(tpl: dict) -> bool:
    return _source_path(tpl).is_file()


def catalog() -> list[dict]:
    """Шаблоны для галереи и агента: имя, описание, состав и локация, длина, миниатюра, источник."""
    names = _names()
    out = []
    for tpl in TEMPLATES:
        if not available(tpl):
            continue
        plan = template_plan(tpl["slug"])
        item = {"slug": tpl["slug"], "name": plan.get("title") or tpl["slug"], "description": tpl["description"],
                "kind": tpl["kind"], "engine": plan["engine"], "renderable": plan["engine"] in SP.RENDERABLE,
                "cast": [{"slug": c, "name": names.get(c, c)} for c in plan["cast"]],
                "location": {"slug": plan["location"], "name": names.get(plan["location"], plan["location"])},
                "lines": len(plan["lines"]), "length_sec": SP.estimate_sec(plan),
                "thumb": THUMB_URL.format(slug=tpl["slug"]) if thumb_path(tpl["slug"]) else None,
                "source": f"tools/{'' if tpl['kind'] == 'meme' else 'vtubika_plans/'}{tpl['file']}"}
        if tpl.get("series"):
            item["series"] = tpl["series"]
        if tpl["kind"] == "vtubika":
            item["note"] = VTUBIKA_NOTE
        out.append(item)
    return out


def thumb_path(slug: str) -> Path | None:
    path = THUMBS_DIR / f"{slug}.jpg"
    return path if slug in BY_SLUG and path.is_file() else None


def overrides_to_changes(overrides: dict | None) -> list[dict]:
    """Правки поверх шаблона: поля серии (title, cast, location, narrator, presets, …), lines — {id реплики: текст
    или {поля}}, changes — правки по шагам как у series3d_update_plan."""
    ov = dict(overrides or {})
    unknown = [k for k in ov if k not in (*SP.TOP_FIELDS, "lines", "changes")]
    if unknown:
        raise SP.PlanError([f"overrides: неизвестное поле «{k}» (можно: {', '.join(SP.TOP_FIELDS)}, lines, changes)"
                            for k in unknown])
    changes: list[dict] = [{"op": "set", "field": k, "value": copy.deepcopy(ov[k])} for k in SP.TOP_FIELDS if k in ov]
    lines = ov.get("lines") or {}
    if not isinstance(lines, dict):
        raise SP.PlanError(["overrides.lines: {id реплики: новый текст или {поля реплики}}"])
    for lid, value in lines.items():
        changes.append({"op": "line", "id": lid, "set": value if isinstance(value, dict) else {"text": str(value)}})
    extra = ov.get("changes") or []
    if not isinstance(extra, list):
        raise SP.PlanError(["overrides.changes: список правок {op, …}"])
    return changes + extra


def from_template(store: Any, project_id: int, template: str, overrides: dict | None = None, *,
                  author: str = "agent") -> dict:
    """Новая серия-черновик из шаблона в проекте. Ничего не рендерит и не тратит."""
    from app import projects as P
    from app import s3d_service as SVC

    pid = int(project_id)
    P.get_project(store, pid, include_sources=False)
    tpl = BY_SLUG.get(str(template or ""))
    if tpl is None:
        raise KeyError(f"нет шаблона «{template}» (есть: {', '.join(BY_SLUG)})")
    if not available(tpl):
        raise ValueError(f"файл шаблона «{tpl['file']}» не найден в сервисе")
    base = template_plan(tpl["slug"])
    plan = SP.apply_changes(base, overrides_to_changes(overrides))
    old_text = {ln.get("id"): ln.get("text") for ln in base["lines"]}
    notes = [n for ln in plan.get("lines") or [] if ln.get("text") != old_text.get(ln.get("id")) for n in drop_stale_anchors(ln)]
    builtin = {slug for _k, slug, _n, _d in D.builtin_items()}
    need = [s for s in [*(plan.get("cast") or []), plan.get("location"), *(plan.get("props") or [])] if s in builtin]
    if need:
        D.seed_builtins(store, pid, need)
    if plan.get("location") == "meme_desk":
        I.ensure_meme_desk(store, pid)
    lib, cfg = D.library(store, pid), D.get_settings(store, pid)
    clean, warnings = SP.validate_plan(plan, lib, cfg)
    ep = D.create_episode(store, pid, clean.get("title") or tpl["slug"], clean, author=author,
                          note=f"из шаблона «{clean.get('title') or tpl['slug']}»", source=clean.get("source"))
    return {**SVC.episode_full(store, int(ep["id"])), "warnings": notes + warnings, "template": tpl["slug"]}


def drop_stale_anchors(line: dict) -> list[str]:
    """Реплику переписали — якоря ``at`` (слово, с которого начинается кадр, жест или событие), которых в новом тексте
    нет, снимаются: кадр/событие встаёт на начало реплики (у событий истории ``vtubika.do``/``vrm`` — так же), вместо
    ошибки плана или падения рендера. Возвращает пояснения для предупреждений."""
    text = str(line.get("text") or "")
    dropped: list[str] = []
    vt = line.get("vtubika") if isinstance(line.get("vtubika"), dict) else {}
    groups = [line.get("shots"), line.get("performance"), line.get("events"), vt.get("do"), vt.get("vrm")]
    for group in groups:
        for item in group or []:
            for key in ("at", "until"):  # until — слово конца окна события истории
                word = item.get(key) if isinstance(item, dict) else None
                if isinstance(word, str) and not SP.anchor_ok(text, word):
                    item.pop(key)
                    dropped.append(word)
    if not dropped:
        return []
    words = ", ".join(f"«{w}»" for w in dict.fromkeys(dropped))
    return [f"реплика {line.get('id')}: текст поменялся — привязки к словам {words} сняты, кадры и события с начала реплики"]
