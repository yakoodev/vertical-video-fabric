"""«Видеомемы»: библиотека коротких реакций (0.5–4 с) для мем-монтажа — человек орёт «вау», молится, кричит.

Видеомем = «Файл для монтажа» вида ``video`` (``montage_assets``: сам файл, длительность, размеры, звук) плюс
строка ``video_memes`` с тем, что нужно монтажёру выбрать мем по смыслу:

* ``description`` — что значит / что происходит, ``when_to_use`` — когда вставлять;
* ``transcript`` (+ ``transcript_lang``, слова с временем) — что сказано, Whisper в фоне после загрузки;
* ``tags`` — список (шок, фейл, победа, кринж, смех, злость, тиммейт…);
* ``has_real_people`` — на видео реальные люди (по умолчанию да: реакции почти всегда с людьми);
* ``source_url``, ``license_note`` (по умолчанию «без открытой лицензии»);
* ``approved`` — владелец одобрил. **Неодобренный мем ИИ не ставит никогда**: ``place_video_meme`` и
  ``add_insert`` отказывают, ИИ-монтаж и ``list_assets`` его не видят.

В клип мем ставится обычной вставкой (``render.inserts``, app/montage_assets.py): ``full`` — врезка во весь кадр
со своим звуком (звук игры глушится или приглушается), ``pip`` — окошко поверх клипа. Удаление мема удаляет и файл;
удаление файла из «Файлов для монтажа» удаляет мем (ON DELETE CASCADE).

Здесь — таблица, чтение/правка и поиск. Загрузка, подрезка и расшифровка — app/video_meme_ingest.py, ручки API —
app/video_memes_api.py, инструменты агента — app/video_meme_tools.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

DEFAULT_LICENSE = "без открытой лицензии"
# Подсказка в форме и для агента; теги свободные — это не перечень-ограничение.
SUGGESTED_TAGS = ("шок", "фейл", "победа", "кринж", "смех", "злость", "тиммейт", "молитва", "крик", "вау",
                  "грусть", "страх", "радость", "неловко", "уважение", "отказ")
TRANSCRIPT_STATES = ("pending", "running", "done", "empty", "failed", "off")
MAX_TAGS = 16
MAX_TAG_LEN = 32
TEXT_LIMITS = {"description": 1000, "when_to_use": 1000, "transcript": 4000, "source_url": 2000,
               "license_note": 300, "transcript_lang": 16}
# Мем — короткая врезка: длиннее в клип не ставим (пусть подрежут при загрузке).
MAX_MEME_SEC = 8.0
# Полнокадровая врезка мема со своим звуком: до 4 с (обычная вставка во весь кадр — 3 с, app/layer_lint.py).
MEME_FULL_MAX_SEC = 4.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS video_memes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL UNIQUE REFERENCES montage_assets(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    when_to_use TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    transcript TEXT NOT NULL DEFAULT '',
    transcript_lang TEXT NOT NULL DEFAULT '',
    transcript_words TEXT NOT NULL DEFAULT '[]',
    transcript_status TEXT NOT NULL DEFAULT 'pending',
    transcript_error TEXT NOT NULL DEFAULT '',
    has_real_people INTEGER NOT NULL DEFAULT 1,
    source_url TEXT NOT NULL DEFAULT '',
    license_note TEXT NOT NULL DEFAULT 'без открытой лицензии',
    approved INTEGER NOT NULL DEFAULT 0,
    trimmed TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Таблица видеомемов (зовёт app/db.Database.init)."""
    conn.execute(SCHEMA)


# --------------------------------------------------------------------------- #
# Поля
# --------------------------------------------------------------------------- #


def normalize_tags(raw: Any) -> list[str]:
    """Теги: список или строка через запятую/пробел/#; нижний регистр, без повторов, до MAX_TAGS."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw.strip().startswith("[") else None
        except ValueError:
            parsed = None
        items: Iterable[Any] = parsed if isinstance(parsed, list) else re.split(r"[,;#\n]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = []
    out: list[str] = []
    for item in items:
        tag = re.sub(r"\s+", " ", str(item or "")).strip().strip("#").lower().replace("ё", "е")[:MAX_TAG_LEN]
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= MAX_TAGS:
            break
    return out


def _text(name: str, value: Any) -> str:
    return str(value or "").strip()[: TEXT_LIMITS.get(name, 1000)]


def _decode(row: Any, asset: dict | None) -> dict:
    meme = dict(row)
    for key, default in (("tags", []), ("transcript_words", [])):
        try:
            value = json.loads(meme.get(key) or "null")
        except (TypeError, ValueError):
            value = None
        meme[key] = value if isinstance(value, list) else default
    try:
        trimmed = json.loads(meme.get("trimmed") or "null")
    except (TypeError, ValueError):
        trimmed = None
    meme["trimmed"] = trimmed if isinstance(trimmed, dict) else None
    meme["approved"] = bool(meme.get("approved"))
    meme["has_real_people"] = bool(meme.get("has_real_people"))
    asset = asset or {}
    meme.update(
        title=asset.get("label") or f"мем #{meme['id']}",
        duration_sec=round(float(asset.get("duration_sec") or 0), 3),
        width=int(asset.get("width") or 0),
        height=int(asset.get("height") or 0),
        has_audio=bool(asset.get("has_audio")),
        size_bytes=int(asset.get("size_bytes") or 0),
        url=f"/media/montage-assets/{meme['asset_id']}",
        thumb_url=f"/media/montage-assets/{meme['asset_id']}/thumb",
        file_path=asset.get("file_path") or "",
    )
    return meme


# --------------------------------------------------------------------------- #
# Хранилище (поверх store.db; файл — store.*_montage_asset)
# --------------------------------------------------------------------------- #


def create_meme(store: Any, asset_id: int, **fields: Any) -> dict:
    """Строка мема для уже заведённого файла библиотеки ``asset_id`` (вид video)."""
    has_people = fields.get("has_real_people")
    cur = store.db.execute(
        """
        INSERT INTO video_memes (asset_id, description, when_to_use, tags, has_real_people, source_url,
                                 license_note, approved, transcript_status, trimmed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(asset_id), _text("description", fields.get("description")),
            _text("when_to_use", fields.get("when_to_use")),
            json.dumps(normalize_tags(fields.get("tags")), ensure_ascii=False),
            int(True if has_people is None else bool(has_people)),
            _text("source_url", fields.get("source_url")),
            _text("license_note", fields.get("license_note")) or DEFAULT_LICENSE,
            int(bool(fields.get("approved"))),
            fields.get("transcript_status") if fields.get("transcript_status") in TRANSCRIPT_STATES else "pending",
            json.dumps(fields["trimmed"], ensure_ascii=False) if isinstance(fields.get("trimmed"), dict) else "",
        ),
    )
    meme = get_meme(store, int(cur.lastrowid))
    _sync_asset(store, meme)
    return meme


def get_meme(store: Any, meme_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM video_memes WHERE id = ?", (int(meme_id),))
    if not row:
        raise KeyError(f"видеомема #{meme_id} нет")
    try:
        asset = store.get_montage_asset(int(row["asset_id"]))
    except KeyError:
        asset = None
    return _decode(row, asset)


def meme_for_asset(store: Any, asset_id: Any) -> dict | None:
    """Мем, чей файл — ``asset_id``, или None (обычный файл библиотеки)."""
    try:
        row = store.db.query_one("SELECT id FROM video_memes WHERE asset_id = ?", (int(asset_id),))
    except (TypeError, ValueError, sqlite3.OperationalError):
        return None
    return get_meme(store, int(row["id"])) if row else None


def list_memes(store: Any, *, approved: bool | None = None) -> list[dict]:
    where, params = "", ()
    if approved is not None:
        where, params = "WHERE approved = ?", (int(bool(approved)),)
    rows = store.db.query_all(f"SELECT * FROM video_memes {where} ORDER BY id DESC", params)
    assets = {int(a["id"]): a for a in store.list_montage_assets()} if rows else {}
    return [_decode(r, assets.get(int(r["asset_id"]))) for r in rows]


def unapproved_asset_ids(store: Any) -> set[int]:
    """Файлы неодобренных мемов: ИИ их не видит и не ставит."""
    try:
        rows = store.db.query_all("SELECT asset_id FROM video_memes WHERE approved = 0")
    except sqlite3.OperationalError:  # старая база без таблицы
        return set()
    return {int(r["asset_id"]) for r in rows}


EDITABLE = ("description", "when_to_use", "tags", "transcript", "transcript_lang", "has_real_people", "source_url",
            "license_note", "approved")


def update_meme(store: Any, meme_id: int, **fields: Any) -> dict:
    """Правка полей мема; ``title`` — подпись файла. Неизвестные поля — ValueError."""
    meme = get_meme(store, meme_id)
    unknown = set(fields) - set(EDITABLE) - {"title"}
    if unknown:
        raise ValueError(f"неизвестные поля: {', '.join(sorted(unknown))}")
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "title":
            continue
        if key == "tags":
            updates[key] = json.dumps(normalize_tags(value), ensure_ascii=False)
        elif key in ("approved", "has_real_people"):
            updates[key] = int(bool(value))
        else:
            updates[key] = _text(key, value)
    if "transcript" in fields:
        # Правка руками — расшифровка готова; слова с временем от Whisper больше не про этот текст.
        updates["transcript_status"] = "done" if updates["transcript"] else "empty"
        updates["transcript_words"] = "[]"
        updates["transcript_error"] = ""
    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates)
        store.db.execute(f"UPDATE video_memes SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(meme_id)))
    title = str(fields.get("title") or "").strip()
    if title and title != meme["title"]:
        store.update_montage_asset(int(meme["asset_id"]), label=title)
    fresh = get_meme(store, meme_id)
    _sync_asset(store, fresh)
    return fresh


def set_transcript(store: Any, meme_id: int, *, status: str, text: str = "", language: str = "",
                   words: list[dict] | None = None, error: str = "") -> dict:
    """Итог фоновой расшифровки (app/video_meme_ingest.transcribe_meme)."""
    store.db.execute(
        "UPDATE video_memes SET transcript_status = ?, transcript = ?, transcript_lang = ?, transcript_words = ?, "
        "transcript_error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status if status in TRANSCRIPT_STATES else "failed", _text("transcript", text),
         _text("transcript_lang", language), json.dumps(words or [], ensure_ascii=False), str(error or "")[:300],
         int(meme_id)),
    )
    fresh = get_meme(store, meme_id)
    _sync_asset(store, fresh)
    return fresh


def delete_meme(store: Any, meme_id: int) -> dict:
    """Удалить мем вместе с его файлом (строка мема уходит каскадом)."""
    meme = get_meme(store, meme_id)
    try:
        store.delete_montage_asset(int(meme["asset_id"]))
    except KeyError:
        store.db.execute("DELETE FROM video_memes WHERE id = ?", (int(meme_id),))
    return meme


def _sync_asset(store: Any, meme: dict) -> None:
    """Файл библиотеки несёт текст мема (описание и теги — ими его находят ``list_assets`` и редактор) и отметку
    владельца «реальные люди» как готовую проверку (app/ai/people_check.py не зовёт Gemini)."""
    when = meme.get("when_to_use") or meme.get("description") or ""
    said = f" Говорит: «{meme['transcript']}»." if meme.get("transcript") else ""
    tags = ", ".join(["видеомем", *meme.get("tags", [])])
    try:
        store.update_montage_asset(
            int(meme["asset_id"]),
            description=f"Видеомем #{meme['id']}: {when}{said}".strip()[:500],
            tags=tags[:200],
            people_check={"real_person": bool(meme.get("has_real_people")),
                          "what": "отметка владельца у видеомема"},
        )
    except KeyError:
        pass


# --------------------------------------------------------------------------- #
# Поиск
# --------------------------------------------------------------------------- #

FIELD_WEIGHTS = (("tags", 3.0), ("title", 2.0), ("when_to_use", 2.5), ("description", 2.0), ("transcript", 1.5))
STEM_LEN = 5  # «грубая основа»: шок/шоке/шоком, победа/победы, кричит/кричать — по первым буквам
_WORD = re.compile(r"[a-zа-я0-9]+")
_STOP = frozenset("и в во на не с со к ко по за из у о об от до а но что как это для или же ли бы то".split())


def tokens(text: Any) -> list[str]:
    words = _WORD.findall(str(text or "").lower().replace("ё", "е"))
    return [w for w in words if w not in _STOP]


def _stem(word: str) -> str:
    return word[:STEM_LEN] if len(word) > STEM_LEN else word


def _field_text(meme: dict, name: str) -> str:
    value = meme.get(name)
    return " ".join(value) if isinstance(value, list) else str(value or "")


def score(meme: dict, query: str) -> float:
    """Сколько слов запроса нашлось в полях мема (с весом поля); 0 — ни одного."""
    words = tokens(query)
    if not words:
        return 0.0
    fields = [(set(map(_stem, tokens(_field_text(meme, name)))), _field_text(meme, name).lower().replace("ё", "е"),
               weight) for name, weight in FIELD_WEIGHTS]
    total, hit = 0.0, 0
    for word in words:
        stem = _stem(word)
        best = 0.0
        for stems, raw, weight in fields:
            if stem in stems or any(s.startswith(stem) or (len(s) >= 3 and stem.startswith(s)) for s in stems):
                best = max(best, weight)
            elif len(word) >= 3 and word in raw:
                best = max(best, weight * 0.6)
        if best:
            hit += 1
            total += best
    if not hit:
        return 0.0
    # Нашлись все слова запроса — выше, чем одно слово с большим весом.
    return round(total * (hit / len(words)) ** 2, 3)


def search_memes(store: Any, query: str = "", *, tag: str | None = None, approved: bool | None = True,
                 max_sec: float | None = None, limit: int = 8) -> list[dict]:
    """Мемы по смыслу: слова запроса в тегах, когда вставлять, описании, словах; фильтры одобрения, тега и длины.
    Пустой запрос — новые первыми."""
    want_tag = normalize_tags(tag)[0] if tag and normalize_tags(tag) else None
    out: list[tuple[float, dict]] = []
    for meme in list_memes(store, approved=approved):
        if want_tag and not any(t == want_tag or t.startswith(_stem(want_tag)) for t in meme["tags"]):
            continue
        if max_sec is not None and meme["duration_sec"] > float(max_sec) + 1e-6:
            continue
        points = score(meme, query) if query.strip() else 0.0
        if query.strip() and points <= 0:
            continue
        out.append((points, meme))
    out.sort(key=lambda pair: (-pair[0], -int(pair[1]["id"])))
    return [{**meme, "score": points} for points, meme in out[: max(1, min(50, int(limit or 8)))]]


def brief(meme: dict) -> dict:
    """Мем для агента: без пути к файлу и слов с временем."""
    return {
        "meme_id": meme["id"], "asset_id": meme["asset_id"], "title": meme["title"],
        "duration_sec": meme["duration_sec"], "description": meme["description"],
        "when_to_use": meme["when_to_use"], "transcript": meme["transcript"],
        "transcript_lang": meme["transcript_lang"], "transcript_status": meme["transcript_status"],
        "tags": meme["tags"], "has_real_people": meme["has_real_people"], "approved": meme["approved"],
        "has_audio": meme["has_audio"], "size": f"{meme['width']}×{meme['height']}",
        **({"score": meme["score"]} if "score" in meme else {}),
    }
