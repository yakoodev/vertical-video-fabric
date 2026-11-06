"""🎬 Пакет монтажа для Premiere и DaVinci Resolve: zip без рендера и без исходного видео.

В архиве: ``timeline/*.xml`` (FCP7 xmeml) и ``timeline/*.otio`` с дорожками на готовом таймлайне,
``subtitles/*.srt`` с таймингами после вырезов, текст слоёв (.srt и .csv, PNG с прозрачностью на
своей дорожке, если есть компоновщик), файлы картинок, мемов, звуков и музыки, ``README.md`` на русском,
``relink.py`` и ``motion_keys.csv``. Времена берутся из ``app/playback.py`` — того же кода, что у
рендера и предпросмотра.

Запись стрима весит 10+ ГБ и в архив не кладётся: монтаж ссылается на ``media/source/<имя файла>``.
С ``with_pieces`` в архив идут короткие куски 720p, и монтаж ссылается на них.

Что формат не держит, сказано в README пакета (``_readme``): это часть результата, а не сноска.

Где что лежит. Здесь — README пакета и сборка ``build_package``. Вынесено: форматы FCP7 и OTIO ``app/timeline_package_formats.py``, модель пакета
``app/timeline_package_model.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import csv
import io
import shutil
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import opentimelineio as otio

from app.settings import settings

from app.timeline_package_formats import (  # noqa: F401
    _sub, _rate, _pathurl, _Xml, _param, _motion_filter, _speed_filter, _levels_filter, build_xml, build_otio,
)
from app.timeline_package_model import (  # noqa: F401
    SAFE, MAX_TEXT_PNG, PIECE_HANDLE_SEC, PIECE_HEIGHT, TEXT_TYPES, _safe, _layer_text, Item, _level_keys,
    _camera_view, _base_motion, _focus_at, _motion_keys, _probe, _sniff_ext, _Files, _package_dir, _cut_piece,
    _text_pngs, _layer_motion, build_model,
)

def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, float(t)) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _tc(t: float) -> str:
    return _srt_time(t).replace(",", ".")


def srt_text(rows: list[tuple[float, float, str]]) -> str:
    out = []
    for index, (a, b, text) in enumerate(sorted(rows, key=lambda r: r[0]), 1):
        out.append(f"{index}\n{_srt_time(a)} --> {_srt_time(max(b, a + 0.05))}\n{text.strip()}\n")
    return "\n".join(out)


# ---------------------------------------------------------------- README и сопутствующее


RELINK_PY = '''#!/usr/bin/env python3
"""Сделать из timeline/*.xml версию с абсолютными путями: Premiere и Resolve тогда находят файлы сами.

    python relink.py                          файлы рядом с этим скриптом
    python relink.py "D:/записи/запись.mp4"   запись стрима лежит в другом месте

Только стандартная библиотека. Создаёт timeline/<имя>.absolute.xml, исходный файл не меняет.
"""
import pathlib
import re
import sys
from urllib.parse import quote, unquote

here = pathlib.Path(__file__).resolve().parent
record = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None


def absolute(match):
    rel = unquote(match.group(1))
    path = here / rel
    if record is not None and rel.startswith("media/source/"):
        path = record
    return "<pathurl>file://localhost/" + quote(path.as_posix().lstrip("/"), safe="/:") + "</pathurl>"


for xml in (here / "timeline").glob("*.xml"):
    if xml.name.endswith(".absolute.xml"):
        continue
    out = xml.with_name(xml.stem + ".absolute.xml")
    out.write_text(re.sub(r"<pathurl>(.*?)</pathurl>", absolute, xml.read_text(encoding="utf-8")), encoding="utf-8")
    print("готово:", out)
'''


def _readme(model: dict, counts: dict, public_link: str) -> str:
    plan = model["plan"]
    name = model["name"]
    layout = (plan.get("layout") or {}).get("mode") if isinstance(plan.get("layout"), dict) else None
    transition = plan["transition"]["type"]
    fx_list = sorted({str(f.get("fx")) for f in plan["audio"].get("fx") or []})
    lines = [
        f"# {plan.get('title') or name}: пакет монтажа",
        "",
        f"Кадр {model['width']}×{model['height']} ({plan['format']}), {model['fps']:g} к/с, длина {_tc(model['duration'])}.",
        "Собрано сервисом без рендера. Времена — как в готовом ролике: вырезы и наплывы уже учтены.",
        "",
        "## Как открыть",
        "",
    ]
    if model["with_pieces"]:
        lines += [model["pieces_note"], ""]
    elif model["source_rel"]:
        lines += [
            f"Запись стрима в архив не положена (размер). Положите её сюда: `{model['source_rel']}` — имя файла как здесь.",
            f"Где взять: {public_link}" if public_link else "",
            "",
        ]
    lines += [
        "1. Распакуйте архив целиком. Самый надёжный путь: `python relink.py` рядом с README сделает "
        f"`timeline/{name}.absolute.xml` с полными путями (запись в другом месте: `python relink.py \"D:/путь/запись.mp4\"`).",
        f"2. **Premiere Pro**: File → Import → `timeline/{name}.absolute.xml` (или `{name}.xml`, тогда Link Media и укажите файл).",
        f"3. **DaVinci Resolve**: File → Import → Timeline → выберите xml; включите «Automatically import source clips into media pool» "
        "и «Use sizing information». Не нашёл файлы: Reconform from Media Pool, предварительно импортировав папку `media`.",
        f"4. **Kdenlive и др.**: `timeline/{name}.otio` (пути относительно этого файла на уровень выше).",
        "",
        "## Что доехало",
        "",
        f"- Куски на дорожке V1 (штук: {counts['parts']}), с настоящими таймкодами записи, после вырезов.",
    ]
    if counts["transitions"]:
        lines.append(f"- Наплывы как Cross Dissolve той же длины (штук: {counts['transitions']}). Выбран переход «{transition}».")
    if counts["srt_lines"]:
        extra = "" if plan["subtitles"].get("on") else " В самом клипе прожиг субтитров выключен, файл — на случай, если понадобятся."
        lines.append(f"- Субтитры: `subtitles/{name}.srt` (строк: {counts['srt_lines']}), тайминги после вырезов.{extra}")
    if counts["pictures"]:
        lines.append(f"- Картинки и мемы на своих дорожках (клипов: {counts['pictures']}), файлы в `media/images` и `media/video`, положение и размер заданы (статично).")
    if counts["inserts"]:
        lines.append(f"- Вставки поверх записи на нужных таймкодах (клипов: {counts['inserts']}).")
    if counts["text_tracks"]:
        lines.append(f"- Текст: PNG с прозрачностью на дорожках «Текст» (штук: {counts['text_tracks']}), выглядит как в кадре. Менять текст — заново, данные в `text_layers.csv`.")
    if counts["text_layers"]:
        lines.append(f"- Текст слоёв списком: `subtitles/text_layers.srt` и `subtitles/text_layers.csv` (слоёв: {counts['text_layers']}; таймкоды, текст, положение, шрифт, цвет).")
    if counts["sfx"]:
        lines.append(f"- Звуки на дорожках «Звуки» (клипов: {counts['sfx']}), файлы в `media/sounds`, громкость выставлена.")
    if counts["music"]:
        lines.append("- Музыка: своя дорожка, начало с той же секунды трека, с повтором, если трек короче клипа. Файл в `media/music`.")
    if counts["level_keys"]:
        lines.append("- Тишина и громкость отрезков: ключи Audio Levels на дорожке звука записи.")
    lines += [
        f"- Кроп под формат и наезды камеры: Basic Motion (масштаб и центр, ключи там, где кадр движется). Те же числа по таймкодам: `motion_keys.csv`.",
        "",
        "## Что НЕ доехало (делается руками или остаётся за рендером)",
        "",
        "- Караоке-подсветка слов и стиль субтитров: в .srt только текст и время.",
        "- Анимации текста и слоёв (вход, выход, пульсации): на дорожке слой стоит статично.",
        "- Анимированные субтитры (слой captions), чат, PNGtuber (рот по голосу), частицы, эффекты кадра (fx), фигуры, графики: "
        + ("в этом клипе не перенесены слои: " + ", ".join(f"{k} ×{v}" for k, v in sorted(model["dropped"].items())) + "." if model["dropped"] else "в этом клипе таких слоёв нет."),
        "- Цвет: лук пресета и цветокоррекция кусков. Хромакей и анимированные фоны холста.",
    ]
    if layout and layout != "single":
        lines.append(f"- Раскладка кадра «{layout}» (вебка отдельным окном, split): на V1 лежит цельный кадр записи, раскладку собрать руками.")
    if transition not in ("cut", "fade", "dissolve"):
        lines.append(f"- Форма перехода «{transition}»: в файле Cross Dissolve, замените своим.")
    if plan.get("freezes_raw") or plan.get("hold"):
        lines.append("- Стоп-кадры: только метки на таймлайне, нужен Frame Hold.")
    if any(p.get("speed", 1) != 1 or p.get("reverse") for p in plan["parts"]):
        lines.append("- Скорость и реверс кусков: Time Remap записан, но у разных монтажек он читается по-разному, проверьте длины.")
    if fx_list:
        lines.append(f"- Эффекты звука на отрезках ({', '.join(fx_list)}), J/L-склейки, автоматика и ducking музыки: не переносятся.")
    else:
        lines.append("- J/L-склейки, автоматика и ducking музыки, сведение: не переносятся.")
    lines += [
        "- Итоговая громкость −14 LUFS: рендер выравнивает в конце, в монтажке выставьте сами.",
    ]
    if model["notes"]:
        lines += ["", "## Замечания сборки", ""] + [f"- {n}" for n in model["notes"]]
    lines += [
        "",
        "## Что проверено, а что нет",
        "",
        "Файлы проверены парсером OpenTimelineIO (и `.otio`, и адаптером `fcp_xml` для `.xml`): дорожки, клипы и длина читаются. "
        "Настоящего импорта в Premiere и Resolve при сборке не было.",
        "Не подтверждено на живой монтажке: **центр в Basic Motion**. Здесь смещение от центра кадра в долях его сторон, "
        "а описания формата расходятся. Если картинка уехала, но масштаб верный, сверьте с `motion_keys.csv`: `dx` и `dy` там в пикселях кадра. "
        "Для ключей `when` — кадр от начала клипа на таймлайне.",
    ]
    return "\n".join(line for line in lines if line is not None) + "\n"


# ---------------------------------------------------------------- пакет


def build_package(store: Any, clip_plan_id: int, with_pieces: bool = False) -> dict:
    """Собрать zip: ``{path, filename, url, size_bytes, files, summary, notes}``."""
    work = settings.tmp_dir / f"timeline-package-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        model = build_model(store, clip_plan_id, with_pieces, work)
        name = model["name"]
        plan = model["plan"]
        directory = _package_dir()
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "-pieces" if model["with_pieces"] else ""
        filename = f"{clip_plan_id}-{name}-package{suffix}.zip"
        target = directory / filename
        part = directory / f"{filename}.{uuid4().hex}.part"

        xml_bytes = build_xml(model)
        timeline = build_otio(model)
        otio_path = work / f"{name}.otio"
        otio.adapters.write_to_file(timeline, str(otio_path), adapter_name="otio_json")

        srt_rows = [(float(l["start"]), float(l["end"]), str(l.get("text") or "")) for l in plan["subtitles"]["lines"] if str(l.get("text") or "").strip()]
        text_rows = [(float(l["start"]), float(l["end"]), _layer_text(l)) for l in model["text_layers"]]
        csv_buf = io.StringIO()
        writer = csv.writer(csv_buf)
        writer.writerow(["start", "end", "start_tc", "end_tc", "type", "text", "x", "y", "font", "size", "color", "id"])
        for l in sorted(model["text_layers"], key=lambda r: float(r["start"])):
            props = l.get("props") or {}
            writer.writerow([l["start"], l["end"], _tc(l["start"]), _tc(l["end"]), l.get("type"), _layer_text(l), l.get("x"), l.get("y"),
                             props.get("font") or props.get("fontFamily") or "", props.get("size") or "", props.get("color") or "", l.get("id")])
        motion_buf = io.StringIO()
        writer = csv.writer(motion_buf)
        writer.writerow(["t", "tc", "piece", "scale_pct", "dx_px", "dy_px", "center_horiz", "center_vert"])
        for row in model["motion_rows"]:
            writer.writerow([round(row["t"], 3), _tc(row["t"]), row["part"], row["scale"], row["dx"], row["dy"], row["horiz"], row["vert"]])

        source = model["source"]
        link_lines = []
        if source.get("original_url"):
            link_lines.append(str(source["original_url"]))
        link_lines.append(f"в сервисе: /media/sources/{source['id']} (нужен вход)")
        if plan["media"].get("proxy", {}).get("url"):
            link_lines.append(f"лёгкий прокси 480p: {plan['media']['proxy']['url']}")
        public_link = "; ".join(link_lines)

        video_items = [i for t in model["video_tracks"] for i in t["items"]]
        audio_items = [i for t in model["audio_tracks"] for i in t["items"]]
        counts = {
            "parts": sum(1 for i in video_items if i.get("kind") == "piece"),
            "transitions": sum(1 for i in video_items if i.get("transition")),
            "srt_lines": len(srt_rows),
            "pictures": sum(1 for i in video_items if i.get("kind") in ("image", "video")),
            "inserts": sum(1 for i in video_items if i.get("kind") == "insert"),
            "text_tracks": sum(1 for i in video_items if i.get("kind") == "text"),
            "text_layers": len(text_rows),
            "sfx": sum(1 for i in audio_items if i.get("kind") in ("sfx", "join_sfx", "insert")),
            "music": sum(1 for i in audio_items if i.get("kind") == "music"),
            "level_keys": sum(1 for i in audio_items if i.get("levels")),
            "motion_rows": len(model["motion_rows"]),
        }
        readme = _readme(model, counts, public_link)

        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.md", readme)
            z.writestr("relink.py", RELINK_PY)
            z.writestr(f"timeline/{name}.xml", xml_bytes)
            z.write(otio_path, f"timeline/{name}.otio")
            z.writestr("motion_keys.csv", motion_buf.getvalue().encode("utf-8-sig"))
            if srt_rows:
                z.writestr(f"subtitles/{name}.srt", srt_text(srt_rows))
            if text_rows:
                z.writestr("subtitles/text_layers.srt", srt_text(text_rows))
                z.writestr("subtitles/text_layers.csv", csv_buf.getvalue().encode("utf-8-sig"))
            if model["source_rel"]:
                z.writestr("media/source/ПОЛОЖИТЕ_ЗАПИСЬ_СЮДА.txt", f"Файл: {Path(model['source_rel']).name}\nГде взять: {public_link}\n")
            for src, rel in model["copies"]:
                # Видео и звук уже сжаты: не тратим процессор на второй раз.
                z.write(src, rel, compress_type=zipfile.ZIP_STORED)
        part.replace(target)
        return {
            "path": target,
            "filename": filename,
            "url": f"/api/clip-plans/{clip_plan_id}/timeline/package" + ("?pieces=true" if with_pieces else ""),
            "size_bytes": target.stat().st_size,
            "files": len(model["copies"]),
            "summary": counts,
            "notes": model["notes"],
            "tracks": {"video": [t["name"] for t in model["video_tracks"]], "audio": [t["name"] for t in model["audio_tracks"]]},
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
