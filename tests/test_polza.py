import json

import httpx
import pytest

from app.ai import polza as polza_module
from app.ai.polza import PolzaClient, PolzaVideoAnalyzer, _build_payload, _windows
from app.settings import settings


def _clip_response(start, end, title):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "clips": [
                                {
                                    "title": title,
                                    "description": "desc",
                                    "score": 0.9,
                                    "category": "экшн",
                                    "color": "#ef4444",
                                    "segments": [
                                        {
                                            "start_sec": start,
                                            "end_sec": end,
                                            "title": title,
                                            "description": "desc",
                                            "score": 0.9,
                                            "category": "экшн",
                                            "color": "#ef4444",
                                            "reason": "сильный момент",
                                        }
                                    ],
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }


def test_polza_analyzer_runs_windowed_vlm_and_merges(monkeypatch, tmp_path):
    media = tmp_path / "film.mp4"
    media.write_bytes(b"fake")

    # No real ffmpeg / Whisper: dense frames across ~20 minutes, no transcript.
    frames = [(float(t), b"jpeg-bytes") for t in range(0, 1200, 10)]
    monkeypatch.setattr(polza_module, "_extract_frames", lambda *a, **k: frames)
    monkeypatch.setattr(polza_module, "_transcribe", lambda *a, **k: [])
    monkeypatch.setattr(settings, "polza_window_seconds", 600)

    captured_windows = []

    class FakeClient:
        def chat_completion(self, payload):
            # Each call carries image parts → it is a true multimodal request.
            content = payload["messages"][1]["content"]
            images = [part for part in content if part.get("type") == "image_url"]
            assert images, "window request must include sampled frames"
            assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            # Use the first frame timestamp in this window as the clip start so each
            # window yields a distinct clip.
            first_ts = next(
                int(part["text"][3:-2])
                for part in content
                if part.get("type") == "text" and part["text"].startswith("[t=")
            )
            captured_windows.append(first_ts)
            return _clip_response(first_ts + 5, first_ts + 45, f"Момент {first_ts}")

    result = PolzaVideoAnalyzer(FakeClient()).analyze(
        {"local_path": str(media), "duration_sec": 1200},
        "Найди вирусные боевые моменты",
        "google/gemini-2.5-pro-preview",
    )

    # 1200s / 600s window = two windows, each producing one clip.
    assert len(captured_windows) == 2
    assert len(result.clips) == 2
    assert result.usage["frames"] == len(frames)
    assert all(seg.end_sec > seg.start_sec for seg in result.segments)


def test_polza_build_payload_is_multimodal_with_absolute_timestamps():
    frames = [(120.0, b"a"), (130.0, b"b")]
    transcript = [(118.0, 124.0, "первая реплика"), (900.0, 905.0, "вне окна")]
    payload = _build_payload(
        "Найди моменты",
        "google/gemini-2.5-pro-preview",
        100.0,
        200.0,
        10.0,
        frames,
        transcript,
    )

    assert payload["model"] == "google/gemini-2.5-pro-preview"
    assert payload["response_format"]["type"] == "json_schema"
    content = payload["messages"][1]["content"]
    text0 = content[0]["text"]
    # Window bounds are explicit and absolute; only in-window speech is included.
    assert "с 100 c по 200 c" in text0
    assert "первая реплика" in text0
    assert "вне окна" not in text0
    assert "по-русски" in text0
    images = [part for part in content if part.get("type") == "image_url"]
    assert len(images) == 2
    stamps = [part["text"] for part in content if part.get("type") == "text" and part["text"].startswith("[t=")]
    assert stamps == ["[t=120s]", "[t=130s]"]


def test_polza_windows_split_long_source():
    assert _windows(500, 600) == [(0.0, 600)]
    windows = _windows(1500, 600)
    assert len(windows) == 3
    assert windows[0][0] == 0.0
    assert windows[-1][1] == 1500


def test_polza_client_auth_error(monkeypatch):
    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise AssertionError("auth error should be handled before raise_for_status")

        def json(self):
            return {}

    monkeypatch.setattr("app.ai.polza.httpx.post", lambda *args, **kwargs: FakeResponse())
    client = PolzaClient(api_key="unit-test", base_url="https://polza.ai/api/v1")

    with pytest.raises(RuntimeError, match="Polza auth failed"):
        client.chat_completion({"model": "test", "messages": []})


def test_polza_client_retries_transient_network_error(monkeypatch):
    monkeypatch.setattr(settings, "external_http_retries", 2)
    monkeypatch.setattr(settings, "external_http_retry_seconds", 0)
    calls = {"count": 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{\"clips\": []}"}}]}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("temporary network failure")
        return FakeResponse()

    monkeypatch.setattr("app.ai.polza.httpx.post", fake_post)
    client = PolzaClient(api_key="unit-test", base_url="https://polza.ai/api/v1")

    response = client.chat_completion({"model": "test", "messages": []})

    assert response["choices"]
    assert calls["count"] == 2
