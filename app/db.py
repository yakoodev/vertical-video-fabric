from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from functools import wraps
from typing import Any, Callable, Iterable, Iterator

from app.db_prompt_fingerprints import _should_update_seeded_prompt
from app.default_prompts import (
    DEFAULT_PROMPT_PRESETS,
    DEFAULT_PROMPT_SEED_KEY,
    DEFAULT_PROMPT_SEED_VERSION,
    LEGACY_DEFAULT_PROMPTS,
)


_log = logging.getLogger(__name__)

# Сколько запись ждёт чужую запись (другой процесс, скрипт из tools/), прежде чем «database is locked».
BUSY_TIMEOUT_SEC = 30.0
# WAL держит общую память в mmap и полагается на блокировки файлов: через границу ядра (9p в Docker Desktop,
# сетевые диски) это ненадёжно и может испортить базу. Там остаёмся в обычном журнале, чтение и так без замка.
_NO_WAL_FILESYSTEMS = {"9p", "nfs", "nfs4", "cifs", "smb3", "smbfs", "virtiofs", "fuse", "drvfs", "vboxsf", "afs"}


def atomic(fn: Callable) -> Callable:
    """Функция хранилища целиком — одна транзакция. Первый аргумент — ``store`` или ``self`` с полем ``db``."""

    @wraps(fn)
    def run(store: Any, *args: Any, **kwargs: Any) -> Any:
        with store.db.transaction():
            return fn(store, *args, **kwargs)

    return run


def _filesystem_of(path: Path) -> str:
    """Тип файловой системы базы; не Linux — пусто."""
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _mount_fstype(str(Path(path).resolve()), mounts)


def _mount_fstype(target: str, mounts: str) -> str:
    """Самая длинная точка монтирования из таблицы /proc/mounts, которая содержит ``target``."""
    best, fstype = -1, ""
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point = parts[1].replace("\\040", " ")
        if (target == point or target.startswith(point.rstrip("/") + "/")) and len(point) > best:
            best, fstype = len(point), parts[2]
    return fstype


class Database:
    """Чтение идёт каждое на своём соединении и ничего не ждёт. ``_lock`` — замок записи: одна запись за раз
    в этом процессе (другие процессы ждут ``BUSY_TIMEOUT_SEC`` на уровне SQLite). Несколько записей, которые
    должны пройти целиком, — :meth:`transaction`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._local = threading.local()
        self._wal = False

    def connect(self) -> sqlite3.Connection:
        """Новое соединение. Писать через него внутри :meth:`transaction` нельзя: оно будет ждать саму транзакцию."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SEC, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self._wal:
            conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _enable_wal(self) -> None:
        """Режим WAL (хранится в самом файле базы). Копировать живую базу теперь только ``python -m app.db_backup``."""
        fstype = _filesystem_of(self.path)
        if fstype in _NO_WAL_FILESYSTEMS:
            _log.warning("база на %s: WAL небезопасен, остаётся обычный журнал", fstype)
            return
        conn = self.connect()
        try:
            self._wal = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() == "wal"
        except sqlite3.OperationalError as exc:  # файл занят другим процессом: включится при следующем старте
            _log.warning("WAL не включён: %s", exc)
        finally:
            conn.close()

    def init(self) -> None:
        self._enable_wal()
        with self._lock, closing(self.connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    label TEXT NOT NULL,
                    encrypted_cookies TEXT NOT NULL,
                    encrypted_proxy_url TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
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
            # UA браузера, из которого сняты cookie (расширение browser-extension/); пусто — TIKTOK_USER_AGENT.
            self._ensure_column(conn, "accounts", "user_agent", "TEXT NOT NULL DEFAULT ''")
            # Ник аккаунта на площадке (TikTok — без @, маленькими): сервис узнаёт его сам в TikTok Studio
            # и по нему находит аккаунт видео, выложенного руками (app/tiktok_studio_collect.py).
            self._ensure_column(conn, "accounts", "handle", "TEXT NOT NULL DEFAULT ''")
            # Живость входа (app/session_keepalive.py, app/session_health.py). cookies_set_at — когда владелец
            # сам прислал cookie (не ротация): по нему профиль браузера аккаунта засевается заново.
            # keepalive: NULL — по умолчанию (включено, если у аккаунта свой прокси), 1 — вкл, 0 — выкл.
            # session_state: '' (не проверяли) / ok / needs_reauth / captcha / error; session_alerted — о каком
            # состоянии уже написали в Telegram (одно сообщение на случай).
            self._ensure_column(conn, "accounts", "cookies_set_at", "TEXT")
            self._ensure_column(conn, "accounts", "keepalive", "INTEGER")
            self._ensure_column(conn, "accounts", "keepalive_at", "TEXT")
            self._ensure_column(conn, "accounts", "keepalive_next_at", "TEXT")
            self._ensure_column(conn, "accounts", "session_state", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "session_reason", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "accounts", "session_checked_at", "TEXT")
            self._ensure_column(conn, "accounts", "session_alerted", "TEXT NOT NULL DEFAULT ''")
            # Запрос поста ушёл, а ответа нет: ролик мог выйти (app/store_jobs.py не даст повторить молча).
            self._ensure_column(conn, "job_targets", "maybe_posted", "INTEGER NOT NULL DEFAULT 0")
            # Content crop rect (JSON {x,y,w,h} normalized 0..1) to strip letter/pillarbox bars.
            self._ensure_column(conn, "sources", "content_crop", "TEXT NOT NULL DEFAULT ''")
            # Chroma key of a green-screen source (JSON {color, similarity, blend}), app/chroma_key.py.
            self._ensure_column(conn, "sources", "chroma_key", "TEXT NOT NULL DEFAULT ''")
            # Звуковые дорожки исходника для звука клипов (JSON [0] / [0,1]; '' — первая), app/source_audio.py.
            self._ensure_column(conn, "sources", "audio_tracks", "TEXT NOT NULL DEFAULT ''")
            # Дорожки для распознавания речи (JSON [1]; '' — та же смесь, что для рендера), app/source_audio.py.
            self._ensure_column(conn, "sources", "speech_tracks", "TEXT NOT NULL DEFAULT ''")
            # Live progress of a running analysis: {"stage", "done", "total"} — drives
            # the step list on the Source tab instead of a fake percentage.
            self._ensure_column(conn, "ai_analyses", "progress_json", "TEXT NOT NULL DEFAULT '{}'")
            # Cached Whisper transcript (JSON [{start,end,text}]) so analysis does not
            # re-transcribe the same source every run.
            self._ensure_column(conn, "sources", "transcript_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "sources", "transcript_model", "TEXT NOT NULL DEFAULT ''")
            # Autofocus tuning per source (talking / animation / action / static…).
            self._ensure_column(conn, "sources", "focus_preset", "TEXT NOT NULL DEFAULT ''")
            # Global autofocus strategy (shot / follow / center / auto).
            self._ensure_column(conn, "sources", "focus_strategy", "TEXT NOT NULL DEFAULT ''")
            # Вебка стримера (JSON {x,y,w,h} в долях кадра) — раскладки split/pip_cam
            # (app/stream_layout.py); новые клипы источника берут её по умолчанию.
            self._ensure_column(conn, "sources", "facecam_json", "TEXT NOT NULL DEFAULT ''")
            # Cut-refinement hypothesis + cached scene-cut times for it.
            self._ensure_column(conn, "sources", "cut_strategy", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "sources", "scene_cuts_json", "TEXT NOT NULL DEFAULT ''")
            # Raw (pre-refinement) LLM boundaries, so a strategy can be re-applied
            # without re-running the analysis.
            self._ensure_column(conn, "ai_segments", "raw_start_sec", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ai_segments", "raw_end_sec", "REAL NOT NULL DEFAULT 0")
            # Point-of-interest focus track for dynamic reframing (JSON [{t,x,y}]).
            self._ensure_column(conn, "ai_segments", "focus_json", "TEXT NOT NULL DEFAULT '[]'")
            # Optional per-clip manual horizontal frame position (full-frame 0..1);
            # NULL = use autofocus. Overrides the focus track with a fixed frame.
            self._ensure_column(conn, "ai_segments", "manual_focus_x", "REAL")
            # «Вписать шире окна 9:16» для одного куска (app/video_crop.normalize_fit): JSON {w, bg} или ''.
            self._ensure_column(conn, "ai_segments", "fit_json", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "jobs", "clip_id", "INTEGER REFERENCES clips(id)")
            self._ensure_column(conn, "jobs", "scheduled_at", "TEXT")
            # Кто поставил публикацию: 'agent' — настройка проекта «Публиковать самому» (app/agent_settings.py).
            self._ensure_column(conn, "jobs", "made_by", "TEXT NOT NULL DEFAULT ''")
            # Секунда кадра-обложки TikTok (app/providers/tiktok.py); NULL — кадр TikTok по умолчанию.
            self._ensure_column(conn, "jobs", "cover_sec", "REAL")
            self._ensure_column(conn, "ffmpeg_presets", "audio_mix_mode", "TEXT NOT NULL DEFAULT 'primary'")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_stream", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "ffmpeg_presets", "audio_primary_volume", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_stream", "INTEGER")
            self._ensure_column(conn, "ffmpeg_presets", "audio_secondary_volume", "REAL NOT NULL DEFAULT 1")
            self._ensure_column(conn, "audio_tracks", "volume", "REAL NOT NULL DEFAULT 0.25")
            # Где у трека энергия (app/music_energy.py): старт громкой части, дроп, темп;
            # energy_version 0 — ещё не размечен (разметится лениво при первом запросе).
            self._ensure_column(conn, "audio_tracks", "energy_start_sec", "REAL")
            self._ensure_column(conn, "audio_tracks", "drop_sec", "REAL")
            self._ensure_column(conn, "audio_tracks", "bpm", "REAL")
            self._ensure_column(conn, "audio_tracks", "energy_version", "INTEGER NOT NULL DEFAULT 0")
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
            # Dynamic point-of-interest reframing for wide → vertical (needs focus data).
            self._ensure_column(conn, "ffmpeg_presets", "smart_reframe", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "clips", "clip_plan_id", "INTEGER REFERENCES clip_plans(id) ON DELETE SET NULL")
            # Triage flags: ⭐ favourite (promote to the clip editor) and hidden
            # (dismiss a weak candidate from the moments list).
            self._ensure_column(conn, "clip_plans", "favorite", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "clip_plans", "hidden", "INTEGER NOT NULL DEFAULT 0")
            # Черновик владелец взял в работу (app/agent_settings.py): звёздочка агента этого не значит.
            self._ensure_column(conn, "clip_plans", "owner_picked", "INTEGER NOT NULL DEFAULT 0")
            # Per-clip render settings (subs, banner, music, mirror, transitions…):
            # each clip is set up on its own in the editor, see app/clip_settings.py.
            self._ensure_column(conn, "clip_plans", "render_settings_json", "TEXT NOT NULL DEFAULT '{}'")
            # The clip file as it was BEFORE the first 🤖 ИИ-монтаж — «↩ Откатить».
            self._ensure_column(conn, "clip_plans", "montage_backup_json", "TEXT NOT NULL DEFAULT ''")
            # ✨ ИИ выбирает лучшие: {"score", "reason"} of an AI pick, shown on the card.
            self._ensure_column(conn, "clip_plans", "ai_pick_json", "TEXT NOT NULL DEFAULT ''")
            # Субтитры клипа, сделанные ДО рендера и правленные в редакторе.
            self._ensure_column(conn, "clip_plans", "subtitles_json", "TEXT NOT NULL DEFAULT ''")
            # Стили с провайдером «mock» жгли текст-заглушку вместо распознанной речи.
            conn.execute("UPDATE subtitle_profiles SET provider = '' WHERE provider = 'mock'")
            # Шаг назад по правке клипа: снимок состояния ПЕРЕД изменением.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clip_plan_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_plan_id INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_clip_plan_history ON clip_plan_history(clip_plan_id, id)"
            )
            # Шаг вперёд (Ctrl+Shift+Z): что отменили и отпечаток клипа сразу после отмены (app/clip_history.py).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clip_plan_redo (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_plan_id INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    after_undo TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_plan_redo ON clip_plan_redo(clip_plan_id, id)")
            # Что уже залито в Gemini Files API: файл живёт там 48 часов и его
            # можно переиспользовать — повторный анализ не гоняет гигабайты заново.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gemini_files (
                    local_path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # «Файлы для монтажа»: memes / reactions / sounds the AI may drop into clips.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS montage_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL,
                    original_filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    duration_sec REAL NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    has_audio INTEGER NOT NULL DEFAULT 0,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # 💬 Чат с ИИ-монтажёром в редакторе: разговор продолжается между
            # заходами, а шаги (вызовы инструментов) видны под ответом.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clip_agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_plan_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'done',
                    run_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_clip_agent_messages ON clip_agent_messages(clip_plan_id, id)"
            )
            # Брендовый шаблон: шрифт, цвета, логотип, концовка-призыв, музыка заказчика —
            # повторяющиеся ролики одного бренда в одном стиле (app/brand_kit.py).
            # Всё оформление — одним JSON: форму держит нормализация, а не схема таблицы.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS brands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kit_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # 💬 Чат Twitch-VOD источника (source_chat — статус импорта и карты
            # смайлов/значков, chat_messages — сами сообщения), см. app/twitch_chat.py.
            from app.twitch_chat import ensure_chat_schema

            ensure_chat_schema(conn)
            # Банк голосов: пары «голос → человек» (app/voice_bank.py).
            from app.voice_bank import ensure_voice_schema

            ensure_voice_schema(conn)
            # Проекты (серии и каналы): бриф, правила владельца, ручные публикации,
            # теги клипов, гипотезы + sources.project_id (app/projects.py).
            from app.projects import ensure_projects_schema

            ensure_projects_schema(conn)
            # Автопилот канала: очередь одобрения, лента, расписание, Telegram, чат проекта (app/autopilot.py).
            from app.autopilot import ensure_autopilot_schema

            ensure_autopilot_schema(conn)
            # ИИ-видео: стиль-библия, персонажи, серии и планы, версии картинок, запуски
            # генерации и журнал трат (app/aivideo.py).
            from app.aivideo import ensure_aiv_schema

            ensure_aiv_schema(conn)
            # Объяснялки: маскоты, библиотека поз, ролики «тема → готовый клип» (app/explainer.py).
            from app.explainer import ensure_explainer_schema

            ensure_explainer_schema(conn)
            # Канал объяснялок: маскот, очередь тем, производство к слотам (app/explainer_channel.py).
            from app.explainer_channel import ensure_channel_schema

            ensure_channel_schema(conn)
            # Тревога «0 просмотров» через N часов после публикации (app/zero_views.py).
            from app.zero_views import ensure_schema as ensure_zero_views_schema

            ensure_zero_views_schema(conn)
            # Истории: мир (стиль, рассказчик), актёры, локации, библиотека сцен, эпизоды (app/stories.py).
            from app.stories import ensure_stories_schema

            ensure_stories_schema(conn)
            # Очередь 3D-рендера для хост-воркера (Blender на компьютере с GPU, app/render_queue.py).
            from app.render_queue import ensure_render_queue_schema

            ensure_render_queue_schema(conn)
            # 3D-серия в сервисе: библиотека проекта, серии с версиями плана, задания рендера (app/s3d_store.py).
            from app.s3d_store import ensure_schema as ensure_series3d_schema

            ensure_series3d_schema(conn)
            # Варианты обложек готовых клипов (app/cover_variants.py).
            from app.cover_variants import ensure_cover_variants_schema

            ensure_cover_variants_schema(conn)
            # Документы обложек, собранных руками, и ссылки на редактирование (app/cover_docs.py)
            from app.cover_docs import ensure_cover_docs_schema

            ensure_cover_docs_schema(conn)
            # Вопросы владельцу по стриму и выбор мемов (app/stream_questions.py).
            from app.stream_questions import ensure_stream_questions_schema

            ensure_stream_questions_schema(conn)
            # Снимки страниц сайтов и их версии с правками через HTML (app/webpages.py).
            from app.webpages import ensure_web_pages_schema

            ensure_web_pages_schema(conn)
            # Траты Gemini: каждый запрос к модели с токенами и ценой (app/gemini_usage.py).
            from app.gemini_usage import ensure_schema as ensure_gemini_usage_schema

            ensure_gemini_usage_schema(conn)
            # Статистика опубликованных клипов (app/platform_stats.py): снимки чисел
            # площадки по времени — обратная связь «что заходит» для монтажа.
            # error — снимок не удался (причина для UI), числа тогда NULL.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS publication_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL REFERENCES job_targets(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    views INTEGER,
                    likes INTEGER,
                    comments INTEGER,
                    shares INTEGER,
                    avg_retention REAL,
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_publication_stats ON publication_stats(target_id, id)"
            )
            # Аналитика TikTok Studio: удержание, досмотры, источники (app/tiktok_studio.py).
            from app.tiktok_studio import ensure_schema as ensure_tiktok_studio_schema

            ensure_tiktok_studio_schema(conn)
            # Free-form notes of the clip file (why it is cut this way) — for the
            # human and for outside agents working through the clip file API.
            self._ensure_column(conn, "clip_plans", "notes", "TEXT NOT NULL DEFAULT ''")
            # Quality report of a finished render (black/frozen frames, silence,
            # loudness) — see app/render_qc.py.
            self._ensure_column(conn, "clips", "qc_json", "TEXT")
            # Cover of the rendered clip (a still) and why it failed, if it did.
            self._ensure_column(conn, "clips", "cover_path", "TEXT")
            self._ensure_column(conn, "clips", "cover_error", "TEXT")
            # Метаданные публикации клипа (app/ai/publish_meta.py): варианты площадок,
            # тексты обложки, откуда взялись (auto после рендера / ai / manual).
            self._ensure_column(conn, "clips", "publish_meta_json", "TEXT")
            # Отпечаток состояния клипа на момент рендера (app/quality_lint.plan_fingerprint):
            # lint_clip честно говорит «клип правили после рендера», а не по времени статуса.
            self._ensure_column(conn, "clips", "plan_fingerprint", "TEXT")
            # Клип = группа планов одного момента и формата (app/clip_groups.py). NULL — план сам себе клип.
            self._ensure_column(conn, "clip_plans", "clip_group_id", "INTEGER")
            # Владелец отделил план руками: скрипт группировки и автопривязка его не трогают.
            self._ensure_column(conn, "clip_plans", "group_locked", "INTEGER NOT NULL DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_plans_group ON clip_plans(clip_group_id)")
            # Вечные снимки монтажа: у каждого нового рендера и перед откатом (история правок — всего 20 шагов).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clip_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clip_plan_id INTEGER NOT NULL REFERENCES clip_plans(id) ON DELETE CASCADE,
                    clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL DEFAULT 'render',
                    label TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_versions_plan ON clip_versions(clip_plan_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_versions_clip ON clip_versions(clip_id)")
            self._ensure_column(
                conn,
                "subtitle_profiles",
                "timing_offset_sec",
                "REAL NOT NULL DEFAULT 0",
            )
            # Проверка вставки Gemini-зрением «реальный человек на фото/видео» (app/ai/people_check.py), JSON.
            self._ensure_column(conn, "montage_assets", "people_check", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "subtitle_profiles", "outline_width", "REAL NOT NULL DEFAULT 5")
            self._ensure_column(conn, "subtitle_profiles", "shadow", "REAL NOT NULL DEFAULT 1")
            self._reset_legacy_gemini_subtitle_offset(conn)
            self._seed_default_prompt_presets(conn)
            # Ничейные холсты — в выведенные проекты, один раз (app/canvas_projects.py: только добавляет приписки).
            from app.canvas_projects import migrate_canvas_projects

            migrate_canvas_projects(conn)

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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Всё внутри проходит целиком или никак. ``execute``/``query_*`` этого потока сами идут в транзакцию, так что
        обычные методы хранилища можно звать как есть. Вложенная — точка сохранения: её ошибка, пойманная снаружи,
        откатывает только её. Читатели в других потоках не ждут и видят базу до коммита; ждут только записи, поэтому
        внутри не должно быть сети, долгой работы и ожидания других потоков."""
        conn = self._tx()
        if conn is not None:
            self._local.depth += 1
            name = f"tx_{self._local.depth}"
            conn.execute(f"SAVEPOINT {name}")
            try:
                yield conn
                conn.execute(f"RELEASE {name}")
            except BaseException:
                conn.execute(f"ROLLBACK TO {name}")
                conn.execute(f"RELEASE {name}")
                raise
            finally:
                self._local.depth -= 1
            return
        with self._lock:
            conn = self.connect()
            try:
                # Деньги, решения и удаления переживают и падение машины, не только процесса.
                conn.execute("PRAGMA synchronous = FULL")
                conn.execute("BEGIN IMMEDIATE")
                self._local.conn, self._local.depth, self._local.after = conn, 0, []
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    self._local.conn = None
            finally:
                conn.close()
        for callback in self._local.after:
            callback()

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Выполнить после коммита текущей транзакции (разбудить работника, чтобы он уже увидел строки); без неё — сразу."""
        if self._tx() is None:
            callback()
        else:
            self._local.after.append(callback)

    def _tx(self) -> sqlite3.Connection | None:
        return getattr(self._local, "conn", None)

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        tx = self._tx()
        if tx is not None:
            return tx.execute(sql, tuple(params))
        with self._lock:
            conn = self.connect()
            try:
                cur = conn.execute(sql, tuple(params))
                conn.commit()
                return cur  # lastrowid и rowcount живут и после закрытия
            finally:
                conn.close()  # сам не закроется: соединение держит цикл ссылок до сборщика мусора

    def query_one(self, sql: str, params: Iterable = ()) -> sqlite3.Row | None:
        tx = self._tx()
        if tx is not None:
            return tx.execute(sql, tuple(params)).fetchone()
        conn = self.connect()
        try:
            return conn.execute(sql, tuple(params)).fetchone()
        finally:
            conn.close()

    def query_all(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        tx = self._tx()
        if tx is not None:
            return tx.execute(sql, tuple(params)).fetchall()
        conn = self.connect()
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()


def _json_value(value_json: str):
    try:
        return json.loads(value_json)
    except (TypeError, json.JSONDecodeError):
        return None
