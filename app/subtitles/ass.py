from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.settings import settings
from app.subtitles.contracts import SubtitleResult, SubtitleWord


def write_ass_subtitles(
    result: SubtitleResult,
    profile: dict,
    output_path: Path | None = None,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    if not result.words:
        raise ValueError("word-level subtitle timestamps are required")
    settings.subtitle_dir.mkdir(parents=True, exist_ok=True)
    path = output_path or settings.subtitle_dir / f"{uuid4().hex}.ass"
    if not path.is_absolute():
        path = settings.subtitle_dir / path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _ass_header(profile, width, height)
    text += "\n".join(_dialogue_events(result.words, profile))
    text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _ass_header(profile: dict, width: int, height: int) -> str:
    font_family = _style_value(profile.get("font_family") or "Arial")
    font_size = int(profile.get("font_size") or 64)
    alignment = int(profile.get("alignment") or 2)
    margin_v = int(profile.get("margin_v") or 160)
    primary = ass_color(profile.get("primary_color") or "#FFFFFF")
    outline = ass_color(profile.get("outline_color") or "#111827")
    back = ass_color(profile.get("back_color") or "#000000")
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(width or 1080)}\n"
        f"PlayResY: {int(height or 1920)}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font_family},{font_size},{primary},{primary},{outline},{back},"
        f"-1,0,0,0,100,100,0,0,1,5,1,{alignment},80,80,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue_events(words: list[SubtitleWord], profile: dict) -> list[str]:
    primary = ass_color(profile.get("primary_color") or "#FFFFFF")
    active = ass_color(profile.get("active_word_color") or "#FACC15")
    max_words_per_line = max(1, int(profile.get("max_words_per_line") or 5))
    words_per_page = max_words_per_line * 2
    uppercase = bool(profile.get("uppercase"))
    normalized = [
        SubtitleWord(
            word=_normalize_word(word.word, uppercase),
            start=max(0.0, float(word.start)),
            end=max(float(word.start), float(word.end)),
        )
        for word in words
        if str(word.word).strip()
    ]
    if not normalized:
        return []
    events: list[str] = []
    for page in _chunks(normalized, words_per_page):
        for index, word in enumerate(page):
            next_start = page[index + 1].start if index + 1 < len(page) else word.end + 0.2
            event_end = max(word.end, next_start)
            if event_end <= word.start:
                event_end = word.start + 0.2
            text = _page_text(page, index, max_words_per_line, primary, active)
            events.append(
                "Dialogue: 0,"
                f"{ass_timestamp(word.start)},{ass_timestamp(event_end)},"
                f"Karaoke,,0,0,0,,{text}"
            )
    return events


def _page_text(
    page: list[SubtitleWord],
    active_index: int,
    max_words_per_line: int,
    primary: str,
    active: str,
) -> str:
    rendered: list[str] = []
    for index, word in enumerate(page):
        color = active if index == active_index else primary
        rendered.append(r"{\c" + color + "}" + _ass_text(word.word))
        if index == max_words_per_line - 1 and index + 1 < len(page):
            rendered.append(r"\N")
        elif index + 1 < len(page):
            rendered.append(" ")
    return "".join(rendered)


def ass_color(css_hex: str) -> str:
    value = str(css_hex or "").strip()
    if len(value) != 7 or not value.startswith("#"):
        value = "#FFFFFF"
    red = value[1:3]
    green = value[3:5]
    blue = value[5:7]
    return f"&H00{blue}{green}{red}&"


def ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(float(seconds) * 100)))
    hours, rem = divmod(centiseconds, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _chunks(words: list[SubtitleWord], chunk_size: int) -> list[list[SubtitleWord]]:
    return [words[index : index + chunk_size] for index in range(0, len(words), chunk_size)]


def _normalize_word(word: str, uppercase: bool) -> str:
    text = " ".join(str(word).split())
    return text.upper() if uppercase else text


def _ass_text(text: str) -> str:
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _style_value(text: str) -> str:
    return str(text).replace(",", " ").replace("\n", " ").strip() or "Arial"
