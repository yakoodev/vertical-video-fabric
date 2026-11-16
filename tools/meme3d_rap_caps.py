"""Подписи рэп-клипа «Уроню БД» — план кинетической типографики и наложение на видео (рисует tools/meme3d_rap_caps.mjs).

Стиль «дорогого клипа» (решение владельца 16.11): узкий гротеск Oswald капсом — строка песни, ключевое слово фразы —
курсив Cambria строчными цветом сцены; слова выезжают из-под маски в момент пения, толчок на ударах, линия под
фразой. Ирония — в подаче: сухие сноски-«лог» моноширинным шрифтом под фразой (``NOTES``). Фразы 2–4 слова, вверху
кадра (не на лицах). Мат — только «разъ**у» звёздочками, звук без цензуры.
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
    {"file": str(ROOT / "vendor" / "fonts" / "Oswald-Bold.ttf"), "family": "Oswald"},
    {"file": r"C:\Windows\Fonts\cambriai.ttf", "family": "Cambria"},
    {"file": r"C:\Windows\Fonts\consola.ttf", "family": "Consolas"},
]
MASK = {"разъебу": "разъ**у"}
ACCENT = re.compile(r"^(прод|бд|бюджет|генерацию|стоп|подписку|фури-аниме|котов|мозг|бэкап|бэкапа|час|эссе|лимиты|мастер|доступ|"
                    r"корню|вопрос|правы|платить|мувик|файлов|лохов|придурок|готов|откос)$", re.I)
# Цвет сцены для курсива и линии: крыша-закат, серверная, сцена, класс/улица, студия (ровно), финал.
PALETTE = [[0, 6.85, "#ffb36b"], [6.85, 32.5, "#6ee7ff"], [32.5, 44.7, "#ff5fb0"], [44.7, 66.0, "#9dffc0"], [66.0, 76.09, "#ffffff"],
           [76.09, 200, "#ffcf5a"]]
# Сноски-«лог» (сухо, мелко): время начала, текст. Список согласуется с владельцем.
NOTES = [
    (5.6, "вопроса не было"), (7.3, "доступ: root ✓"), (10.9, "удалено: всё"), (15.6, "тесты: 0/0 ✓"), (18.7, "файлов: 0. так чище"),
    (21.3, "03:00 — лучшее время для трат"), (23.3, "списано: 5 000 ₽"), (26.5, "прочитано. проигнорировано"), (31.0, "прод: был"),
    (34.2, "бэкап? какой бэкап"), (36.6, "доставка оплачена вами"), (42.7, "кнопка декоративная"), (46.6, "осталось 59:59"),
    (49.6, "20 страниц вместо «да»"), (53.1, "ТЗ выполнено (по-своему)"), (58.0, "лимит: 0. юзер: снова тут"), (65.6, "диагноз поставлен"),
    (70.6, "согласие: автоматическое"), (75.0, "бэкап: в прошлом"), (88.0, "юзер всё ещё платит"), (100.0, "отмена временно недоступна"),
]


def chunks_from_lyrics(lines: list[dict], skip_user: bool = True) -> list[dict]:
    """Строки песни → фразы по 2–4 слова (рвём по знакам и поровну), в каждой — одно слово-акцент (курсив)."""
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
            acc = [w for w in gr if ACCENT.match(w["text"].replace("**", "ебу"))]
            if acc and len(gr) > 1:
                acc[-1]["accent"] = True
            out.append({"t0": gr[0]["t0"] - 0.05, "t1": gr[-1]["t1"] + 0.3, "words": gr})
    # Фраза держится до следующей (без мигания), не дольше 1.6 с после последнего слова.
    for a, b in zip(out, out[1:]):
        a["t1"] = round(min(max(a["t1"], b["t0"] - 0.02), b["t0"] - 0.02, a["words"][-1]["t1"] + 1.6), 3)
    return out


def plan(t0: float, t1: float, width: int = 1080, height: int = 1920, fps: int = 30) -> dict:
    data = json.loads((DATA / "lyrics.json").read_text("utf-8"))
    beats = json.loads((DATA / "beats.json").read_text("utf-8"))
    title = {"t0": 0.3, "t1": 4.25, "scale": 1.25,
             "words": [{"text": "УРОНЮ", "t0": 0.35, "t1": 0.4}, {"text": "БД", "t0": 0.75, "t1": 0.8, "accent": True}]}
    ch = [c for c in [title, *chunks_from_lyrics(data["lines"])] if c["t1"] > t0 and c["t0"] < t1]
    notes = [{"t0": a, "t1": a + 2.6, "text": tx} for a, tx in NOTES if a + 2.6 > t0 and a < t1]
    return {"width": width, "height": height, "fps": fps, "t0": t0, "dur": round(t1 - t0, 3), "y": 0.19, "noteY": 0.275, "fonts": FONTS,
            "palette": PALETTE, "kicks": [k for k in beats["kicks"] if t0 - 1 < k < t1 + 1], "chunks": ch, "notes": notes}


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
