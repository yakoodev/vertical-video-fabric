"""Правила соло-ДнД «Мира от зрителей» (проект 10): статы, наборы статов, стартовые предметы, бросок d20, урон и
смерть, создание героини по голосам зрителей. Чистые функции без сети; дизайн — docs/design/viewer-turn-game.md.

* Четыре стата — СИЛА, ЛОВКОСТЬ, УМ, ВОЛЯ; значение стата — сразу бонус к d20 (−1…+3). HP — отдельно.
* Бросок: d20 + стат + предмет против сложности (8 легко, 12 обычно, 15 трудно, 18 почти невозможно).
  Натуральная 20 — крит-успех (успех всегда, с бонусом сюжета), натуральная 1 — крит-провал (провал всегда и
  неприятность сверху). Кубик честный и проверяемый: число — из sha256 от открытой строки (номер серии + id
  победившего коммента), считается до сценария; перебросов нет (``roll_d20``).
* Урон по риску: лёгкий 1–2, средний 2–4, тяжёлый 3–6 (тот же хеш); 0 HP — «на краю» (дизайн: «Смерть»).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

STATS = ("СИЛА", "ЛОВКОСТЬ", "УМ", "ВОЛЯ")
SHORT = {"СИЛА": "СИЛ", "ЛОВКОСТЬ": "ЛОВ", "УМ": "УМ", "ВОЛЯ": "ВОЛ"}

# Наборы статов: название-характер, бонусы (сумма 4), HP. Номер — как в меню серии 0.
PRESETS: dict[int, dict[str, Any]] = {
    1: {"title": "ТЯЖЁЛАЯ РУКА", "say": "тяжёлая рука", "stats": {"СИЛА": 3, "ЛОВКОСТЬ": 0, "УМ": 0, "ВОЛЯ": 1}, "hp": 12},
    2: {"title": "ТИХАЯ ТЕНЬ", "say": "тихая тень", "stats": {"СИЛА": 0, "ЛОВКОСТЬ": 3, "УМ": 1, "ВОЛЯ": 0}, "hp": 9},
    3: {"title": "ХОЛОДНАЯ ГОЛОВА", "say": "холодная голова", "stats": {"СИЛА": 0, "ЛОВКОСТЬ": 0, "УМ": 2, "ВОЛЯ": 2}, "hp": 10},
}

# Стартовые предметы: механика — бонус к броскам своего типа (tags), урон, заряды лечения, особое.
ITEMS: dict[int, dict[str, Any]] = {
    1: {"id": "flashlight", "title": "ФОНАРИК", "say": "фонарик", "sub": "+2: искать, видеть, пугать",
        "bonus": 2, "tags": ("look", "search", "scare"), "charges": None,
        "note": "светит конусом (свет сцены); батарейки — 6 серий, потом нужно искать"},
    2: {"id": "crowbar", "title": "РЖАВЫЙ ЛОМ", "say": "ржавый лом", "sub": "+2: ломать, драться; урон 2",
        "bonus": 2, "tags": ("break", "fight"), "damage": 2, "charges": None,
        "note": "на крит-провале в драке гнётся: бонус +1 до ремонта"},
    3: {"id": "medkit", "title": "АПТЕЧКА", "say": "аптечка", "sub": "+4 HP ×3, спасёт на 0 HP",
        "bonus": 0, "tags": (), "heal": 4, "charges": 3,
        "note": "на 0 HP тратит заряд сама — героиня встаёт с 1 HP"},
}

DC = {"easy": 8, "normal": 12, "hard": 15, "extreme": 18}
DAMAGE = {"easy": (1, 2), "normal": (2, 4), "hard": (3, 6), "extreme": (3, 6)}

# Свободный текст → стат и тег предмета: ключевые основы слов (первый совпавший по порядку).
_STAT_HINTS: list[tuple[str, str, str]] = [
    (r"\b(ударь|бей|бить|драть|дерись|атаку|отбива)", "СИЛА", "fight"),
    (r"\b(лома|слома|взлома|выби|выбе|вышиб|толк|подним|тащ|тян|сдвин|отодвин|вскро|открой двер)", "СИЛА", "break"),
    (r"\b(беги|бег|убег|прыг|перепрыг|залез|лезь|карабк|увернис|спрячь|прячь|прокрад|красть|укради|тихо)", "ЛОВКОСТЬ", ""),
    (r"\b(осмотр|смотри|посмотр|ищи|поищ|обыщ|найди|прочит|читай|изуч|вспомн|подума|разгада|почин|чини)", "УМ", "look"),
    (r"\b(не бойся|держись|терпи|молись|кричи|позови|говори|поговор|уговор|успокой|сопротив|иди к нему)", "ВОЛЯ", "scare"),
]
_RISKY = r"\b(прыг|ударь|бей|бить|драть|дерись|атаку|беги|убег|залез|лезь|ломай|сломай|взломай|выбей|спустись|в темноту|в туман|открой двер)"


def preset_line(n: int) -> str:
    """Статы набора в одну строку меню: «СИЛ+3 ЛОВ+0 УМ+0 ВОЛ+1 HP12»."""
    p = PRESETS[n]
    return " ".join(f"{SHORT[k]}{'+' if v >= 0 else '−'}{abs(v)}" for k, v in p["stats"].items()) + f" HP{p['hp']}"


def new_heroine(name: str, preset: int, item: int) -> dict[str, Any]:
    """Героиня после серии 0: имя, статы набора, HP, предмет (с зарядами)."""
    if preset not in PRESETS or item not in ITEMS:
        raise ValueError("набор статов и предмет — номера 1..3")
    p, it = PRESETS[preset], ITEMS[item]
    return {"name": name, "preset": preset, "stats": dict(p["stats"]), "hp": p["hp"], "hp_max": p["hp"],
            "items": [{"id": it["id"], "title": it["title"], "charges": it.get("charges")}], "flags": {}, "wisp_dim": 0,
            "episode": 0}


def _hash_int(seed: str, salt: str, lo: int, hi: int) -> int:
    h = hashlib.sha256(f"{seed}|{salt}".encode("utf-8")).digest()
    return lo + int.from_bytes(h[:8], "big") % (hi - lo + 1)


def roll_d20(seed: str) -> int:
    """Честный d20: число из sha256 открытой строки (зрители могут пересчитать). Строка — до сценария:
    ``f"серия {n}|{id победившего коммента}"``."""
    return _hash_int(seed, "d20", 1, 20)


def item_bonus(heroine: dict, tag: str) -> int:
    if not tag:
        return 0
    best = 0
    for it in heroine.get("items") or []:
        spec = next((v for v in ITEMS.values() if v["id"] == it["id"]), None)
        if spec and tag in spec["tags"]:
            best = max(best, int(spec["bonus"]) - (1 if it.get("bent") else 0))
    return best


def resolve(heroine: dict, stat: str, difficulty: str, seed: str, tag: str = "") -> dict[str, Any]:
    """Бросок по правилам: d20 + стат + предмет против сложности; урон на провале. Героиню не меняет
    (``apply_result`` — отдельно, после одобрения владельцем)."""
    if stat not in STATS:
        raise ValueError(f"стат: {', '.join(STATS)}")
    if difficulty not in DC:
        raise ValueError(f"сложность: {', '.join(DC)}")
    die = roll_d20(seed)
    bonus = int(heroine["stats"][stat]) + item_bonus(heroine, tag)
    total = die + bonus
    crit = "success" if die == 20 else "fail" if die == 1 else ""
    ok = crit == "success" or (crit != "fail" and total >= DC[difficulty])
    lo, hi = DAMAGE[difficulty]
    damage = 0 if ok else _hash_int(seed, "dmg", lo, hi) + (1 if crit == "fail" else 0)
    return {"stat": stat, "difficulty": difficulty, "dc": DC[difficulty], "die": die, "bonus": bonus, "total": total,
            "ok": ok, "crit": crit, "damage": damage, "seed": seed}


def apply_result(heroine: dict, result: dict) -> dict[str, Any]:
    """Новое состояние героини после броска: HP, аптечка на 0 HP, «на краю» (edge) — без мутации входа."""
    h = {**heroine, "items": [dict(i) for i in heroine.get("items") or []], "flags": dict(heroine.get("flags") or {})}
    h["hp"] = max(0, int(h["hp"]) - int(result.get("damage", 0)))
    if h["hp"] == 0:
        kit = next((i for i in h["items"] if i["id"] == "medkit" and (i.get("charges") or 0) > 0), None)
        if kit:
            kit["charges"] -= 1
            h["hp"] = 1
            h["flags"]["saved_by_medkit"] = True
        else:
            h["flags"]["edge"] = True  # 0 HP: следующая серия — «на краю» (дизайн: «Смерть»)
    return h


def classify_free_text(text: str) -> dict[str, Any]:
    """Свободное действие зрителя → стат, тег предмета и риск (правила без сети; сомнения решает классификатор
    viewer_comments). Нет совпадений — УМ, без риска (осмотреться/подумать — безопасный разбор)."""
    low = text.casefold().replace("ё", "е")
    stat, tag = "УМ", ""
    for rx, st, tg in _STAT_HINTS:
        if re.search(rx, low):
            stat, tag = st, tg
            break
    risky = bool(re.search(_RISKY, low))
    return {"stat": stat, "tag": tag, "risky": risky, "difficulty": "normal" if risky else "easy"}


# --------------------------------------------------------------------------- голоса серии 0

_NAME_RX = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]{1,15}$")
_NUM_WORDS = {"один": 1, "одна": 1, "первый": 1, "первая": 1, "два": 2, "две": 2, "второй": 2, "вторая": 2,
              "три": 3, "третий": 3, "третья": 3}
_STOP = {"имя", "статы", "стат", "предмет", "номер", "она", "её", "ее", "пусть", "будет", "зовут", "назовите", "давай",
         "давайте", "хочу", "это", "и", "а", "или", "с", "в", "на"} | set(_NUM_WORDS)


def parse_creation(text: str) -> dict[str, Any]:
    """Коммент серии 0 → {name, preset, item}: имя — первое слово из букв (не служебное), номера — по порядку
    (первый — статы, второй — предмет; «статы 2», «предмет 3» — явные). Чего нет — None."""
    low = text.casefold().replace("ё", "е")
    out: dict[str, Any] = {"name": None, "preset": None, "item": None}
    m = re.search(r"стат\w*\D{0,3}([1-3])", low)
    if m:
        out["preset"] = int(m.group(1))
    m = re.search(r"предмет\w*\D{0,3}([1-3])", low)
    if m:
        out["item"] = int(m.group(1))
    nums = [int(x) for x in re.findall(r"(?<!\d)([1-3])(?!\d)", low)]
    nums += [_NUM_WORDS[w] for w in re.findall(r"[а-я]+", low) if w in _NUM_WORDS]
    rest = [n for n in nums]
    for key in ("preset", "item"):
        if out[key] is not None and out[key] in rest:
            rest.remove(out[key])
    for key in ("preset", "item"):
        if out[key] is None and rest:
            out[key] = rest.pop(0)
    for tok in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё'-]*", text):
        if tok.casefold().replace("ё", "е") in _STOP:
            continue
        if _NAME_RX.match(tok):
            out["name"] = tok[:1].upper() + tok[1:].lower()
        break
    return out
