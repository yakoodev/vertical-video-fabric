"""Мем-серия 11 «Дизайн отдел» — субтитры в стиле оригинала, звук оригинала, выпуск, проверки (сцена и хореография —
meme3d_ep11_anim.py, разметка — meme3d_ep11.py).

Субтитры как в оригинале: белые жирные капсы по слову в центре кадра (на 0.70 высоты), мягкая тень, сверху маленькая
шапка «дизайн отдел»; мат под «*». Короткие слова (на, я, у, ты, а…) идут вместе со следующим, если тот же голос и
без паузы — как «НА ОДИН», «Я Е*У?» у оригинала. Рисуются PIL-картинками, накладываются ffmpeg (оверлей по времени).

Звук — дорожка оригинала байт-в-байт (без пересжатия): тренд-звук не режем и не переозвучиваем.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_ep11 as E  # noqa: E402
import meme3d_pilot as P1  # noqa: E402

FPS = 30
DUR = E.DURATION
W, H = 1080, 1920
FONTS = ROOT.parent.parent.parent / "vendor" / "fonts" if ".claude" in str(ROOT) else ROOT / "vendor" / "fonts"
CAP_FONT, CAP_SIZE, CAP_TRACK, CAP_Y = "Montserrat-Black.ttf", 56, 3, 0.705
HEAD_FONT, HEAD_SIZE, HEAD_Y = "Inter-SemiBold.ttf", 66, 0.18
HOLD = 0.35  # подпись висит после слова, если следующая не началась

# --------------------------------------------------------------------------- субтитры


def _short(w: str) -> bool:
    core = re.sub(r"[^\wа-яё*]", "", w.lower())
    return len(core) <= 2 and not core.isdigit()


def caption_chunks() -> list[dict]:
    """Слова → показы: {text, t0, t1, who}; короткое слово без знака на конце — со следующим длинным (тот же голос, пауза
    < 0.1 с, не число): «НА ОДИН», «Я Е*У?», но «ДА?», «НА», «1081» — отдельно, как в оригинале."""
    ws = E.words()
    chunks: list[dict] = []
    i = 0
    while i < len(ws):
        w = ws[i]
        text, t0, t1, who = w["w"], w["t0"], w["t1"], w["who"]
        if _short(text) and not re.search(r"[?!,.…]$", text) and i + 1 < len(ws) and ws[i + 1]["who"] == who \
                and ws[i + 1]["t0"] - t1 < 0.1 and not _short(ws[i + 1]["w"]) and not ws[i + 1]["w"][0].isdigit():
            i += 1
            text, t1 = f"{text} {ws[i]['w']}", ws[i]["t1"]
        chunks.append({"text": text, "t0": t0, "t1": t1, "who": who})
        i += 1
    for k, c in enumerate(chunks):
        nxt = chunks[k + 1]["t0"] if k + 1 < len(chunks) else DUR
        c["show0"] = round(c["t0"], 3)
        c["show1"] = round(min(nxt, c["t1"] + HOLD, DUR), 3)
    return chunks


def _text_png(text: str, font: str, size: int, track: int, y: float, out: Path, upper: bool = True) -> tuple[Path, tuple]:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    f = ImageFont.truetype(str(FONTS / font), size)
    s = text.upper() if upper else text
    widths = [f.getlength(ch) for ch in s]
    total = sum(widths) + track * (len(s) - 1)
    x0 = (W - total) / 2
    cy = int(H * y)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    x = x0
    for ch, cw in zip(s, widths):
        d.text((x, cy), ch, font=f, fill=255, anchor="lm")
        x += cw + track
    # Мягкая тень, как у оригинала, + тонкий тёмный контур: у нас фон часто светлый (клавиатура, стена у двери).
    shadow = mask.filter(ImageFilter.GaussianBlur(size * 0.14)).point(lambda v: min(255, int(v * 0.9)))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh.putalpha(shadow)
    base.alpha_composite(sh, (0, int(size * 0.05)))
    edge = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1)).point(lambda v: int(v * 0.55))
    ed = Image.new("RGBA", (W, H), (20, 12, 14, 0))
    ed.putalpha(edge)
    base.alpha_composite(ed)
    white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    white.putalpha(mask)
    base.alpha_composite(white)
    bbox = base.getbbox() or (0, 0, 2, 2)
    base.crop(bbox).save(out)
    return out, bbox


def captions(video: Path, out: Path, work: Path) -> list[dict]:
    """Шапка + подписи по времени поверх ролика (без звука), crf 14."""
    d = work / "caps"
    d.mkdir(exist_ok=True)
    items = []
    png, box = _text_png(E.HEADER, HEAD_FONT, HEAD_SIZE, 0, HEAD_Y, d / "header.png", upper=False)
    items.append({"png": png, "box": box, "t0": 0.0, "t1": DUR})
    chunks = caption_chunks()
    for k, c in enumerate(chunks):
        png, box = _text_png(c["text"], CAP_FONT, CAP_SIZE, CAP_TRACK, CAP_Y, d / f"cap{k:02d}.png")
        items.append({"png": png, "box": box, "t0": c["show0"], "t1": c["show1"]})
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for it in items:
        args += ["-i", str(it["png"])]
    chain, last = [], "0:v"
    for n, it in enumerate(items, start=1):
        x, y = it["box"][0], it["box"][1]
        tag = f"v{n}"
        chain.append(f"[{last}][{n}:v]overlay={x}:{y}:enable='between(t,{it['t0']:.3f},{it['t1'] - 0.001:.3f})'[{tag}]")
        last = tag
    script = work / "caps_filter.txt"
    script.write_text(";\n".join(chain), "utf-8")
    args += ["-filter_complex_script", str(script), "-map", f"[{last}]", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
             "-pix_fmt", "yuv420p", "-an", str(out)]
    P1._run(args, "субтитры")
    return chunks


# --------------------------------------------------------------------------- выпуск


def encode(src: Path, final: Path, tg: Path, work: Path) -> None:
    """Финал (crf 17) и копия для Telegram (x264 в два прохода под ~18 МБ); звук — дорожка оригинала как есть."""
    common = ["-fps_mode", "cfr", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2", "-g", str(FPS)]
    amap = ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy", "-t", f"{DUR:.3f}", "-movflags", "+faststart"]
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(E.REF), *common, "-preset", "slow", "-crf", "17", "-maxrate", "12M",
             "-bufsize", "24M", *amap, str(final)], "финал")
    a_kbps = int(float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "csv=p=0",
                                       str(E.REF)], capture_output=True, text=True).stdout.strip() or 130000) / 1000)
    kbps = int(18.0 * 8 * 1024 / DUR) - a_kbps
    log = work / "x264_2pass"
    for n in (1, 2):
        dst = ["-an", "-f", "mp4", "NUL"] if n == 1 else [*amap, str(tg)]
        P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(E.REF), *common, "-preset", "slow", "-b:v", f"{kbps}k",
                 "-maxrate", f"{int(kbps * 1.6)}k", "-bufsize", f"{kbps * 2}k", "-pass", str(n), "-passlogfile", str(log), *dst],
                f"копия для Telegram, проход {n}")


def sync_check(final: Path, out: Path) -> None:
    """Два ролика рядом (оригинал слева, наш справа) под звук оригинала, сверху подписи и таймкод."""
    font = str(FONTS / "Montserrat-Bold.ttf").replace("\\", "/").replace(":", "\\:")
    lab = lambda t: f"drawtext=fontfile='{font}':text='{t}':x=(w-tw)/2:y=12:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8"
    tc = f"drawtext=fontfile='{font}':text='%{{pts\\:hms}}':x=(w-tw)/2:y=h-60:fontsize=34:fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=8"
    fc = (f"[0:v]scale=540:960,setsar=1,{lab('оригинал')}[a];[1:v]scale=540:960,setsar=1,{lab('наш')}[b];"
          f"[a][b]hstack=inputs=2,{tc}[v]")
    P1._run(["ffmpeg", "-y", "-v", "error", "-i", str(E.REF), "-i", str(final), "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0",
             "-c:v", "libx264", "-crf", "22", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{DUR:.3f}",
             "-movflags", "+faststart", str(out)], "сверка 2-в-1")


def cuts_of(video: Path, thr: float = 0.25) -> list[float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", f"select='gt(scene,{thr})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]


def cut_sync(final: Path) -> dict:
    """Склейки нашего ролика против склеек оригинала: расхождение каждой (кадры 30 к/с)."""
    ours = cuts_of(final, 0.3)
    rows = []
    for c in E.ORIG_CUTS:
        near = min(ours, key=lambda x: abs(x - c)) if ours else None
        rows.append({"orig": c, "ours": near, "frames": None if near is None else round((near - c) * FPS, 1)})
    return {"orig": E.ORIG_CUTS, "ours": ours, "pairs": rows, "max_abs_frames": max((abs(r["frames"]) for r in rows if r["frames"] is not None), default=None)}


def scene_checks(render: list[str], work: Path) -> dict:
    """Осмотр сцены по кадрам: рывки ручек обоих героев вне склеек (anim_checks.jumps) и где папка Кодекса."""
    import numpy as np

    sys.path.insert(0, str(ROOT / "tools" / "model_check"))
    import anim_checks as AC

    ts = [i / FPS for i in range(int(DUR * FPS))]  # точное время кадра: округление до мс сдвигает границу шага поз
    r = P1._run([*render, "--inspect-list", ",".join(f"{t:.7f}" for t in ts)], "осмотр")
    got = json.loads(r.stdout.strip().splitlines()[-1])["inspect"]
    frames = [round(s["t"] * FPS) for s in got]
    cuts = [round(a * FPS) for a, _b, _n in E.SHOTS]
    res = {}
    for cid in ("claude", "kodex"):
        pos = [[np.asarray(s["characters"][cid]["hand_l"], float), np.asarray(s["characters"][cid]["hand_r"], float)] for s in got]
        res[cid] = AC.jumps(np.asarray(pos), frames, cuts=cuts, labels=["hand_l", "hand_r"])
    (work / "checks.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    return res


def caption_txt(out_dir: Path) -> Path:
    p = out_dir / "caption.txt"
    p.write_text("Кодекс принёс правочки: баннер «на один размер увеличить» 💀 Какие самые странные правки прилетали тебе? Пиши 👇\n\n"
                 "#claude #codex #ии #нейросеть #дизайнер #правки #мем #юмор\n", "utf-8")
    return p


def release(render: list[str], work: Path, out_dir: Path, name: str, skip_render: bool = False) -> int:
    report: dict = {}
    raw = work / "scene.mp4"
    if not skip_render:
        P1.wait_ram(4.0)
        t0 = time.time()
        r = P1._run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    report["captions"] = captions(raw, capped, work)
    final, tg = out_dir / f"{name}.mp4", out_dir / f"{name}_tg.mp4"
    encode(capped, final, tg, work)
    times = [round(a + (b - a) / 2, 2) for a, b, _ in E.SHOTS]
    P1.frame_sheet(final, times, [f"{t:.1f}s {n}" for t, (_, _, n) in zip(times, E.SHOTS)], out_dir / "contact_sheet.jpg", cols=10)
    sync_check(final, out_dir / f"{name}_sync_check.mp4")
    caption_txt(out_dir)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2), "tg_mb": round(tg.stat().st_size / 2**20, 2),
                   "duration": DUR, "cut_sync": cut_sync(final)})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "captions"}, ensure_ascii=False, indent=1))
    return 0
