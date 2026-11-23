"""Мем-серии (tools/meme3d_*.py) рендерят сцены с библиотекой 3D-вещей.

22.11 мем 9: render.mjs запускался без --models и без SCENE3D_MODELS_DIR, искал <репозиторий>/data/runtime/models3d,
получал 404 на manifest.json — «библиотека моделей недоступна (HTTP 404)», вещи из примитивов. Теперь общий модуль
серий (meme3d_pilot) находит библиотеку и передаёт её рендеру через окружение. Импорт — в отдельном процессе:
модуль правит os.environ, а тестам сервиса это окружение не нужно."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ("import sys, os, json; sys.path[:0] = ['tools', '.']; import meme3d_pilot as P1; "
         "print(json.dumps({'models': str(P1.MODELS) if P1.MODELS else None, 'env': os.environ.get('SCENE3D_MODELS_DIR')}))")


def _probe(env: dict) -> dict:
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_library_with_manifest_reaches_render_env(tmp_path):
    lib = tmp_path / "models3d"
    lib.mkdir()
    (lib / "manifest.json").write_text('{"version": 1, "models": {}, "categories": {}}', encoding="utf-8")
    got = _probe({**os.environ, "SCENE3D_MODELS_DIR": str(lib)})
    assert got == {"models": str(lib), "env": str(lib)}


def test_dir_without_manifest_is_not_taken(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    got = _probe({**os.environ, "SCENE3D_MODELS_DIR": str(empty)})
    assert got["models"] != str(empty) and (got["models"] is None or got["env"] == got["models"])
