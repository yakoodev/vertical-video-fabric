"""Концовка истории «Витубики»: анимированная карточка бренда ~2.8 с в конце каждого ролика.

Тёмный фон с мягкими пятнами цветов бренда и частицами, значок магазина (``assets/brand/vtubika/logo.svg``, с сайта
владельца) выпрыгивает с отскоком, ниже — надпись «vtubika» (Inter SemiBold, оранжевая точка над «k», как в
og-картинке сайта), «vtubika.store» и слоган сайта «Купить VTuber-модель / с оплатой в рублях». Никаких других
обещаний. Звук — мягкий «стинг» (звуки паков или свои записи бренда, CC0), музыка истории затихает под карточкой.

* ``settings(plan)`` — настройки карточки (``app.vtubika.END_CARD`` + поле плана ``end_card``; ``false`` — без неё).
* ``card_svg(cfg)`` — SVG с CSS-анимацией (чистая функция, тестируется без браузера).
* ``render(cfg, work)`` — SVG → кадры headless Chromium (``node/svg-to-webm.mjs``, покадрово, детерминированно) →
  webm в кэше ``work`` (по хешу SVG).
* ``append(video, card, out)`` — ролик + карточка одним mp4 (BT.709, как кадры сцены).
* ``sting(cfg)`` / ``card_sfx(cfg, t0)`` — звук карточки (``END_CARD["sting"]`` из ``END_CARD_STINGS``) на общей
  шкале ролика. Звук стинга — ``ref`` (звук пака, ``sfx:…``) или ``path`` (свой файл бренда от корня репозитория,
  ``assets/brand/vtubika/sfx/…``, см. ``sound_path``); ``cue`` — время ``at`` от события анимации (``cue()``:
  «logo» — значок выпрыгнул, «dot» — точка села на «k»), а не от начала карточки.
"""

from __future__ import annotations

import hashlib
import random
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "brand" / "vtubika" / "logo.svg"
FONTS = ROOT / "vendor" / "fonts"
W, H, FPS = 1080, 1920, 30
FONT_FAMILY = "Inter"
FONT_FILE = "Inter-SemiBold.ttf"
FONT_WEIGHT = 600

# Палитра бренда (комментарий logo.svg): оранжевый, персиковый, мятный, лавандовый, графит.
ORANGE, PEACH, MINT, LAVENDER, SLATE = "#FF8A4C", "#FFD7BF", "#8EDFD1", "#BDA7F7", "#2B2F36"
INK = "#F3F4F8"

# Анимации, под которые ставится звук (с от начала карточки; CSS собирается из них же).
LOGO_POP = {"delay": 0.12, "dur": 0.7, "peak": 0.55}  # значок: пик отскока (scale 1.12) на 55 % анимации
DOT_DROP = {"delay": 0.95, "dur": 0.5, "land": 0.45}  # точка над «k»: касание (сплющивание) на 45 %


def cue(name: str) -> float:
    """Время события карточки по кадрам (ближайший кадр сетки ``FPS``): «logo» — значок выпрыгнул (кадр пика
    отскока, 0.5 с), «dot» — точка приземлилась на «k» (кадр касания, ≈1.167 с)."""
    if name == "logo":
        t = LOGO_POP["delay"] + LOGO_POP["dur"] * LOGO_POP["peak"]
    elif name == "dot":
        t = DOT_DROP["delay"] + DOT_DROP["dur"] * DOT_DROP["land"]
    else:
        raise ValueError(f"нет события карточки {name!r}: logo, dot")
    return round(round(t * FPS) / FPS, 4)


def _css_s(v: float) -> str:
    """Секунды для CSS как в исходной разметке: 0.7 → «.7» (хеш карточки и кэш webm не меняются)."""
    text = f"{v:g}"
    return text[1:] if text.startswith("0.") else text


def settings(plan: dict | None) -> dict | None:
    """Настройки карточки: общие (``END_CARD``) + ``plan["end_card"]`` (dict — поверх, ``false`` — выключена)."""
    from app import vtubika as U

    raw = (plan or {}).get("end_card", True)
    if raw is False or raw is None:
        return None
    cfg = {**U.END_CARD, **(raw if isinstance(raw, dict) else {})}
    if cfg.get("enabled") is False:
        return None
    return cfg


# --------------------------------------------------------------------------- геометрия надписи


def _font(size: float):
    from PIL import ImageFont

    return ImageFont.truetype(str(FONTS / FONT_FILE), size=max(1, int(round(size))))


def wordmark_layout(size: float, tracking: float, cx: float, baseline: float) -> dict:
    """Где стоит «vtubika» и оранжевая точка: метрики того же файла шрифта, что у браузера (без кернинга с обеих
    сторон — в SVG ``font-kerning: none``). Точка — как в og-картинке сайта: над «k», правее её ствола, чуть выше
    линии выносных, диаметр ≈ 0.28 высоты «k» (замер по og.png)."""
    font = _font(size)
    word = "vtubika"
    ls = tracking * size
    width = font.getlength(word) + ls * (len(word) - 1)
    x0 = cx - width / 2
    k_origin = x0 + font.getlength("vtubi") + ls * 5
    kx0, ky0, kx1, _ky1 = font.getbbox("k")
    ascent = font.getmetrics()[0]
    k_height = ascent - ky0  # высота «k» над базовой линией
    return {
        "x": round(x0, 2), "width": round(width, 2), "size": size, "letter_spacing": round(ls, 2),
        "baseline": baseline,
        "dot": {"cx": round(k_origin + kx0 + 0.62 * (kx1 - kx0), 2), "cy": round(baseline - 1.07 * k_height, 2),
                "r": round(0.14 * k_height, 2)},
    }


def _logo_inner() -> tuple[str, str]:
    """Содержимое logo.svg без корня и комментариев → (viewBox, разметка)."""
    svg = LOGO.read_text("utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
    view = re.search(r'viewBox="([^"]+)"', svg).group(1)
    inner = re.sub(r"^.*?<svg\b[^>]*>", "", svg, count=1, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner.strip())
    return view, re.sub(r"\n\s*\n", "\n", inner)


def _particles(seed: int, n: int, dur: float) -> list[str]:
    rnd = random.Random(seed)
    colors = [PEACH, MINT, LAVENDER, ORANGE, PEACH, MINT]
    out = []
    for i in range(n):
        x, y = rnd.uniform(40, W - 40), rnd.uniform(80, H - 80)
        r = rnd.choice([3, 4, 5, 6, 8])
        rise = rnd.uniform(60, 160)
        op = rnd.uniform(0.25, 0.6)
        tw = rnd.uniform(0.9, 1.8)
        out.append(
            f'<g transform="translate({x:.0f} {y:.0f})"><circle r="{r}" fill="{colors[i % len(colors)]}" '
            f'style="--rise:-{rise:.0f}px;--op:{op:.2f};animation:rise {dur:.2f}s linear both,'
            f'twinkle {tw:.2f}s ease-in-out -{rnd.uniform(0, tw):.2f}s infinite alternate"/></g>')
    for i in range(6):  # искры как на ценнике значка
        x, y = rnd.uniform(90, W - 90), rnd.uniform(240, H - 300)
        s = rnd.uniform(0.9, 1.6)
        out.append(
            f'<g transform="translate({x:.0f} {y:.0f}) scale({s:.2f})"><path d="M0-14c1.5 8.4 5.6 12.5 14 14-8.4 1.5-12.5 '
            f'5.6-14 14-1.5-8.4-5.6-12.5-14-14 8.4-1.5 12.5-5.6 14-14Z" fill="{[PEACH, MINT, LAVENDER][i % 3]}" '
            f'class="spark" style="animation-delay:{0.3 + i * 0.27:.2f}s"/></g>')
    return out


def card_svg(cfg: dict) -> str:
    """SVG 1080×1920 с CSS-анимацией на ``cfg["duration"]`` секунд."""
    dur = float(cfg["duration"])
    view, logo = _logo_inner()
    lsize = float(cfg.get("logo_size", 300))
    lcy = float(cfg.get("logo_y", 600))
    wm = wordmark_layout(float(cfg.get("word_size", 196)), float(cfg.get("tracking", -0.02)), W / 2,
                         float(cfg.get("word_baseline", 1010)))
    dot = wm["dot"]
    site, slogan = str(cfg["site"]), [str(s) for s in cfg["slogan"]]
    text_style = f"font-family:{FONT_FAMILY};font-weight:{FONT_WEIGHT};font-kerning:none"
    slogan_svg = "".join(
        f'<text x="{W / 2}" y="{1262 + i * 84}" text-anchor="middle" data-fit="960" class="in" '
        f'style="{text_style};font-size:66px;fill:{INK if i == 0 else ORANGE};animation-delay:{1.25 + i * 0.12:.2f}s">'
        f"{_esc(line)}</text>" for i, line in enumerate(slogan))
    css = f"""
.in{{animation:fadeup .45s cubic-bezier(.2,.8,.2,1) both;transform-box:fill-box;transform-origin:center}}
.logo{{animation:pop {_css_s(LOGO_POP["dur"])}s linear {_css_s(LOGO_POP["delay"])}s both;transform-box:fill-box;transform-origin:center}}
.halo{{animation:halo 1.1s ease-out .3s both;transform-box:fill-box;transform-origin:center}}
.dot{{animation:drop {_css_s(DOT_DROP["dur"])}s linear {_css_s(DOT_DROP["delay"])}s both;transform-box:fill-box;transform-origin:50% 100%}}
.spark{{animation:spark .9s ease-in-out both;transform-box:fill-box;transform-origin:center}}
.blob1{{animation:drift1 {dur:.2f}s ease-in-out both}}
.blob2{{animation:drift2 {dur:.2f}s ease-in-out both}}
.veil{{animation:veil .28s linear both}}
@keyframes rise{{from{{transform:translateY(0);opacity:0}}15%{{opacity:var(--op)}}to{{transform:translateY(var(--rise));opacity:var(--op)}}}}
@keyframes twinkle{{from{{fill-opacity:.35}}to{{fill-opacity:1}}}}
@keyframes fadeup{{from{{opacity:0;transform:translateY(36px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes pop{{0%{{opacity:0;transform:scale(.2) rotate(-12deg)}}30%{{opacity:1}}{LOGO_POP["peak"] * 100:g}%{{transform:scale(1.12) rotate(3deg)}}
75%{{transform:scale(.95) rotate(-1deg)}}90%{{transform:scale(1.02) rotate(0)}}100%{{opacity:1;transform:scale(1) rotate(0)}}}}
@keyframes halo{{from{{opacity:0;transform:scale(.6)}}40%{{opacity:.9}}to{{opacity:.55;transform:scale(1.05)}}}}
@keyframes drop{{0%{{opacity:0;transform:translateY(-140px) scale(.6)}}{DOT_DROP["land"] * 100:g}%{{opacity:1;transform:translateY(0) scale(1.15,.82)}}
65%{{transform:translateY(-22px) scale(.95,1.06)}}85%{{transform:translateY(0) scale(1.04,.96)}}100%{{opacity:1;transform:translateY(0) scale(1)}}}}
@keyframes spark{{0%{{opacity:0;transform:scale(.2) rotate(0)}}50%{{opacity:.85;transform:scale(1) rotate(45deg)}}100%{{opacity:0;transform:scale(.3) rotate(90deg)}}}}
@keyframes drift1{{from{{transform:translate(0,0)}}to{{transform:translate(-60px,40px)}}}}
@keyframes drift2{{from{{transform:translate(0,0)}}to{{transform:translate(70px,-50px)}}}}
@keyframes veil{{from{{opacity:1}}to{{opacity:0}}}}
"""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f"<style>{css}</style>",
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#171a28"/>'
        f'<stop offset=".55" stop-color="#10121b"/><stop offset="1" stop-color="#0b0c12"/></linearGradient>',
        f'<radialGradient id="gO"><stop offset="0" stop-color="{ORANGE}" stop-opacity=".30"/>'
        f'<stop offset="1" stop-color="{ORANGE}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="gL"><stop offset="0" stop-color="{LAVENDER}" stop-opacity=".22"/>'
        f'<stop offset="1" stop-color="{LAVENDER}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="gM"><stop offset="0" stop-color="{MINT}" stop-opacity=".14"/>'
        f'<stop offset="1" stop-color="{MINT}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="halo"><stop offset="0" stop-color="{ORANGE}" stop-opacity=".45"/>'
        f'<stop offset=".6" stop-color="{ORANGE}" stop-opacity=".10"/><stop offset="1" stop-color="{ORANGE}" stop-opacity="0"/>'
        f"</radialGradient>",
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
        f'<g class="blob1"><circle cx="880" cy="420" r="620" fill="url(#gO)"/></g>',
        f'<g class="blob2"><circle cx="160" cy="1500" r="700" fill="url(#gL)"/></g>',
        f'<circle cx="900" cy="1300" r="520" fill="url(#gM)"/>',
        *_particles(int(cfg.get("seed", 7)), int(cfg.get("particles", 30)), dur),
        f'<circle cx="{W / 2}" cy="{lcy}" r="{lsize * 0.95:.0f}" fill="url(#halo)" class="halo"/>',
        f'<svg x="{W / 2 - lsize / 2}" y="{lcy - lsize / 2}" width="{lsize}" height="{lsize}" viewBox="{view}" '
        f'overflow="visible"><g class="logo">{logo}</g></svg>',
        f'<g class="in" style="animation-delay:.55s"><text x="{wm["x"]}" y="{wm["baseline"]}" '
        f'style="{text_style};font-size:{wm["size"]}px;letter-spacing:{wm["letter_spacing"]}px;fill:{INK}">vtubika</text></g>',
        f'<circle class="dot" cx="{dot["cx"]}" cy="{dot["cy"]}" r="{dot["r"]}" fill="{ORANGE}"/>',
        f'<text x="{W / 2}" y="1112" text-anchor="middle" class="in" style="{text_style};font-size:60px;'
        f'fill:{PEACH};letter-spacing:1px;animation-delay:1.05s">{_esc(site)}</text>',
        f'<rect x="{W / 2 - 60}" y="1160" width="120" height="6" rx="3" fill="{ORANGE}" class="in" '
        f'style="animation-delay:1.15s;opacity:.85"/>',
        slogan_svg,
        # Без наплыва (crossfade 0) карточка проявляется из чёрного; с наплывом истории — сразу своим фоном.
        "" if float(cfg.get("crossfade") or 0) > 0 else f'<rect width="{W}" height="{H}" fill="#000" class="veil"/>',
        "</svg>",
    ]
    return "\n".join(parts)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- рендер и склейка


def render(cfg: dict, work: Path) -> Path:
    """Карточка → webm (кэш по хешу SVG). Один браузер, кадр за кадром на времени t = i/30."""
    svg = card_svg(cfg)
    key = hashlib.sha1((svg + str(cfg["duration"])).encode("utf-8")).hexdigest()[:12]
    out = work / f"endcard_{key}.webm"
    if out.is_file():
        return out
    src = work / f"endcard_{key}.svg"
    src.write_text(svg, "utf-8")
    proc = subprocess.run(["node", str(ROOT / "node" / "svg-to-webm.mjs"), "--in", str(src), "--out", str(out),
                           "--fps", str(FPS), "--duration", f"{float(cfg['duration']):.3f}", "--crf", "12",
                           "--fonts-dir", str(FONTS)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"концовка: {proc.stderr.strip()[-600:]}")
    return out


def append_args(video: Path, card: Path, out: Path, total: float | None = None, crossfade: float = 0.0) -> list[str]:
    """ffmpeg: ролик + карточка (кадры PNG → yuv BT.601 в webm → BT.709, как у сцены) → один mp4 без звука.
    ``crossfade`` > 0 (нужна длина ролика ``total``) — последний кадр истории растворяется в карточке (xfade):
    карточка начинается на ``crossfade`` раньше конца ролика, чёрного кадра между ними нет."""
    card_chain = ("[1:v]scale=1080:1920:in_color_matrix=bt601:out_color_matrix=bt709:out_range=tv,fps=30,setsar=1,"
                  "format=yuv420p,settb=AVTB[b]")
    if crossfade > 0 and total:
        graph = (f"[0:v]setsar=1,format=yuv420p,fps=30,settb=AVTB[a];{card_chain};"
                 f"[a][b]xfade=transition=fade:duration={crossfade:.3f}:offset={max(0.0, total - crossfade):.3f}[v]")
    else:
        graph = f"[0:v]setsar=1,format=yuv420p,settb=AVTB[a];{card_chain};[a][b]concat=n=2:v=1:a=0[v]"
    return ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-c:v", "libvpx-vp9", "-i", str(card),
            "-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-r", str(FPS), "-movflags", "+faststart", str(out)]


def append(video: Path, card: Path, out: Path, total: float | None = None, crossfade: float = 0.0) -> None:
    proc = subprocess.run(append_args(video, card, out, total, crossfade), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"склейка с концовкой: {proc.stderr.strip()[-600:]}")


def sting(cfg: dict) -> list[dict[str, Any]]:
    """Звуки карточки: явный ``sfx`` плана или стинг ``cfg["sting"]`` из ``app.vtubika.END_CARD_STINGS``."""
    from app import vtubika as U

    if cfg.get("sfx"):
        return list(cfg["sfx"])
    name = cfg.get("sting") or "kenney_classic"
    if name not in U.END_CARD_STINGS:
        raise ValueError(f"нет стинга концовки {name!r}: {', '.join(U.END_CARD_STINGS)}")
    return U.END_CARD_STINGS[name]


def card_sfx(cfg: dict, t0: float) -> list[dict[str, Any]]:
    """Звуки карточки ({ref | path, t, volume}) от начала карточки ``t0`` (``at`` < 0 — ещё под концом истории;
    с ``cue`` — от события анимации)."""
    out = []
    for s in sting(cfg):
        if not (s.get("ref") or s.get("path")):
            raise ValueError(f"у звука концовки нет ни ref, ни path: {s!r}")
        at = float(s.get("at", 0.0)) + (cue(s["cue"]) if s.get("cue") else 0.0)
        src = {"path": str(s["path"])} if s.get("path") else {"ref": s["ref"]}
        out.append({**src, "t": round(max(0.0, t0 + at), 3), "volume": float(s["volume"])})
    return out


def sound_path(s: dict) -> Path | None:
    """Файл звука: ``path`` — свой файл от корня репозитория (только внутри него, например
    ``assets/brand/vtubika/sfx/kalimba_chord.ogg``), ``ref`` — звук пака (``app.motion_assets.resolve_pack_ref``).
    None — файла нет или путь ведёт за пределы репозитория."""
    if s.get("path"):
        p = Path(str(s["path"]))
        p = (p if p.is_absolute() else ROOT / p).resolve()
        if not p.is_relative_to(ROOT.resolve()) or not p.is_file():
            return None
        return p
    if s.get("ref"):
        from app.motion_assets import resolve_pack_ref

        return resolve_pack_ref(str(s["ref"]))
    return None
