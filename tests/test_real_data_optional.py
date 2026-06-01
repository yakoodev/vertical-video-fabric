import os
from pathlib import Path

import pytest

from app.ingest import probe_media


@pytest.mark.skipif(
    not os.getenv("VVF_REAL_SOURCE_VIDEO"),
    reason="set VVF_REAL_SOURCE_VIDEO to run local real-video smoke test",
)
def test_real_source_video_probe_smoke():
    path = Path(os.environ["VVF_REAL_SOURCE_VIDEO"]).expanduser()
    assert path.exists()
    metadata = probe_media(path)
    assert metadata.duration_sec > 0
    assert metadata.width > 0
    assert metadata.height > 0
