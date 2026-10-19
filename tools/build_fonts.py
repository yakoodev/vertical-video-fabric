"""Собирает vendor/fonts/ — шрифты для моушн-слоёв (титры, плашки, счётчики).

Запускается вручную разработчиком, НЕ при сборке образа: результат (ttf +
fonts.json + OFL.txt + LICENSES.md) лежит в репозитории, чтобы рендер не
зависел от сети.

Зачем отдельный скрипт, а не просто скачанные файлы: в google/fonts почти все
семейства лежат вариативными (``Montserrat[wght].ttf``), а canvas-движки
(@napi-rs/canvas / Skia) из вариативного файла видят только экземпляр по
умолчанию — «Montserrat 900» превратился бы в тонкий Thin. Поэтому нужные
насыщенности режем в статические файлы через fontTools.varLib.instancer.

Шрифты с Reserved Font Name (OFL) не модифицируем вовсе: берём статику как есть
(из google/fonts или из upstream-репозитория семейства), иначе по OFL пришлось
бы переименовывать семейство.

Требует fonttools (только для разработчика):
    python -m pip install --target <tmp>/pylib fonttools
    python tools/build_fonts.py --pylib <tmp>/pylib --cache <tmp>/fonts_dl
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "vendor" / "fonts"

# Коммит google/fonts, с которого всё взято — для воспроизводимости.
GF_SHA = "47d54ca1ebb14fd864695f1d39dc734f39017c33"
GF_RAW = f"https://raw.githubusercontent.com/google/fonts/{GF_SHA}/ofl"
COMFORTAA_SHA = "4aa21cfb7b018629dcf17aab6220e638411c7164"
COMFORTAA_RAW = f"https://raw.githubusercontent.com/alexeiva/comfortaa/{COMFORTAA_SHA}"

WEIGHT_NAMES = {
    100: ("Thin", "Тонкий"),
    200: ("ExtraLight", "Сверхсветлый"),
    300: ("Light", "Светлый"),
    400: ("Regular", "Обычный"),
    500: ("Medium", "Средний"),
    600: ("SemiBold", "Полужирный"),
    700: ("Bold", "Жирный"),
    800: ("ExtraBold", "Сверхжирный"),
    900: ("Black", "Чёрный"),
}

# family, каталог в google/fonts, исходный файл, насыщенности (для вариативных)
# или weight (статика как есть), теги, подсказка для UI.
# rfn=True — у шрифта Reserved Font Name: файл не трогаем.
FAMILIES: list[dict] = [
    # --- Жирные заголовки / подписи для шортсов
    {"family": "Montserrat", "dir": "montserrat", "src": "Montserrat[wght].ttf",
     "weights": [500, 700, 800, 900], "tags": ["display", "text"], "hint": "универсальный жирный заголовок"},
    {"family": "Rubik", "dir": "rubik", "src": "Rubik[wght].ttf",
     "weights": [500, 700, 900], "tags": ["display", "text"], "hint": "округлый, дружелюбный"},
    {"family": "Unbounded", "dir": "unbounded", "src": "Unbounded[wght].ttf",
     "weights": [700, 900], "tags": ["display"], "hint": "широкий, модный"},
    {"family": "Russo One", "dir": "russoone", "src": "RussoOne-Regular.ttf", "weight": 400, "rfn": True,
     "tags": ["display", "meme"], "hint": "спортивный, геймерский"},
    {"family": "Exo 2", "dir": "exo2", "src": "Exo2[wght].ttf",
     "weights": [700, 900], "tags": ["display"], "hint": "техно, фантастика"},
    {"family": "Dela Gothic One", "dir": "delagothicone", "src": "DelaGothicOne-Regular.ttf", "weight": 400,
     "subset": True, "tags": ["display", "meme"], "hint": "очень плотный, кричащий"},
    {"family": "Rubik Mono One", "dir": "rubikmonoone", "src": "RubikMonoOne-Regular.ttf", "weight": 400,
     "tags": ["display"], "hint": "широкий моноширинный, капс"},
    {"family": "Rubik Glitch", "dir": "rubikglitch", "src": "RubikGlitch-Regular.ttf", "weight": 400,
     "tags": ["display", "meme"], "hint": "глитч, помехи"},
    # --- Узкие
    {"family": "Oswald", "dir": "oswald", "src": "Oswald[wght].ttf",
     "weights": [500, 700], "tags": ["condensed", "display", "meme"], "hint": "узкий, ближе всего к Impact"},
    {"family": "Roboto Condensed", "dir": "robotocondensed", "src": "RobotoCondensed[wght].ttf",
     "weights": [700, 900], "tags": ["condensed", "text"], "hint": "узкий, нейтральный"},
    {"family": "PT Sans Narrow", "dir": "ptsansnarrow", "src": "PT_Sans-Narrow-Web-Bold.ttf", "weight": 700,
     "rfn": True, "file": "PTSansNarrow-Bold.ttf", "tags": ["condensed", "text"], "hint": "узкий, классика"},
    {"family": "Alumni Sans", "dir": "alumnisans", "src": "AlumniSans[wght].ttf",
     "weights": [800, 900], "tags": ["condensed", "meme", "display"], "hint": "высокий узкий, мемный"},
    # --- Чистый текст
    {"family": "Inter", "dir": "inter", "src": "Inter[opsz,wght].ttf",
     "weights": [400, 600, 800], "tags": ["text"], "hint": "чистый интерфейсный"},
    {"family": "Manrope", "dir": "manrope", "src": "Manrope[wght].ttf",
     "weights": [500, 800], "tags": ["text"], "hint": "современный гротеск"},
    {"family": "Golos Text", "dir": "golostext", "src": "GolosText[wght].ttf",
     "weights": [500, 700, 900], "tags": ["text"], "hint": "отечественный гротеск"},
    {"family": "Nunito", "dir": "nunito", "src": "Nunito[wght].ttf",
     "weights": [700, 900], "tags": ["text", "display"], "hint": "мягкий округлый"},
    # --- Рукописные / весёлые
    {"family": "Caveat", "dir": "caveat", "src": "Caveat[wght].ttf",
     "weights": [700], "tags": ["hand"], "hint": "от руки маркером"},
    {"family": "Marck Script", "dir": "marckscript", "src": "MarckScript-Regular.ttf", "weight": 400,
     "rfn": True, "tags": ["hand"], "hint": "прописи, письмо"},
    {"family": "Comfortaa", "upstream": f"{COMFORTAA_RAW}/fonts/TTF/Comfortaa-Bold.ttf",
     "license_url": f"{COMFORTAA_RAW}/OFL.txt", "src": "Comfortaa-Bold.ttf", "weight": 700,
     "rfn": True, "tags": ["hand", "display"], "hint": "геометрический округлый"},
    {"family": "Lobster", "dir": "lobster", "src": "Lobster-Regular.ttf", "weight": 400,
     "rfn": True, "tags": ["hand", "display"], "hint": "ретро-вывеска"},
    {"family": "Pacifico", "dir": "pacifico", "src": "Pacifico-Regular.ttf", "weight": 400,
     "tags": ["hand", "display"], "hint": "сёрф-скрипт"},
    {"family": "Pangolin", "dir": "pangolin", "src": "Pangolin-Regular.ttf", "weight": 400,
     "tags": ["hand", "meme"], "hint": "комиксный, как Comic Sans"},
    # --- Пиксель / засечки
    {"family": "Press Start 2P", "dir": "pressstart2p", "src": "PressStart2P-Regular.ttf", "weight": 400,
     "rfn": True, "tags": ["pixel"], "hint": "8-бит, игры"},
    {"family": "PT Serif", "dir": "ptserif", "src": "PT_Serif-Web-Regular.ttf", "weight": 400,
     "rfn": True, "file": "PTSerif-Regular.ttf", "tags": ["serif", "text"], "hint": "книжный с засечками"},
    {"family": "PT Serif", "dir": "ptserif", "src": "PT_Serif-Web-Bold.ttf", "weight": 700,
     "rfn": True, "file": "PTSerif-Bold.ttf", "tags": ["serif", "text"], "hint": "книжный с засечками"},
]

# Блоки Unicode, которые оставляем при прореживании CJK-шрифта (Dela Gothic One
# весит 2,4 МБ из-за японских иероглифов, которые нам не нужны).
SUBSET_UNICODES = (
    list(range(0x20, 0x250))  # Latin, Latin-1, Latin Extended-A/B
    + list(range(0x300, 0x370))  # комбинируемые диакритики
    + list(range(0x400, 0x530))  # Cyrillic + Supplement
    + list(range(0x2000, 0x2070))  # пунктуация
    + list(range(0x20A0, 0x20C0))  # валюты (₽)
    + list(range(0x2100, 0x2200))  # буквоподобные (№, ™) и стрелки
)


def _fetch(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("fetch", url)
    with urllib.request.urlopen(url, timeout=120) as resp:
        dest.write_bytes(resp.read())
    return dest


def _source_url(item: dict) -> str:
    if item.get("upstream"):
        return item["upstream"]
    return f"{GF_RAW}/{item['dir']}/{quote(item['src'])}"


def _license_url(item: dict) -> str:
    return item.get("license_url") or f"{GF_RAW}/{item['dir']}/OFL.txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pylib", help="каталог с установленным fonttools", default="")
    ap.add_argument("--cache", required=True, help="куда складывать скачанное (отдельный пустой каталог)")
    args = ap.parse_args()
    if args.pylib:
        sys.path.insert(0, args.pylib)
    from fontTools import subset as ft_subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    cache = Path(args.cache)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.ttf"):
        old.unlink()
    entries: list[dict] = []
    licenses: dict[str, dict] = {}
    ofl_body = ""

    for item in FAMILIES:
        family = item["family"]
        key = item.get("dir") or family.lower().replace(" ", "") + "-upstream"
        src = _fetch(_source_url(item), cache / key / item["src"])
        lic = _fetch(_license_url(item), cache / key / "OFL.txt")
        lic_text = lic.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
        # Копирайт — первый абзац OFL.txt (у ParaType он на несколько строк),
        # дальше — сам текст OFL-1.1, одинаковый у всех.
        head, ofl_body = lic_text.strip().split("\n\n", 1)
        licenses[family] = {"url": _source_url(item), "copyright": " ".join(head.split())}
        stem = family.replace(" ", "")
        if item.get("weights"):
            for weight in item["weights"]:
                font = TTFont(src)
                # Прочие оси (opsz у Inter) — None = прибить к значению по умолчанию,
                # как у статики Google Fonts.
                axes = {a.axisTag: None for a in font["fvar"].axes}
                axes["wght"] = weight
                inst = instancer.instantiateVariableFont(font, axes, updateFontNames=True)
                assert "fvar" not in inst, f"{family}: остались вариативные оси"
                inst["OS/2"].usWeightClass = weight
                fname = f"{stem}-{WEIGHT_NAMES[weight][0]}.ttf"
                inst.save(OUT / fname)
                entries.append(_entry(item, fname, weight))
        else:
            fname = item.get("file") or item["src"]
            if item.get("subset"):
                assert not item.get("rfn"), "шрифт с RFN модифицировать нельзя"
                opts = ft_subset.Options()
                opts.layout_features = ["*"]
                opts.name_IDs = ["*"]
                opts.name_languages = ["*"]
                opts.notdef_outline = True
                font = TTFont(src)
                sub = ft_subset.Subsetter(opts)
                sub.populate(unicodes=SUBSET_UNICODES)
                sub.subset(font)
                font.save(OUT / fname)
            else:
                (OUT / fname).write_bytes(src.read_bytes())
            entries.append(_entry(item, fname, item["weight"]))

    # Проверяем то, что обещаем в fonts.json: кириллица на месте.
    need = [*range(0x410, 0x450), 0x401, 0x451]
    for e in entries:
        cmap = TTFont(OUT / e["file"]).getBestCmap()
        missing = [c for c in need if c not in cmap]
        e["cyrillic"] = not missing
        if missing:
            print("!! no cyrillic:", e["file"], len(missing))

    # По записи на строку — диф читается глазами.
    body = ",\n".join(" " + json.dumps(e, ensure_ascii=False) for e in entries)
    (OUT / "fonts.json").write_text("[\n" + body + "\n]\n", encoding="utf-8", newline="\n")
    # Условие OFL — класть текст лицензии рядом со шрифтами.
    (OUT / "OFL.txt").write_text(ofl_body.strip() + "\n", encoding="utf-8", newline="\n")
    _write_licenses(licenses)
    total = sum((OUT / e["file"]).stat().st_size for e in entries)
    print(f"{len(entries)} files, {len({e['family'] for e in entries})} families, {total / 1e6:.1f} MB")
    return 0


def _entry(item: dict, fname: str, weight: int) -> dict:
    return {
        "family": item["family"],
        "file": fname,
        "weight": weight,
        "style": "normal",
        "cyrillic": True,
        "tags": item["tags"],
        "label": f"{item['family']} · {WEIGHT_NAMES[weight][1]}",
        "hint": item["hint"],
    }


def _write_licenses(licenses: dict[str, dict]) -> None:
    lines = [
        "# Шрифты моушн-слоёв — лицензии",
        "",
        "Все шрифты распространяются по **SIL Open Font License 1.1** (OFL-1.1),",
        "текст лицензии — `OFL.txt` рядом (https://openfontlicense.org).",
        "Шрифты можно свободно использовать в видео (готовый рендер под OFL не попадает),",
        "встраивать в приложение и распространять вместе с ним; продавать сами файлы",
        "отдельно нельзя.",
        "",
        f"Источник — репозиторий https://github.com/google/fonts (коммит `{GF_SHA}`),",
        f"Comfortaa — upstream https://github.com/alexeiva/comfortaa (коммит `{COMFORTAA_SHA}`).",
        "",
        "Модификации: вариативные шрифты без Reserved Font Name разрезаны на статические",
        "экземпляры нужных насыщенностей (fontTools instancer); Dela Gothic One прорежен",
        "до латиницы+кириллицы (выброшены японские глифы). Шрифты с Reserved Font Name",
        "(Russo One, PT Sans Narrow, PT Serif, Marck Script, Comfortaa, Lobster,",
        "Press Start 2P) лежат без изменений, только файлы переименованы.",
        "Пересборка: `tools/build_fonts.py`.",
        "",
        "| Семейство | Copyright | Исходный файл |",
        "|---|---|---|",
    ]
    for family, info in licenses.items():
        lines.append(f"| {family} | {info['copyright']} | {info['url']} |")
    (OUT / "LICENSES.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
