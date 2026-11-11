"""Страницы сайтов как материал для роликов: снимок страницы, копия HTML и её версии с правками.

Снимок (``snapshot``) — headless Chromium (``node/webpage/snapshot.mjs``) открывает страницу ТОЛЬКО ДЛЯ ПРОСМОТРА
(GET и прокрутка, пустой временный профиль, без входа и кликов) и сохраняет самодостаточную копию: DOM без
скриптов, стили, картинки и шрифты лежат рядом (``data/web_pages/<id>/assets``), адреса переписаны на локальные.
К копии — кадр всей страницы PNG (DPR 3 у телефона, 2 у компьютера), заголовок, адрес, время. Капча или «вы не
робот» — страница помечается «blocked», дальше не идём.

Правка (``edit``) идёт ЧЕРЕЗ HTML: правки DOM (app/webpages_ops.py: set_text, hide/remove, set_style,
add_highlight, crop_to, insert_badge) применяются в Chromium к копии предыдущей версии, получается новая версия
(``v<n>/index.html``) и новый кадр. Версии не теряются. Если меняется текст на странице чужого сайта (не из
ссылок проекта), у версии пометка «изменён текст на чужой странице» — её видно в карточке (не запрет).

Кадр версии — картинка слоя ``page`` моушн-движка (ссылка ``webpage:<id>:<n>``, см. ``image_path``): движок
режет из неё окно, листает и приближает (frontend/src/motion/layers/page.ts).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.settings import settings

_log = logging.getLogger(__name__)

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS web_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
        url TEXT NOT NULL,
        final_url TEXT NOT NULL DEFAULT '',
        host TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        viewport TEXT NOT NULL DEFAULT 'mobile',
        status TEXT NOT NULL DEFAULT 'pending',
        status_note TEXT NOT NULL DEFAULT '',
        current_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_web_pages_project ON web_pages(project_id, id)",
    """
    CREATE TABLE IF NOT EXISTS web_page_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
        n INTEGER NOT NULL,
        parent_n INTEGER,
        html_path TEXT NOT NULL DEFAULT '',
        png_path TEXT NOT NULL DEFAULT '',
        thumb_path TEXT NOT NULL DEFAULT '',
        meta_json TEXT NOT NULL DEFAULT '{}',
        ops_json TEXT NOT NULL DEFAULT '[]',
        instruction TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        foreign_edit INTEGER NOT NULL DEFAULT 0,
        made_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(page_id, n)
    )
    """,
]

VIEWPORTS = ("mobile", "desktop")
VIEWPORT_WIDTH = {"mobile": 390, "desktop": 1280}
MAX_PAGE_HEIGHT = {"mobile": 6000, "desktop": 4500}
FOREIGN_NOTE = "изменён текст на чужой странице"
THUMB_W = 360
THUMB_MAX_H = 720
SCRIPT = Path(settings.root_dir) / "node" / "webpage" / "snapshot.mjs"
CHROME_CANDIDATES = (
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
)
# Один Chromium за раз на весь процесс (памяти мало): снимки и правки страниц идут по очереди.
_BROWSER_LOCK = threading.Lock()


class WebPageError(RuntimeError):
    """Понятная ошибка снимка или правки: текст — для владельца и модели."""


def ensure_web_pages_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def pages_dir() -> Path:
    return Path(settings.data_dir) / "web_pages"


def page_dir(page_id: int) -> Path:
    return pages_dir() / str(int(page_id))


def chromium_available() -> bool:
    explicit = os.getenv("WEBPAGE_CHROMIUM") or os.getenv("SCENE3D_CHROMIUM") or os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    if explicit:
        return Path(explicit).is_file()
    return any(Path(p).is_file() for p in CHROME_CANDIDATES)


# --------------------------------------------------------------------------- #
# Свой сайт или чужой
# --------------------------------------------------------------------------- #


def host_of(url: str) -> str:
    text = str(url or "").strip()
    if text and "://" not in text:
        text = "https://" + text
    try:
        host = (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def own_hosts(store: Any, project_id: int | None) -> list[str]:
    """Домены из ссылок проекта (его сайт, магазин, каналы)."""
    if not project_id:
        return []
    from app import projects as P

    try:
        links = P.get_project(store, int(project_id), include_sources=False).get("links") or []
    except KeyError:
        return []
    return sorted({h for h in (host_of(link.get("url")) for link in links if isinstance(link, dict)) if h})


def is_own_host(host: str, owned: list[str]) -> bool:
    host = host_of(host) if "/" in host else host.lower().removeprefix("www.")
    return any(host == o or host.endswith("." + o) for o in owned)


# --------------------------------------------------------------------------- #
# Строки и вид для API
# --------------------------------------------------------------------------- #


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def image_ref(page_id: int, n: int) -> str:
    return f"webpage:{int(page_id)}:{int(n)}"


def _version_view(page_id: int, row: Any) -> dict:
    data = dict(row)
    meta = _loads(data.pop("meta_json", "{}"), {})
    n = int(data["n"])
    base = f"/api/web-pages/{page_id}/versions/{n}"
    return {
        "n": n, "parent_n": data.get("parent_n"), "made_by": data.get("made_by") or "",
        "instruction": data.get("instruction") or "", "note": data.get("note") or "",
        "foreign_edit": bool(data.get("foreign_edit")), "created_at": data.get("created_at"),
        "ops": _loads(data.get("ops_json"), []),
        "image_url": f"{base}/image" if data.get("png_path") else None,
        "thumb_url": f"{base}/thumb" if data.get("thumb_path") else None,
        "html_url": f"{base}/html/index.html" if data.get("html_path") else None,
        "ref": image_ref(page_id, n),
        "width": meta.get("width"), "height": meta.get("height"), "dpr": meta.get("dpr"),
        "page_width": meta.get("page_width"), "page_height": meta.get("page_height"),
        "shot_height": meta.get("shot_height"), "region": meta.get("region"),
        "applied": meta.get("applied") or [],
    }


def get_page(store: Any, page_id: int, with_outline: bool = False) -> dict:
    row = store.db.query_one("SELECT * FROM web_pages WHERE id = ?", (int(page_id),))
    if not row:
        raise KeyError(f"страница {page_id} не найдена")
    page = dict(row)
    rows = store.db.query_all("SELECT * FROM web_page_versions WHERE page_id = ? ORDER BY n", (int(page_id),))
    page["versions"] = [_version_view(int(page_id), r) for r in rows]
    current = next((v for v in page["versions"] if v["n"] == page["current_version"]), None)
    page["current"] = current
    page["thumb_url"] = (current or {}).get("thumb_url") or (page["versions"][-1]["thumb_url"] if page["versions"] else None)
    page["foreign_edit"] = bool(current and current["foreign_edit"])
    page["note"] = (current or {}).get("note") or ""
    owned = own_hosts(store, page.get("project_id"))
    page["own_site"] = is_own_host(page.get("host") or "", owned)
    if with_outline and current:
        meta = version_meta(store, int(page_id), current["n"])
        page["outline"] = meta.get("outline") or []
    return page


def list_pages(store: Any, project_id: int | None = None, limit: int = 100) -> list[dict]:
    if project_id is None:
        rows = store.db.query_all("SELECT id FROM web_pages ORDER BY id DESC LIMIT ?", (int(limit),))
    else:
        rows = store.db.query_all("SELECT id FROM web_pages WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                                  (int(project_id), int(limit)))
    return [get_page(store, int(r["id"])) for r in rows]


def _version_row(store: Any, page_id: int, n: int | None) -> Any:
    if n is None:
        page = store.db.query_one("SELECT current_version FROM web_pages WHERE id = ?", (int(page_id),))
        if not page:
            raise KeyError(f"страница {page_id} не найдена")
        n = int(page["current_version"])
    row = store.db.query_one("SELECT * FROM web_page_versions WHERE page_id = ? AND n = ?", (int(page_id), int(n)))
    if not row:
        raise KeyError(f"у страницы {page_id} нет версии {n}")
    return row


def version_meta(store: Any, page_id: int, n: int | None = None) -> dict:
    row = _version_row(store, page_id, n)
    return {"n": int(row["n"]), **_loads(row["meta_json"], {})}


def version_file(store: Any, page_id: int, n: int, what: str) -> Path:
    """Файл версии: ``png`` | ``thumb`` | ``html`` (внутри папки страницы)."""
    row = _version_row(store, page_id, n)
    rel = row[{"png": "png_path", "thumb": "thumb_path", "html": "html_path"}[what]]
    if not rel:
        raise KeyError(f"у версии {n} нет файла {what}")
    path = (page_dir(page_id) / rel).resolve()
    if page_dir(page_id).resolve() not in path.parents or not path.is_file():
        raise KeyError(f"файл версии {n} пропал")
    return path


def asset_file(page_id: int, name: str) -> Path:
    """Файл копии (стиль, картинка, шрифт) — только из папки assets страницы."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise KeyError("нет такого файла")
    path = page_dir(page_id) / "assets" / name
    if not path.is_file():
        raise KeyError("нет такого файла")
    return path


def image_path(store: Any, ref: str) -> Path | None:
    """``webpage:<id>:<n>`` → PNG кадра версии (для рендера слоя page) или None."""
    parts = str(ref or "").split(":")
    if len(parts) != 3 or parts[0] != "webpage" or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    try:
        return version_file(store, int(parts[1]), int(parts[2]), "png")
    except (KeyError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# Chromium
# --------------------------------------------------------------------------- #

Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def _run_node(args: list[str], timeout: int, script: Path = SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run([settings.node_bin, str(script), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _browser(args: list[str], label: str, timeout: int = 150, runner: Runner | None = None,
             script: Path = SCRIPT) -> dict:
    """Запустить снимок/правку/разбор в Chromium (по одному на процесс и в общей очереди тяжёлых задач)."""
    from app.heavy_queue import heavy_slot

    with heavy_slot("webpage", label), _BROWSER_LOCK:
        try:
            proc = runner(args, timeout) if runner else _run_node(args, timeout, script)
        except subprocess.TimeoutExpired as exc:
            raise WebPageError("страница не успела открыться (браузер остановлен по времени)") from exc
        except FileNotFoundError as exc:
            raise WebPageError("нет Node.js для браузера снимков") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["без подробностей"]
        raise WebPageError(f"браузер не справился: {tail[0][:300]}")
    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise WebPageError("браузер вернул непонятный ответ") from exc


def _stitch(vdir: Path, tiles: list[dict]) -> tuple[int, int]:
    """Полосы кадра → v<n>/page.png и миниатюра thumb.jpg; полосы удаляются. Вернуть (ширина, высота) PNG."""
    import cv2

    parts = []
    for tile in tiles:
        path = vdir / tile["file"]
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise WebPageError("кадр страницы не прочитался")
        parts.append(img)
    width = min(p.shape[1] for p in parts)
    full = cv2.vconcat([p[:, :width] for p in parts]) if len(parts) > 1 else parts[0]
    del parts
    if not cv2.imwrite(str(vdir / "page.png"), full, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise WebPageError("кадр страницы не записался")
    _thumb(full, vdir / "thumb.jpg")
    height, width = full.shape[:2]
    del full
    for tile in tiles:
        (vdir / tile["file"]).unlink(missing_ok=True)
    return int(width), int(height)


def _thumb(img: Any, out: Path) -> None:
    import cv2

    scale = THUMB_W / img.shape[1]
    small = cv2.resize(img, (THUMB_W, max(1, int(img.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), small[:THUMB_MAX_H], [cv2.IMWRITE_JPEG_QUALITY, 82])


# --------------------------------------------------------------------------- #
# Снимок
# --------------------------------------------------------------------------- #


def _clean_url(url: str) -> str:
    text = str(url or "").strip()
    if text and "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("нужен адрес страницы http(s)://…")
    if parts.username or parts.password:
        raise ValueError("адрес с логином и паролем не снимаю — только открытые страницы")
    return text[:2000]


def snapshot(store: Any, url: str, project_id: int | None = None, viewport: str = "mobile",
             made_by: str = "owner", runner: Runner | None = None) -> dict:
    """Снять страницу: новая запись web_pages с версией 1 (или статусом blocked). Ошибка — WebPageError."""
    clean = _clean_url(url)
    viewport = viewport if viewport in VIEWPORTS else "mobile"
    if project_id is not None:
        from app import projects as P

        P.get_project(store, int(project_id), include_sources=False)  # KeyError — нет проекта
    if runner is None and not chromium_available():
        raise WebPageError("нет headless Chromium для снимков страниц (в образе — /usr/bin/chromium)")
    cur = store.db.execute(
        "INSERT INTO web_pages(project_id, url, host, viewport, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (project_id, clean, host_of(clean), viewport, _now(), _now()),
    )
    page_id = int(cur.lastrowid)
    root = page_dir(page_id)
    root.mkdir(parents=True, exist_ok=True)
    try:
        res = _browser(["--capture", clean, "--out", str(root), "--viewport", viewport,
                        "--max-height", str(MAX_PAGE_HEIGHT[viewport])], f"страница {host_of(clean)}", runner=runner)
    except WebPageError:
        _drop(store, page_id)
        raise
    if not res.get("ok"):
        _drop(store, page_id)
        raise WebPageError(str(res.get("reason") or "страница не открылась"))
    final = str(res.get("final_url") or clean)[:2000]
    title = str(res.get("title") or "")[:300]
    if res.get("status") == "blocked":
        thumb = ""
        preview = root / "v1" / "blocked.png"
        if preview.is_file():
            import cv2

            img = cv2.imread(str(preview), cv2.IMREAD_COLOR)
            if img is not None:
                _thumb(img, root / "v1" / "thumb.jpg")
                thumb = "v1/thumb.jpg"
        store.db.execute(
            "UPDATE web_pages SET status = 'blocked', status_note = ?, final_url = ?, title = ?, updated_at = ? WHERE id = ?",
            (str(res.get("reason") or "капча")[:300], final, title, _now(), page_id),
        )
        if thumb:
            store.db.execute(
                "INSERT INTO web_page_versions(page_id, n, thumb_path, made_by, note, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                (page_id, thumb, made_by, "заблокировано: снимка нет", _now()),
            )
        return get_page(store, page_id)
    width, height = _stitch(root / "v1", res.get("tiles") or [])
    meta = _meta(res, width, height)
    store.db.execute(
        "INSERT INTO web_page_versions(page_id, n, parent_n, html_path, png_path, thumb_path, meta_json, made_by, created_at) "
        "VALUES (?, 1, NULL, 'v1/index.html', 'v1/page.png', 'v1/thumb.jpg', ?, ?, ?)",
        (page_id, json.dumps(meta, ensure_ascii=False), made_by, _now()),
    )
    store.db.execute(
        "UPDATE web_pages SET status = 'ok', status_note = '', final_url = ?, host = ?, title = ?, current_version = 1, "
        "updated_at = ? WHERE id = ?",
        (final, host_of(final) or host_of(clean), title, _now(), page_id),
    )
    return get_page(store, page_id, with_outline=True)


def _meta(res: dict, width: int, height: int, region: dict | None = None, applied: list | None = None) -> dict:
    return {
        "width": width, "height": height, "dpr": res.get("dpr"), "page_width": res.get("page_width"),
        "page_height": res.get("page_height"), "shot_height": res.get("shot_height"), "region": region,
        "outline": res.get("outline") or [], "applied": applied or [], "assets": res.get("assets"),
        "captured_at": _now(),
    }


def _drop(store: Any, page_id: int) -> None:
    store.db.execute("DELETE FROM web_pages WHERE id = ?", (int(page_id),))
    shutil.rmtree(page_dir(page_id), ignore_errors=True)


def delete_page(store: Any, page_id: int) -> None:
    get_page(store, page_id)
    store.db.execute("DELETE FROM web_page_versions WHERE page_id = ?", (int(page_id),))
    _drop(store, page_id)


def set_current(store: Any, page_id: int, n: int) -> dict:
    _version_row(store, page_id, n)
    store.db.execute("UPDATE web_pages SET current_version = ?, updated_at = ? WHERE id = ?", (int(n), _now(), int(page_id)))
    return get_page(store, page_id)


# --------------------------------------------------------------------------- #
# Правка: новая версия
# --------------------------------------------------------------------------- #


def apply_ops(store: Any, page_id: int, ops: list[dict], base: int | None = None, instruction: str = "",
              made_by: str = "owner", runner: Runner | None = None) -> dict:
    """Правки DOM (уже проверенные ``webpages_ops.validate_ops``) к версии ``base`` (нет — текущей) → новая версия.

    Возвращает {page, version, before, applied, problems, note}."""
    page = get_page(store, page_id)
    if page["status"] != "ok":
        raise WebPageError("страница заблокирована капчей — править нечего" if page["status"] == "blocked"
                           else "у страницы нет снимка")
    base_row = _version_row(store, page_id, base)
    base_n = int(base_row["n"])
    if not ops:
        raise WebPageError("нет правок")
    n = int(store.db.query_one("SELECT MAX(n) AS m FROM web_page_versions WHERE page_id = ?", (int(page_id),))["m"]) + 1
    root = page_dir(page_id)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(ops, fh, ensure_ascii=False)
        ops_file = fh.name
    try:
        res = _browser(["--render", str(root), "--from", f"v{base_n}", "--to", f"v{n}", "--ops", ops_file,
                        "--viewport", page["viewport"], "--max-height", str(MAX_PAGE_HEIGHT.get(page["viewport"], 6000))],
                       f"правка страницы {page_id}", runner=runner)
    finally:
        Path(ops_file).unlink(missing_ok=True)
    vdir = root / f"v{n}"
    try:
        width, height = _stitch(vdir, res.get("tiles") or [])
    except WebPageError:
        shutil.rmtree(vdir, ignore_errors=True)
        raise
    applied = res.get("applied") or []
    base_meta = _loads(base_row["meta_json"], {})
    region = res.get("region") or base_meta.get("region")
    from app.webpages_ops import foreign_note

    changed_text = any(op.get("op") == "set_text" and (applied[i].get("count") if i < len(applied) else 0)
                       for i, op in enumerate(ops))
    foreign = bool(base_row["foreign_edit"]) or (changed_text and not is_own_host(page["host"], own_hosts(store, page.get("project_id"))))
    note = foreign_note(foreign)
    store.db.execute(
        "INSERT INTO web_page_versions(page_id, n, parent_n, html_path, png_path, thumb_path, meta_json, ops_json, instruction, "
        "note, foreign_edit, made_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (page_id, n, base_n, f"v{n}/index.html", f"v{n}/page.png", f"v{n}/thumb.jpg",
         json.dumps(_meta(res, width, height, region, applied), ensure_ascii=False), json.dumps(ops, ensure_ascii=False),
         str(instruction or "")[:1000], note, int(foreign), made_by, _now()),
    )
    store.db.execute("UPDATE web_pages SET current_version = ?, updated_at = ? WHERE id = ?", (n, _now(), int(page_id)))
    fresh = get_page(store, page_id, with_outline=True)
    version = next(v for v in fresh["versions"] if v["n"] == n)
    before = next(v for v in fresh["versions"] if v["n"] == base_n)
    problems = [f"правка {a.get('i', 0) + 1} ({a.get('op')}): {a['problem']}" for a in applied if a.get("problem")]
    return {"page": fresh, "version": version, "before": before, "applied": applied, "problems": problems, "note": note}


def page_layer(store: Any, page_id: int, n: int | None = None, duration: float = 4.0, start: float = 0.0,
               phone: bool = True) -> dict:
    """Готовый слой ``page`` версии: окно — область crop_to (или первый экран), прокрутка до конца области."""
    page = get_page(store, page_id)
    meta = version_meta(store, page_id, n)
    pw = float(meta.get("page_width") or VIEWPORT_WIDTH.get(page["viewport"], 390))
    shot_h = float(meta.get("shot_height") or meta.get("page_height") or pw * 2)
    region = meta.get("region")
    view_h = round(pw * (19.5 / 9 if page["viewport"] == "mobile" else 16 / 10))
    if region:
        win = {"x": region["x"], "y": region["y"], "w": region["w"], "h": min(region["h"], view_h)}
        scroll_to = max(region["y"], min(region["y"] + region["h"] - win["h"], shot_h - win["h"]))
    else:
        win = {"x": 0, "y": 0, "w": pw, "h": min(view_h, shot_h)}
        scroll_to = max(0.0, min(shot_h - win["h"], shot_h * 0.5))
    props: dict = {"page_id": int(page_id), "version": int(meta["n"]), "pageWidth": pw, "region": win,
                   "w": 560 if phone else 960}
    if scroll_to > win["y"] + 1:
        props["scroll"] = [{"t": round(min(0.6, duration * 0.15), 2), "y": win["y"]},
                           {"t": round(max(0.8, duration * 0.8), 2), "y": round(scroll_to)}]
    if phone:
        props.update({"device": "phone", "radius": 0})
    else:
        props.update({"radius": 24})
    return {"type": "page", "start": start, "end": round(start + duration, 3), "x": 0.5, "y": 0.55,
            "name": (page.get("title") or page.get("host") or "Страница")[:40],
            "in": {"type": "slideUp", "duration": 0.45}, "out": {"type": "fade", "duration": 0.25},
            "shadow": {"color": "#000000", "blur": 60, "x": 0, "y": 28, "opacity": 0.5}, "props": props}
