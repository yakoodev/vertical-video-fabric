from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Iterable


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

                CREATE TABLE IF NOT EXISTS subtitle_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'mock',
                    model TEXT NOT NULL DEFAULT 'openai/gpt-4o-transcribe',
                    language TEXT NOT NULL DEFAULT '',
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

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
                CREATE INDEX IF NOT EXISTS idx_job_targets_job ON job_targets(job_id);
                CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status, id);
                CREATE INDEX IF NOT EXISTS idx_ai_analyses_source ON ai_analyses(source_id, id);
                CREATE INDEX IF NOT EXISTS idx_ai_segments_source ON ai_segments(source_id, start_sec);
                CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status, id);
                CREATE INDEX IF NOT EXISTS idx_clips_source ON clips(source_id, id);
                """
            )
            self._ensure_column(conn, "accounts", "encrypted_proxy_url", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "jobs", "clip_id", "INTEGER REFERENCES clips(id)")
            self._ensure_column(conn, "ffmpeg_presets", "audio_mix_mode", "TEXT NOT NULL DEFAULT 'primary'")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_stream", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_volume", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_stream", "INTEGER")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_volume", "REAL NOT NULL DEFAULT 1")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

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
