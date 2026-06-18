from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from app.default_prompts import (
    DEFAULT_PROMPT_PRESETS,
    DEFAULT_PROMPT_SEED_KEY,
    DEFAULT_PROMPT_SEED_VERSION,
    LEGACY_DEFAULT_PROMPTS,
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    label TEXT NOT NULL,
                    encrypted_cookies TEXT NOT NULL,
                    encrypted_proxy_url TEXT NOT NULL DEFAULT '',
                    cookie_count INTEGER NOT NULL DEFAULT 0,
                    has_required_cookies INTEGER NOT NULL DEFAULT 0,
                    missing_cookies TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(platform, label)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_id INTEGER REFERENCES clips(id),
                    status TEXT NOT NULL DEFAULT 'queued',
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    privacy TEXT NOT NULL DEFAULT 'public',
                    allow_comments INTEGER NOT NULL DEFAULT 1,
                    scheduled_at TEXT,
                    source_filename TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS job_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    account_id INTEGER NOT NULL REFERENCES accounts(id),
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    remote_id TEXT NOT NULL DEFAULT '',
                    remote_url TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL DEFAULT 'created',
                    source_type TEXT NOT NULL,
                    original_url TEXT NOT NULL DEFAULT '',
                    original_filename TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    duration_sec REAL NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    fps REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS ai_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    analysis_id INTEGER NOT NULL REFERENCES ai_analyses(id) ON DELETE CASCADE,
                    start_sec REAL NOT NULL,
                    end_sec REAL NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '#64748B',
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS clip_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    analysis_id INTEGER REFERENCES ai_analyses(id) ON DELETE SET NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    score REAL NOT NULL DEFAULT 0,
                    category TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '#64748B',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS clip_plan_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_plan_id INTEGER NOT NULL REFERENCES clip_plans(id) ON DELETE CASCADE,
                    segment_id INTEGER NOT NULL REFERENCES ai_segments(id) ON DELETE CASCADE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(clip_plan_id, segment_id)
                );

                CREATE TABLE IF NOT EXISTS ffmpeg_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    output_width INTEGER NOT NULL DEFAULT 1080,
                    output_height INTEGER NOT NULL DEFAULT 1920,
                    fps REAL NOT NULL DEFAULT 30,
                    video_codec TEXT NOT NULL DEFAULT 'libx264',
                    audio_codec TEXT NOT NULL DEFAULT 'aac',
                    video_bitrate TEXT NOT NULL DEFAULT '',
                    audio_bitrate TEXT NOT NULL DEFAULT '',
                    audio_mix_mode TEXT NOT NULL DEFAULT 'primary',
                    audio_primary_stream INTEGER NOT NULL DEFAULT 0,
                    audio_primary_volume REAL NOT NULL DEFAULT 1,
                    audio_secondary_stream INTEGER,
                    audio_secondary_volume REAL NOT NULL DEFAULT 1,
                    scale_mode TEXT NOT NULL DEFAULT 'cover',
                    crop_anchor TEXT NOT NULL DEFAULT 'center',
                    banner_id INTEGER REFERENCES banners(id),
                    subtitle_profile_id INTEGER REFERENCES subtitle_profiles(id),
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS banners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    duration_sec REAL NOT NULL DEFAULT 0,
                    position TEXT NOT NULL DEFAULT 'bottom',
                    x INTEGER,
                    y INTEGER,
                    opacity REAL NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS audio_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    duration_sec REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0.25,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS subtitle_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'mock',
                    model TEXT NOT NULL DEFAULT 'openai/gpt-4o-transcribe',
                    language TEXT NOT NULL DEFAULT '',
                    timing_offset_sec REAL NOT NULL DEFAULT 0,
                    font_family TEXT NOT NULL DEFAULT 'Arial',
                    font_size INTEGER NOT NULL DEFAULT 64,
                    primary_color TEXT NOT NULL DEFAULT '#FFFFFF',
                    active_word_color TEXT NOT NULL DEFAULT '#FACC15',
                    outline_color TEXT NOT NULL DEFAULT '#111827',
                    back_color TEXT NOT NULL DEFAULT '#000000',
                    alignment INTEGER NOT NULL DEFAULT 2,
                    margin_v INTEGER NOT NULL DEFAULT 160,
                    max_words_per_line INTEGER NOT NULL DEFAULT 5,
                    uppercase INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS subtitle_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                    subtitle_profile_id INTEGER REFERENCES subtitle_profiles(id),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    transcript_json TEXT NOT NULL DEFAULT '{}',
                    ass_path TEXT NOT NULL DEFAULT '',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    clip_plan_id INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL,
                    segment_id INTEGER REFERENCES ai_segments(id) ON DELETE SET NULL,
                    ffmpeg_preset_id INTEGER REFERENCES ffmpeg_presets(id),
                    subtitle_profile_id INTEGER REFERENCES subtitle_profiles(id),
                    subtitle_track_id INTEGER REFERENCES subtitle_tracks(id),
                    status TEXT NOT NULL DEFAULT 'queued',
                    output_path TEXT NOT NULL DEFAULT '',
                    preview_path TEXT NOT NULL DEFAULT '',
                    duration_sec REAL NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    encrypted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS prompt_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    label TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
                CREATE INDEX IF NOT EXISTS idx_job_targets_job ON job_targets(job_id);
                CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status, id);
                CREATE INDEX IF NOT EXISTS idx_ai_analyses_source ON ai_analyses(source_id, id);
                CREATE INDEX IF NOT EXISTS idx_ai_segments_source ON ai_segments(source_id, start_sec);
                CREATE INDEX IF NOT EXISTS idx_clip_plans_source ON clip_plans(source_id, id);
                CREATE INDEX IF NOT EXISTS idx_clip_plan_segments_plan ON clip_plan_segments(clip_plan_id, sort_order);
                CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status, id);
                CREATE INDEX IF NOT EXISTS idx_clips_source ON clips(source_id, id);
                CREATE INDEX IF NOT EXISTS idx_prompt_presets_task ON prompt_presets(task, is_default, id);
                """
            )
            self._ensure_column(conn, "accounts", "encrypted_proxy_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "deleted_at", "TEXT")
            self._ensure_column(conn, "jobs", "clip_id", "INTEGER REFERENCES clips(id)")
            self._ensure_column(conn, "jobs", "scheduled_at", "TEXT")
            self._ensure_column(conn, "ffmpeg_presets", "audio_mix_mode", "TEXT NOT NULL DEFAULT 'primary'")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_stream", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_volume", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_stream", "INTEGER")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_volume", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "audio_tracks", "volume", "REAL NOT NULL DEFAULT 0.25")
            self._ensure_column(conn, "ffmpeg_presets", "music_track_id", "INTEGER REFERENCES audio_tracks(id)")
            self._ensure_column(conn, "ffmpeg_presets", "music_volume", "REAL NOT NULL DEFAULT 0.25")
            self._ensure_column(conn, "ffmpeg_presets", "music_loop", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "music_fade_in_sec", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "music_fade_out_sec", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "music_duck", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "music_duck_amount", "REAL NOT NULL DEFAULT 0.6")
            self._ensure_column(conn, "ffmpeg_presets", "color_style", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column(conn, "ffmpeg_presets", "color_strength", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "vignette", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "grain", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "clips", "clip_plan_id", "INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL")
            self._ensure_column(
                conn,
                "subtitle_profiles",
                "timing_offset_sec",
                "REAL NOT NULL DEFAULT 0",
            )
            self._ensure_column(conn, "subtitle_profiles", "outline_width", "REAL NOT NULL DEFAULT 5")
            self._ensure_column(conn, "subtitle_profiles", "shadow", "REAL NOT NULL DEFAULT 1")
            self._reset_legacy_gemini_subtitle_offset(conn)
            self._seed_default_prompt_presets(conn)

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> bool:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
            return True
        return False

    def _reset_legacy_gemini_subtitle_offset(self, conn: sqlite3.Connection) -> None:
        """Drop the legacy +0.35s Gemini subtitle nudge.

        Older builds shifted every Gemini subtitle 0.35s later to paper over
        early word timestamps. The reworked timeline/ASS engine syncs honestly,
        so that global nudge now reads as "subtitles arrive late". Reset profiles
        that still carry the exact legacy value, once, leaving any value the user
        tuned by hand untouched.
        """

        flag_key = "subtitle_legacy_offset_reset_v1"
        row = conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (flag_key,)).fetchone()
        if row is None:
            conn.execute(
                "UPDATE subtitle_profiles SET timing_offset_sec = 0 "
                "WHERE provider = 'gemini' AND ABS(timing_offset_sec - 0.35) < 0.001"
            )
            conn.execute(
                "INSERT INTO app_settings (key, value_json, encrypted) VALUES (?, ?, 0) "
                "ON CONFLICT(key) DO NOTHING",
                (flag_key, json.dumps(True)),
            )

    def _seed_default_prompt_presets(self, conn: sqlite3.Connection) -> None:
        seed_row = conn.execute(
            "SELECT value_json, updated_at FROM app_settings WHERE key = ?",
            (DEFAULT_PROMPT_SEED_KEY,),
        ).fetchone()
        seed_is_current = bool(seed_row and _json_value(seed_row["value_json"]) == DEFAULT_PROMPT_SEED_VERSION)
        seed_updated_at = str(seed_row["updated_at"] or "") if seed_row else ""

        existing_rows = {
            (row["task"], row["label"]): row
            for row in conn.execute("SELECT id, task, label, prompt, updated_at FROM prompt_presets")
        }
        task_counts = {
            row["task"]: int(row["count"])
            for row in conn.execute("SELECT task, COUNT(*) AS count FROM prompt_presets GROUP BY task")
        }
        for preset in DEFAULT_PROMPT_PRESETS:
            task = str(preset["task"])
            label = str(preset["label"])
            key = (task, label)
            existing = existing_rows.get(key)
            if existing:
                legacy_prompt = LEGACY_DEFAULT_PROMPTS.get(key)
                if str(existing["prompt"] or "").strip() == str(preset["prompt"]).strip():
                    continue
                if _should_update_seeded_prompt(existing, legacy_prompt, seed_updated_at):
                    conn.execute(
                        """
                        UPDATE prompt_presets
                        SET prompt = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (str(preset["prompt"]), int(existing["id"])),
                    )
                continue
            if seed_is_current:
                continue
            is_default = bool(preset.get("is_default")) and task_counts.get(task, 0) == 0
            conn.execute(
                """
                INSERT INTO prompt_presets (task, label, prompt, is_default)
                VALUES (?, ?, ?, ?)
                """,
                (task, label, str(preset["prompt"]), int(is_default)),
            )
            task_counts[task] = task_counts.get(task, 0) + 1

        conn.execute(
            """
            INSERT INTO app_settings (key, value_json, encrypted)
            VALUES (?, ?, 0)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                encrypted = 0,
                updated_at = CURRENT_TIMESTAMP
            """,
            (DEFAULT_PROMPT_SEED_KEY, json.dumps(DEFAULT_PROMPT_SEED_VERSION)),
        )

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock, self.connect() as conn:
            cur = conn.execute(sql, tuple(params))
            conn.commit()
            return cur

    def query_one(self, sql: str, params: Iterable = ()) -> sqlite3.Row | None:
        with self._lock, self.connect() as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return conn.execute(sql, tuple(params)).fetchall()


def _json_value(value_json: str):
    try:
        return json.loads(value_json)
    except (TypeError, json.JSONDecodeError):
        return None


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
