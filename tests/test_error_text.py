"""Ошибки простыми словами (app/error_text.py): сырой текст из базы → что случилось и что сделать."""

from __future__ import annotations

import pytest

from app import error_text
from app.store_common import (PUBLISH_FILE_CHANGED_ERROR, PUBLISH_FILE_MISSING_ERROR, PUBLISH_INTERRUPTED_ERROR,
                              PUBLISH_NOT_STARTED_ERROR, RENDER_INTERRUPTED_ERROR)

# Настоящие тексты из базы (17.11) и из кода площадок.
CASES = [
    ("канал UCk6xxxxxxxxxxxxxxxxxxxx не найден у этого входа (есть: ни одного)", "youtube_logged_out"),
    ("канал UCk6xxxxxxxxxxxxxxxxxxxx не найден у этого входа (есть: Другой канал (UCzz))", "youtube_channel_missing"),
    ("активен другой канал: Мой (UC1), а нужен Второй (UC2)", "youtube_wrong_channel"),
    ("канал YouTube не задан", "youtube_channel_unset"),
    ("Нужны свежие cookie: Error: Request to https://upload.youtube.com//upload/youtubei?prettyPrint=false&alt=json "
     "failed with status code 401\n    at HTTPClient.fetch (file:///app/node_modules/youtubei.js/dist/src/utils/"
     "HTTPClient.js:1:1)", "needs_reauth"),
    ("YouTube отвечает без входа — Google выкинул из аккаунта", "needs_reauth"),
    ("TikTok sessionid cookie is missing", "needs_reauth"),
    ("TikTok auth failed (HTTP 200): session expired", "needs_reauth"),
    ("TikTok просит войти заново: обновите cookie аккаунта", "needs_reauth"),
    ("TikTok upload auth failed (HTTP 500)", "tiktok_upload"),
    ("TikTok chunk upload failed at part 3 (HTTP 500)", "tiktok_upload"),
    ("TikTok upload commit failed (HTTP 200): {}", "tiktok_upload"),
    ("TikTok signature helper timed out after 60 s", "tiktok_signature"),
    ("Связь оборвалась на самом посте, ролик мог выйти: проверьте на TikTok, прежде чем публиковать снова. "
     "ReadTimeout", "maybe_posted"),
    ("Нет связи через прокси http://***:***@1.2.3.4:8080: ролик не отправлен, напрямую не пробовали. ProxyError: x",
     "proxy_down"),
    ("Нет связи с TikTok: ролик не отправлен. ConnectTimeout: timed out", "network"),
    ("youtube upload timed out", "network"),
    ("ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:1000)", "network"),
    ("ERROR: [TikTok] 7: Unable to download webpage: HTTP Error 502: Bad Gateway", "network"),
    ("ERROR: [TikTok] 7: Your IP address is blocked from accessing this post", "ip_blocked"),
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. This helps protect our community.", "bot_check"),
    ("quotaExceeded: The request cannot be completed because you have exceeded your quota.", "quota"),
    ("TikTok publish failed (HTTP 429): Too Many Requests", "quota"),
    ("The user has exceeded the number of videos they may upload.", "quota"),
    ("upload exceeds MAX_UPLOAD_BYTES", "too_large"),
    ("TikTok chunk upload failed at part 9 (HTTP 413): Request Entity Too Large", "too_large"),
    ("/data/sources/a.mp4: Invalid data found when processing input", "bad_file"),
    ("moov atom not found", "bad_file"),
    ("[Parsed_xfade_3 @ 0x1] First input link main timebase (1/30) do not match the corresponding second input link "
     "xfade timebase (1/25)\nError reinitializing filters!", "render_transition"),
    ("[Parsed_concat_2 @ 0x1] Input link in1:v0 parameters (size 1080x1920, SAR 1:1) do not match the corresponding "
     "output link in0:v0 parameters (720x1280, SAR 1:1)", "render_concat"),
    ("[Parsed_crop_1 @ 0x1] [Eval @ 0x2] Missing ')' or too many args in 'if(lt(t,1)", "render_reframe"),
    ("громкость не выровнена: [Parsed_aresample_0 @ 0x1] Cannot select channel layout for the link", "render_audio_layout"),
    ("слой st_fx1: sfx:kenney-impact/impactMetal_medium_000.ogg не найден — пропущен", "sfx_missing"),
    ("ffmpeg failed", "ffmpeg"),
    ("RuntimeError: Gemini generateContent failed: User location is not supported for the API use.", "gemini_location"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "video_removed"),
    ("Все провайдеры модели \"minimax/hailuo-2-max\" вернули ошибку. Повторите запрос позже.", "generator_down"),
    (PUBLISH_INTERRUPTED_ERROR, "publish_interrupted"),
    (PUBLISH_NOT_STARTED_ERROR, "publish_not_started"),
    (PUBLISH_FILE_MISSING_ERROR, "publish_file_missing"),
    (PUBLISH_FILE_CHANGED_ERROR, "publish_file_changed"),
    (RENDER_INTERRUPTED_ERROR, "render_interrupted"),
    ("analysis was interrupted by service restart", "analysis_interrupted"),
]


@pytest.mark.parametrize(("raw", "key"), CASES)
def test_known_errors(raw: str, key: str) -> None:
    view = error_text.explain(raw)
    assert view is not None, raw
    assert view["key"] == key
    assert view["text"] and view["action"]
    # Простые слова: без сырых кусков ответа.
    assert "http" not in view["text"].lower() and "error" not in view["text"].lower()


def test_youtube_logged_out_names_account_and_points_to_accounts() -> None:
    view = error_text.explain("канал UCk6 не найден у этого входа (есть: ни одного)", platform="youtube",
                              account="vtubika.store")
    assert view == {
        "key": "youtube_logged_out",
        "text": "Слетел вход в YouTube для аккаунта «vtubika.store»",
        "action": "обнови куки в «Публикации и аккаунты → Аккаунты»",
        "to": "/pubs?tab=accounts",
    }


def test_platform_from_context_or_text() -> None:
    assert error_text.explain("session expired", platform="tiktok")["text"] == "Слетел вход в TikTok"
    assert error_text.explain("TikTok sessionid cookie is missing")["text"] == "Слетел вход в TikTok"
    assert error_text.explain("ReadTimeout: read timed out")["text"] == "Не было связи с площадкой"


def test_unknown_error_stays_raw_unless_reauth_status() -> None:
    assert error_text.explain("что-то совсем новое") is None
    assert error_text.explain("") is None
    assert error_text.explain(None) is None
    view = error_text.explain("что-то совсем новое", platform="youtube", account="A", status="needs_reauth")
    assert view["key"] == "needs_reauth" and view["text"] == "Слетел вход в YouTube для аккаунта «A»"


def test_explain_job_takes_platform_and_account_from_failed_target() -> None:
    job = {"status": "needs_reauth", "error": "Нужны свежие cookie: канал UC1 не найден у этого входа (есть: ни одного)"}
    targets = [
        {"status": "succeeded", "error": "", "platform": "tiktok", "account_label": "tt"},
        {"status": "needs_reauth", "error": "канал UC1 не найден у этого входа (есть: ни одного)",
         "platform": "youtube", "account_label": "Мура"},
    ]
    view = error_text.explain_job(job, targets)
    assert view["key"] == "youtube_logged_out"
    assert view["text"] == "Слетел вход в YouTube для аккаунта «Мура»"


RAW_JOB = "Нужны свежие cookie: канал UC1 не найден у этого входа (есть: ни одного)"
RAW_TARGET = "канал UC1 не найден у этого входа (есть: ни одного)"


def _seed_failed_job(store) -> int:
    account = store.db.execute(
        "INSERT INTO accounts (platform, label, encrypted_cookies) VALUES ('youtube', 'Мура', 'x')").lastrowid
    job_id = store.db.execute(
        "INSERT INTO jobs (status, title, source_filename, source_path, source_sha256, source_size_bytes, error) "
        "VALUES ('needs_reauth', 'Ролик', 'a.mp4', '/a.mp4', '', 1, ?)", (RAW_JOB,)).lastrowid
    store.db.execute("INSERT INTO job_targets (job_id, account_id, platform, status, error) "
                     "VALUES (?, ?, 'youtube', 'needs_reauth', ?)", (job_id, account, RAW_TARGET))
    return int(job_id)


def test_job_api_home_and_tasks_carry_error_view(tmp_path, monkeypatch) -> None:
    """Публикация в API, на «Главной» и в очереди задач: error как был, рядом error_view."""
    from app import home
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(home, "_module_on", lambda _store, _module: True)
    job_id = _seed_failed_job(store)
    human = "Слетел вход в YouTube для аккаунта «Мура»"

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["error"] == RAW_JOB
    assert job["error_view"]["text"] == human and job["error_view"]["to"] == "/pubs?tab=accounts"
    assert job["targets"][0]["error"] == RAW_TARGET
    assert job["targets"][0]["error_view"]["key"] == "youtube_logged_out"
    assert client.get("/api/jobs").json()[0]["error_view"]["text"] == human

    failed = client.get("/api/home").json()["waiting"]["failed"]["items"]
    row = next(i for i in failed if i["kind"] == "job")
    assert row["error"] == RAW_JOB and row["error_view"]["text"] == human

    task = next(t for t in client.get("/api/tasks").json() if t["kind"] == "job")
    assert task["error"] == RAW_JOB and task["error_view"]["text"] == human
