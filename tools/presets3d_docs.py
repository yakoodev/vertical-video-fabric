"""Каталог 3D-пресетов для людей: docs/3d/presets.md (русский) с миниатюрой на каждый пресет.

    python tools/presets3d_docs.py [--stills D:/vvf-offload/tmp/presets3d/stills]

Миниатюры — из кадров каталога (tools/presets3d_catalog.py), уменьшенные JPEG в docs/3d/img/<категория>/<имя>.jpg.
Текст — из снимка реестра (node/scene3d/presets/catalog.json): имя для плана, русское название и описание, параметры.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import presets3d  # noqa: E402

DOCS = ROOT / "docs" / "3d"

INTRO = """# 3D-пресеты: каталог

Готовые «кирпичики» картинки для 3D-сцен (движок `node/scene3d`): стиль, свет, камера, атмосфера, окружение,
игра персонажа, переходы. Пресеты складываются по именам в блоке `presets` сцены или плана эпизода — движок сам
разворачивает их в лампы, материалы, постобработку, ключи камеры и клипы героя. Всё процедурное, без платной
генерации; вещи интерьера — модели своей библиотеки с открытыми лицензиями (CC0, CC BY), см. `docs/3d/models.md`.

```json
"presets": {
  "look": "anime_soft",
  "light": "golden_hour",
  "room": "cozy_desk",
  "mood": "night_rain",
  "fx": ["film_grain", {"name": "rain", "amount": 0.6}],
  "camera": [{"move": "slow_push", "t0": 0, "t1": 4, "target": "hero"},
             {"move": "crash_zoom", "t0": 4, "t1": 6, "target": "hero", "at": 0.2}],
  "transitions": [{"kind": "whip", "t": 4}],
  "performance": [{"who": "hero", "preset": "shocked", "t": 4.1}],
  "subject": "hero"
}
```

- Любое значение — имя пресета или объект `{"name": ..., ...параметры}` (у камеры — `move`, у перехода — `kind`,
  у игры — `preset`). Параметры и их пределы — в таблицах ниже и в `list_3d_presets`.
- `mood` — готовая связка «свет + эффекты + подтон»; `light` и `fx` рядом с ней уточняют её.
- `room` ставит окружение; персонаж с `"at": "hero"` встаёт (или садится) на место героя. `hide` убирает вещи
  окружения, `colors` меняет палитру: `{"name": "cozy_desk", "colors": {"wall": "#3a4060"}, "hide": ["lamp"]}`.
- `camera` — список движений, каждое — новый план (склейка); `target` — id персонажа (`hero` — лицо,
  `hero:chest` — грудь, `hero:full` — во весь рост), якорь окружения (`desk`, `window`, …) или `[x, y, z]`.
- `transitions` — переходы в момент склейки `t`; для склейки отдельно отрисованных роликов —
  `"half": "out"` в конце первого и `"half": "in"` в начале второго.
- Явные поля сцены сильнее пресетов: свои лампы и вещи добавляются, `environment` перекрывает пресетное.

**Где работает.** Сцены движка `node/scene3d` (`app/scene3d.py`, `render_scene`), планы историй Витубики
(поле плана `presets3d` — стиль, свет, настроение, эффекты, переходы поверх истории). Игра персонажа — и для
героя из примитивов (`style: "voxel"`), и для VRM-героя (`node/vrm`, ключи `timeline`).

**Агент и MCP.** `list_3d_presets` — список с русскими описаниями (`category`, `q` — с параметрами);
`check_3d_plan` — проверка блока `presets` до рендера (опечатки — с подсказкой ближайших имён).

**Вещи — модели библиотеки.** Окружения `cozy_desk`, `gamer_rgb`, `office_openspace`, кухня ставят клавиатуру, мышь,
монитор (экран — на якоре модели), стол, настольную лампу, кружки и растения из библиотеки 3D-моделей
(`docs/3d/models.md`): вариант засеян `seed` окружения и `models.seed` сцены, без библиотеки — прежние процедурные
вещи. Список моделей для поля `objects[].model` — `list_3d_presets category=model`; «до/после» — `models: {"off": true}`.

**Каталог картинками.** `python tools/presets3d_catalog.py --category all` — по кадру на пресет и листы
`catalog_<категория>.jpg`; `python tools/presets3d_showcase.py` — шоурил ~36 с; `python tools/presets3d_docs.py` —
этот файл. Реестр — `node/scene3d/src/presets/*.ts` (одна правда), снимок для бэкенда —
`node/scene3d/presets/catalog.json` (`node node/scene3d/presets_dump.mjs`).

**toon_ink / toon_sketch / toon_comic** — слоты под шейдер мем-пилота (`environment.toon`, ветка мем-пилота).
Пока он не слит, движок рисует запасной cel-вид и пишет предупреждение; после слияния имена те же.
"""


def thumb(cat: str, name: str, stills: Path) -> str | None:
    src = stills / cat / f"{name}.png"
    if not src.exists():
        return None
    im = Image.open(src).convert("RGB")
    w = 180 if im.width <= im.height else 360
    im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
    dst = DOCS / "img" / cat / f"{name}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=72, optimize=True)
    return f"img/{cat}/{name}.jpg"


def params_text(p: dict) -> str:
    out = []
    for par in p.get("params") or []:
        if par["name"] in ("colors", "hide", "seed"):
            continue
        d = f" = {par['default']}" if par.get("default") is not None else ""
        rng = f" [{par.get('min')}…{par.get('max')}]" if par.get("min") is not None and par.get("max") is not None else ""
        opts = f" ({' / '.join(par['options'])})" if par.get("options") else ""
        out.append(f"`{par['name']}`{d}{rng}{opts} — {par.get('ru', '')}")
    return "<br>".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stills", default="D:/vvf-offload/tmp/presets3d/stills")
    a = ap.parse_args(argv)
    stills = Path(a.stills)
    cat = presets3d.catalog()
    lines = [INTRO]
    lines.append("## Содержание\n")
    for k, v in cat.items():
        lines.append(f"- [{v['title']}](#{k}) — поле `{v['field']}`, {len(v['presets'])} шт.")
    for k, v in cat.items():
        lines.append(f"\n<a id=\"{k}\"></a>\n## {v['title']} — `{v['field']}`\n")
        lines.append("| Кадр | Имя для плана | Что это | Параметры |")
        lines.append("|---|---|---|---|")
        for p in v["presets"]:
            img = thumb(k, p["name"], stills)
            pic = f"![{p['name']}]({img})" if img else "—"
            extra = []
            if p.get("status") == "slot":
                extra.append("**слот мем-пилота**")
            if k == "mood":
                extra.append(f"свет `{p.get('light')}`, эффекты {', '.join(f'`{f}`' for f in p.get('fx') or [])}")
            if k == "room":
                sug = p.get("suggest") or {}
                extra.append(f"к нему: свет `{sug.get('light')}`, стиль `{sug.get('look')}`; якоря {', '.join(f'`{x}`' for x in p.get('anchors') or [])}; "
                             f"палитра {', '.join(f'`{x}`' for x in (p.get('palette') or {}))}")
            if k in ("emotion", "gesture"):
                extra.append(f"длина {p.get('duration')} с; движки: {', '.join(p.get('engines') or [])}")
            if k == "transition":
                extra.append(f"окно {p.get('duration')} с")
            desc = f"**{p['title']}**. {p['ru']}" + ("<br>" + "; ".join(extra) if extra else "")
            lines.append(f"| {pic} | `{p['name']}` | {desc} | {params_text(p)} |")
    out = DOCS / "presets.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
