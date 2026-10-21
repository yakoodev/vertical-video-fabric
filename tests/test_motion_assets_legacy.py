"""Старые клипы со ссылками на убранные синтетические звуки играют замену из freesound."""

from app.motion_assets import LEGACY_SFX


def test_legacy_sfx_map_points_to_freesound_files():
    from tools.fetch_packs import FREESOUND_SFX

    files = {f"{name}.ogg" for name, *_rest in FREESOUND_SFX}
    for mapping in LEGACY_SFX.values():
        for target in mapping.values():
            assert target in files, target
