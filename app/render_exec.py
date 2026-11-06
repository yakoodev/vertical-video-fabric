"""Рендер: запуск ffmpeg и компоновщика, проверка дорожки звука и мелкие преобразования значений
для аргументов. Без зависимостей от других частей рендера (``app/render.py``)."""

from __future__ import annotations

import contextvars
import json
import subprocess
from pathlib import Path
from typing import Any


def _clamp(value, *, default: float, low: float, high: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _video_duration(meta: Any) -> float:
    """Длина видеодорожки куска (контейнер берёт самую длинную дорожку)."""
    for stream in (getattr(meta, "raw", None) or {}).get("streams") or []:
        if stream.get("codec_type") == "video":
            try:
                value = float(stream.get("duration") or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                return value
    return float(getattr(meta, "duration_sec", 0) or 0)


def _seconds(value: float) -> str:
    return f"{max(0, value):.3f}"


def _fps(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _stream_index(value, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    return max(0, int(value))


def _volume(value, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    return max(0.0, min(4.0, float(value)))


def _ff_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filter_filename(filename: str) -> str:
    return str(Path(filename).name).replace("\\", "/").replace(":", r"\:")


def _concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


# Перехват запуска для прохода предпросмотра (app/stream_preview.py); None — как всегда.
RENDER_RUNNER: contextvars.ContextVar[Any] = contextvars.ContextVar("render_runner", default=None)


def _run_ffmpeg(args: list[str], timeout: int, cwd: Path | None = None) -> None:
    runner = RENDER_RUNNER.get()
    if runner is not None:
        runner.ffmpeg(args, timeout, cwd)
        return
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )
    if proc.returncode != 0:
        raise RuntimeError(_safe_error(proc.stderr or proc.stdout or "ffmpeg failed"))


def _run_compositor(args: list[str], timeout: int) -> None:
    """Node-компоновщик моушна. Строки ``progress i/n`` в stderr — не ошибка, их
    в сообщение не тащим: прогресса рендера в сервисе нет, а причину падения
    из-за них не было бы видно."""
    runner = RENDER_RUNNER.get()
    if runner is not None:
        runner.compositor(args, timeout)
        return
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        lines = [line for line in (proc.stderr or "").splitlines() if not line.startswith("progress ")]
        detail = "\n".join(lines[-15:]).strip() or (proc.stdout or "").strip()[-500:]
        raise RuntimeError(_safe_error(detail or f"компоновщик завершился с кодом {proc.returncode}"))


def _safe_error(exc) -> str:
    text = str(exc or "").strip()
    return text[:1000] or "render failed"


def _has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return bool(proc.stdout.strip())
