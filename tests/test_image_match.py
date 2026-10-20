"""Найденная в интернете картинка проверяется глазами Gemini: мимо запроса — не берём."""

import subprocess

from app.ai.asset_generate import image_matches
from test_api_pipeline import _require_ffmpeg


class _Client:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.prompts = answer, fail, []

    def generate_content(self, model, payload):
        if self.fail:
            raise RuntimeError("сеть")
        self.prompts.append(payload["contents"][0]["parts"][0]["text"])
        return {"candidates": [{"content": {"parts": [{"text": self.answer}]}}]}


def test_image_match_rejects_only_explicit_no(tmp_path):
    _require_ffmpeg()
    img = tmp_path / "truck.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=yellow:s=640x360", "-frames:v", "1", str(img)], check=True)
    asset = {"file_path": str(img)}
    no = _Client('{"match": false}')
    assert image_matches(asset, "Олег Монгол", no) is False
    assert "Олег Монгол" in no.prompts[0]
    assert image_matches(asset, "Олег Монгол", _Client('{"match": true}')) is True
    assert image_matches(asset, "Олег Монгол", _Client(fail=True)) is True
    assert image_matches(asset, "Олег Монгол", _Client("мусор")) is True
