"""Правки копии страницы (app/webpages.py) — список безопасных операций над DOM.

Операция — объект ``{op, selector | find, …}``: ``selector`` — CSS-селектор (из ``outline`` версии), ``find`` —
текст на странице (берётся самый вложенный элемент с ним). ``all: true`` — все совпадения, иначе первое.

* ``set_text`` — ``text``: новый текст элемента; с ``from`` — заменить только этот кусок (цена «1 990» → «1 690»),
  тогда цель можно не давать — замена по всей странице.
* ``hide`` — спрятать, место остаётся (visibility); ``remove`` — убрать из страницы (баннер cookie).
* ``set_style`` — ``style``: {свойство CSS: значение} (без url(), скриптов и @import).
* ``add_highlight`` — ``kind`` outline | glow | pulse, ``color``, ``width``, ``radius``: подсветка CSS в самой странице.
* ``crop_to`` — видимая область для слоя (рамка элемента + ``pad``); DOM не меняет.
* ``insert_badge`` — ``text`` плашкой: ``position`` top-right | top-left | bottom-right | bottom-left | before | after,
  ``color``, ``bg``, ``size``.

Скрипты и обработчики в страницу не попадают: текст ставится как текст (textContent), стили — свойствами.
Словами («подсвети кнопку Купить») правки переводит Gemini (``instruction_to_ops``) по карте страницы ``outline``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.webpages import FOREIGN_NOTE

OPS = ("set_text", "hide", "remove", "set_style", "add_highlight", "crop_to", "insert_badge")
ALIASES = {
    "text": "set_text", "replace_text": "set_text", "change_text": "set_text", "style": "set_style",
    "highlight": "add_highlight", "outline": "add_highlight", "glow": "add_highlight", "pulse": "add_highlight",
    "crop": "crop_to", "badge": "insert_badge", "delete": "remove", "hide_element": "hide",
}
HIGHLIGHT_KINDS = ("glow", "outline", "pulse")
BADGE_POSITIONS = ("top-right", "top-left", "bottom-right", "bottom-left", "before", "after")
MAX_OPS = 30
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
PROP_RE = re.compile(r"^-?[a-z][a-z-]{0,40}$")
BAD_VALUE_RE = re.compile(r"url\s*\(|expression|javascript:|@import|<|behavior|-moz-binding", re.I)
ROOT_SELECTORS = {"html", "body", "head", ":root", "*"}


class OpsError(ValueError):
    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


def foreign_note(foreign: bool) -> str:
    return FOREIGN_NOTE if foreign else ""


def _text(value: Any, limit: int) -> str:
    raw = value if isinstance(value, str) else ("" if value is None else str(value))
    return "".join(ch for ch in raw if ch == "\n" or ch >= " ")[:limit]


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else max(lo, min(hi, number))


def _color(value: Any, default: str) -> str:
    return value if isinstance(value, str) and COLOR_RE.match(value.strip()) else default


def _target(raw: dict, where: str, problems: list[str], required: bool = True) -> dict | None:
    selector = _text(raw.get("selector"), 300).strip()
    find = _text(raw.get("find"), 200).strip()
    if selector:
        if selector.lower() in ROOT_SELECTORS:
            problems.append(f"{where}: целиком всю страницу так не правят — нужен конкретный элемент")
            return None
        return {"selector": selector, **({"all": True} if raw.get("all") is True else {})}
    if find:
        return {"find": find, **({"all": True} if raw.get("all") is True else {})}
    if required:
        problems.append(f"{where}: нужна цель — selector (из outline) или find (текст на странице)")
        return None
    return {}


def validate_ops(raw_ops: Any) -> tuple[list[dict], list[str]]:
    """Проверить и привести правки. Вернуть (годные, проблемы); ни одной годной — OpsError."""
    if isinstance(raw_ops, dict):
        raw_ops = raw_ops.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise OpsError("нужен список правок ops")
    problems: list[str] = []
    if len(raw_ops) > MAX_OPS:
        problems.append(f"не больше {MAX_OPS} правок за раз — лишние отброшены")
    clean: list[dict] = []
    for index, raw in enumerate(raw_ops[:MAX_OPS]):
        where = f"ops[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: нужен объект")
            continue
        kind = str(raw.get("op") or raw.get("type") or "").strip()
        kind = ALIASES.get(kind, kind)
        if kind not in OPS:
            problems.append(f"{where}: нет правки «{kind}» — есть {', '.join(OPS)}")
            continue
        if kind == "set_text":
            text = _text(raw.get("text") if "text" in raw else raw.get("to"), 500)
            source = _text(raw.get("from"), 200)
            target = _target(raw, where, problems, required=not source)
            if target is None:
                continue
            if not text.strip() and not source:
                problems.append(f"{where}: set_text без text")
                continue
            clean.append({"op": kind, **target, "text": text, **({"from": source} if source else {})})
        elif kind in ("hide", "remove"):
            target = _target(raw, where, problems)
            if target is not None:
                clean.append({"op": kind, **target})
        elif kind == "set_style":
            target = _target(raw, where, problems)
            style = raw.get("style")
            if target is None:
                continue
            if not isinstance(style, dict) or not style:
                problems.append(f"{where}: set_style без style {{свойство: значение}}")
                continue
            good = {}
            for prop, value in list(style.items())[:12]:
                prop = str(prop).strip().lower()
                value = _text(value, 200).strip()
                if not PROP_RE.match(prop) or not value or BAD_VALUE_RE.search(value):
                    problems.append(f"{where}: свойство {prop}: {value!r} не принято")
                    continue
                good[prop] = value
            if good:
                clean.append({"op": kind, **target, "style": good})
        elif kind == "add_highlight":
            target = _target(raw, where, problems)
            if target is None:
                continue
            hl = str(raw.get("kind") or raw.get("style") or ("pulse" if raw.get("op") == "pulse" else "glow"))
            clean.append({"op": kind, **target, "kind": hl if hl in HIGHLIGHT_KINDS else "glow",
                          "color": _color(raw.get("color"), "#ffd400"), "width": _num(raw.get("width"), 6, 2, 20),
                          "radius": _num(raw.get("radius"), 10, 0, 60)})
        elif kind == "crop_to":
            target = _target(raw, where, problems)
            if target is not None:
                target.pop("all", None)
                clean.append({"op": kind, **target, "pad": _num(raw.get("pad"), 24, 0, 300)})
        elif kind == "insert_badge":
            target = _target(raw, where, problems)
            text = _text(raw.get("text"), 40).strip()
            if target is None:
                continue
            if not text:
                problems.append(f"{where}: insert_badge без text")
                continue
            position = str(raw.get("position") or "top-right")
            clean.append({"op": kind, **target, "text": text,
                          "position": position if position in BADGE_POSITIONS else "top-right",
                          "color": _color(raw.get("color"), "#ffffff"), "bg": _color(raw.get("bg"), "#ff2d55"),
                          "size": _num(raw.get("size"), 18, 10, 60)})
    if not clean:
        raise OpsError("ни одной годной правки", problems)
    return clean, problems


# --------------------------------------------------------------------------- #
# Словами → правки (Gemini)
# --------------------------------------------------------------------------- #

PROMPT = """Ты правишь копию веб-страницы для рекламного ролика. Правки — только списком операций над DOM (JSON).
Страница: {title} ({url}), ширина {width} CSS px.
Карта страницы (вид | CSS-селектор | текст | рамка x,y,w,h в CSS px), сверху вниз:
{outline}

Операции (цель — "selector" из карты ИЛИ "find" — точный кусок текста на странице):
- {{"op":"set_text","selector":"…","text":"новый текст"}}; заменить кусок: {{"op":"set_text","find":"1 990","from":"1 990","text":"1 690"}}
- {{"op":"hide","selector":"…"}} — спрятать (место остаётся); {{"op":"remove","selector":"…"}} — убрать (баннер cookie, попап)
- {{"op":"set_style","selector":"…","style":{{"color":"#ff0000","font-size":"28px"}}}}
- {{"op":"add_highlight","selector":"…","kind":"glow|outline|pulse","color":"#ffd400"}}
- {{"op":"crop_to","selector":"…","pad":24}} — показывать в ролике только этот блок
- {{"op":"insert_badge","selector":"…","text":"−15%","position":"top-right|top-left|bottom-right|bottom-left|before|after","bg":"#ff2d55"}}
Делай ровно то, что просит владелец, не больше. Цены и текст меняй только если об этом сказано.
Ответ — JSON {{"ops":[…],"comment":"коротко, что сделано или чего не нашёл"}}.

Просьба владельца: {instruction}"""


def outline_text(outline: list[dict], limit: int = 120) -> str:
    rows = []
    for item in outline[:limit]:
        box = ",".join(str(int(v)) for v in item.get("box") or [])
        rows.append(f"{item.get('kind')} | {item.get('sel')} | {str(item.get('text') or '')[:100]} | {box}")
    return "\n".join(rows) or "(пусто)"


def instruction_to_ops(instruction: str, outline: list[dict], title: str = "", url: str = "", width: Any = 390,
                       client: Any = None, model: str | None = None) -> dict:
    """Просьба словами → {ops, comment}. Gemini недоступен — OpsError с понятным текстом (платный шлюз не зовём)."""
    from app.ai.gemini import GeminiClient, _extract_response_text
    from app.settings import settings
    from app.web_search import json_object

    wish = " ".join(str(instruction or "").split())[:1000]
    if not wish:
        raise OpsError("нужна просьба словами или список ops")
    if client is None and not settings.gemini_api_key:
        raise OpsError("Gemini не настроен — передай правки списком ops")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT.format(
            title=title or "без заголовка", url=url, width=width, outline=outline_text(outline), instruction=wish)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
    }
    try:
        response = (client or GeminiClient(timeout=90)).generate_content(model or settings.gemini_text_model, payload)
        parsed = json_object(_extract_response_text(response))
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise OpsError(f"Gemini не перевёл просьбу в правки ({str(exc)[:160]}) — передай правки списком ops") from exc
    ops = parsed.get("ops") if isinstance(parsed, dict) else None
    if not isinstance(ops, list) or not ops:
        comment = str((parsed or {}).get("comment") or "") if isinstance(parsed, dict) else ""
        raise OpsError("Gemini не нашёл, что править" + (f": {comment[:200]}" if comment else ""))
    return {"ops": ops, "comment": str(parsed.get("comment") or "")[:300]}


def edit(store: Any, page_id: int, instruction: str = "", ops: Any = None, base: int | None = None,
         made_by: str = "owner", client: Any = None, runner: Any = None) -> dict:
    """Правка страницы словами (Gemini → ops) или готовым списком ops → новая версия (app/webpages.apply_ops)."""
    from app import webpages as W

    comment = ""
    if ops in (None, "", []):
        page = W.get_page(store, page_id)
        meta = W.version_meta(store, page_id, base)
        got = instruction_to_ops(instruction, meta.get("outline") or [], page.get("title") or "", page.get("url") or "",
                                 meta.get("page_width") or 390, client=client)
        ops, comment = got["ops"], got["comment"]
    clean, problems = validate_ops(ops)
    out = W.apply_ops(store, page_id, clean, base=base, instruction=instruction, made_by=made_by, runner=runner)
    out["problems"] = problems + out["problems"]
    out["ops"] = clean
    if comment:
        out["comment"] = comment
    return out
