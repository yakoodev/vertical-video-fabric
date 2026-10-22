"""Старые клипы со ссылками на убранные звуки (синтетика, дешёвые интерфейсные) играют замену из freesound."""

import json
import re
from pathlib import Path

from app import motion_assets as ma
from app.motion_assets import FREESOUND_ALIASES, LEGACY_SFX
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]


def _files():
    from tools.fetch_sfx import FREESOUND_SFX

    return {f"{name}.ogg" for name, *_rest in FREESOUND_SFX}


def test_legacy_sfx_map_points_to_freesound_files():
    files = _files()
    for mapping in LEGACY_SFX.values():
        for target in mapping.values():
            assert target in files, target
    for old, new in FREESOUND_ALIASES.items():
        assert old not in files, f"{old} ещё в паке — алиас не нужен"
        assert new in files, new


def test_curated_pack_is_cc0_pinned_and_without_cheap_ui_sounds():
    from tools.fetch_sfx import FREESOUND_SFX

    assert 20 <= len(FREESOUND_SFX) <= 35
    names = [name for name, *_rest in FREESOUND_SFX]
    assert len(names) == len(set(names))
    for name, sound_id, url, sha, label, tags, max_sec in FREESOUND_SFX:
        assert re.fullmatch(rf"https://cdn\.freesound\.org/previews/\d+/{sound_id}_\d+-hq\.mp3", url), name
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name
        assert label and tags and 0 < max_sec <= 4.0
    for cheap in ("ding", "triangle", "click", "boing", "pop-cartoon", "typing-digital"):
        assert cheap not in names
    # Под каждую задачу монтажа есть звук.
    for needed in ("whoosh-soft", "riser", "impact-low", "drama-boom", "record-scratch", "laugh-sitcom",
                   "crowd-gasp", "camera", "cash", "pop-soft"):
        assert needed in names


def test_every_sfx_reference_in_code_exists_in_pack():
    from app.transitions import JOIN_SFX_FILES

    files = _files()
    for name, *_rest in JOIN_SFX_FILES.values():
        assert name in files, name
    for path in [*(ROOT / "app").rglob("*.py")]:
        for ref in re.findall(r"sfx:freesound/([a-z0-9-]+\.ogg)", path.read_text(encoding="utf-8")):
            assert ref in files, f"{path.name}: {ref}"


def _pack(tmp_path, files):
    packs = tmp_path / "packs"
    (packs / "freesound").mkdir(parents=True)
    for f in files:
        (packs / "freesound" / f).write_bytes(b"OggS")
    (packs / "packs.json").write_text(json.dumps({"version": 1, "packs": [
        {"id": "freesound", "kind": "sfx", "items": [{"file": f, "name": f, "tags": []} for f in files]}]}),
        encoding="utf-8")
    return packs


def test_removed_sound_resolves_to_alias_only_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "motion_packs_dir", _pack(tmp_path, ["pop-soft.ogg", "camera-2.ogg"]), raising=False)
    assert ma.resolve_pack_ref("sfx:freesound/ding.ogg").name == "pop-soft.ogg"
    assert ma.pack_item_info("sfx:freesound/ding.ogg")["file"] == "pop-soft.ogg"
    assert ma.resolve_pack_ref("sfx:generated/click.ogg").name == "camera-2.ogg"
    assert ma.resolve_pack_ref("sfx:freesound/nothing.ogg") is None

    # Старый пак (файл ещё есть) — играет сам файл, без подмены.
    monkeypatch.setattr(settings, "motion_packs_dir", _pack(tmp_path / "old", ["ding.ogg", "pop-soft.ogg"]),
                        raising=False)
    assert ma.resolve_pack_ref("sfx:freesound/ding.ogg").name == "ding.ogg"


def test_fetch_sfx_swaps_only_its_pack_in_index(tmp_path):
    from tools import fetch_sfx

    (tmp_path / "packs.json").write_text(json.dumps({"version": 1, "packs": [
        {"id": "twemoji", "kind": "emoji", "items": []},
        {"id": "freesound", "kind": "sfx", "items": [{"file": "ding.ogg"}], "version": "1"}]}), encoding="utf-8")
    fetch_sfx.update_index(tmp_path, {"id": "freesound", "kind": "sfx", "items": [{"file": "pop-soft.ogg"}],
                                      "version": "2", "count": 1, "size": 0})
    index = json.loads((tmp_path / "packs.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in index["packs"]] == ["twemoji", "freesound"]
    assert index["packs"][1]["version"] == "2" and index["packs"][1]["items"] == [{"file": "pop-soft.ogg"}]
    # Индекса нет (fetch_packs не запускали) — создаётся с одним паком.
    (tmp_path / "empty").mkdir()
    fetch_sfx.update_index(tmp_path / "empty", {"id": "freesound", "items": []})
    assert json.loads((tmp_path / "empty" / "packs.json").read_text(encoding="utf-8"))["packs"][0]["id"] == "freesound"


def test_search_without_pack_skips_cheap_ui_sound_packs(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool

    packs = _pack(tmp_path, ["pop-soft.ogg"])
    (packs / "kenney-ui").mkdir()
    (packs / "kenney-ui" / "click1.ogg").write_bytes(b"OggS")
    index = json.loads((packs / "packs.json").read_text(encoding="utf-8"))
    index["packs"][0]["items"][0]["tags"] = ["поп"]
    index["packs"].append({"id": "kenney-ui", "kind": "sfx", "items": [{"file": "click1.ogg", "name": "Клик поп",
                                                                       "tags": ["поп"]}]})
    (packs / "packs.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(settings, "motion_packs_dir", packs, raising=False)
    found, ok = run_tool(ToolContext(None), "search_pack", {"q": "поп", "kind": "sfx"})
    assert ok and found["results"] and {r["pack"] for r in found["results"]} == {"freesound"}
    named, ok = run_tool(ToolContext(None), "search_pack", {"pack": "kenney-ui", "q": "поп"})
    assert ok and named["results"][0]["pack"] == "kenney-ui"
