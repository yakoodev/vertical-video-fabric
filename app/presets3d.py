"""3D-пресеты: реестр и проверка плана (стиль картинки, свет, камера, атмосфера, настроения, окружения, игра
персонажа, переходы) для движка сцен ``node/scene3d``.

Одна правда — движок: имена, русские описания и параметры живут в ``node/scene3d/src/presets/*.ts``, сюда они
приходят снимком ``node/scene3d/presets/catalog.json`` (``node node/scene3d/presets_dump.mjs``; тест сверяет
свежесть). Сцена или план эпизода несёт блок ``presets``::

    {"look": "anime_soft", "light": "golden_hour", "room": "cozy_desk", "mood": "night_rain",
     "fx": ["film_grain", {"name": "rain", "amount": 0.6}],
     "camera": [{"move": "slow_push", "t0": 0, "t1": 4, "target": "hero"}, {"move": "crash_zoom", "t0": 4, "t1": 6}],
     "transitions": [{"kind": "whip", "t": 4.0}],
     "performance": [{"who": "hero", "preset": "shocked", "t": 1.2}],
     "subject": "hero"}

Любое значение — имя или объект ``{"name": ..., ...параметры}`` (камера — ``move``, переход — ``kind``, игра —
``preset``). Движок разворачивает блок при загрузке сцены (presets/index.ts, ``expandPresets``); здесь — только
проверка с понятными ошибками и подсказками (``validate_presets``), чтобы агент и инструменты историй не узнавали
об опечатке от рендера.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "node" / "scene3d" / "presets" / "catalog.json"
DUMP_SCRIPT = ROOT / "node" / "scene3d" / "presets_dump.mjs"

CATEGORIES = ("look", "light", "camera", "fx", "mood", "room", "emotion", "gesture", "transition")
#: Поле плана → категория(и) пресетов.
FIELDS = {"look": ("look",), "light": ("light",), "mood": ("mood",), "room": ("room",), "fx": ("fx",),
          "camera": ("camera",), "transitions": ("transition",), "performance": ("emotion", "gesture")}
EXTRA_FIELDS = ("subject", "heading")

_CACHE: dict[str, Any] = {}


class PresetError(ValueError):
    """План с ошибками: ``problems`` — по одной строке на ошибку (по-русски, с подсказкой)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def catalog() -> dict:
    """Каталог по категориям: {категория: {title, field, presets: [{name, title, ru, params, ...}]}}."""
    mtime = CATALOG_PATH.stat().st_mtime
    if _CACHE.get("mtime") != mtime:
        _CACHE["data"] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        _CACHE["mtime"] = mtime
    return _CACHE["data"]


def names(category: str) -> list[str]:
    return [p["name"] for p in catalog().get(category, {}).get("presets", [])]


def get(category: str, name: str) -> dict | None:
    for p in catalog().get(category, {}).get("presets", []):
        if p["name"] == name:
            return p
    return None


def list_presets(category: str | None = None, q: str | None = None, *, full: bool = False) -> list[dict]:
    """Пресеты (все или категории), поиск ``q`` по имени, названию, описанию и тегам. ``full`` — с параметрами."""
    query = (q or "").strip().lower()
    out = []
    for cat in CATEGORIES:
        if category and cat != category:
            continue
        for p in catalog().get(cat, {}).get("presets", []):
            hay = f"{p['name']} {p.get('title', '')} {p.get('ru', '')} {' '.join(p.get('tags') or [])}".lower()
            if query and query not in hay:
                continue
            item = {"category": cat, "name": p["name"], "title": p.get("title"), "ru": p.get("ru")}
            if p.get("status") == "slot":
                item["status"] = "slot"
            if full:
                for key in ("params", "note", "light", "fx", "duration", "engines", "suggest", "props", "anchors", "tags"):
                    if p.get(key) is not None:
                        item[key] = p[key]
            out.append(item)
    return out


def _hint(category: str, name: str) -> str:
    near = difflib.get_close_matches(str(name), names(category), n=3, cutoff=0.45)
    return f" (может, {', '.join(near)}?)" if near else f" (есть: {', '.join(names(category))})"


def _named(value: Any, key: str = "name") -> tuple[str | None, dict]:
    if isinstance(value, str):
        return value, {}
    if isinstance(value, dict):
        n = value.get(key)
        return (str(n) if n is not None else None), {k: v for k, v in value.items() if k != key}
    return None, {}


def _check_params(category: str, name: str, params: dict, where: str, problems: list[str]) -> None:
    meta = get(category, name) or {}
    known = {p["name"]: p for p in meta.get("params") or []}
    for key, value in params.items():
        p = known.get(key)
        if not p:
            continue
        if p["type"] == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"{where}: «{key}» — число ({p.get('ru', '')})")
            elif ("min" in p and value < p["min"]) or ("max" in p and value > p["max"]):
                problems.append(f"{where}: «{key}» = {value} вне [{p.get('min')}, {p.get('max')}] ({p.get('ru', '')})")
        elif p["type"] == "enum" and value not in (p.get("options") or []):
            problems.append(f"{where}: «{key}» — одно из {', '.join(p.get('options') or [])}")
        elif p["type"] == "color" and not (isinstance(value, str) and value.startswith("#") and len(value) in (4, 7)):
            problems.append(f"{where}: «{key}» — цвет «#rrggbb»")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_presets(block: Any, *, duration: float | None = None, characters: list[str] | None = None) -> dict:
    """Проверить блок ``presets``: возвращает его же (нормализованные строки — как есть); ошибки — PresetError
    со списком проблем. ``duration`` — длина сцены (времена камеры/переходов/игры не дальше), ``characters`` —
    id персонажей (для ``who`` и целей камеры)."""
    if not isinstance(block, dict):
        raise PresetError(["presets — объект {look, light, room, mood, fx, camera, transitions, performance}"])
    problems: list[str] = []
    for key in block:
        if key not in FIELDS and key not in EXTRA_FIELDS:
            problems.append(f"неизвестное поле presets.{key} (есть: {', '.join([*FIELDS, *EXTRA_FIELDS])})")
    for field in ("look", "light", "mood", "room"):
        if field not in block:
            continue
        name, params = _named(block[field])
        if not name:
            problems.append(f"presets.{field}: имя пресета или {{\"name\": ...}}")
        elif not get(field, name):
            problems.append(f"presets.{field}: нет «{name}»{_hint(field, name)}")
        else:
            _check_params(field, name, params, f"presets.{field}", problems)
    fx = block.get("fx")
    if fx is not None:
        for i, item in enumerate(fx if isinstance(fx, list) else [fx]):
            name, params = _named(item)
            if not name or not get("fx", name):
                problems.append(f"presets.fx[{i}]: нет «{name}»{_hint('fx', name or '')}")
            else:
                _check_params("fx", name, params, f"presets.fx[{i}]", problems)
    cam = block.get("camera")
    if cam is not None:
        moves = cam if isinstance(cam, list) else [cam]
        last = -1.0
        for i, m in enumerate(moves):
            name, params = _named(m, "move")
            where = f"presets.camera[{i}]"
            if not name or not get("camera", name):
                problems.append(f"{where}: нет движения «{name}»{_hint('camera', name or '')}")
                continue
            _check_params("camera", name, params, where, problems)
            t0, t1 = params.get("t0"), params.get("t1")
            for k, v in (("t0", t0), ("t1", t1), ("duration", params.get("duration"))):
                if v is not None and (not _num(v) or v < 0):
                    problems.append(f"{where}: «{k}» — неотрицательное число секунд")
            if _num(t0) and _num(t1) and t1 <= t0:
                problems.append(f"{where}: t1 ({t1}) должно быть больше t0 ({t0})")
            if _num(t0) and t0 < last - 1e-6:
                problems.append(f"{where}: движения по порядку времени (t0 {t0} < конца прошлого {last})")
            if _num(t1):
                last = float(t1)
                if duration is not None and t1 > duration + 1e-6:
                    problems.append(f"{where}: t1 {t1} дальше конца сцены {duration}")
            if name == "over_shoulder" and characters is not None and params.get("who") not in characters:
                problems.append(f"{where}: over_shoulder нужен who — id персонажа ({', '.join(characters) or 'персонажей нет'})")
    trs = block.get("transitions")
    if trs is not None:
        for i, tr in enumerate(trs if isinstance(trs, list) else [trs]):
            name, params = _named(tr, "kind")
            where = f"presets.transitions[{i}]"
            if not name or not get("transition", name):
                problems.append(f"{where}: нет перехода «{name}»{_hint('transition', name or '')}")
            if not _num(params.get("t")):
                problems.append(f"{where}: нужен t — момент склейки, с")
            elif duration is not None and params["t"] > duration + 1e-6:
                problems.append(f"{where}: t {params['t']} дальше конца сцены {duration}")
            if params.get("half") not in (None, "in", "out"):
                problems.append(f"{where}: half — in или out")
    perf = block.get("performance")
    if perf is not None:
        for i, cue in enumerate(perf if isinstance(perf, list) else [perf]):
            where = f"presets.performance[{i}]"
            if not isinstance(cue, dict):
                problems.append(f"{where}: {{who, preset, t}}")
                continue
            name = cue.get("preset")
            if not (get("emotion", name) or get("gesture", name)):
                allp = [*names("emotion"), *names("gesture")]
                near = difflib.get_close_matches(str(name), allp, n=3, cutoff=0.45)
                problems.append(f"{where}: нет эмоции/жеста «{name}»" + (f" (может, {', '.join(near)}?)" if near else f" (есть: {', '.join(allp)})"))
            if not _num(cue.get("t")):
                problems.append(f"{where}: нужен t — когда начать, с")
            if characters is not None and cue.get("who") is not None and cue["who"] not in characters:
                problems.append(f"{where}: нет персонажа «{cue['who']}» ({', '.join(characters) or 'персонажей нет'})")
    if problems:
        raise PresetError(problems)
    return block


def compose(base: dict | None = None, **fields: Any) -> dict:
    """Сцена с блоком presets: поля плана (look=..., light=..., camera=[...]) поверх ``base`` (спека сцены).
    Проверяет блок и возвращает копию спеки."""
    spec = json.loads(json.dumps(base or {}))
    block = {**(spec.get("presets") or {}), **{k: v for k, v in fields.items() if v is not None}}
    chars = [c.get("id") for c in spec.get("characters") or [] if isinstance(c, dict)]
    spec["presets"] = validate_presets(block, duration=spec.get("duration"), characters=chars or None)
    return spec


def expand(spec: dict, *, timeout: float = 30) -> dict:
    """Развернуть presets сцены тем же кодом, что движок (node, без браузера) — для проверки и отладки."""
    from app.scene3d import node_bin

    with tempfile.TemporaryDirectory(prefix="vvf-presets-") as tmp:
        path = Path(tmp) / "spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([node_bin(), str(DUMP_SCRIPT), "--expand", str(path)], capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"развёртка пресетов: {(proc.stderr or '').strip()[-400:]}")
    return json.loads(proc.stdout)


def plan_cheatsheet() -> str:
    """Короткая шпаргалка полей плана для агента (по-русски)."""
    c = catalog()
    lines = ["Блок presets сцены/эпизода (имя или {\"name\": ..., параметры}):"]
    for field, cats in FIELDS.items():
        opts = ", ".join(n for cat in cats for n in names(cat))
        lines.append(f"- {field}: {opts}")
    lines.append("- subject: id персонажа или [x, y, z] — опора света и камеры по умолчанию")
    lines.append("camera: [{move, t0, t1, target: id|якорь|[x,y,z], ...параметры}] — каждое движение — новый план (склейка); "
                 "transitions: [{kind, t, duration?}]; performance: [{who, preset, t, dur?}].")
    lines.append(f"Категории: {', '.join(f'{k} — {v['title']}' for k, v in c.items())}.")
    return "\n".join(lines)
