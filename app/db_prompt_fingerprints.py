"""Начальные промпты: можно ли обновить строку базы новым текстом (из ``app/db.py``)."""

from __future__ import annotations


def _should_update_seeded_prompt(existing, legacy_prompt: str | None, seed_updated_at: str) -> bool:
    current_prompt = str(existing["prompt"] or "").strip()
    if legacy_prompt and current_prompt == legacy_prompt:
        return True
    if not seed_updated_at:
        return False
    updated_at = str(existing["updated_at"] or "")
    key = (str(existing["task"]), str(existing["label"]))
    return bool(
        updated_at
        and updated_at <= seed_updated_at
        and _matches_seeded_prompt_fingerprint(key, current_prompt)
    )


def _matches_seeded_prompt_fingerprint(key: tuple[str, str], prompt: str) -> bool:
    fingerprints = {
        ("analysis", "Apex analysis"): (
            (
                "Analyze this source for vertical short-form publishing.",
                "Return clips[] as finished edit plans, not just isolated detections.",
                "If a clip needs context, include a short setup segment before the payoff.",
            ),
        ),
        ("analysis", "Anime analysis"): (
            (
                "Analyze this anime source for vertical short-form publishing.",
                "Return clips[] as finished edit plans.",
                "setup and reaction, joke and punchline",
            ),
            (
                "Analyze this anime episode as a connected story digest",
                "Build clips[] as chronological story",
                "Prefer combined clip length around 45 to 150 seconds.",
            ),
            (
                "Analyze this anime episode as a connected story digest",
                "Keep each individual segment around 25 to 70 seconds",
                "never longer than 90 seconds",
            ),
            (
                "Analyze this anime episode for self-contained main story clips",
                "Do not cover the episode mechanically from beginning to end",
                "Every returned clip must have its own local hook",
            ),
            (
                "Analyze this anime episode for vertical publishing.",
                "clips[0] is mandatory and must be titled like",
                "Episode Story Recap",
            ),
            (
                "Analyze this anime episode for vertical publishing.",
                "Prefer recap clip length around 120 to 180 seconds",
                "5 to 6 ordered segments",
            ),
            (
                "Analyze this anime episode for vertical publishing.",
                "Prefer recap clip length around 90 to 150 seconds",
                "Do not return any finished clip around 3 minutes",
            ),
        ),
        ("analysis", "Series analysis"): (
            (
                "Analyze this TV series or episodic live-action source for vertical short-form",
                "Return clips[] as finished edit plans.",
                "setup and reveal, accusation and response",
            ),
            (
                "Analyze this TV series or episodic live-action source as a connected story",
                "chronological story chapters",
                "Prefer combined clip length around 45 to 150 seconds.",
            ),
            (
                "Analyze this TV series or episodic live-action source as a connected story",
                "Keep each individual segment around 25 to 70 seconds",
                "never longer than 90 seconds",
            ),
            (
                "Analyze this TV series or episodic live-action source for self-contained main",
                "Do not cover the episode mechanically from beginning to end",
                "Every returned clip must have its own local hook",
            ),
            (
                "Analyze this TV series or episodic live-action source for vertical publishing.",
                "clips[0] is mandatory and must be titled like",
                "Episode Story Recap",
            ),
            (
                "Analyze this TV series or episodic live-action source for vertical publishing.",
                "Prefer recap clip length around 120 to 180 seconds",
                "5 to 6 ordered segments",
            ),
            (
                "Analyze this TV series or episodic live-action source for vertical publishing.",
                "Prefer recap clip length around 90 to 150 seconds",
                "Do not return any finished clip around 3 minutes",
            ),
        ),
        # Эти пресеты появились позже и отпечатков не имели — значит, обновить
        # их сид не мог. Строки взяты из частей, которые правки длины не трогали.
        ("analysis", "Action drama highlights"): (
            (
                "This is a live-action ACTION series",
                "Your #1 job is to find the FIGHTS and the ACTION.",
                "Never cut mid-punch, mid-line, or mid-action.",
            ),
        ),
        ("analysis", "Подкаст: цитаты"): (
            (
                "Analyze this podcast, interview, or talking-head source for vertical short-form.",
                "Find the most quotable, shareable spoken moments",
                "Merge a setup question and its answer into one clip",
            ),
        ),
        ("analysis", "Обучающее: тезисы"): (
            (
                "Analyze this educational, tutorial, lecture, or explainer source for vertical",
                "Each clip teaches one",
                "Skip long tangents, repeated recaps",
            ),
        ),
        ("analysis", "Юмор: смешные моменты"): (
            (
                "Analyze this source for the funniest standalone moments for vertical short-form:",
                "Always include the setup and the punchline",
                "Never cut through the punchline or a key reaction.",
            ),
        ),
        ("publishing", "Publishing metadata"): (
            (
                "Generate publishing metadata for a rendered vertical clip.",
                "Return JSON only with title and description suitable for YouTube Shorts and TikTok.",
                "Keep the title short, direct, and based on the clip content.",
            ),
            (
                "Generate publishing metadata for a rendered vertical clip.",
                "Make the title specific to the clip, not generic.",
                "Avoid clickbait that misrepresents the scene.",
            ),
        ),
        ("subtitle", "Apex subtitles"): (
            (
                "Transcribe this audio for karaoke subtitles.",
                "Split readable subtitle segments by phrase",
                "Keep word timestamps aligned tightly enough for karaoke highlighting.",
            ),
            (
                "Transcribe this audio for karaoke subtitles.",
                "Do not stretch a word or segment through silence.",
                "Word end timestamps should be close to the audible end of the word",
            ),
        ),
        ("subtitle", "Anime subtitles"): (
            (
                "Transcribe this anime audio for karaoke subtitles.",
                "Preserve character names, honorifics",
                "keep word timestamps tight for karaoke highlighting",
            ),
            (
                "Transcribe this anime audio for karaoke subtitles.",
                "Preserve character names, honorifics",
                "Keep word timestamps tight for karaoke highlighting.",
            ),
            (
                "Transcribe this anime audio for karaoke subtitles.",
                "Do not stretch subtitles across dramatic silence",
                "Word end timestamps should be close to the audible end",
            ),
        ),
        ("subtitle", "Series subtitles"): (
            (
                "Transcribe this TV series audio for karaoke subtitles.",
                "Preserve exact dialogue, names, places",
                "Keep word timestamps tight for karaoke highlighting.",
            ),
            (
                "Transcribe this TV series audio for karaoke subtitles.",
                "Do not stretch subtitles across dramatic silence",
                "Word end timestamps should be close to the audible end",
            ),
        ),
    }
    expected_options = fingerprints.get(key)
    return bool(expected_options and any(all(part in prompt for part in expected) for expected in expected_options))
