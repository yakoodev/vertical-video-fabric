"""Test isolation: tests must never touch the real data directory.

``app.main`` builds its store at import time from ``settings.db_path``, which
comes from ``DATA_DIR``. Inside the container ``DATA_DIR=/data`` — the working
database. ``test_core_api_flow`` repointed ``settings`` AFTER another test had
already imported ``app.main``, so it wrote straight into ``/data/app.sqlite``:
19 phantom "Lofi loop" audio tracks pointing at ``/tmp/pytest-…`` (they broke
renders), a fake YouTube account, 20 fake sources and 57 publishing jobs.

So: point ``DATA_DIR`` at a throwaway directory before anything imports
``app.*`` (conftest is loaded before the test modules), and refuse to run if
the settings still point anywhere else.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TEST_DATA = tempfile.mkdtemp(prefix="vvf-tests-")
os.environ["DATA_DIR"] = _TEST_DATA
os.environ.setdefault("POSTING_PROVIDER_MODE", "mock")


@pytest.fixture(autouse=True, scope="session")
def _refuse_real_data_dir():
    from app.settings import settings

    real = os.path.realpath(_TEST_DATA)
    if not os.path.realpath(str(settings.db_path)).startswith(real):
        pytest.exit(f"tests would write to a real database: {settings.db_path}", returncode=3)
    yield
