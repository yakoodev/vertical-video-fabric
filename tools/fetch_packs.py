"""Скачивает паки контента для моушн-слоёв: эмодзи, стикеры, звуки, мем-шаблоны.

    python3 tools/fetch_packs.py [TARGET]        # по умолчанию vendor/packs
    python3 tools/fetch_packs.py /app/vendor/packs --only twemoji,freesound

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

Монтажные звуки «freesound» — настоящие записи CC0 с freesound.org (закреплены
sha256); ffmpeg режет тишину, ограничивает длину и кодирует в OGG.
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
    # kenney-digital (лазеры, пауэр-апы) убран: синтетика, владелец просил её убрать.
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
# Монтажные звуки «freesound» — настоящие записи CC0 с freesound.org
# --------------------------------------------------------------------------- #
# Владелец: «синтетические звуки убрать нахуй из проекта, найди готовые
# библиотеки звуковых эффектов». Синтез (шум + фильтры) звучал металлически.
# Берём превью HQ (128 кбит/с MP3) звуков с лицензией CC0 — без указания
# автора, можно в монетизацию. Каждый файл закреплён sha256: подмена на CDN
# роняет сборку. Обработка: тишина в начале срезается, длина ограничивается,
# короткое затухание в конце и одинаковая громкость у всех звуков (ниже речи),
# чтобы громкость 1.0 у любого эффекта значила одно и то же.
SR = 44100
FREESOUND_SFX = [  # (файл, id звука, превью, sha256, подпись, теги, макс. секунд) — всё CC0
    ("whoosh-1", 60013, "https://cdn.freesound.org/previews/60/60013_71257-hq.mp3",
     "ebdb219e7f36f7e1aecdb045d6d8bdad059119af319863777c7302a69fa41a92", "Вжух короткий", ["вжух", "whoosh", "переход"], 1.0),
    ("whoosh-2", 332003, "https://cdn.freesound.org/previews/332/332003_2567799-hq.mp3",
     "7cd36e4623e0d316277827c14b0647f086436f9abacf52bd127f98a6438fd357", "Вжух", ["вжух", "whoosh", "переход"], 1.2),
    ("whoosh-3", 169867, "https://cdn.freesound.org/previews/169/169867_2834921-hq.mp3",
     "5cc27a658b5cdbbfed83c4986c5d94de98d83c146ac0158a43750d3cd77479ff", "Свуш длинный", ["вжух", "swoosh", "переход"], 2.0),
    ("whoosh-4", 683101, "https://cdn.freesound.org/previews/683/683101_6253486-hq.mp3",
     "3319e044194b8185671ff8d3a2c9660d101fbae67ea31c2f6460e9cf6c1eac9d", "Быстрый вжух", ["вжух", "whoosh"], 1.6),
    ("whip", 346373, "https://cdn.freesound.org/previews/346/346373_4882199-hq.mp3",
     "1f79dfeca3b8bc99a98df74ee2ff3fcff249fd9f0bb73495dfca40a62afed5cc", "Хлыст", ["хлыст", "whip", "вжух"], 0.8),
    ("impact-big", 430977, "https://cdn.freesound.org/previews/430/430977_8698658-hq.mp3",
     "76b8435cf9f536ed689c774e111341b3e89ed0bcf8f75394757055844642aa42", "Мощный удар", ["удар", "impact", "бум"], 2.5),
    ("impact-low", 430978, "https://cdn.freesound.org/previews/430/430978_8698658-hq.mp3",
     "bcf27da9931daf7030a8a4954235e1cf91eaecd1a7da254b9e2b748aa3c55a39", "Низкий удар", ["удар", "impact", "бум"], 2.5),
    ("hit-transition", 648534, "https://cdn.freesound.org/previews/648/648534_8698658-hq.mp3",
     "bd8652a2aaef4eaeb18be1fb54c1cc90917dd7d849c2cf74aef6c8950d00edee", "Удар-переход", ["удар", "hit", "переход"], 2.5),
    ("drama-boom", 802074, "https://cdn.freesound.org/previews/802/802074_15956618-hq.mp3",
     "dc9d06afa0890cd889671b4c19d0dd4996309702a57d18024eadac53b530fd66", "Драма-бум (вайн-бум)", ["бум", "vine boom", "драма", "панчлайн"], 2.5),
    ("bass-drop", 212768, "https://cdn.freesound.org/previews/212/212768_71257-hq.mp3",
     "6a964a1b97bfdfccafbc05169029690f60ddf00959f3d341c8a941b02e9ebb71", "Бас-дроп", ["бас", "drop", "бум"], 3.0),
    ("bass-drop-2", 165320, "https://cdn.freesound.org/previews/165/165320_3008343-hq.mp3",
     "6cd2374163d1a77de222668d003ab7cff14b0ed93bd055f93c0511a2759cf299", "Бас-дроп резкий", ["бас", "drop"], 2.4),
    ("riser", 685256, "https://cdn.freesound.org/previews/685/685256_12265588-hq.mp3",
     "1a48f213348b8b362844c6ba1f87cf30dc69873e16c8cfb58d9211dc2348f726", "Райзер короткий", ["райзер", "riser", "нагнетание"], 3.0),
    ("riser-2", 561207, "https://cdn.freesound.org/previews/561/561207_10825267-hq.mp3",
     "c512a725e9c866277b3fa0f8e7f8f311d5cca619bb0fac2e3620958f2a8b7e49", "Райзер", ["райзер", "riser"], 2.0),
    ("record-scratch", 43404, "https://cdn.freesound.org/previews/43/43404_465423-hq.mp3",
     "ee71af1d2da7d46916a5ab06a9a5d5ec7ec307bb7deb98f556853d000c7fa0ad", "Скретч пластинки", ["скретч", "scratch", "стоп"], 1.6),
    ("record-scratch-2", 431780, "https://cdn.freesound.org/previews/431/431780_817038-hq.mp3",
     "1a5793f33a416ed82b687abe06417f17e7dbcf50244e0b5b710369286e5fc538", "Скретч пластинки 2", ["скретч", "scratch"], 1.9),
    ("ding", 571513, "https://cdn.freesound.org/previews/571/571513_2226836-hq.mp3",
     "de3aedd444fb21e4c46bb581f069a6e3fdd98afc475d2bf235939a0452e7fcc6", "Динь-дон уведомление", ["динь", "уведомление", "ding"], 1.0),
    ("triangle", 349503, "https://cdn.freesound.org/previews/349/349503_5450487-hq.mp3",
     "def3410155c9d8246054c98e495f54e8e466c56c19b440654ff583da3f2fe1a8", "Треугольник динь", ["динь", "ding", "колокольчик"], 1.8),
    ("pop", 202230, "https://cdn.freesound.org/previews/202/202230_3725006-hq.mp3",
     "f710da77f4fcafb1dafb16b677b108469ee4127f03198738c3c0d3f39608aec7", "Поп", ["поп", "pop", "появление"], 0.7),
    ("pop-cartoon", 245646, "https://cdn.freesound.org/previews/245/245646_1038806-hq.mp3",
     "5c766933e63fa3e7dda8531899fd69544e5a5e4c19c34af50418335962423e5c", "Мультяшный поп", ["поп", "pop"], 0.3),
    ("camera", 431588, "https://cdn.freesound.org/previews/431/431588_2339776-hq.mp3",
     "e805680b4ee8a48bc537f266c866e74d60b076a76e52af3acbaacf77e8cb7392", "Камера телефона", ["камера", "затвор", "фото"], 0.6),
    ("camera-2", 170229, "https://cdn.freesound.org/previews/170/170229_3133582-hq.mp3",
     "b5c29f81b6daedb089273859ce88575549154cbe33fb13d37c12795364f95a5f", "Затвор камеры", ["камера", "затвор"], 0.4),
    ("laugh-sitcom", 371562, "https://cdn.freesound.org/previews/371/371562_2247456-hq.mp3",
     "9b8129273534e3199a48360c462b3a33a903f2328051b2cda6689b698475a53f", "Смех зала (ситком)", ["смех", "laugh", "ситком"], 4.0),
    ("laugh-crowd", 138112, "https://cdn.freesound.org/previews/138/138112_1572282-hq.mp3",
     "e782571b5d06fba33343ff013cbc4218c9b8e5c8f5d8b633f8cb105c31fe51c0", "Смех толпы", ["смех", "laugh"], 3.5),
    ("applause", 462362, "https://cdn.freesound.org/previews/462/462362_9159316-hq.mp3",
     "0297dc19883bf2ffd6ea2634101eb744c6d52acb588a7b8eab1760d6ebd543ba", "Аплодисменты", ["аплодисменты", "applause"], 4.0),
    ("cheer", 400590, "https://cdn.freesound.org/previews/400/400590_3372256-hq.mp3",
     "8f702343651525abc1814211b1b122c697019e0070572cabe4e8a4ca0bed4753", "Радостные крики", ["аплодисменты", "ура", "cheer"], 4.0),
    ("boing", 161122, "https://cdn.freesound.org/previews/161/161122_2750714-hq.mp3",
     "c354584c0d083c13444a8acb6be4726a90b420463e5a66cec58769f7bb98acf3", "Боинг", ["боинг", "мульт", "прыжок"], 1.5),
    ("boing-2", 540790, "https://cdn.freesound.org/previews/540/540790_11537497-hq.mp3",
     "83a4c9e99ed1397f3938fbaf0f2aafae7826e04a101bb84bc576845543613743", "Боинг 2", ["боинг", "мульт"], 1.2),
    ("glitch", 332711, "https://cdn.freesound.org/previews/332/332711_5695297-hq.mp3",
     "3c0563b52882b6b74d68435cbe9e583aa5a48a5ad82e2631275e087d56d24aeb", "Глитч", ["глитч", "glitch", "сбой"], 1.6),
    ("glitch-2", 2314, "https://cdn.freesound.org/previews/2/2314_3619-hq.mp3",
     "0b4dcd0959fc7f36641773af8324a7a5a4cf7cf6dc88eaba9bd4a914f1981a47", "Глитч 2", ["глитч", "glitch"], 1.6),
    ("typing", 447909, "https://cdn.freesound.org/previews/447/447909_9159316-hq.mp3",
     "9777520f7b6a68f2b33104ee908437a78c434fb643a3038fab3fbdf235a848c5", "Печать на клавиатуре", ["клавиатура", "печать", "typing"], 3.0),
    ("typing-digital", 416777, "https://cdn.freesound.org/previews/416/416777_7935195-hq.mp3",
     "8b055da73040ccfcd8efb3c47b9c8071218d33b3a1311f47653600a75e4497f8", "Цифровая печать", ["печать", "typing"], 1.0),
    ("cash", 209578, "https://cdn.freesound.org/previews/209/209578_2558531-hq.mp3",
     "7459185c471d0b8ce87f8616f6f48f6e372be9c89f1ae86c6982618ab74e96d3", "Касса", ["касса", "деньги", "покупка"], 2.5),
    ("cha-ching", 351304, "https://cdn.freesound.org/previews/351/351304_96253-hq.mp3",
     "89a3e797b08677dbf298dc50aed4b52df045e9471934839ef9ed18a526c1398c", "Ча-чинг", ["деньги", "касса", "ча-чинг"], 2.5),
    ("sad-trombone", 175409, "https://cdn.freesound.org/previews/175/175409_1326576-hq.mp3",
     "de4a029a0537dbecaf49b4936b0969919f5b7b54083d3859d584b5c4cc7dee3d", "Грустный тромбон", ["фейл", "тромбон", "провал"], 4.0),
    ("fail-horn", 362206, "https://cdn.freesound.org/previews/362/362206_6629901-hq.mp3",
     "6e540e4fbbd2c7a13ecd8c8bf39ff607f145e6ff0ef6df907f54ee64974e5259", "Фейл-рожок", ["фейл", "провал"], 4.0),
    ("tension", 506295, "https://cdn.freesound.org/previews/506/506295_10643288-hq.mp3",
     "4085d585111f39a9ba84b252bfca3ae5f8a41254e288048e9de1501eb12a5fd8", "Напряжение", ["саспенс", "напряжение"], 3.5),
    ("sting", 578363, "https://cdn.freesound.org/previews/578/578363_10522382-hq.mp3",
     "518636593dcd5195ab885fcf55ce481b37c99a0621333f7e58220a9b7c051aae", "Резкий аккорд", ["саспенс", "sting", "шок"], 3.0),
    ("punch", 118513, "https://cdn.freesound.org/previews/118/118513_2136023-hq.mp3",
     "3843df72e4433db9cf6ebc68314494ad843b6ab97c2f16df33728cb4488504a3", "Удар кулаком", ["удар", "punch"], 0.7),
    ("chop", 423526, "https://cdn.freesound.org/previews/423/423526_4586522-hq.mp3",
     "d4ac685208fbf3bc141b450f772c14fe48b8e1bf48433de6ee6e57a4b8a72c2b", "Каратэ-удар", ["удар", "chop"], 0.6),
    ("click", 678248, "https://cdn.freesound.org/previews/678/678248_7806746-hq.mp3",
     "a8d0369a986abe53067b160e0fe902e1a6ebfdfc5ce443ac67a37185fc04462d", "Клик мыши", ["клик", "click"], 0.6),
]
FREESOUND_VERSION = "1"
FREESOUND_RMS_DB = -20.0  # «громкая часть» звука; речь клипа после нормализации ~ −14 LUFS
FREESOUND_PEAK_DB = -4.0


def _normalize(samples: list[float], target_rms_db: float = -15.0, peak_db: float = -1.0) -> list[float]:
    """Приводим громкость к одному уровню: RMS «громкой части» к target, но
    пик не выше peak_db. loudnorm на звуках короче 3 с врёт — считаем сами."""
    peak = max((abs(s) for s in samples), default=0.0) or 1.0
    loud = sorted((s * s for s in samples), reverse=True)
    top = loud[: max(1, len(loud) // 3)]
    rms = math.sqrt(sum(top) / len(top)) or 1e-9
    gain = min(10 ** (target_rms_db / 20) / rms, 10 ** (peak_db / 20) / peak)
    return [s * gain for s in samples]


def _read_wav(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    count = len(raw) // 2
    return [v / 32768.0 for v in struct.unpack(f"<{count}h", raw[: count * 2])]


def _write_wav(path: Path, samples: list[float]) -> None:
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(frames)


def fetch_freesound(target: Path, dl: Path) -> dict:
    pack_id = "freesound"
    pack_dir = target / pack_id
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("для звуков нужен ffmpeg")
    stamp = f"freesound v{FREESOUND_VERSION} n{len(FREESOUND_SFX)} idx{INDEX_VERSION}"
    meta_path = pack_dir / "items.json"
    if read_stamp(pack_dir) != stamp or not meta_path.exists():
        fresh_dir(pack_dir)
        work = fresh_dir(dl / "freesound")
        items: list[dict] = []
        for name, sound_id, url, sha, label, tags, max_sec in FREESOUND_SFX:
            mp3 = download(url, work / f"{name}.mp3", sha)
            wav = work / f"{name}.wav"
            fade = min(0.25, max_sec / 4)
            subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp3),
                 "-af", f"silenceremove=start_periods=1:start_threshold=-45dB,atrim=0:{max_sec},"
                        f"afade=t=out:st={max(0.0, max_sec - fade):.3f}:d={fade:.3f}",
                 "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(wav)],
                check=True, timeout=60,
            )
            samples = _normalize(_read_wav(wav), FREESOUND_RMS_DB, FREESOUND_PEAK_DB)
            _write_wav(wav, samples)
            dest = pack_dir / f"{safe_name(name)}.ogg"
            subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                 "-c:a", "libvorbis", "-q:a", "5", "-ar", str(SR), str(dest)],
                check=True, timeout=60,
            )
            items.append(describe_item(dest, "sfx", label, list(tags),
                                       source_url=f"https://freesound.org/s/{sound_id}/"))
        (pack_dir / "LICENSE.txt").write_text(
            "Звуки с freesound.org под лицензией CC0 1.0 (public domain). Источник каждого — source_url в items.json.\n",
            encoding="utf-8",
        )
        meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        write_stamp(pack_dir, stamp)
        log(f"freesound: {len(items)} звуков")
    items = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "id": pack_id,
        "name": "Монтажные звуки (Freesound, CC0)",
        "kind": "sfx",
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "attribution": "",
        "source": "https://freesound.org",
        "version": FREESOUND_VERSION,
        "items": items,
    }


# --------------------------------------------------------------------------- #

PACK_ORDER = ["twemoji", "openmoji-extras", "kenney-particles", "freesound",
              "kenney-impact", "kenney-interface", "kenney-ui", "imgflip"]


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
        "freesound": lambda: fetch_freesound(target, dl),
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
