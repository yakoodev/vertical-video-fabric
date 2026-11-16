"""Подписи рэп-клипа «Уроню БД» — план «иронично-крутой» типографики и наложение на видео (рисует tools/meme3d_rap_caps.mjs).

Система, а не случайность: у каждой фразы один из четырёх видов по смыслу строки —
- ``gold`` (золото с засечками) — деньги: бюджет, тыщи, платить, подписка;
- ``glitch`` (RGB-сдвиг, срезы) — разрушение: rm -rf, форс-пуш, коммит, «уроню БД», бэкап, лимиты;
- ``wordart`` (радуга с объёмом, 90-е) — панчлайны припева и «минус мозг»;
- ``chrome`` (хром Y2K с искрами) — всё остальное.
Фразы — 2–4 слова (рвём по знакам), вверху кадра в безопасной зоне (не на лицах). Значки к словам: БД → цилиндры +
вспышка, деньги → монеты, фури-аниме/коты → котик-наклейка, прод → огонь. Наклейки-эдлибы с нарочными ошибками и штамп
PARENTAL ADVISORY на входе в припевы. Мат — только «разъ**у» звёздочками, звук без цензуры.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(r"D:\vvf-offload\memes\claude_rap\work")
FONTS = [
    {"file": str(ROOT / "vendor" / "fonts" / "Montserrat-Black.ttf"), "family": "Montserrat Black"},
    {"file": r"C:\Windows\Fonts\georgiaz.ttf", "family": "Georgia"},
    {"file": r"C:\Windows\Fonts\impact.ttf", "family": "Impact"},
    {"file": r"C:\Windows\Fonts\comicbd.ttf", "family": "Comic Sans MS"},
]
MASK = {"разъебу": "разъ**у"}

GOLD = re.compile(r"бюджет|тыщ|платить|подписк|кредит|генерац", re.I)
GLITCH = re.compile(r"rm|-rf|форс|коммит|уроню|бэкап|удалил|лимит|откос|файлов", re.I)
WORDART = re.compile(r"разъ|похуй|придурок|мозг|фури|отличный|правы|котов|мувик", re.I)
ICONS = [(re.compile(r"^бд$", re.I), "db"), (re.compile(r"бюджет|тыщ|платить", re.I), "coin"), (re.compile(r"аниме|котов", re.I), "cat"),
         (re.compile(r"^прод$", re.I), "fire")]

# Эдлибы-наклейки (нарочно с ошибками), углы кадра ниже середины — не на лицах.
STICKERS = [
    (11.9, 13.4, "ИЗИ КАТКА", 0.2, 0.66, -8, "#7b5cff"), (16.0, 17.0, "ЛАХИ ))", 0.78, 0.64, 7, "#ff3fa4"),
    (19.6, 20.4, "УПС", 0.24, 0.66, -10, "#18c8a8"), (22.4, 23.3, "ЖОСКА", 0.76, 0.66, 8, "#ff9f1c"),
    (25.4, 26.2, "А ЧО", 0.22, 0.66, -6, "#ff3fa4"), (30.6, 32.3, "ПРОД ВСЁ((", 0.74, 0.64, 9, "#7b5cff"),
    (37.0, 38.4, "УИИИ", 0.24, 0.66, -9, "#ff3fa4"), (40.6, 41.6, "ЛАДНА", 0.76, 0.66, 8, "#18c8a8"),
    (47.2, 48.2, "ДУМОЮ…", 0.24, 0.66, -7, "#7b5cff"), (53.4, 54.4, "МЯФ", 0.76, 0.64, 8, "#ff3fa4"),
    (56.6, 57.4, "ГАТОФ", 0.22, 0.66, -8, "#ff9f1c"), (60.8, 61.9, "ПЛАК-ПЛАК", 0.74, 0.66, 7, "#18c8a8"),
    (75.0, 76.0, "ОЙ", 0.24, 0.62, -9, "#ff3fa4"), (79.6, 80.6, "ЖОСКА x2", 0.76, 0.66, 8, "#ff9f1c"),
    (88.0, 89.0, "ИЗИ", 0.22, 0.66, -8, "#7b5cff"), (100.6, 102.0, "АЙ ВСЁ", 0.76, 0.64, 8, "#ff3fa4"),
]
STAMPS = [(32.55, 35.6), (76.12, 79.0)]


def _look(text: str) -> str:
    if GOLD.search(text):
        return "gold"
    if GLITCH.search(text):
        return "glitch"
    if WORDART.search(text):
        return "wordart"
    return "chrome"


def chunks_from_lyrics(lines: list[dict], skip_user: bool = True) -> list[dict]:
    """Строки песни → фразы по 2–4 слова (рвём по запятой/тире и по 3 слова), вид по смыслу, значки к словам."""
    out = []
    for ln in lines:
        if skip_user and any(x in ln["text"] for x in ("зачем базу", "Восстанови")):
            continue  # реплики юзера на мосту — пузырём чата в кадре
        # Куски строки по знакам препинания; длинный кусок — поровну по 2–4 слова.
        segs: list[list[dict]] = [[]]
        for w in ln["words"]:
            raw = str(w["w"])
            core = raw.strip("—–-,.:;!?«»\"' ")
            if not core:
                if segs[-1]:
                    segs.append([])
                continue
            core = MASK.get(core.lower(), core)
            segs[-1].append({"text": core.upper(), "t0": round(w["t0"], 3), "t1": round(w["t1"], 3)})
            if re.search(r"[,—:;!?.]$", raw):
                segs.append([])
        groups: list[list[dict]] = []
        for sg in [s for s in segs if s]:
            n = len(sg)
            k = 1 if n <= 4 else -(-n // 3)
            size = -(-n // k)
            parts = [sg[i:i + size] for i in range(0, n, size)]
            if len(parts) > 1 and len(parts[-1]) == 1:
                last = parts.pop()
                parts[-1] = parts[-1] + last
            groups += parts
        # Одинокое слово в начале/середине строки прилипает к соседу.
        merged: list[list[dict]] = []
        for gr in groups:
            if merged and (len(gr) == 1 or len(merged[-1]) == 1) and len(merged[-1]) + len(gr) <= 4:
                merged[-1] = merged[-1] + gr
            else:
                merged.append(gr)
        groups = merged
        for gr in groups:
            text = " ".join(w["text"] for w in gr)
            for w in gr:
                for rx, kind in ICONS:
                    if rx.search(w["text"]):
                        w["icon"] = kind
            out.append({"t0": gr[0]["t0"] - 0.05, "t1": gr[-1]["t1"] + 0.25, "look": _look(text), "words": gr})
    # Фраза держится до следующей (без мигания), не дольше 1.6 с после последнего слова.
    for a, b in zip(out, out[1:]):
        a["t1"] = round(min(max(a["t1"], b["t0"] - 0.02), b["t0"] - 0.02, a["words"][-1]["t1"] + 1.6), 3)
    return out


def plan(t0: float, t1: float, width: int = 1080, height: int = 1920, fps: int = 30) -> dict:
    data = json.loads((DATA / "lyrics.json").read_text("utf-8"))
    beats = json.loads((DATA / "beats.json").read_text("utf-8"))
    title = {"t0": 0.25, "t1": 4.25, "look": "wordart", "scale": 1.35,
             "words": [{"text": "УРОНЮ", "t0": 0.25, "t1": 0.26}, {"text": "БД", "t0": 0.26, "t1": 0.27, "icon": "db"}]}
    ch = [c for c in [title, *chunks_from_lyrics(data["lines"])] if c["t1"] > t0 and c["t0"] < t1]
    st = [{"t0": a, "t1": b, "text": tx, "x": x, "y": y, "rot": r, "bg": bg} for a, b, tx, x, y, r, bg in STICKERS if b > t0 and a < t1]
    sp = [{"t0": a, "t1": b, "x": 0.8, "y": 0.86} for a, b in STAMPS if b > t0 and a < t1]
    return {"width": width, "height": height, "fps": fps, "t0": t0, "dur": round(t1 - t0, 3), "y": 0.2, "fonts": FONTS,
            "kicks": [k for k in beats["kicks"] if t0 - 1 < k < t1 + 1], "chunks": ch, "stickers": st, "stamps": sp}


def overlay(video: Path, out: Path, t0: float, t1: float, work: Path) -> Path:
    """Рисует подписи (node) и накладывает на ``video`` (участок песни [t0, t1]); звук — из ``video``."""
    work.mkdir(parents=True, exist_ok=True)
    pf = work / "caps_plan.json"
    p = plan(t0, t1)
    pf.write_text(json.dumps(p, ensure_ascii=False), "utf-8")
    node = subprocess.Popen(["node", str(ROOT / "tools" / "meme3d_rap_caps.mjs"), str(pf)], stdout=subprocess.PIPE, cwd=ROOT)
    ff = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{p['width']}x{p['height']}",
                         "-r", str(p["fps"]), "-i", "-", "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]", "-map", "[v]", "-map", "0:a?",
                         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                        stdin=node.stdout, capture_output=False)
    node.wait()
    if ff.returncode != 0 or node.returncode != 0:
        raise RuntimeError(f"подписи: ffmpeg {ff.returncode}, node {node.returncode}")
    return out


if __name__ == "__main__":
    a, b = float(sys.argv[2]), float(sys.argv[3])
    print(overlay(Path(sys.argv[1]), Path(sys.argv[4]), a, b, Path(sys.argv[4]).parent / "caps_work"))
