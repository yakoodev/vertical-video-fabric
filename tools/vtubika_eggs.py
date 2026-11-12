"""Пасхалки для монитора на столе VRM-историй «Витубики» (node/vrm/src/monitor.ts, план ``vrm.monitor.eggs``) — только
свои персонажи и ассеты: наклейка с совой-объяснялкой (лист поз маскота сервиса) и фото героини прошлой серии.

    python tools/vtubika_eggs.py [--packs D:/vvf-offload/vtubika/vrm/vendor] [--owl-sheet D:/vvf-data/runtime/explainer/owl_sheet.jpg]
                                 [--photo-video D:/vvf-offload/vtubika/story6_vrm/story6_vrm.mp4 --photo-at 8.0]

Кладёт ``eggs/owl_sticker.png`` (вырезанная сова с белой каймой) и ``eggs/sonya_photo.jpg`` (кадр прошлой серии) в
пак VRM — render.mjs отдаёт их как ``/packs/eggs/…``.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

# Радостная сова (нижний правый угол листа поз 1376×768) и кадр Сони крупно из серии 6.
OWL_BOX = (1030, 440, 1340, 735)
PHOTO_CROP = "crop=900:1080:100:200"


def owl_sticker(sheet: Path, out: Path) -> None:
    from PIL import Image, ImageDraw

    owl = Image.open(sheet).convert("RGBA").crop(OWL_BOX).resize((620, 590), Image.LANCZOS)
    w, h, b = 680, 650, 30
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    edge = Image.new("L", (w, h), 0)
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, w - 1, h - 1), 90, fill=255)
    img.paste(Image.new("RGBA", (w, h), (250, 248, 255, 255)), (0, 0), edge)
    inner = Image.new("L", owl.size, 0)
    ImageDraw.Draw(inner).rounded_rectangle((0, 0, owl.size[0] - 1, owl.size[1] - 1), 66, fill=255)
    img.paste(owl, (b, b), inner)
    img.save(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="пасхалки монитора VRM-историй")
    ap.add_argument("--packs", default=r"D:\vvf-offload\vtubika\vrm\vendor")
    ap.add_argument("--owl-sheet", default=r"D:\vvf-data\runtime\explainer\owl_sheet.jpg")
    ap.add_argument("--photo-video", default=r"D:\vvf-offload\vtubika\story6_vrm\story6_vrm.mp4")
    ap.add_argument("--photo-at", type=float, default=8.0)
    args = ap.parse_args(argv)
    out = Path(args.packs) / "eggs"
    out.mkdir(parents=True, exist_ok=True)
    owl_sticker(Path(args.owl_sheet), out / "owl_sticker.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{args.photo_at:.3f}", "-i", args.photo_video, "-frames:v", "1",
                    "-vf", PHOTO_CROP, "-q:v", "2", str(out / "sonya_photo.jpg")], check=True)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
