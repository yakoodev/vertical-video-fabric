"""Каталог мем-шаблонов: ручной список без реальных людей и чужих логотипов.

Пак imgflip из tools/fetch_packs.py берёт топ-100 целиком, а в нём половина — фото реальных
людей (Drake, Roll Safe, Harold), и владелец такие мемы в нарезках вырезает. Поэтому здесь
белый список по точному имени шаблона: смотрел человек, а не модель, угадывающая лица.
Шаблона нет в списке — его нет в каталоге, даже если он в топе.

Качается только то, что предложили: картинка кладётся в data/meme_catalog и больше не тянется.
Сначала открытый репозиторий memegen на GitHub (MEMEGEN): картинки imgflip у нас режет провайдер
(CDN рвёт соединение, основной домен отдаёт по килобайту в секунду), а GitHub отвечает за секунду
и с хоста, и из контейнера. imgflip остаётся запасным для того, чего в зеркале нет.
Права: это чужие кадры и персонажи (Nickelodeon, Disney, Warner…), открытых лицензий у них нет.
Поэтому у каждого шаблона ``license`` честно «без открытой лицензии», решение за владельцем.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from app.settings import settings

IMGFLIP_API = "https://api.imgflip.com/get_memes"
IMGFLIP_FALLBACK = "https://imgflip.com/s/meme/{slug}{ext}"
IMGFLIP_HOSTS = {"i.imgflip.com", "imgflip.com"}
MEMEGEN_RAW = "https://raw.githubusercontent.com/jacebrowning/memegen/main/templates/{id}/default.{ext}"
MEMEGEN_SOURCE = "memegen, GitHub"
ALLOWED_HOSTS = IMGFLIP_HOSTS | {"raw.githubusercontent.com"}
LICENSE_NOTE = "без открытой лицензии"
LIST_TTL_SEC = 24 * 3600
MAX_BYTES = 8 * 1024 * 1024

# Имя шаблона в imgflip → когда он смешной. Сверено со списком get_memes 05.11.2026.
ALLOWED: dict[str, str] = {
    "Two Buttons": "мучительный выбор из двух вариантов",
    "Always Has Been": "открылось, что так было всегда",
    "Left Exit 12 Off Ramp": "резко свернул от правильного к глупому",
    "Gru's Plan": "план, который на последнем шаге идёт не так",
    "Waiting Skeleton": "ждать так долго, что истлел",
    "X, X Everywhere": "этого теперь везде полно",
    "Running Away Balloon": "тянется к цели, но что-то оттаскивает",
    "Batman Slapping Robin": "резко заткнули на полуслове",
    "Mocking Spongebob": "передразнивает чужие слова",
    "Is This A Pigeon": "совсем не понимает, что перед ним",
    "is this butterfly": "совсем не понимает, что перед ним",
    "0 days without (Lenny, Simpsons)": "опять случилось то же самое",
    "Bike Fall": "сам себе всё сломал и обвиняет других",
    "Squidward window": "смотрит со стороны, как другим весело",
    "Buff Doge vs. Cheems": "как было раньше и как стало теперь",
    "This Is Where I'd Put My Trophy If I Had One": "гордиться нечем",
    "Expanding Brain": "идеи всё абсурднее, а выглядят умнее",
    "This Is Fine": "всё горит, а герою нормально",
    "Tuxedo Winnie The Pooh": "то же самое, но с важным видом",
    "Megamind peeking": "ехидно заглядывает",
    "Megamind no bitches": "вопрос в лоб с пустым взглядом",
    "They don't know": "стоит в стороне и думает о своём",
    "Bell Curve": "глупый и умный пришли к одному",
    "Monkey Puppet": "неловко отвёл взгляд",
    "Imagination Spongebob": "вообразил невозможное",
    "Spider Man Triple": "все друг друга обвиняют",
    "spiderman pointing at spiderman": "двое одинаковых показывают друг на друга",
    "Boardroom Meeting Suggestion": "за здравую мысль выкинули из окна",
    "Evil Kermit": "внутренний голос подбивает на плохое",
    "Three-headed Dragon": "двое умных и один идиот",
    "Scooby doo mask reveal": "сняли маску, а там всё то же",
    "Types of Headaches meme": "что болит сильнее всего",
    "Grim Reaper Knocking Door": "следующий на очереди",
    "Panik Kalm Panik": "паника, облегчение, снова паника",
    "Futurama Fry": "не уверен, подозрительно щурится",
    "Surprised Pikachu": "сделал очевидное и удивился результату",
    "say the line bart! simpsons": "все ждут знакомую фразу",
    "Blank Nut Button": "жмёт кнопку с большим наслаждением",
    "Star Wars Yoda": "мудрый совет",
    "The Scroll Of Truth": "прочитал правду и выкинул",
    "Bugs bunny communist": "«наше», всё общее",
    # Только в зеркале, в топе imgflip их нет.
    "Doge": "простодушно и восторженно",
    "Cheems": "слабый и грустный",
    "Stonks": "сомнительная выгода",
    "Feels Good": "наконец-то приятно",
    "Feels Bad Man": "грустно, не повезло",
    "Bongo Cat": "кот бьёт лапами в такт",
    "Grumpy Cat": "всем недоволен",
    "Y U NO": "ну почему ты так не делаешь",
    "But That's None of My Business": "ехидно заметил и пьёт чай",
    "Stop It Patrick": "прекрати, ты его пугаешь",
    "I Have No Idea What I'm Doing": "делает с умным видом, не понимая что",
    "I Should Buy a Boat Cat": "сидит и обдумывает глупость",
    "Philosoraptor": "глубокомысленный глупый вопрос",
    "Awkward Moment Seal": "очень неловко",
    "You Should Feel Bad": "тебе должно быть стыдно",
    "Genie Rules": "правила, которые тут же нарушают",
    "What Color Do You Want Your Dragon": "спрашивают не о том, о чём просили",
}

# Имя в каталоге → папка шаблона в memegen. Каждый сверен глазами по листу 05.11.2026.
MEMEGEN: dict[str, str] = {
    "Two Buttons": "ds", "Left Exit 12 Off Ramp": "exit", "Gru's Plan": "gru", "X, X Everywhere": "buzz",
    "Mocking Spongebob": "spongebob", "Is This A Pigeon": "pigeon", "is this butterfly": "pigeon", "This Is Fine": "fine",
    "Tuxedo Winnie The Pooh": "pooh", "Expanding Brain": "gb", "spiderman pointing at spiderman": "spiderman",
    "Scooby doo mask reveal": "reveal", "Types of Headaches meme": "headaches", "Panik Kalm Panik": "panik-kalm-panik",
    "Futurama Fry": "fry", "Bugs bunny communist": "cbb", "Bell Curve": "midwit", "Running Away Balloon": "balloon",
    "Always Has Been": "astronaut", "Three-headed Dragon": "3hd", "say the line bart! simpsons": "say",
    "Doge": "doge", "Cheems": "cheems", "Stonks": "stonks", "Feels Good": "feelsgood", "Feels Bad Man": "sadfrog",
    "Bongo Cat": "bongo", "Grumpy Cat": "grumpycat", "Y U NO": "yuno", "But That's None of My Business": "kermit",
    "Stop It Patrick": "stop", "I Have No Idea What I'm Doing": "noidea", "I Should Buy a Boat Cat": "boat",
    "Philosoraptor": "philosoraptor", "Awkward Moment Seal": "ams", "You Should Feel Bad": "bad", "Genie Rules": "wishes",
    "What Color Do You Want Your Dragon": "dragon",
}


class CatalogError(ValueError):
    pass


_list_cache: tuple[float, list[dict]] = (0.0, [])
_cdn_ok = True  # CDN раз не ответил — дальше сразу запасной адрес, не ждём таймаут на каждом шаблоне


def cache_dir() -> Path:
    return settings.data_dir / "meme_catalog"


def _api_list() -> list[dict]:
    """Список шаблонов imgflip без картинок. Сеть упала — каталог без адресов, но с именами."""
    global _list_cache
    if _list_cache[1] and time.time() - _list_cache[0] < LIST_TTL_SEC:
        return _list_cache[1]
    import httpx

    response = httpx.get(IMGFLIP_API, timeout=15, headers={"User-Agent": "VerticalVideoFabric/1.0"})
    response.raise_for_status()
    payload = response.json()
    memes = (payload.get("data") or {}).get("memes") or [] if payload.get("success") else []
    _list_cache = (time.time(), memes)
    return memes


def _filter(query: str) -> list[tuple[str, str]]:
    words = [w for w in re.split(r"\s+", str(query or "").lower()) if w]
    return [(name, use) for name, use in ALLOWED.items()
            if all(w in f"{name} {use}".lower() for w in words)]


def list_catalog(query: str = "") -> dict:
    """Что можно предложить. Без сети отвечает всё равно: имя и смысл здесь, в коде."""
    try:
        live = {str(m.get("name")): m for m in _api_list()}
    except Exception as exc:  # noqa: BLE001 - каталог полезен и без адресов
        live, warning = {}, f"imgflip недоступен: {str(exc)[:120]}"
    else:
        warning = ""
    # Без зеркала картинка идёт через imgflip: на части сетей она может не скачаться.
    items = [{"name": name, "use": use, "license": LICENSE_NOTE,
              "source": MEMEGEN_SOURCE if name in MEMEGEN else "imgflip.com",
              "reliable": name in MEMEGEN, "available": name in MEMEGEN or name in live}
             for name, use in _filter(query)]
    out: dict[str, Any] = {"items": items, "total": len(ALLOWED),
                           "note": "Ручной список без реальных людей и логотипов. Лицензий нет: решение за владельцем."}
    if warning:
        out["warning"] = warning
    return out


def _is_image(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"GIF8"


def _get(url: str) -> bytes:
    import httpx

    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise CatalogError("шаблон не с разрешённого адреса")
    response = httpx.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    if len(response.content) > MAX_BYTES or not _is_image(response.content):
        raise CatalogError("по адресу шаблона не картинка")
    return response.content


def _same_size(data: bytes, meme: dict) -> bool:
    """Запасной адрес строится по имени: проверяем, что это та же картинка, а не чужой шаблон."""
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as image:
            return image.size == (int(meme.get("width") or 0), int(meme.get("height") or 0))
    except Exception:  # noqa: BLE001 - битая картинка — не та
        return False


def _from_mirror(name: str) -> dict | None:
    template = MEMEGEN.get(name)
    if not template:
        return None
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "jpg", "gif"):
        url = MEMEGEN_RAW.format(id=template, ext=ext)
        dest = folder / f"memegen-{template}.{ext}"
        if not dest.is_file():
            try:
                data = _get(url)
            except Exception:  # noqa: BLE001 - у шаблона другое расширение или зеркало недоступно
                continue
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
        return {"path": dest, "title": name, "source": MEMEGEN_SOURCE, "license": LICENSE_NOTE,
                "origin_url": url, "use": ALLOWED[name]}
    return None


def fetch_template(name: str) -> dict:
    """Скачать один шаблон из белого списка (кэш на диске). {path, title, source, license, origin_url, use}."""
    if name not in ALLOWED:
        raise CatalogError(f"шаблона «{str(name)[:80]}» нет в каталоге: смотри list_meme_catalog")
    mirrored = _from_mirror(name)
    if mirrored:
        return mirrored
    meme = next((m for m in _api_list() if str(m.get("name")) == name), None)
    if meme is None:
        raise CatalogError(f"imgflip больше не отдаёт шаблон «{name}»")
    url = str(meme.get("url") or "")
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext not in (".jpg", ".png", ".gif"):
        raise CatalogError("у шаблона неизвестный формат")
    mid = re.sub(r"\D", "", str(meme.get("id") or "")) or "0"
    folder = cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{mid}{ext}"
    if not dest.is_file():
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
        fallback = IMGFLIP_FALLBACK.format(slug=urllib.parse.quote(slug), ext=ext)
        global _cdn_ok
        data = b""
        problem = ""
        # CDN в части сетей режется на TLS, основной домен отдаёт то же.
        for candidate in ([url, fallback] if _cdn_ok else [fallback]):
            try:
                got = _get(candidate)
            except Exception as exc:  # noqa: BLE001
                problem = str(exc)[:160]
                if candidate == url:
                    _cdn_ok = False
                continue
            if candidate == url or _same_size(got, meme):
                data = got
                break
            problem = "по запасному адресу другая картинка"
        if not data:
            raise CatalogError(f"шаблон не скачался: {problem}")
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return {"path": dest, "title": name, "source": "imgflip.com", "license": LICENSE_NOTE,
            "origin_url": url, "use": ALLOWED[name]}


def dump_for_docs() -> str:
    return json.dumps(ALLOWED, ensure_ascii=False, indent=1)
