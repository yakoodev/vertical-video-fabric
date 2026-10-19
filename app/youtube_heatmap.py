"""«Самые пересматриваемые» места YouTube-видео — бесплатный сигнал от зрителей.

YouTube рисует над плеером кривую повторных просмотров; yt-dlp отдаёт её как поле
``heatmap`` ([{start_time, end_time, value 0..1}]). Пики — то, что зрители
переслушивали и пересматривали: готовые кандидаты в шортсы. Кривая уходит в промпт
анализа подсказкой, как всплески чата Twitch.

Кривая кэшируется на источник (runtime/heatmaps/<id>.json): метаданные видео
не меняются, а лишний запрос к YouTube — лишняя задержка анализа.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.settings import settings

TOP_PEAKS = 8
PEAK_MIN_VALUE = 0.35


def _cache_path(source_id: int) -> Path:
    directory = settings.runtime_dir / "heatmaps"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{source_id}.json"


def fetch_heatmap(url: str, timeout: int = 90) -> list[dict]:
    """Кривая пересмотров из метаданных yt-dlp (без скачивания видео)."""
    proc = subprocess.run(
        ["yt-dlp", "-J", "--skip-download", "--no-warnings", url],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        return []
    out = []
    for point in info.get("heatmap") or []:
        try:
            out.append({"start": float(point["start_time"]), "end": float(point["end_time"]), "value": float(point["value"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def source_heatmap(store: Any, source_id: int) -> list[dict]:
    path = _cache_path(source_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    source = store.get_source(source_id)
    url = source.get("original_url") or ""
    if "youtube.com" not in url and "youtu.be" not in url:
        return []
    points = fetch_heatmap(url)
    try:
        path.write_text(json.dumps(points), encoding="utf-8")
    except OSError:
        pass
    return points


def heatmap_peaks(points: list[dict], top: int = TOP_PEAKS) -> list[dict]:
    """Локальные пики кривой выше порога, самые сильные первыми, не ближе 30 с."""
    peaks = []
    for i, p in enumerate(points):
        left = points[i - 1]["value"] if i > 0 else -1
        right = points[i + 1]["value"] if i + 1 < len(points) else -1
        if p["value"] >= PEAK_MIN_VALUE and p["value"] >= left and p["value"] >= right:
            peaks.append(p)
    picked: list[dict] = []
    for p in sorted(peaks, key=lambda x: -x["value"]):
        if all(abs(p["start"] - q["start"]) >= 30 for q in picked):
            picked.append(p)
        if len(picked) >= top:
            break
    return sorted(picked, key=lambda x: x["start"])


def heatmap_prompt_block(store: Any, source_id: int) -> str:
    """Блок для промпта анализа; пусто, если кривой нет или подсказка выключена."""
    try:
        if not bool(store.get_app_setting_value("analysis_use_heatmap", True)):
            return ""
        peaks = heatmap_peaks(source_heatmap(store, source_id))
    except Exception:  # noqa: BLE001 - подсказка, анализ без неё не падает
        return ""
    if not peaks:
        return ""
    from app.twitch_chat import format_ts

    lines = [
        "YouTube «самые пересматриваемые» места этого видео (зрители переслушивали их чаще всего). "
        "Подсказка, а не приказ: бери момент, только если он и на видео сильный."
    ]
    for p in peaks:
        lines.append(f"- {format_ts(p['start'])}–{format_ts(p['end'])}, пересмотры {int(round(p['value'] * 100))}%")
    return "\n".join(lines)
