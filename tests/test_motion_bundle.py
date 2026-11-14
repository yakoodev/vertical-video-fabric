"""Локальный бандл компоновщика не устаревает молча (app/motion_bundle.py).

node/motion-render.mjs в git не лежит и локально собирается руками; однажды в нём не было fx
negate, и локальный рендер тихо рисовал другой эффект. Теперь исходники новее бандла —
пересборка локальным esbuild (той же командой, что npm run build:motion) или громкое
предупреждение."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

import pytest

from app import motion_bundle as MB

FAKE_ESBUILD = """#!/usr/bin/env node
// Заглушка esbuild: пишет --outfile и запоминает аргументы.
const fs = require("fs");
const out = process.argv.find(a => a.startsWith("--outfile=")).slice("--outfile=".length);
fs.writeFileSync(out, "// built\\n" + JSON.stringify(process.argv.slice(2)));
"""
FAILING_ESBUILD = """#!/usr/bin/env node
console.error("✘ [ERROR] Could not resolve './nope'"); process.exit(1);
"""


def _repo(tmp_path: Path, esbuild: str | None = FAKE_ESBUILD) -> Path:
    root = tmp_path / "repo"
    (root / "node" / "motion-render").mkdir(parents=True)
    (root / "frontend" / "src" / "motion" / "fx").mkdir(parents=True)
    (root / "node" / "motion-render.ts").write_text("import './motion-render/args';\n", encoding="utf-8")
    (root / "node" / "motion-render" / "args.ts").write_text("export {};\n", encoding="utf-8")
    (root / "frontend" / "src" / "motion" / "fx" / "negate.ts").write_text("export {};\n", encoding="utf-8")
    script = ("esbuild node/motion-render.ts --bundle --platform=node --format=esm --target=node20 "
              "--external:@napi-rs/canvas --outfile=node/motion-render.mjs --log-level=warning")
    (root / "package.json").write_text(json.dumps({"scripts": {"build:motion": script}}), encoding="utf-8")
    if esbuild is not None:
        bin_dir = root / "node_modules" / "esbuild" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "esbuild").write_text(esbuild, encoding="utf-8")
    return root


def _age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(MB, "CHECK_EVERY_SEC", 0.0)
    monkeypatch.setattr(MB, "_last", {"at": 0.0, "bundle": None, "failed": None})
    monkeypatch.delenv("MOTION_AUTO_BUILD", raising=False)


def test_stale_sources_by_mtime(tmp_path):
    root = _repo(tmp_path)
    bundle = root / "node" / "motion-render.mjs"
    assert len(MB.stale_sources(bundle, root)) == 3  # бандла нет — устарело всё
    bundle.write_text("// old", encoding="utf-8")
    for p in MB.bundle_sources(root):
        _age(p, 60)
    assert MB.stale_sources(bundle, root) == []
    negate = root / "frontend" / "src" / "motion" / "fx" / "negate.ts"
    _age(bundle, 30)
    _age(negate, 0)
    assert MB.stale_sources(bundle, root) == [negate]


def test_build_command_is_the_npm_script_with_local_esbuild(tmp_path):
    root = _repo(tmp_path)
    bundle = root / "node" / "motion-render.mjs"
    cmd = MB.build_command(bundle, root, "node")
    assert cmd[0] == "node" and cmd[1].endswith(str(Path("node_modules/esbuild/bin/esbuild")))
    assert "--bundle" in cmd and "--external:@napi-rs/canvas" in cmd
    assert cmd[-1] == f"--outfile={bundle}" and sum(a.startswith("--outfile=") for a in cmd) == 1
    assert MB.build_command(bundle, _repo(tmp_path / "x", esbuild=None), "node") is None  # esbuild не установлен


def test_stale_bundle_is_rebuilt(tmp_path, caplog):
    if not shutil.which("node"):
        pytest.skip("нет node")
    root = _repo(tmp_path)
    bundle = root / "node" / "motion-render.mjs"
    bundle.write_text("// old без negate", encoding="utf-8")
    _age(bundle, 60)
    with caplog.at_level(logging.WARNING, logger="app.motion_bundle"):
        MB.ensure_fresh(bundle, "node", root)
    assert bundle.read_text(encoding="utf-8").startswith("// built")
    assert MB.stale_sources(bundle, root) == []
    assert "пересобран" in caplog.text


def test_cannot_build_warns_loudly_and_keeps_old_bundle(tmp_path, caplog, monkeypatch):
    root = _repo(tmp_path, esbuild=None)
    bundle = root / "node" / "motion-render.mjs"
    bundle.write_text("// old", encoding="utf-8")
    _age(bundle, 60)
    with caplog.at_level(logging.WARNING, logger="app.motion_bundle"):
        MB.ensure_fresh(bundle, "node", root)
    assert bundle.read_text(encoding="utf-8") == "// old"
    assert "УСТАРЕЛ" in caplog.text and "npm run build:motion" in caplog.text

    # MOTION_AUTO_BUILD=0 — только предупреждение, даже если esbuild есть.
    root2 = _repo(tmp_path / "b")
    bundle2 = root2 / "node" / "motion-render.mjs"
    bundle2.write_text("// old", encoding="utf-8")
    _age(bundle2, 60)
    monkeypatch.setenv("MOTION_AUTO_BUILD", "0")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.motion_bundle"):
        MB.ensure_fresh(bundle2, "node", root2)
    assert bundle2.read_text(encoding="utf-8") == "// old" and "УСТАРЕЛ" in caplog.text


def test_failed_build_is_not_retried_for_the_same_edit(tmp_path, caplog, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("нет node")
    root = _repo(tmp_path, esbuild=FAILING_ESBUILD)
    bundle = root / "node" / "motion-render.mjs"
    bundle.write_text("// old", encoding="utf-8")
    _age(bundle, 60)
    calls = []
    real_run = MB.subprocess.run
    monkeypatch.setattr(MB.subprocess, "run", lambda *a, **k: calls.append(a) or real_run(*a, **k))
    with caplog.at_level(logging.WARNING, logger="app.motion_bundle"):
        MB.ensure_fresh(bundle, "node", root)
        MB.ensure_fresh(bundle, "node", root)
    assert len(calls) == 1 and "esbuild упал" in caplog.text and "УСТАРЕЛ" in caplog.text
    assert bundle.read_text(encoding="utf-8") == "// old"


def test_foreign_bundle_path_is_left_alone(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    foreign = tmp_path / "elsewhere" / "motion-render.mjs"
    foreign.parent.mkdir()
    foreign.write_text("// mine", encoding="utf-8")
    _age(foreign, 60)
    monkeypatch.setattr(MB.subprocess, "run", lambda *a, **k: pytest.fail("не должен собирать чужой бандл"))
    MB.ensure_fresh(foreign, "node", root)
    assert foreign.read_text(encoding="utf-8") == "// mine"
