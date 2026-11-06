"""``app/store.py`` и ``app/render.py`` разложены по частям, а снаружи остались одним объектом и одним модулем.

Эталон ``tests/fixtures/split_surface_golden.json`` снят до разбора: имена модулей и подписи методов ``AppStore`` и
``ClipRenderService``. Новое добавлять можно без эталона; убрать имя или поменять подпись — осознанно:
``VVF_UPDATE_GOLDEN=1 pytest tests/test_store_render_split.py``. Побайтная сверка с коммитом до разбора —
``tools/split_check.py``.
"""

from __future__ import annotations

import ast
import builtins
import dis
import importlib
import inspect
import json
import os
import types
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "split_surface_golden.json"
APP = Path(__file__).parent.parent / "app"
# И до разбора были отдельными модулями, частями не считаются.
NOT_PARTS = {"render_qc", "render_queue", "render_library_api"}
# Что тесты подменяют на app.render: подмена должна доходить до кода частей.
PATCHED_ON_RENDER = ("_run_ffmpeg", "_run_compositor", "probe_media", "run_qc", "_has_audio_stream", "music_volume_for",
                     "uuid4", "fit_loudness", "_measure_loudness")


def _parts(stem: str) -> list[str]:
    return sorted(p.stem for p in APP.glob(f"{stem}_*.py") if p.stem not in NOT_PARTS)


def _module_names(module) -> list[str]:
    return sorted(name for name in vars(module) if not (name.startswith("__") and name.endswith("__")))


def _methods(cls) -> dict[str, str]:
    """Имя члена класса → подпись (у атрибута-значения — пустая строка)."""
    out: dict[str, str] = {}
    for klass in cls.__mro__[:-1]:
        for name, value in vars(klass).items():
            if name in out or (name.startswith("__") and name != "__init__"):
                continue
            target = value.__func__ if isinstance(value, staticmethod) else value
            out[name] = str(inspect.signature(target)) if callable(target) else ""
    return dict(sorted(out.items()))


def _surface() -> dict:
    from app import render, store

    return {
        "app.store": _module_names(store),
        "app.render": _module_names(render),
        "AppStore": _methods(store.AppStore),
        "ClipRenderService": _methods(render.ClipRenderService),
    }


def _global_reads(code: types.CodeType) -> set[str]:
    names = {i.argval for i in dis.get_instructions(code) if i.opname in ("LOAD_GLOBAL", "LOAD_NAME")}
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _global_reads(const)
    return names


def _functions_of(module):
    """Функции и методы, определённые в самом модуле (из-под staticmethod и декораторов)."""
    def plain(value):
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        while hasattr(value, "__wrapped__"):
            value = value.__wrapped__
        return value

    for value in vars(module).values():
        value = plain(value)
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
            yield value
        elif isinstance(value, type) and value.__module__ == module.__name__:
            for member in vars(value).values():
                member = plain(member)
                if isinstance(member, types.FunctionType):
                    yield member


def test_surface_matches_golden():
    got = _surface()
    if os.environ.get("VVF_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for section in ("app.store", "app.render"):
        lost = sorted(set(golden[section]) - set(got[section]))
        assert not lost, f"{section}: имена пропали: {lost}"
    for section in ("AppStore", "ClipRenderService"):
        changed = {name: (sig, got[section].get(name)) for name, sig in golden[section].items()
                   if got[section].get(name) != sig}
        assert not changed, f"{section}: пропали или поменяли подпись: {changed}"


@pytest.mark.parametrize("stem", ["store", "render"])
def test_parts_read_the_same_names_as_the_module(stem):
    """Код части находит каждое глобальное имя, и это тот же объект, что и в собирающем модуле."""
    core = importlib.import_module(f"app.{stem}")
    for name in [stem] + _parts(stem):
        module = importlib.import_module(f"app.{name}")
        for fn in _functions_of(module):
            for read in _global_reads(fn.__code__):
                if read not in fn.__globals__:
                    assert hasattr(builtins, read), f"{name}.{fn.__name__}: нет имени {read}"
                elif read in vars(core):
                    assert fn.__globals__[read] is vars(core)[read], f"{name}.{fn.__name__}: {read} — другой объект"


@pytest.mark.parametrize("stem", ["store", "render"])
def test_parts_do_not_import_the_assembling_module(stem):
    """Части импортируются сами по себе: модуль собирает их, а не наоборот (иначе круговой импорт)."""
    for name in _parts(stem):
        tree = ast.parse((APP / f"{name}.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                assert node.module != f"app.{stem}", name
                assert not (node.module == "app" and any(a.name == stem for a in node.names)), name
            elif isinstance(node, ast.Import):
                assert all(a.name != f"app.{stem}" for a in node.names), name


@pytest.mark.parametrize("name", PATCHED_ON_RENDER)
def test_patch_on_render_reaches_every_part(monkeypatch, name):
    from app import render

    original = getattr(render, name)
    parts = [importlib.import_module(f"app.{part}") for part in _parts("render")]
    holders = [part for part in parts if name in vars(part)]
    fake = object()
    with monkeypatch.context() as patch:
        patch.setattr(f"app.render.{name}", fake)
        assert getattr(render, name) is fake
        assert all(vars(part)[name] is fake for part in holders)
    assert getattr(render, name) is original
    assert all(vars(part)[name] is original for part in holders)
