from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.settings import settings
from app.subtitles.contracts import SubtitleResult, SubtitleWord


# How long the active word may stay highlighted after it stops being audible.
MAX_SUBTITLE_HOLD_AFTER_WORD_SEC = 0.2
# Words further apart than this start a new subtitle page (text disappears in the gap).
MAX_SUBTITLE_GAP_INSIDE_PAGE_SEC = 0.75
# Soft cap on characters per rendered line before we wrap to the next line.
MAX_SUBTITLE_CHARS_PER_LINE = 24
# Never show more than this many lines stacked on screen at once. One line keeps
# the reveal tight and avoids reading ahead of the audio.
MAX_SUBTITLE_LINES_PER_PAGE = 1


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
    text += "\n".join(_dialogue_events(result.words, profile, max_duration=float(result.duration or 0)))
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
    outline_width = _style_number(profile.get("outline_width"), 5, minimum=0, maximum=20)
    shadow = _style_number(profile.get("shadow"), 1, minimum=0, maximum=20)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(width or 1080)}\n"
        f"PlayResY: {int(height or 1920)}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Karaoke,{font_family},{font_size},{primary},{primary},{outline},{back},"
        f"-1,0,0,0,100,100,0,0,1,{_ass_number(outline_width)},{_ass_number(shadow)},{alignment},80,80,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue_events(words: list[SubtitleWord], profile: dict, max_duration: float = 0.0) -> list[str]:
    primary = ass_color(profile.get("primary_color") or "#FFFFFF")
    active = ass_color(profile.get("active_word_color") or "#FACC15")
    max_words_per_line = max(1, int(profile.get("max_words_per_line") or 5))
    uppercase = bool(profile.get("uppercase"))
    normalized = _normalized_words(words, uppercase)
    if not normalized:
        return []
    events: list[str] = []
    for page_indices in _subtitle_page_indices(normalized, max_words_per_line):
        page = [normalized[index] for index in page_indices]
        lines = _line_layout(page, max_words_per_line)
        for local_index, word_index in enumerate(page_indices):
            word = normalized[word_index]
            next_start = normalized[word_index + 1].start if word_index + 1 < len(normalized) else None
            event_end = word.end + MAX_SUBTITLE_HOLD_AFTER_WORD_SEC
            if next_start is not None:
                event_end = min(event_end, next_start)
            event_end = max(word.end, event_end)
            if max_duration > 0:
                event_end = min(event_end, max_duration)
            if event_end <= word.start:
                event_end = word.start + 0.2
                if max_duration > 0:
                    event_end = min(event_end, max_duration)
            if event_end <= word.start:
                continue
            text = _page_text(page, lines, local_index, primary, active)
            events.append(
                "Dialogue: 0,"
                f"{ass_timestamp(word.start)},{ass_timestamp(event_end)},"
                f"Karaoke,,0,0,0,,{text}"
            )
    return events


def _line_layout(page: list[SubtitleWord], max_words_per_line: int) -> list[list[int]]:
    """Split a page into a fixed set of lines once, so layout never reflows.

    The whole page text is shown for the entire page lifetime; only the
    highlighted word changes between events. Because the line breaks are
    decided here a single time, the text block keeps a constant height and
    never jumps vertically while the karaoke highlight moves.
    """

    if not page:
        return []
    lines: list[list[int]] = [[]]
    current_chars = 0
    for index, word in enumerate(page):
        word_len = len(word.word)
        line = lines[-1]
        would_overflow = line and (
            len(line) >= max_words_per_line
            or current_chars + 1 + word_len > MAX_SUBTITLE_CHARS_PER_LINE
        )
        if would_overflow and len(lines) < MAX_SUBTITLE_LINES_PER_PAGE:
            lines.append([])
            line = lines[-1]
            current_chars = 0
        elif would_overflow:
            # Already at the line cap for this page: keep filling the last line
            # rather than dropping words, even if it grows a little long.
            pass
        if line:
            current_chars += 1
        current_chars += word_len
        line.append(index)
    return [line for line in lines if line]


def _page_text(
    page: list[SubtitleWord],
    lines: list[list[int]],
    active_index: int,
    primary: str,
    active: str,
) -> str:
    """Render the whole line with a fixed layout so it never reflows.

    Words after the active one are kept in the text (to reserve their layout
    space) but made fully transparent, so they only appear once they are
    actually spoken. Already-spoken words stay visible in the primary colour and
    the current word is drawn in the active colour. This gives an in-sync
    word-by-word reveal without any vertical or horizontal jumping.
    """

    rendered_lines: list[str] = []
    for line in lines:
        parts: list[str] = []
        for local_index in line:
            if local_index > active_index:
                color, alpha = primary, "FF"
            elif local_index == active_index:
                color, alpha = active, "00"
            else:
                color, alpha = primary, "00"
            parts.append(r"{\c" + color + r"\alpha&H" + alpha + "&}" + _ass_text(page[local_index].word))
        rendered_lines.append(" ".join(parts))
    return r"\N".join(rendered_lines)


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


def _normalized_words(words: list[SubtitleWord], uppercase: bool) -> list[SubtitleWord]:
    normalized: list[SubtitleWord] = []
    previous_end = 0.0
    for word in sorted(words, key=lambda item: (float(item.start), float(item.end))):
        text = _normalize_word(word.word, uppercase)
        if not text:
            continue
        start = max(0.0, float(word.start))
        end = max(start + 0.05, float(word.end))
        if start < previous_end:
            start = previous_end
            end = max(end, start + 0.05)
        normalized.append(SubtitleWord(word=text, start=start, end=end))
        previous_end = end
    return normalized


def _subtitle_page_indices(words: list[SubtitleWord], page_size: int) -> list[list[int]]:
    pages: list[list[int]] = []
    current: list[int] = []
    max_words_per_page = max(1, page_size) * MAX_SUBTITLE_LINES_PER_PAGE
    for index, word in enumerate(words):
        if current:
            previous = words[current[-1]]
            gap = word.start - previous.end
            ends_sentence = _ends_sentence(previous.word)
            if gap > MAX_SUBTITLE_GAP_INSIDE_PAGE_SEC or len(current) >= max_words_per_page or ends_sentence:
                pages.append(current)
                current = []
        current.append(index)
    if current:
        pages.append(current)
    return pages


def _ends_sentence(word: str) -> bool:
    return str(word).strip().endswith((".", "!", "?", "…"))


def _normalize_word(word: str, uppercase: bool) -> str:
    text = " ".join(str(word).split())
    return text.upper() if uppercase else text


def _ass_text(text: str) -> str:
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _style_value(text: str) -> str:
    return str(text).replace(",", " ").replace("\n", " ").strip() or "Arial"


def _style_number(value, default: float, *, minimum: float, maximum: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _ass_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")
