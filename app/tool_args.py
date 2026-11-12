"""Проверка аргументов вызова по схеме инструмента — на входе MCP и чата (``agent_tools.run_tool``, ``chat_tools.run``).

Зачем. Неверное имя аргумента проходило молча (эксперимент 12.11): ``search_pack`` с ``query`` вместо ``q`` отдал
случайные звуки, ``apply_cuts`` с ``keep`` вместо ``cuts`` снял все вырезы и ответил ``cuts: 0``, ``remove_layers`` с
чужим полем ответил «нужен список ids», не сказав, какое поле ждёт. Модель не узнавала об ошибке и шла дальше.

Что проверяется:

* лишний аргумент — отказ: что ждёт инструмент (имя и тип) и «может, …?» — похожее имя (опечатка, синоним вроде
  query → q, keep → cuts);
* нет обязательного — отказ с его именем и описанием.

Старые имена, которые обработчики уже понимают (``set_format`` читает и ``aspect``, ``trim_piece`` —
``source_start``), не ломаются: допустимые имена — схема плюс то, что обработчик и его помощники реально читают из
``args`` (разбор их кода, ``handler_keys``). Так новое поле в обработчике не требует правки списка здесь.
``clip_id`` у инструментов без клипа отбрасывается молча — так MCP делал и раньше.
"""

from __future__ import annotations

import ast
import difflib
import importlib
import inspect
import textwrap
from typing import Any, Callable

# Синонимы, на которых спотыкались модели: имя, которое дали → имена, которые могли иметь в виду.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "query": ("q", "text", "search"), "search": ("q", "query"), "text": ("q", "query", "lines"),
    "q": ("query", "text"), "keep": ("cuts", "ranges", "pieces"), "remove": ("cuts", "ids"),
    "ranges": ("cuts", "pieces"), "segments": ("pieces", "cuts"), "id": ("ids", "layer_id", "clip_id"),
    "layer_ids": ("ids",), "layers_ids": ("ids",), "layer": ("id", "layer_id", "ids"), "layers": ("ids", "layers"),
    "start": ("start_sec", "from_sec", "t"), "end": ("end_sec", "to_sec"), "from": ("from_sec", "start"),
    "to": ("to_sec", "end"), "time": ("t", "at"), "seconds": ("duration", "t"), "length": ("duration",),
    "name": ("title", "label"), "label": ("title", "name"), "url": ("src", "link"), "src": ("url",),
    "project": ("project_id",), "source": ("source_id",), "clip": ("clip_id",), "style": ("preset", "mode"),
    "track": ("track_id",), "music": ("track_id",), "voice_id": ("voice",), "lang": ("language",),
}
_DEPTH = 3
_cache: dict[int, frozenset[str]] = {}


# --------------------------------------------------------------------------- #
# Какие ключи обработчик читает из args
# --------------------------------------------------------------------------- #


def _resolve(name_node: ast.AST, fn_globals: dict, local_imports: dict[str, str]) -> Callable | None:
    """Вызываемое по узлу вызова: имя из модуля, имя из локального ``from x import y`` или ``модуль.имя``."""
    try:
        if isinstance(name_node, ast.Name):
            if name_node.id in local_imports:
                module, _, attr = local_imports[name_node.id].rpartition(".")
                return getattr(importlib.import_module(module), attr, None)
            value = fn_globals.get(name_node.id)
            return value if callable(value) else None
        if isinstance(name_node, ast.Attribute) and isinstance(name_node.value, ast.Name):
            base = name_node.value.id
            if base in local_imports:
                owner: Any = importlib.import_module(local_imports[base])
            else:
                owner = fn_globals.get(base)
            if owner is None:
                return None
            value = getattr(owner, name_node.attr, None)
            return value if callable(value) else None
    except Exception:  # noqa: BLE001 - не разобрали — просто не идём глубже
        return None
    return None


def _keys_of(fn: Callable, names: set[str], depth: int, seen: set[int]) -> set[str]:
    """Строковые ключи, которые ``fn`` читает из переменных ``names`` (и передаёт помощникам — до ``depth``)."""
    fn = inspect.unwrap(fn)
    code = getattr(fn, "__code__", None)
    if code is None or id(code) in seen:
        return set()
    seen.add(id(code))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError, IndentationError):
        return set()
    func = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))), None)
    if func is None:
        return set()
    fn_globals = getattr(fn, "__globals__", {})
    local_imports: dict[str, str] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                local_imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local_imports[alias.asname or alias.name.split(".")[0]] = alias.name
    tracked = set(names)
    # args = dict(args or {}); a = {**args}; data = args — та же словарь под другим именем.
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            inner = value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict" and value.args:
                inner = value.args[0]
            if isinstance(inner, ast.BoolOp):
                inner = inner.values[0]
            if isinstance(inner, ast.Name) and inner.id in tracked:
                tracked.add(node.targets[0].id)
            elif isinstance(value, ast.Dict) and any(k is None and isinstance(v, ast.Name) and v.id in tracked
                                                     for k, v in zip(value.keys, value.values)):
                tracked.add(node.targets[0].id)
    keys: set[str] = set()

    def is_tracked(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in tracked

    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_tracked(node.func.value) \
                and node.func.attr in ("get", "pop", "setdefault") and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            keys.add(node.args[0].value)
        elif isinstance(node, ast.Subscript) and is_tracked(node.value):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                keys.add(index.value)
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str) \
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops) and any(is_tracked(c) for c in node.comparators):
            keys.add(node.left.value)
        elif isinstance(node, ast.Call) and depth > 0:
            passed = [(i, a) for i, a in enumerate(node.args) if is_tracked(a)]
            passed_kw = [k.arg for k in node.keywords if k.arg and is_tracked(k.value)]
            if not passed and not passed_kw:
                continue
            callee = _resolve(node.func, fn_globals, local_imports)
            if callee is None or not str(getattr(callee, "__module__", "") or "").startswith("app"):
                continue
            try:
                params = list(inspect.signature(callee).parameters)
            except (TypeError, ValueError):
                continue
            if inspect.ismethod(callee) and params and params[0] in ("self", "cls"):
                params = params[1:]
            targets = {params[i] for i, _a in passed if i < len(params)} | {k for k in passed_kw if k in params}
            if targets:
                keys |= _keys_of(callee, targets, depth - 1, seen)
    return keys


def handler_keys(handler: Callable) -> frozenset[str]:
    """Ключи ``args``, которые читает обработчик ``handler(ctx, args)`` и помощники, которым он их передаёт."""
    key = id(getattr(handler, "__code__", handler))
    if key not in _cache:
        try:
            params = list(inspect.signature(handler).parameters)
        except (TypeError, ValueError):
            params = []
        names = {params[1]} if len(params) > 1 else {"args"}
        try:
            _cache[key] = frozenset(_keys_of(handler, names | {"args"}, _DEPTH, set()))
        except Exception:  # noqa: BLE001 - разбор не должен ронять вызов
            _cache[key] = frozenset()
    return _cache[key]


# --------------------------------------------------------------------------- #
# Проверка
# --------------------------------------------------------------------------- #


def _type_of(spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    if spec.get("enum"):
        return "|".join(str(v) for v in spec["enum"][:8])
    kind = spec.get("type")
    if isinstance(kind, list):
        return "/".join(str(k) for k in kind)
    if kind == "array":
        item = spec.get("items") or {}
        inner = item.get("type") if isinstance(item, dict) else None
        return f"array of {inner}" if inner else "array"
    return str(kind or "")


def expected_text(props: dict, required: list[str]) -> str:
    """«ids* (array of string), camera (boolean)» — что ждёт инструмент; * — обязательный."""
    parts = []
    for name, spec in props.items():
        kind = _type_of(spec)
        parts.append(f"{name}{'*' if name in required else ''}" + (f" ({kind})" if kind else ""))
    return ", ".join(parts) if parts else "без аргументов"


def suggest(name: str, allowed: list[str]) -> list[str]:
    """Похожие имена: синоним, вхождение (layer_ids ~ ids), опечатка."""
    low = name.lower()
    out = [s for s in SYNONYMS.get(low, ()) if s in allowed]
    out += [a for a in allowed if len(a) >= 2 and len(low) >= 2 and (a in low or low in a)]
    out += difflib.get_close_matches(low, allowed, n=3, cutoff=0.6)
    return list(dict.fromkeys(a for a in out if a != name))[:3]


def check_args(tool: Any, args: dict, injected: Any = ()) -> dict | None:
    """Ответ-отказ ``{ok: false, error, expected, …}`` для неверных аргументов; None — всё в порядке.
    ``args`` правится на месте: ``clip_id`` у инструмента без него убирается (как в MCP). ``injected`` — ключи,
    которые подставил вызывающий (чат — project_id своего проекта), а не модель: их не судим."""
    schema = tool.parameters if isinstance(getattr(tool, "parameters", None), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = [r for r in schema.get("required") or [] if isinstance(r, str)]
    if "clip_id" in args and "clip_id" not in props and "clip_id" not in handler_keys(tool.handler):
        args.pop("clip_id")
    accepted = set(props) | set(handler_keys(tool.handler)) | set(injected or ())
    unknown = [] if schema.get("additionalProperties") is True else [k for k in args if k not in accepted]
    # Дано старое имя, которое обработчик понимает (aspect вместо format): обязательное он проверит сам.
    legacy = [k for k in args if k not in props and k in accepted and k not in set(injected or ())]
    missing = [] if legacy else [r for r in required if args.get(r) is None]
    if not unknown and not missing:
        return None
    allowed = list(props)
    expected = expected_text(props, required)
    problems: list[str] = []
    hints: dict[str, list[str]] = {}
    for key in unknown:
        near = suggest(key, allowed)
        if near:
            hints[key] = near
        problems.append(f"неизвестный аргумент {key}" + (f" — может, {' или '.join(near)}?" if near else ""))
    for key in missing:
        spec = props.get(key) or {}
        about = spec.get("description") if isinstance(spec, dict) else ""
        kind = _type_of(spec)
        problems.append(f"нет обязательного аргумента {key}" + (f" ({kind})" if kind else "")
                        + (f": {about}" if about else ""))
    error = f"{tool.name}: " + "; ".join(problems) + f". Ожидаются: {expected}"
    out: dict = {"ok": False, "error": error, "problems": problems, "expected": allowed}
    if unknown:
        out["unknown"] = unknown
    if hints:
        out["did_you_mean"] = hints
    if missing:
        out["missing"] = missing
    return out
