"""Монтажные звуки «freesound»: меньше, но качественнее (CC0, закреплены sha256).

    python3 tools/fetch_sfx.py [TARGET]          # по умолчанию vendor/packs

Владелец назвал интерфейсные «дзынь/клик/треугольник» и мультяшные боинги
дешёвыми, синтетику запретил. Здесь — только настоящие «киношные» и монтажные
записи: мягкие вжухи и райзеры на переход, низкие удары и бум на панчлайн,
скретч, реакции зала, затвор камеры, касса, мягкий поп.

Отдельно от tools/fetch_packs.py — свой слой в Dockerfile (как tools/fetch_music.py):
правка списка звуков не перекачивает все паки. fetch_packs.py по-прежнему
собирает старый пак v1 в своём (закэшированном) слое; этот скрипт идёт следом и
пересобирает пак ``freesound`` по списку ниже той же функцией
``fetch_packs.fetch_freesound`` (тишина в начале — долой, длина ограничена,
громкость выровнена ниже речи) и подменяет его запись в ``packs.json``. Id пака
прежний — ссылки ``sfx:freesound/<файл>`` в слоях клипов не ломаются, а
убранным звукам app/motion_assets.FREESOUND_ALIASES подставляет ближайшие новые.

Только данные — ничего скачанного не исполняется.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_packs  # noqa: E402 - соседний модуль из той же папки

P = "https://cdn.freesound.org/previews"

# (файл, id звука, превью, sha256, подпись, теги, макс. секунд) — всё CC0 1.0.
# Теги — по-русски «когда ставить»: агент ищет их через search_pack freesound.
FREESOUND_SFX = [
    # --- Переход: вжух / райзер -------------------------------------------- #
    ("whoosh-1", 60013, f"{P}/60/60013_71257-hq.mp3",
     "ebdb219e7f36f7e1aecdb045d6d8bdad059119af319863777c7302a69fa41a92",
     "Вжух короткий", ["вжух", "whoosh", "переход", "смена темы"], 1.0),
    ("whoosh-soft", 701104, f"{P}/701/701104_13504080-hq.mp3",
     "791bf392615c81ef7b0fd1a92fa39b847241ae0f995669f22573763edbaef07b", "Вжух мягкий", ["вжух", "whoosh", "переход", "мягкий"], 1.6),
    ("whoosh-cinematic", 648729, f"{P}/648/648729_8698658-hq.mp3",
     "eea81c01bbf483b72d0a146973ae433b42515bb85d337eeef1e626c76124d9f6", "Вжух киношный", ["вжух", "whoosh", "переход", "кино"], 1.8),
    ("whoosh-4", 683101, f"{P}/683/683101_6253486-hq.mp3",
     "3319e044194b8185671ff8d3a2c9660d101fbae67ea31c2f6460e9cf6c1eac9d",
     "Быстрый вжух", ["вжух", "whoosh", "быстро"], 1.6),
    ("whip", 346373, f"{P}/346/346373_4882199-hq.mp3",
     "1f79dfeca3b8bc99a98df74ee2ff3fcff249fd9f0bb73495dfca40a62afed5cc",
     "Хлыст", ["хлыст", "whip", "вжух", "резкая смена"], 0.8),
    ("riser", 685256, f"{P}/685/685256_12265588-hq.mp3",
     "1a48f213348b8b362844c6ba1f87cf30dc69873e16c8cfb58d9211dc2348f726",
     "Райзер короткий", ["райзер", "riser", "нагнетание", "перед панчлайном"], 3.0),
    ("riser-subtle", 859482, f"{P}/859/859482_18689440-hq.mp3",
     "5425022716bdc5a8a8ce9ceb534ce2fc1d0b81ad3bf8aa5a03dbd03c085ee927", "Райзер тихий", ["райзер", "riser", "нагнетание", "мягкий"], 3.0),
    ("hit-transition", 648534, f"{P}/648/648534_8698658-hq.mp3",
     "bd8652a2aaef4eaeb18be1fb54c1cc90917dd7d849c2cf74aef6c8950d00edee",
     "Удар-переход", ["удар", "hit", "переход", "кино"], 2.5),
    # --- Панчлайн: низкий удар / бум ---------------------------------------- #
    ("drama-boom", 802074, f"{P}/802/802074_15956618-hq.mp3",
     "dc9d06afa0890cd889671b4c19d0dd4996309702a57d18024eadac53b530fd66",
     "Драма-бум (вайн-бум)", ["бум", "vine boom", "драма", "панчлайн"], 2.5),
    ("impact-low", 430978, f"{P}/430/430978_8698658-hq.mp3",
     "bcf27da9931daf7030a8a4954235e1cf91eaecd1a7da254b9e2b748aa3c55a39",
     "Низкий удар", ["удар", "impact", "бум", "панчлайн"], 2.5),
    ("impact-cinematic", 427803, f"{P}/427/427803_2866779-hq.mp3",
     "f4ad9d82b8bb2340bbddfd93dab07031b1bd6a988af760ebd7306e848dfff396", "Киношный удар с медью", ["удар", "impact", "кино", "брам", "шок"], 3.5),
    ("braam", 592041, f"{P}/592/592041_9015615-hq.mp3",
     "365661f9c51281351c254b70189a92bf70c0d9a084efa59cf89ab0a9bfd9d806", "Брам (трейлер)", ["брам", "braam", "кино", "трейлер", "шок"], 3.3),
    ("sub-drop", 428073, f"{P}/428/428073_4067257-hq.mp3",
     "cd886173aa0270c9adbdb04fa14eb7f4cde53bd70506a4bf8d6e14de00e6bfd9", "Саб-дроп мягкий", ["бас", "sub drop", "бум", "панчлайн"], 3.0),
    ("bass-drop", 212768, f"{P}/212/212768_71257-hq.mp3",
     "6a964a1b97bfdfccafbc05169029690f60ddf00959f3d341c8a941b02e9ebb71",
     "Бас-дроп", ["бас", "drop", "бум"], 3.0),
    ("punch", 118513, f"{P}/118/118513_2136023-hq.mp3",
     "3843df72e4433db9cf6ebc68314494ad843b6ab97c2f16df33728cb4488504a3",
     "Удар кулаком", ["удар", "punch"], 0.7),
    # --- Стоп / поворот ------------------------------------------------------ #
    ("record-scratch", 43404, f"{P}/43/43404_465423-hq.mp3",
     "ee71af1d2da7d46916a5ab06a9a5d5ec7ec307bb7deb98f556853d000c7fa0ad",
     "Скретч пластинки", ["скретч", "scratch", "стоп", "поворот"], 1.6),
    ("tension", 506295, f"{P}/506/506295_10643288-hq.mp3",
     "4085d585111f39a9ba84b252bfca3ae5f8a41254e288048e9de1501eb12a5fd8",
     "Напряжение", ["саспенс", "напряжение"], 3.5),
    ("sting", 578363, f"{P}/578/578363_10522382-hq.mp3",
     "518636593dcd5195ab885fcf55ce481b37c99a0621333f7e58220a9b7c051aae",
     "Резкий аккорд", ["саспенс", "sting", "шок"], 3.0),
    ("sad-trombone", 175409, f"{P}/175/175409_1326576-hq.mp3",
     "de4a029a0537dbecaf49b4936b0969919f5b7b54083d3859d584b5c4cc7dee3d",
     "Грустный тромбон", ["фейл", "тромбон", "провал"], 4.0),
    # --- Реакция зала -------------------------------------------------------- #
    ("laugh-sitcom", 371562, f"{P}/371/371562_2247456-hq.mp3",
     "9b8129273534e3199a48360c462b3a33a903f2328051b2cda6689b698475a53f",
     "Смех зала (ситком)", ["смех", "laugh", "ситком", "зал"], 4.0),
    ("laugh-small", 324894, f"{P}/324/324894_2104797-hq.mp3",
     "79bff10084dc6c4f8ba0dc1cfcfbacf7d756934284388d1b04058b792eb3ca8a", "Смешок зала", ["смех", "laugh", "зал", "тихо"], 3.3),
    ("crowd-gasp", 324898, f"{P}/324/324898_2104797-hq.mp3",
     "4423fa8f2aea3f9f928147acba68f6b63e9bdc8116b9d9455559cb84f49b692f", "Зал ахнул (ох!)", ["ох", "ooh", "удивление", "шок", "зал"], 3.0),
    ("crowd-aww", 124996, f"{P}/124/124996_687791-hq.mp3",
     "6648ca3ec87a6d4ccaafc139bf1c7b6a3629c1888101630c4003428df152ce3b",
     "Зал сочувствует (оу-у)", ["оу", "aww", "сочувствие", "фейл", "зал"], 2.0),
    ("applause", 462362, f"{P}/462/462362_9159316-hq.mp3",
     "0297dc19883bf2ffd6ea2634101eb744c6d52acb588a7b8eab1760d6ebd543ba",
     "Аплодисменты", ["аплодисменты", "applause"], 4.0),
    ("cheer", 400590, f"{P}/400/400590_3372256-hq.mp3",
     "8f702343651525abc1814211b1b122c697019e0070572cabe4e8a4ca0bed4753",
     "Радостные крики", ["аплодисменты", "ура", "cheer"], 4.0),
    # --- Предметные ---------------------------------------------------------- #
    ("camera", 431588, f"{P}/431/431588_2339776-hq.mp3",
     "e805680b4ee8a48bc537f266c866e74d60b076a76e52af3acbaacf77e8cb7392",
     "Камера телефона", ["камера", "затвор", "фото", "скрин"], 0.6),
    ("camera-2", 170229, f"{P}/170/170229_3133582-hq.mp3",
     "b5c29f81b6daedb089273859ce88575549154cbe33fb13d37c12795364f95a5f",
     "Затвор камеры", ["камера", "затвор", "фото", "щелчок"], 0.4),
    ("cash", 209578, f"{P}/209/209578_2558531-hq.mp3",
     "7459185c471d0b8ce87f8616f6f48f6e372be9c89f1ae86c6982618ab74e96d3",
     "Касса", ["касса", "деньги", "цена", "покупка"], 2.5),
    ("cha-ching", 351304, f"{P}/351/351304_96253-hq.mp3",
     "89a3e797b08677dbf298dc50aed4b52df045e9471934839ef9ed18a526c1398c",
     "Ча-чинг", ["деньги", "касса", "цена", "ча-чинг"], 2.5),
    ("pop", 202230, f"{P}/202/202230_3725006-hq.mp3",
     "f710da77f4fcafb1dafb16b677b108469ee4127f03198738c3c0d3f39608aec7",
     "Поп", ["поп", "pop", "появление"], 0.7),
    ("pop-soft", 258269, f"{P}/258/258269_4824549-hq.mp3",
     "7d8a262c747a1733da0b6af232a6c3e2a00a63c353d81e6eca82eef398eaf737",
     "Поп мягкий (губами)", ["поп", "pop", "появление", "уведомление", "мягкий"], 0.4),
]
# Из пака v1 (40 звуков) убраны как дешёвые — интерфейс, мультяшное, цифровое:
# ding, triangle, click, pop-cartoon, boing, boing-2, typing, typing-digital, glitch,
# glitch-2, chop, fail-horn; и дубли послабее: whoosh-2, whoosh-3, riser-2, bass-drop-2,
# impact-big, record-scratch-2, laugh-crowd. Их ссылки ведут на замену —
# app/motion_assets.FREESOUND_ALIASES.
FREESOUND_VERSION = "2"


def fetch_sfx(target: Path, dl: Path) -> dict:
    """Пак freesound по списку выше — той же обработкой, что и в fetch_packs."""
    fetch_packs.FREESOUND_SFX = FREESOUND_SFX
    fetch_packs.FREESOUND_VERSION = FREESOUND_VERSION
    pack = fetch_packs.fetch_freesound(target, dl)
    pack["count"] = len(pack["items"])
    pack["size"] = sum(int(i.get("size") or 0) for i in pack["items"])
    return pack


def update_index(target: Path, pack: dict) -> None:
    """Подменить запись пака в packs.json (остальные паки — как собрал fetch_packs)."""
    index_path = target / "packs.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        packs = [p for p in index.get("packs") or [] if isinstance(p, dict)]
    except (OSError, ValueError):
        index, packs = {"version": fetch_packs.INDEX_VERSION}, []
    for i, p in enumerate(packs):
        if p.get("id") == pack["id"]:
            packs[i] = pack
            break
    else:
        packs.append(pack)
    index.update(packs=packs, generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    index.setdefault("version", fetch_packs.INDEX_VERSION)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0] if args else fetch_packs.DEFAULT_TARGET).resolve()
    target.mkdir(parents=True, exist_ok=True)
    dl = target / ".downloads-sfx"
    try:
        pack = fetch_sfx(target, dl)
        update_index(target, pack)
    finally:
        shutil.rmtree(dl, ignore_errors=True)
    fetch_packs.log(f"freesound: {pack['count']} звуков, {pack['size'] / 1e6:.1f} МБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
