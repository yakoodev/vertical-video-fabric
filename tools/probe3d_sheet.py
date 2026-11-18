"""Лист сравнения пробника 3D: кадры рядом с подписями (рендерер, время на кадр) + уменьшенная копия для телефона.

    python tools/probe3d_sheet.py --out D:/vvf-offload/tmp/probe3d/compare_1.jpg \
        --panel "Blender Eevee (серия, fast)|0.75 с/кадр" blender/still_0673.png \
        --panel "three.js растр + проба GI|8 мс/кадр" three/f_0673_raster.png ...
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/bahnschrift.ttf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--panel", nargs=2, action="append", metavar=("LABEL", "IMAGE"), required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--width", type=int, default=720, help="ширина одной панели в полном листе")
    ap.add_argument("--phone", type=int, default=1080, help="ширина копии для телефона (весь лист)")
    a = ap.parse_args()
    w = a.width
    h = int(w * 1920 / 1080)
    cap = 120
    top = 70 if a.title else 0
    gap = 12
    sheet = Image.new("RGB", (len(a.panel) * w + (len(a.panel) - 1) * gap, top + h + cap), (14, 14, 16))
    d = ImageDraw.Draw(sheet)
    f1 = ImageFont.truetype(FONT, 34)
    f2 = ImageFont.truetype(FONT, 28)
    if a.title:
        d.text((20, 16), a.title, font=f1, fill=(235, 235, 235))
    for i, (label, path) in enumerate(a.panel):
        im = Image.open(path).convert("RGB").resize((w, h), Image.LANCZOS)
        x = i * (w + gap)
        sheet.paste(im, (x, top))
        name, _, sub = label.partition("|")
        d.text((x + 16, top + h + 14), name, font=f1, fill=(240, 240, 240))
        if sub:
            d.text((x + 16, top + h + 62), sub, font=f2, fill=(190, 190, 190))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)
    pw = a.phone
    small = sheet.resize((pw, int(sheet.height * pw / sheet.width)), Image.LANCZOS)
    small.save(out.with_name(out.stem + "_phone.jpg"), quality=85)
    print(out, sheet.size, small.size)


if __name__ == "__main__":
    main()
