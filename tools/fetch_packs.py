"""Скачивает паки контента для моушн-слоёв: эмодзи, стикеры, звуки, мем-шаблоны.

    python3 tools/fetch_packs.py [TARGET]        # по умолчанию vendor/packs
    python3 tools/fetch_packs.py /app/vendor/packs --only twemoji,generated

Запускается при сборке docker-образа (слой кэшируется, пока не меняется этот
файл) и локально у разработчика. Только стандартная библиотека Python 3.11+.

Почему не в репозитории: это тысячи файлов (одни эмодзи — 4000 SVG), а они
целиком определяются версиями ниже. Каждая версия прибита (тег / коммит / хэш
архива), sha256 архива сверяется — подменённый или «тихо обновлённый» архив
сборку роняет, а не пролезает в образ.

Безопасность: скачанное — только данные (SVG/PNG/JPG/OGG/JSON). Ничего из
архивов не исполняется; распаковываются лишь нужные файлы, имена очищаются до
``[A-Za-z0-9._-]`` (никаких путей из архива), архивы лежат в отдельном каталоге
``.downloads`` и по умолчанию удаляются в конце.

Идемпотентность: у каждого пака есть штамп ``<pack>/.stamp`` с версией; если
версия совпадает и файлы на месте — пак пропускается. ``packs.json`` (индекс для
API) пересобирается всегда — это быстро.

Звуки «generated» синтезируются здесь же (чистый Python: шум, фильтры,
огибающие), ffmpeg только кодирует их в OGG. Нет ffmpeg — останутся WAV.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "vendor" / "packs"
USER_AGENT = "vertical-video-fabric-fetch-packs/1 (+https://github.com)"
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
INDEX_VERSION = 1

# --------------------------------------------------------------------------- #
# Источники. Меняешь версию — меняй и sha256 (скрипт подскажет фактический).
# --------------------------------------------------------------------------- #
TWEMOJI = {
    "version": "v17.0.3",
    "commit": "b6b55fef1e8636b540a6d016a4729ca8cdf2e60b",
    "url": "https://codeload.github.com/jdecked/twemoji/tar.gz/b6b55fef1e8636b540a6d016a4729ca8cdf2e60b",
    "sha256": "705d79de1460e5e775f362f0d0f01fbe3ef8d65bf4648c490e4649704584f747",
}
EMOJIBASE_VERSION = "18.0.0"
EMOJIBASE = {
    "en/compact.json": "3d4039a933500007bbb015b23df1a6dfb5721032f7471b8602465666c8f00597",
    "ru/compact.json": "e73a899cf0116c39fe73b5eb8491f685b27d41e123a098fca02b427c04c29e6e",
    "meta/groups.json": "3ce4f0a8e5890ed9d746b12e0056b170340928c1936be35cf92bb3e6d83a71e1",
    "ru/messages.json": "d7f0e89c0082eb8006479f629db3581419b80809887f7a8ce529bbf27d3043bc",
}
OPENMOJI = {
    "version": "17.0.0",
    "zip": "https://github.com/hfg-gmuend/openmoji/releases/download/17.0.0/openmoji-svg-color.zip",
    "zip_sha256": "59b0cd9f6fe033818fc02585cea42bea9fba5d68a4d3a3639bfe5d5cc3805689",
    "data": "https://raw.githubusercontent.com/hfg-gmuend/openmoji/17.0.0/data/openmoji.json",
    "data_sha256": "ab181dd523021dfbe2b98a13d96ab384b7b3014d17815261bc267fb39d9a211b",
    "license": "https://raw.githubusercontent.com/hfg-gmuend/openmoji/17.0.0/LICENSE.txt",
}
# Логотипы брендов не берём — это чужие товарные знаки, а не стикеры.
OPENMOJI_SKIP_SUBGROUPS = {"brand"}

KENNEY_BASE = "https://kenney.nl/media/pages/assets"
KENNEY_PACKS: list[dict] = [
    {
        "id": "kenney-particles", "kind": "sticker", "name": "Эффекты: искры, дым, вспышки",
        "path": "particle-pack/f8fe0f8cb8-1677578741/kenney_particle-pack.zip",
        "sha256": "b631d4b07f7002549fdcf155f01141ad482f79f3440e4e301eed49ce5f1d8958",
        "member": re.compile(r"^PNG \(Transparent\)/([^/]+\.png)$"),
        "page": "https://kenney.nl/assets/particle-pack",
    },
    {
        "id": "kenney-impact", "kind": "sfx", "name": "Удары и шаги",
        "path": "impact-sounds/87b4ddecda-1677589768/kenney_impact-sounds.zip",
        "sha256": "029d734af1582474edf3a694d1b0cebc97c1c152f2f39fa34d4c2bafc5de77f8",
        "member": re.compile(r"^Audio/([^/]+\.ogg)$"),
        "page": "https://kenney.nl/assets/impact-sounds",
    },
    {
        "id": "kenney-interface", "kind": "sfx", "name": "Интерфейс: клики, ошибки, глитчи",
        "path": "interface-sounds/fa43c1dd4d-1677589452/kenney_interface-sounds.zip",
        "sha256": "f2193d072726d6758a5f7871b2dcc54dcce0d5c35c6f0a62f92549b327c81232",
        "member": re.compile(r"^Audio/([^/]+\.ogg)$"),
        "page": "https://kenney.nl/assets/interface-sounds",
    },
    {
        "id": "kenney-ui", "kind": "sfx", "name": "Кнопки и переключатели",
        "path": "ui-audio/490d233f68-1677590494/kenney_ui-audio.zip",
        "sha256": "946fc23a63d535d693eb31b2eabb80c8c28d6351e2186b344ceb71b2cb1d5eb6",
        "member": re.compile(r"^Audio/([^/]+\.ogg)$"),
        "page": "https://kenney.nl/assets/ui-audio",
    },
    {
        "id": "kenney-digital", "kind": "sfx", "name": "Цифровые: лазеры, пауэр-апы",
        "path": "digital-audio/216eac4753-1677590265/kenney_digital-audio.zip",
        "sha256": "24e6ce28b76a6d8c89cff4d331e0965ff5c3de8a73c612028e9d363cc64e4f06",
        "member": re.compile(r"^Audio/([^/]+\.ogg)$"),
        "page": "https://kenney.nl/assets/digital-audio",
    },
]

IMGFLIP_API = "https://api.imgflip.com/get_memes"
IMGFLIP_HOSTS = {"i.imgflip.com"}
# Запасной адрес той же картинки на основном домене: i.imgflip.com (CDN) из
# части сетей режется на TLS, а imgflip.com/s/meme/<Имя-Шаблона>.jpg отдаёт тот
# же файл. Что это та самая картинка, проверяем по размерам из API.
IMGFLIP_FALLBACK = "https://imgflip.com/s/meme/{slug}{ext}"
MAX_MEME_BYTES = 8 * 1024 * 1024

# Русские подписи к звукам Kenney: имя файла -> «что это». Ключ — префикс до
# первой цифры/подчёркивания с номером.
SFX_RU = {
    "footstep_carpet": "Шаг по ковру", "footstep_concrete": "Шаг по бетону", "footstep_grass": "Шаг по траве",
    "footstep_snow": "Шаг по снегу", "footstep_wood": "Шаг по дереву",
    "impactBell_heavy": "Удар в колокол", "impactGeneric_light": "Лёгкий удар",
    "impactGlass_heavy": "Стекло, сильный удар", "impactGlass_light": "Стекло, лёгкий удар",
    "impactGlass_medium": "Стекло, удар", "impactMetal_heavy": "Металл, сильный удар",
    "impactMetal_light": "Металл, лёгкий удар", "impactMetal_medium": "Металл, удар",
    "impactMining": "Кирка по камню", "impactPlank_medium": "Удар по доске",
    "impactPlate_heavy": "Тарелка, сильный удар", "impactPlate_light": "Тарелка, лёгкий удар",
    "impactPlate_medium": "Тарелка, удар", "impactPunch_heavy": "Сильный удар кулаком",
    "impactPunch_medium": "Удар кулаком", "impactSoft_heavy": "Глухой сильный удар",
    "impactSoft_medium": "Глухой удар", "impactTin_medium": "Удар по жести",
    "impactWood_heavy": "Дерево, сильный удар", "impactWood_light": "Дерево, лёгкий удар",
    "impactWood_medium": "Дерево, удар",
    "back": "Назад", "bong": "Бонг", "click": "Клик", "close": "Закрыть", "confirmation": "Подтверждение",
    "drop": "Капля", "error": "Ошибка", "glass": "Звон стекла", "glitch": "Глитч", "maximize": "Развернуть",
    "minimize": "Свернуть", "open": "Открыть", "pluck": "Щипок", "question": "Вопрос", "scratch": "Скрэтч",
    "scroll": "Прокрутка", "select": "Выбор", "switch": "Переключатель", "tick": "Тик", "toggle": "Тумблер",
    "mouseclick": "Клик мыши", "mouserelease": "Отпускание мыши", "rollover": "Наведение",
    "highDown": "Тон вниз (высокий)", "highUp": "Тон вверх (высокий)", "laser": "Лазер",
    "lowDown": "Тон вниз (низкий)", "lowRandom": "Случайные тоны", "lowThreeTone": "Три тона (низкие)",
    "pepSound": "Бодрый сигнал", "phaseJump": "Фазовый прыжок", "phaserDown": "Фазер вниз",
    "phaserUp": "Фазер вверх", "powerUp": "Пауэр-ап", "spaceTrash": "Космический мусор",
    "threeTone": "Три тона", "tone": "Тон", "twoTone": "Два тона", "zap": "Разряд",
    "zapThreeToneDown": "Разряд, три тона вниз", "zapThreeToneUp": "Разряд, три тона вверх",
    "zapTwoTone": "Разряд, два тона",
    "circle": "Круг", "dirt": "Грязь", "fire": "Огонь", "flame": "Пламя", "flare": "Блик", "light": "Свет",
    "magic": "Магия", "muzzle": "Вспышка выстрела", "scorch": "Подпалина", "slash": "Удар клинком",
    "smoke": "Дым", "spark": "Искра", "star": "Звезда", "symbol": "Символ", "trace": "След",
    "twirl": "Завихрение", "window": "Окно",
}

# --------------------------------------------------------------------------- #
# Утилиты
# --------------------------------------------------------------------------- #


def log(msg: str) -> None:
    print(f"[fetch_packs] {msg}", flush=True)


def http_get(url: str, *, timeout: int = 120, retries: int = 3, max_bytes: int = 200 * 1024 * 1024) -> bytes:
    """GET с ретраями; только https."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError(f"только https: {url}")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"слишком большой ответ: {url}")
            return data
        except Exception as exc:  # noqa: BLE001 — сеть капризна, пробуем ещё
            last = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"не скачалось {url}: {last}")


def download(url: str, dest: Path, sha256: str | None) -> Path:
    """Скачивает в dest (кэш) и сверяет sha256. Совпал хэш у уже скачанного — не качаем."""
    if dest.exists() and sha256 and _sha256(dest.read_bytes()) == sha256:
        return dest
    log(f"download {url}")
    data = http_get(url)
    actual = _sha256(data)
    if sha256 and actual != sha256:
        raise RuntimeError(f"sha256 не совпал для {url}: ожидали {sha256}, получили {actual}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    """Имя файла без путей и экзотики — то же правило проверяет API."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    if not SAFE_NAME.match(base):
        raise ValueError(f"плохое имя файла: {name!r}")
    return base


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].strip("-") or "item"


def read_stamp(pack_dir: Path) -> str:
    try:
        return (pack_dir / ".stamp").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_stamp(pack_dir: Path, stamp: str) -> None:
    (pack_dir / ".stamp").write_text(stamp + "\n", encoding="utf-8")


def fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


# --- размеры картинок и длительность звука без внешних библиотек ------------ #


def image_size(path: Path, data: bytes | None = None) -> tuple[int, int]:
    """(ширина, высота) для PNG/JPEG/GIF/SVG; (0, 0) если не разобрали."""
    try:
        head = (data if data is not None else path.read_bytes())[:256 * 1024]
    except OSError:
        return 0, 0
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            i += 2 + seg_len
        return 0, 0
    if path.suffix.lower() == ".svg":
        text = head[:4096].decode("utf-8", "replace")
        m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', text)
        if m:
            return round(float(m.group(1))), round(float(m.group(2)))
    return 0, 0


def ogg_duration(path: Path) -> float:
    """Длительность Ogg Vorbis/Opus: гранула последней страницы / частота."""
    try:
        data = path.read_bytes()
    except OSError:
        return 0.0
    rate = 0
    pre_skip = 0
    i = data.find(b"\x01vorbis")
    if i >= 0:
        rate = struct.unpack("<I", data[i + 12:i + 16])[0]
    else:
        i = data.find(b"OpusHead")
        if i >= 0:
            pre_skip = struct.unpack("<H", data[i + 10:i + 12])[0]
            rate = 48000
    last = data.rfind(b"OggS")
    if not rate or last < 0:
        return 0.0
    granule = struct.unpack("<q", data[last + 6:last + 14])[0]
    return max(0.0, (granule - pre_skip) / rate)


def audio_duration(path: Path) -> float:
    if path.suffix.lower() == ".ogg":
        dur = ogg_duration(path)
        if dur > 0:
            return round(dur, 3)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as w:
                return round(w.getnframes() / w.getframerate(), 3)
        except (OSError, wave.Error):
            pass
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            return round(float(out), 3)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return 0.0


def describe_item(path: Path, kind: str, name: str, tags: list[str], **extra: object) -> dict:
    item: dict = {"file": path.name, "name": name, "tags": tags, "size": path.stat().st_size}
    if kind == "sfx":
        item["duration"] = audio_duration(path)
    else:
        w, h = image_size(path)
        item["width"], item["height"] = w, h
    item.update(extra)
    return item


def ru_label(stem: str, zero_based: bool = False) -> tuple[str, list[str]]:
    """«impactMetal_heavy_003» -> («Металл, сильный удар 4», [теги])."""
    m = re.match(r"^(.*?)_?(\d+)$", stem)
    base, digits = (m.group(1).rstrip("_"), m.group(2)) if m else (stem, "")
    label = SFX_RU.get(base) or SFX_RU.get(base.split("_")[0]) or base
    words = [w.lower() for w in re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", base) if w]
    tags = sorted({*words, *[w.lower() for w in re.findall(r"[А-Яа-яЁё]+", label)]})
    if digits:
        # В части паков Kenney нумерация с нуля («_000») — людям привычнее с единицы.
        label = f"{label} {int(digits) + (1 if zero_based else 0)}"
    return label, tags


# --------------------------------------------------------------------------- #
# Паки
# --------------------------------------------------------------------------- #


def fetch_twemoji(target: Path, dl: Path) -> dict:
    """Twemoji SVG (весь набор) + emoji.json с русскими/английскими названиями."""
    pack_dir = target / "twemoji"
    stamp = f"twemoji {TWEMOJI['version']} {TWEMOJI['commit']} emojibase {EMOJIBASE_VERSION} idx{INDEX_VERSION}"
    if read_stamp(pack_dir) != stamp or not (target / "emoji.json").exists():
        archive = download(TWEMOJI["url"], dl / "twemoji.tar.gz", TWEMOJI["sha256"])
        fresh_dir(pack_dir)
        count = 0
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                parts = member.name.split("/")
                if not member.isfile():
                    continue
                if len(parts) == 4 and parts[1:3] == ["assets", "svg"] and parts[3].endswith(".svg"):
                    data = tar.extractfile(member).read()  # type: ignore[union-attr]
                    (pack_dir / safe_name(parts[3])).write_bytes(data)
                    count += 1
                elif len(parts) == 2 and parts[1] in ("LICENSE-GRAPHICS", "LICENSE"):
                    data = tar.extractfile(member).read()  # type: ignore[union-attr]
                    (pack_dir / f"{parts[1]}.txt").write_bytes(data)
        log(f"twemoji: {count} svg")
        build_emoji_index(target, pack_dir, dl)
        write_stamp(pack_dir, stamp)
    files = sorted(p for p in pack_dir.glob("*.svg"))
    return {
        "id": "twemoji",
        "name": "Эмодзи Twemoji",
        "kind": "emoji",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Twemoji © Twitter, Inc and other contributors, jdecked/twemoji — CC-BY 4.0",
        "source": "https://github.com/jdecked/twemoji",
        "version": TWEMOJI["version"],
        "index": "emoji.json",
        "count": len(files),
        "size": sum(p.stat().st_size for p in files),
        "items": [],  # 4000 штук — список в emoji.json, тут не дублируем
    }


def _twemoji_code(char: str) -> str:
    """Правило имени файла Twemoji: FE0F убирается, если в последовательности нет ZWJ."""
    if "‍" not in char:
        char = char.replace("️", "")
    return "-".join(f"{ord(c):x}" for c in char)


def build_emoji_index(target: Path, pack_dir: Path, dl: Path) -> None:
    data: dict[str, object] = {}
    for rel, sha in EMOJIBASE.items():
        url = f"https://cdn.jsdelivr.net/npm/emojibase-data@{EMOJIBASE_VERSION}/{rel}"
        path = download(url, dl / "emojibase" / rel, sha)
        data[rel] = json.loads(path.read_text(encoding="utf-8"))
    en = {e["hexcode"]: e for e in data["en/compact.json"]}  # type: ignore[union-attr]
    group_keys = {int(k): v for k, v in data["meta/groups.json"]["groups"].items()}  # type: ignore[index]
    ru_groups = {g["key"]: g["message"] for g in data["ru/messages.json"]["groups"]}  # type: ignore[index]
    have = {p.stem for p in pack_dir.glob("*.svg")}

    def code_for(e: dict) -> str | None:
        for cand in (_twemoji_code(e["unicode"]), e["hexcode"].lower(), e["hexcode"].lower().replace("-fe0f", "")):
            if cand in have:
                return cand
        return None

    out: list[dict] = []
    for e in data["ru/compact.json"]:  # type: ignore[union-attr]
        if "group" not in e or group_keys.get(e["group"]) == "component":
            continue  # регион-буквы, тона кожи отдельно — не нужны в пикере
        code = code_for(e)
        if not code:
            continue
        en_e = en.get(e["hexcode"], {})
        item = {
            "code": code,
            "char": e["unicode"],
            "name": e.get("label", ""),
            "en": en_e.get("label", ""),
            "tags": e.get("tags", []),
            "en_tags": en_e.get("tags", []),
            "group": group_keys.get(e["group"], ""),
            "order": e.get("order", 0),
        }
        skins = [c for s in e.get("skins", []) if (c := code_for(s))]
        if skins:
            item["skins"] = skins
        out.append(item)
    out.sort(key=lambda x: x["order"])
    groups = [
        {"key": key, "label": ru_groups.get(key, key)}
        for _, key in sorted(group_keys.items())
        if key != "component"
    ]
    index = {
        "version": INDEX_VERSION,
        "source": f"emojibase-data@{EMOJIBASE_VERSION} (MIT) + twemoji {TWEMOJI['version']}",
        "groups": groups,
        "emoji": out,
    }
    (target / "emoji.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"emoji.json: {len(out)} эмодзи")


def fetch_openmoji_extras(target: Path, dl: Path) -> dict:
    """Собственные (не-Unicode) стикеры OpenMoji — группа extras-openmoji, без логотипов брендов.
    extras-unicode не берём: там служебные символы вроде «дефис-минус»."""
    pack_id = "openmoji-extras"
    pack_dir = target / pack_id
    stamp = f"openmoji {OPENMOJI['version']} extras-only idx{INDEX_VERSION}"
    meta_path = pack_dir / "items.json"
    if read_stamp(pack_dir) != stamp or not meta_path.exists():
        archive = download(OPENMOJI["zip"], dl / "openmoji-svg-color.zip", OPENMOJI["zip_sha256"])
        meta = json.loads(download(OPENMOJI["data"], dl / "openmoji.json", OPENMOJI["data_sha256"]).read_text("utf-8"))
        wanted = {
            f"{e['hexcode']}.svg": e
            for e in meta
            if e.get("group") == "extras-openmoji"
            and e.get("subgroups") not in OPENMOJI_SKIP_SUBGROUPS
        }
        fresh_dir(pack_dir)
        items: list[dict] = []
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if info.is_dir() or base not in wanted:
                    continue
                dest = pack_dir / safe_name(base)
                dest.write_bytes(zf.read(info))
                e = wanted[base]
                tags = sorted({
                    t.strip() for t in f"{e.get('tags', '')},{e.get('openmoji_tags', '')}".split(",") if t.strip()
                } | {e.get("subgroups", "")} - {""})
                items.append(describe_item(dest, "sticker", e.get("annotation") or dest.stem, tags))
        lic = http_get(OPENMOJI["license"])
        (pack_dir / "LICENSE.txt").write_bytes(lic)
        items.sort(key=lambda i: i["file"])
        meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        write_stamp(pack_dir, stamp)
        log(f"{pack_id}: {len(items)} svg")
    items = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "id": pack_id,
        "name": "Стикеры OpenMoji",
        "kind": "sticker",
        "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": "OpenMoji — the open-source emoji and icon project, openmoji.org — CC BY-SA 4.0",
        "source": "https://github.com/hfg-gmuend/openmoji",
        "version": OPENMOJI["version"],
        "items": items,
    }


def fetch_kenney(target: Path, dl: Path, spec: dict) -> dict:
    pack_dir = target / spec["id"]
    stamp = f"{spec['path']} {spec['sha256']} idx{INDEX_VERSION}"
    meta_path = pack_dir / "items.json"
    if read_stamp(pack_dir) != stamp or not meta_path.exists():
        url = f"{KENNEY_BASE}/{spec['path']}"
        archive = download(url, dl / spec["path"].rsplit("/", 1)[-1], spec["sha256"])
        fresh_dir(pack_dir)
        items: list[dict] = []
        with zipfile.ZipFile(archive) as zf:
            zero_based = any(n.endswith("_000.ogg") or n.endswith("_000.png") for n in zf.namelist())
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename == "License.txt":
                    (pack_dir / "LICENSE.txt").write_bytes(zf.read(info))
                    continue
                m = spec["member"].match(info.filename)
                if not m:
                    continue
                dest = pack_dir / safe_name(m.group(1))
                dest.write_bytes(zf.read(info))
                label, tags = ru_label(dest.stem, zero_based)
                items.append(describe_item(dest, spec["kind"], label, tags))
        items.sort(key=lambda i: i["file"])
        meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        write_stamp(pack_dir, stamp)
        log(f"{spec['id']}: {len(items)} файлов")
    items = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "id": spec["id"],
        "name": spec["name"],
        "kind": spec["kind"],
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "Kenney (kenney.nl) — CC0, указывать автора не обязательно",
        "source": spec["page"],
        "version": spec["path"].split("/")[1],
        "items": items,
    }


def fetch_imgflip(target: Path, dl: Path) -> dict:
    """Топ-100 мем-шаблонов imgflip (публичный API). Версии нет — список живой,
    поэтому уже скачанные картинки не перекачиваем, а новые докачиваем."""
    pack_id = "imgflip"
    pack_dir = target / pack_id
    pack_dir.mkdir(parents=True, exist_ok=True)
    meta_path = pack_dir / "items.json"
    try:
        payload = json.loads(http_get(IMGFLIP_API, timeout=60))
        memes = payload["data"]["memes"] if payload.get("success") else []
    except Exception as exc:  # noqa: BLE001 — без мемов жить можно
        log(f"imgflip недоступен: {exc}")
        memes = []
    items: list[dict] = []
    cdn_ok = True
    fails_in_row = 0
    if memes:
        for m in memes[:100]:
            url = str(m.get("url", ""))
            parsed = urllib.parse.urlparse(url)
            ext = Path(parsed.path).suffix.lower()
            if parsed.scheme != "https" or parsed.hostname not in IMGFLIP_HOSTS or ext not in (".jpg", ".png", ".gif"):
                continue
            mid = re.sub(r"\D", "", str(m.get("id", ""))) or "0"
            dest = pack_dir / safe_name(f"{mid}-{slug(str(m.get('name', '')))}{ext}")
            if not dest.exists():
                if fails_in_row >= 3:
                    # Сеть совсем не пускает — не ждём 100 таймаутов: уже скачанные
                    # берём, новые не качаем. Помогает HTTPS_PROXY (urllib его понимает).
                    continue
                name_slug = re.sub(r"[^A-Za-z0-9]+", "-", str(m.get("name", ""))).strip("-")
                urls = [url] if cdn_ok else []
                urls.append(IMGFLIP_FALLBACK.format(slug=urllib.parse.quote(name_slug), ext=ext))
                want = (int(m.get("width") or 0), int(m.get("height") or 0))
                data = b""
                for candidate in urls:
                    try:
                        got = http_get(candidate, timeout=20, retries=1,
                                       max_bytes=MAX_MEME_BYTES)
                    except Exception as exc:  # noqa: BLE001
                        if candidate == url:
                            cdn_ok = False  # CDN не отвечает — дальше сразу запасной адрес
                        log(f"мем {candidate}: {exc}")
                        continue
                    is_image = got[:3] == b"\xff\xd8\xff" or got[:8] == b"\x89PNG\r\n\x1a\n" or got[:4] == b"GIF8"
                    if is_image and (candidate == url or image_size(dest, got) == want):
                        data = got
                        break
                    log(f"мем {candidate}: не та картинка, пропускаю")
                if not data:
                    fails_in_row += 1
                    continue
                fails_in_row = 0
                dest.write_bytes(data)
            boxes = int(m.get("box_count") or 2)
            items.append(describe_item(
                dest, "meme", str(m.get("name", dest.stem))[:120],
                ["мем", "шаблон", f"подписей: {boxes}"],
                box_count=boxes, imgflip_id=mid,
            ))
        if fails_in_row >= 3:
            log("imgflip: картинки не качаются (можно задать HTTPS_PROXY)")
        meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        log(f"imgflip: {len(items)} шаблонов")
    elif meta_path.exists():
        items = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "id": pack_id,
        "name": "Мем-шаблоны",
        "kind": "meme",
        "license": "unknown",
        "license_url": "https://imgflip.com/memetemplates",
        "attribution": "Шаблоны из топа imgflip.com; права на исходные изображения — у их авторов",
        "source": IMGFLIP_API,
        "version": time.strftime("%Y-%m-%d"),
        "items": items,
    }


# --------------------------------------------------------------------------- #
# Синтез звуков «generated»
# --------------------------------------------------------------------------- #
SR = 44100


class SVF:
    """Простой state-variable фильтр (Chamberlin, с запасом устойчивости):
    частоту можно менять на каждом сэмпле — на этом держатся свипы шума."""

    def __init__(self) -> None:
        self.low = 0.0
        self.band = 0.0

    def step(self, x: float, freq: float, q: float) -> tuple[float, float, float]:
        f = 2.0 * math.sin(math.pi * min(freq, SR / 6.0) / SR)
        damp = 1.0 / max(q, 0.5)
        # два полушага — устойчивее на высоких частотах
        for _ in range(2):
            self.low += f * 0.5 * self.band
            high = x - self.low - damp * self.band
            self.band += f * 0.5 * high
        return self.low, self.band, high


def _env(t: float, attack: float, decay: float) -> float:
    """Атака по синусу, затем экспоненциальный спад."""
    if t < attack:
        return math.sin(0.5 * math.pi * t / attack) if attack > 0 else 1.0
    return math.exp(-(t - attack) / decay) if decay > 0 else 0.0


def _logsweep(a: float, b: float, x: float) -> float:
    x = min(1.0, max(0.0, x))
    return a * (b / a) ** x


def synth_whoosh(dur: float, f0: float, f1: float, f2: float, peak: float, q: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    flt, flt2 = SVF(), SVF()
    out = []
    for i in range(n):
        x = i / n
        # частота: f0 -> f1 к пику, потом f1 -> f2
        freq = _logsweep(f0, f1, x / peak) if x < peak else _logsweep(f1, f2, (x - peak) / (1 - peak))
        amp = (x / peak) ** 2 if x < peak else (1 - (x - peak) / (1 - peak)) ** 1.6
        noise = rnd.uniform(-1, 1)
        _, band, _ = flt.step(noise, freq, q)
        _, band2, _ = flt2.step(noise, freq * 1.9, q * 1.3)
        out.append((band + 0.35 * band2) * amp)
    return out


def synth_riser(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    flt = SVF()
    phase = phase2 = 0.0
    out = []
    for i in range(n):
        x = i / n
        t = i / SR
        freq = _logsweep(250, 9000, x ** 1.4)
        _, band, _ = flt.step(rnd.uniform(-1, 1), freq, 2.5)
        pitch = _logsweep(110, 880, x ** 1.2) * (1 + 0.012 * math.sin(2 * math.pi * 6 * t))
        phase += pitch / SR
        phase2 += pitch * 1.005 / SR
        saw = (2 * (phase % 1.0) - 1) * 0.5 + (2 * (phase2 % 1.0) - 1) * 0.5
        amp = x ** 2.2
        out.append((0.8 * band + 0.25 * saw * x) * amp)
    # короткий хвост, чтобы не было щелчка
    tail = int(0.01 * SR)
    for k in range(tail):
        out[-tail + k] *= 1 - k / tail
    return out


def synth_boom(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    lp = SVF()
    phase = 0.0
    out = []
    for i in range(n):
        t = i / SR
        f = 35 + 95 * math.exp(-t / 0.09)
        phase += f / SR
        body = math.sin(2 * math.pi * phase) * math.exp(-t / 0.55)
        low, _, _ = lp.step(rnd.uniform(-1, 1), 180 + 2500 * math.exp(-t / 0.05), 0.7)
        crack = low * math.exp(-t / 0.25) * 1.6
        click = rnd.uniform(-1, 1) * math.exp(-t / 0.004)
        s = 1.2 * body + crack + 0.5 * click
        out.append(math.tanh(1.8 * s))
    return out


def synth_bass_drop(dur: float) -> list[float]:
    n = int(dur * SR)
    phase = 0.0
    out = []
    for i in range(n):
        t = i / SR
        f = 32 + 120 * math.exp(-t / 0.18)
        phase += f / SR
        s = math.sin(2 * math.pi * phase) + 0.25 * math.sin(4 * math.pi * phase)
        env = _env(t, 0.004, 0.9)
        out.append(math.tanh(2.2 * s * env) * 0.9)
    return out


def synth_pop(dur: float) -> list[float]:
    n = int(dur * SR)
    phase = 0.0
    out = []
    for i in range(n):
        t = i / SR
        f = 380 + 1100 * math.exp(-t / 0.012)
        phase += f / SR
        out.append(math.sin(2 * math.pi * phase) * _env(t, 0.0015, 0.028))
    return out


def synth_click(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    flt = SVF()
    out = []
    for i in range(n):
        t = i / SR
        _, band, _ = flt.step(rnd.uniform(-1, 1), 3800, 3)
        tick = math.sin(2 * math.pi * 2200 * t)
        out.append((band * 0.8 + 0.6 * tick) * math.exp(-t / 0.0045))
    return out


def synth_ding(dur: float) -> list[float]:
    n = int(dur * SR)
    base = 1318.5  # E6
    partials = [(1.0, 1.0, 0.9), (2.76, 0.45, 0.45), (5.40, 0.25, 0.22), (8.93, 0.12, 0.12), (0.5, 0.15, 1.1)]
    out = []
    for i in range(n):
        t = i / SR
        s = sum(a * math.sin(2 * math.pi * base * r * t) * math.exp(-t / d) for r, a, d in partials)
        out.append(s * min(1.0, t / 0.002))
    return out


def synth_scratch(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    flt = SVF()
    phase = 0.0
    out = []
    for i in range(n):
        t = i / SR
        x = i / n
        # «рука» гоняет пластинку туда-сюда: скорость — синус с ускорением
        speed = math.sin(2 * math.pi * (2.2 * t + 3.5 * t * t))
        mag = abs(speed)
        f = 180 + 2200 * mag
        _, band, _ = flt.step(rnd.uniform(-1, 1), f, 4)
        phase += (120 + 520 * mag) / SR
        tone = (2 * (phase % 1.0) - 1)
        env = min(1.0, x / 0.03) * (1 - x) ** 0.6
        out.append((band * 1.2 + 0.35 * tone * mag) * env * (0.3 + 0.7 * mag))
    return out


def synth_shutter(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    hp = SVF()
    out = [0.0] * n
    for start, amp, decay, freq in ((0.0, 1.0, 0.012, 2600), (0.085, 0.7, 0.02, 1700), (0.16, 0.45, 0.01, 3200)):
        s0 = int(start * SR)
        for i in range(s0, n):
            t = (i - s0) / SR
            if t > 0.12:
                break
            _, band, high = hp.step(rnd.uniform(-1, 1), freq, 1.2)
            body = math.sin(2 * math.pi * 180 * t) * math.exp(-t / 0.02)
            out[i] += amp * (0.6 * high + 0.6 * band + 0.4 * body) * math.exp(-t / decay)
    return out


def synth_typing(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    out = [0.0] * n
    t0 = 0.02
    while t0 < dur - 0.08:
        s0 = int(t0 * SR)
        flt = SVF()
        freq = rnd.uniform(2400, 4200)
        amp = rnd.uniform(0.6, 1.0)
        thock = rnd.uniform(140, 220)
        for i in range(s0, min(n, s0 + int(0.05 * SR))):
            t = (i - s0) / SR
            _, band, _ = flt.step(rnd.uniform(-1, 1), freq, 2)
            s = 0.9 * band * math.exp(-t / 0.006) + 0.5 * math.sin(2 * math.pi * thock * t) * math.exp(-t / 0.012)
            out[i] += amp * s
        t0 += rnd.choice((0.07, 0.09, 0.11, 0.13, 0.16, 0.22))
    return out


def synth_glitch(dur: float, seed: int) -> list[float]:
    rnd = random.Random(seed)
    n = int(dur * SR)
    out = []
    i = 0
    while i < n:
        seg = int(rnd.uniform(0.018, 0.06) * SR)
        mode = rnd.random()
        f = rnd.choice((110, 220, 440, 660, 880, 1760, 3520))
        hold = rnd.choice((1, 4, 8, 16))  # «понижение частоты дискретизации»
        held = 0.0
        for k in range(min(seg, n - i)):
            t = k / SR
            if k % hold == 0:
                if mode < 0.45:
                    held = 1.0 if math.sin(2 * math.pi * f * t) > 0 else -1.0
                elif mode < 0.8:
                    held = rnd.uniform(-1, 1)
                else:
                    held = 0.0  # дырка — заикание
            out.append(round(held * 6) / 6 * 0.7)
        i += seg
    for k in range(int(0.005 * SR)):  # мягкие края
        out[k] *= k / (0.005 * SR)
        out[-1 - k] *= k / (0.005 * SR)
    return out


def synth_buzzer(dur: float) -> list[float]:
    n = int(dur * SR)
    out = []
    lp = SVF()
    for i in range(n):
        t = i / SR
        s = 0.0
        for f in (146.8, 151.0, 293.0):
            s += 1.0 if math.sin(2 * math.pi * f * t) > 0 else -1.0
        low, _, _ = lp.step(s / 3, 1800, 0.8)
        env = min(1.0, t / 0.01) * (1.0 if t < dur - 0.06 else max(0.0, (dur - t) / 0.06))
        out.append(low * env)
    return out


GENERATED: list[dict] = [
    {"file": "whoosh", "name": "Вжух", "tags": ["вжух", "переход", "whoosh", "движение"],
     "fn": lambda: synth_whoosh(0.75, 280, 2600, 700, 0.55, 1.6, 1)},
    {"file": "swoosh", "name": "Быстрый свист", "tags": ["свист", "переход", "swoosh", "быстро"],
     "fn": lambda: synth_whoosh(0.35, 900, 6500, 2500, 0.45, 2.0, 2)},
    {"file": "whoosh-deep", "name": "Глубокий вжух", "tags": ["вжух", "переход", "низкий", "whoosh"],
     "fn": lambda: synth_whoosh(1.1, 120, 900, 250, 0.6, 1.2, 3)},
    {"file": "riser", "name": "Нарастание", "tags": ["нарастание", "напряжение", "riser", "перед дропом"],
     "fn": lambda: synth_riser(2.2, 4)},
    {"file": "boom", "name": "Бум", "tags": ["бум", "удар", "взрыв", "boom", "акцент"],
     "fn": lambda: synth_boom(1.8, 5), "gain_db": 5},
    {"file": "bass-drop", "name": "Бас-дроп", "tags": ["бас", "дроп", "808", "удар", "акцент"],
     "fn": lambda: synth_bass_drop(1.6), "gain_db": 5},
    {"file": "pop", "name": "Поп", "tags": ["поп", "появление", "pop", "пузырь"],
     "fn": lambda: synth_pop(0.12)},
    {"file": "click", "name": "Клик", "tags": ["клик", "щелчок", "click"],
     "fn": lambda: synth_click(0.05, 6)},
    {"file": "ding", "name": "Дзынь", "tags": ["дзынь", "колокольчик", "уведомление", "ding", "правильно"],
     "fn": lambda: synth_ding(1.6)},
    {"file": "record-scratch", "name": "Скрэтч пластинки", "tags": ["скрэтч", "пластинка", "стоп", "scratch"],
     "fn": lambda: synth_scratch(0.6, 7)},
    {"file": "camera-shutter", "name": "Затвор камеры", "tags": ["камера", "фото", "затвор", "снимок"],
     "fn": lambda: synth_shutter(0.32, 8)},
    {"file": "typing", "name": "Печать на клавиатуре", "tags": ["клавиатура", "печать", "текст", "typing"],
     "fn": lambda: synth_typing(1.6, 9)},
    {"file": "glitch", "name": "Глитч", "tags": ["глитч", "помехи", "сбой", "glitch"],
     "fn": lambda: synth_glitch(0.6, 10)},
    {"file": "buzzer", "name": "Неправильно (зуммер)", "tags": ["ошибка", "неправильно", "зуммер", "buzzer"],
     "fn": lambda: synth_buzzer(0.6)},
]
GENERATED_VERSION = "4"


def _normalize(samples: list[float], target_rms_db: float = -15.0, peak_db: float = -1.0) -> list[float]:
    """Приводим громкость к одному уровню: RMS «громкой части» к target, но
    пик не выше peak_db. loudnorm на звуках короче 3 с врёт — считаем сами."""
    peak = max((abs(s) for s in samples), default=0.0) or 1.0
    loud = sorted((s * s for s in samples), reverse=True)
    top = loud[: max(1, len(loud) // 3)]
    rms = math.sqrt(sum(top) / len(top)) or 1e-9
    gain = min(10 ** (target_rms_db / 20) / rms, 10 ** (peak_db / 20) / peak)
    return [s * gain for s in samples]


def _write_wav(path: Path, samples: list[float]) -> None:
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames)


def build_generated(target: Path, dl: Path) -> dict:
    pack_id = "generated"
    pack_dir = target / pack_id
    ffmpeg = shutil.which("ffmpeg")
    stamp = f"generated v{GENERATED_VERSION} {'ogg' if ffmpeg else 'wav'} idx{INDEX_VERSION}"
    meta_path = pack_dir / "items.json"
    if read_stamp(pack_dir) != stamp or not meta_path.exists():
        fresh_dir(pack_dir)
        work = fresh_dir(dl / "generated-wav")
        items: list[dict] = []
        for spec in GENERATED:
            # басы на слух тише при том же RMS — им даём запас сверху
            samples = _normalize(spec["fn"](), target_rms_db=-15.0 + spec.get("gain_db", 0))
            wav = work / f"{spec['file']}.wav"
            _write_wav(wav, samples)
            if ffmpeg:
                dest = pack_dir / f"{spec['file']}.ogg"
                subprocess.run(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                     "-c:a", "libvorbis", "-q:a", "6", "-ar", "44100", str(dest)],
                    check=True, timeout=60,
                )
            else:
                dest = pack_dir / wav.name
                shutil.copyfile(wav, dest)
            items.append(describe_item(dest, "sfx", spec["name"], spec["tags"]))
        (pack_dir / "LICENSE.txt").write_text(
            "Звуки синтезированы tools/fetch_packs.py (vertical-video-fabric). CC0 1.0 — без ограничений.\n",
            encoding="utf-8",
        )
        meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        write_stamp(pack_dir, stamp)
        log(f"generated: {len(items)} звуков ({'ogg' if ffmpeg else 'wav — нет ffmpeg'})")
    items = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "id": pack_id,
        "name": "Монтажные звуки (свои)",
        "kind": "sfx",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "",
        "source": "tools/fetch_packs.py",
        "version": GENERATED_VERSION,
        "items": items,
    }


# --------------------------------------------------------------------------- #

PACK_ORDER = ["twemoji", "openmoji-extras", "kenney-particles", "generated",
              "kenney-impact", "kenney-interface", "kenney-ui", "kenney-digital", "imgflip"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", default=str(DEFAULT_TARGET), help="куда класть паки")
    ap.add_argument("--only", default="", help="через запятую: " + ",".join(PACK_ORDER))
    ap.add_argument("--skip", default="", help="какие паки пропустить (через запятую)")
    ap.add_argument("--keep-downloads", action="store_true", help="не удалять скачанные архивы")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    dl = target / ".downloads"
    dl.mkdir(exist_ok=True)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    unknown = (only | skip) - set(PACK_ORDER)
    if unknown:
        ap.error(f"неизвестные паки: {', '.join(sorted(unknown))}")

    # Уже собранные паки, которые сейчас не трогаем, остаются в индексе.
    index_path = target / "packs.json"
    previous: dict[str, dict] = {}
    if index_path.exists():
        try:
            previous = {p["id"]: p for p in json.loads(index_path.read_text(encoding="utf-8"))["packs"]}
        except (ValueError, KeyError, TypeError):
            previous = {}

    builders = {
        "twemoji": lambda: fetch_twemoji(target, dl),
        "openmoji-extras": lambda: fetch_openmoji_extras(target, dl),
        "generated": lambda: build_generated(target, dl),
        "imgflip": lambda: fetch_imgflip(target, dl),
        **{spec["id"]: (lambda spec=spec: fetch_kenney(target, dl, spec)) for spec in KENNEY_PACKS},
    }
    packs: list[dict] = []
    failed: list[str] = []
    for pack_id in PACK_ORDER:
        if (only and pack_id not in only) or pack_id in skip:
            if pack_id in previous:
                packs.append(previous[pack_id])
            continue
        try:
            pack = builders[pack_id]()
        except Exception as exc:  # noqa: BLE001 — один пак не должен валить остальные
            log(f"ОШИБКА {pack_id}: {exc}")
            failed.append(pack_id)
            if pack_id in previous:
                packs.append(previous[pack_id])
            continue
        if "count" not in pack:
            pack["count"] = len(pack["items"])
            pack["size"] = sum(int(i.get("size") or 0) for i in pack["items"])
        packs.append(pack)

    index = {"version": INDEX_VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "packs": packs}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.keep_downloads:
        shutil.rmtree(dl, ignore_errors=True)
    for p in packs:
        log(f"  {p['id']:18} {p['kind']:8} {p['count']:5} шт  {p['size'] / 1e6:6.1f} МБ")
    # sha256-несовпадение и пр. для закреплённых паков — повод уронить сборку;
    # imgflip — внешний живой сервис, его отказ сборку не роняет.
    hard = [f for f in failed if f != "imgflip"]
    if hard:
        log(f"не собрались: {', '.join(hard)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
