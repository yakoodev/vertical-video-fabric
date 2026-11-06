"""Хранилище: библиотека рендера. Оформления, баннеры, музыка, стили субтитров, бренды, файлы для монтажа.
Часть ``AppStore``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.settings import settings
from app.store_common import (
    _choice, _json_text, _path_inside, CSS_HEX_RE, VALID_AUDIO_MIX_MODES, VALID_BANNER_POSITIONS, VALID_COLOR_STYLES,
    VALID_CROP_ANCHORS, VALID_SCALE_MODES, VALID_SUBTITLE_PROVIDERS,
)


def _prepare_ffmpeg_preset_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "description": "",
        "output_width": 1080,
        "output_height": 1920,
        "fps": 30,
        "video_codec": "libx264",
        "audio_codec": "aac",
        "video_bitrate": "",
        "audio_bitrate": "",
        "audio_mix_mode": "primary",
        "audio_primary_stream": 0,
        "audio_primary_volume": 1,
        "audio_secondary_stream": None,
        "audio_secondary_volume": 1,
        "scale_mode": "cover",
        "crop_anchor": "center",
        "banner_id": None,
        "subtitle_profile_id": None,
        "extra": {},
        "music_track_id": None,
        "music_volume": 0.25,
        "music_loop": True,
        "music_fade_in_sec": 0.0,
        "music_fade_out_sec": 0.0,
        "music_duck": True,
        "music_duck_amount": 0.6,
        "color_style": "none",
        "color_strength": 1.0,
        "vignette": 0.0,
        "grain": 0.0,
        "smart_reframe": True,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key in {"description", "video_codec", "audio_codec", "video_bitrate", "audio_bitrate"}:
            values[key] = str(value).strip()
        elif key in {"output_width", "output_height"}:
            number = int(value)
            if number <= 0:
                raise ValueError(f"{key} must be positive")
            values[key] = number
        elif key == "fps":
            fps = float(value)
            if fps <= 0:
                raise ValueError("fps must be positive")
            values[key] = fps
        elif key == "scale_mode":
            values[key] = _choice(str(value), VALID_SCALE_MODES, key)
        elif key == "crop_anchor":
            values[key] = _choice(str(value), VALID_CROP_ANCHORS, key)
        elif key == "audio_mix_mode":
            values[key] = _choice(str(value), VALID_AUDIO_MIX_MODES, key)
        elif key in {"audio_primary_stream", "audio_secondary_stream"}:
            if value is None or str(value).strip() == "":
                if key == "audio_primary_stream":
                    values[key] = 0
                else:
                    values[key] = None
                continue
            number = int(value)
            if number < 0:
                raise ValueError(f"{key} must be >= 0")
            values[key] = number
        elif key in {"audio_primary_volume", "audio_secondary_volume"}:
            volume = float(value)
            if volume < 0 or volume > 4:
                raise ValueError(f"{key} must be between 0 and 4")
            values[key] = volume
        elif key in {"banner_id", "subtitle_profile_id", "music_track_id"}:
            if value is None or str(value).strip() == "":
                values[key] = None
            else:
                parsed = int(value)
                values[key] = parsed if parsed > 0 else None
        elif key in {"music_volume", "music_duck_amount"}:
            number = float(value)
            if number < 0 or number > 4:
                raise ValueError(f"{key} must be between 0 and 4")
            values[key] = round(number, 4)
        elif key in {"music_fade_in_sec", "music_fade_out_sec"}:
            number = float(value)
            if number < 0 or number > 30:
                raise ValueError(f"{key} must be between 0 and 30")
            values[key] = round(number, 3)
        elif key in {"music_loop", "music_duck", "smart_reframe"}:
            values[key] = int(bool(value))
        elif key == "color_style":
            values[key] = _choice(str(value), VALID_COLOR_STYLES, key)
        elif key in {"color_strength", "vignette", "grain"}:
            number = float(value)
            upper = 2.0 if key == "color_strength" else 1.0
            if number < 0 or number > upper:
                raise ValueError(f"{key} must be between 0 and {upper:g}")
            values[key] = round(number, 4)
        elif key in {"extra", "extra_json"}:
            values["extra_json"] = _json_text(value)
        else:
            raise ValueError(f"unsupported ffmpeg preset field: {key}")
    return values


def _prepare_audio_track_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "file_path": "",
        "original_filename": "",
        "mime_type": "",
        "duration_sec": 0.0,
        "volume": 0.25,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "file_path":
            values[key] = _path_inside(value, settings.audio_dir, "file_path")
        elif key in {"original_filename", "mime_type"}:
            values[key] = str(value).strip()
        elif key == "duration_sec":
            values[key] = max(0.0, float(value))
        elif key == "volume":
            volume = float(value)
            if volume < 0 or volume > 4:
                raise ValueError("volume must be between 0 and 4")
            values[key] = round(volume, 4)
        elif key in {"energy_start_sec", "drop_sec", "bpm"}:
            # Отметки энергии трека (app/music_energy.py); None — не нашлось.
            values[key] = None if value is None else round(max(0.0, float(value)), 2)
        elif key == "energy_version":
            values[key] = max(0, int(value))
        else:
            raise ValueError(f"unsupported audio track field: {key}")
    return values


def _prepare_banner_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "file_path": "",
        "original_filename": "",
        "mime_type": "",
        "width": 0,
        "height": 0,
        "duration_sec": 0,
        "position": "bottom",
        "x": None,
        "y": None,
        "opacity": 1,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "file_path":
            values[key] = _path_inside(value, settings.banner_dir, "file_path")
        elif key in {"original_filename", "mime_type"}:
            values[key] = str(value).strip()
        elif key in {"width", "height"}:
            values[key] = int(value)
        elif key == "duration_sec":
            values[key] = float(value)
        elif key == "position":
            values[key] = _choice(str(value), VALID_BANNER_POSITIONS, key)
        elif key in {"x", "y"}:
            values[key] = int(value) if value is not None else None
        elif key == "opacity":
            opacity = float(value)
            if opacity < 0 or opacity > 1:
                raise ValueError("opacity must be between 0 and 1")
            values[key] = opacity
        else:
            raise ValueError(f"unsupported banner field: {key}")
    return values


def _prepare_subtitle_profile_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        # Пусто = взять движок из настроек (SUBTITLE_PROVIDER). «mock» по умолчанию
        # означал, что стиль «из стиля» жёг тестовую заглушку вместо речи.
        "provider": "",
        "model": "openai/gpt-4o-transcribe",
        "language": "",
        "timing_offset_sec": 0.0,
        "font_family": "Arial",
        "font_size": 64,
        "primary_color": "#FFFFFF",
        "active_word_color": "#FACC15",
        "outline_color": "#111827",
        "back_color": "#000000",
        "alignment": 2,
        "margin_v": 160,
        "max_words_per_line": 5,
        "uppercase": False,
        "outline_width": 5,
        "shadow": 1,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "provider":
            # Пусто — законное значение: «движок из настроек» (SUBTITLE_PROVIDER), так стоит у
            # стилей по умолчанию и у старых стилей после миграции mock → "" (app/db.py).
            text = str(value or "").strip()
            values[key] = _choice(text, VALID_SUBTITLE_PROVIDERS, key) if text else ""
        elif key in {"model", "language", "font_family"}:
            values[key] = str(value).strip()
        elif key == "timing_offset_sec":
            offset = float(value or 0)
            if offset < -2 or offset > 2:
                raise ValueError("timing_offset_sec must be between -2 and 2")
            values[key] = round(offset, 3)
        elif key in {"primary_color", "active_word_color", "outline_color", "back_color"}:
            color = str(value).strip()
            if not CSS_HEX_RE.match(color):
                raise ValueError(f"{key} must be CSS hex #RRGGBB")
            values[key] = color
        elif key in {"font_size", "alignment", "margin_v", "max_words_per_line"}:
            number = int(value)
            if number < 0:
                raise ValueError(f"{key} must be >= 0")
            values[key] = number
        elif key in {"outline_width", "shadow"}:
            number = float(value)
            if number < 0 or number > 20:
                raise ValueError(f"{key} must be between 0 and 20")
            values[key] = round(number, 3)
        elif key == "uppercase":
            values[key] = int(bool(value))
        else:
            raise ValueError(f"unsupported subtitle profile field: {key}")
    return values


def _decode_asset(asset: dict) -> dict:
    asset["has_audio"] = bool(asset.get("has_audio"))
    try:
        check = json.loads(asset.get("people_check") or "null")
    except (TypeError, ValueError):
        check = None
    asset["people_check"] = check if isinstance(check, dict) else None
    asset["url"] = f"/media/montage-assets/{asset['id']}"
    return asset


def _decode_brand(row: dict) -> dict:
    """Бренд наружу — плоско: id + все поля шаблона (форма из normalize_brand) + даты."""
    from app.brand_kit import normalize_brand

    try:
        kit = json.loads(row.get("kit_json") or "{}")
    except (TypeError, ValueError):
        kit = {}
    kit = normalize_brand({**(kit if isinstance(kit, dict) else {}), "name": row.get("name") or ""})
    return {"id": row["id"], **kit, "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


class RenderLibraryStore:
    def create_ffmpeg_preset(self, label: str, **fields: Any) -> dict:
        values = _prepare_ffmpeg_preset_fields({"label": label, **fields}, partial=False)
        if values["banner_id"] is not None:
            self.get_banner(values["banner_id"])
        if values["subtitle_profile_id"] is not None:
            self.get_subtitle_profile(values["subtitle_profile_id"])
        if values["music_track_id"] is not None:
            self.get_audio_track(values["music_track_id"])
        cur = self.db.execute(
            """
            INSERT INTO ffmpeg_presets
                (label, description, output_width, output_height, fps, video_codec, audio_codec,
                 video_bitrate, audio_bitrate, audio_mix_mode, audio_primary_stream,
                 audio_primary_volume, audio_secondary_stream, audio_secondary_volume,
                 scale_mode, crop_anchor, banner_id, subtitle_profile_id, extra_json,
                 music_track_id, music_volume, music_loop, music_fade_in_sec, music_fade_out_sec,
                 music_duck, music_duck_amount, color_style, color_strength, vignette, grain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["description"],
                values["output_width"],
                values["output_height"],
                values["fps"],
                values["video_codec"],
                values["audio_codec"],
                values["video_bitrate"],
                values["audio_bitrate"],
                values["audio_mix_mode"],
                values["audio_primary_stream"],
                values["audio_primary_volume"],
                values["audio_secondary_stream"],
                values["audio_secondary_volume"],
                values["scale_mode"],
                values["crop_anchor"],
                values["banner_id"],
                values["subtitle_profile_id"],
                values["extra_json"],
                values["music_track_id"],
                values["music_volume"],
                values["music_loop"],
                values["music_fade_in_sec"],
                values["music_fade_out_sec"],
                values["music_duck"],
                values["music_duck_amount"],
                values["color_style"],
                values["color_strength"],
                values["vignette"],
                values["grain"],
            ),
        )
        return self.get_ffmpeg_preset(cur.lastrowid)

    def list_ffmpeg_presets(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM ffmpeg_presets ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_ffmpeg_preset(self, preset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM ffmpeg_presets WHERE id = ?", (preset_id,))
        if not row:
            raise KeyError(f"ffmpeg preset not found: {preset_id}")
        return dict(row)

    def update_ffmpeg_preset(self, preset_id: int, **fields: Any) -> dict:
        self.get_ffmpeg_preset(preset_id)
        values = _prepare_ffmpeg_preset_fields(fields, partial=True)
        if values.get("banner_id") is not None:
            self.get_banner(values["banner_id"])
        if values.get("subtitle_profile_id") is not None:
            self.get_subtitle_profile(values["subtitle_profile_id"])
        if values.get("music_track_id") is not None:
            self.get_audio_track(values["music_track_id"])
        self._update_record(
            "ffmpeg_presets",
            preset_id,
            values,
        )
        return self.get_ffmpeg_preset(preset_id)

    def delete_ffmpeg_preset(self, preset_id: int) -> None:
        self.get_ffmpeg_preset(preset_id)
        self._delete_record("ffmpeg_presets", preset_id)

    def dedup_unused_ffmpeg_presets(self) -> int:
        """Remove duplicate render presets (same label), keeping the lowest id.
        A later duplicate is only deleted when no rendered clip references it, so
        history stays intact. Cleans up accidental repeat-saves (e.g. many
        identical 'Music + cinematic')."""
        rows = self.db.query_all("SELECT id, label FROM ffmpeg_presets ORDER BY id")
        seen: set[str] = set()
        removed = 0
        for row in rows:
            label = str(row["label"] or "").strip().lower()
            if label in seen:
                referenced = self.db.query_one(
                    "SELECT COUNT(*) AS c FROM clips WHERE ffmpeg_preset_id = ?", (row["id"],)
                )["c"]
                if not referenced:
                    self.db.execute("DELETE FROM ffmpeg_presets WHERE id = ?", (row["id"],))
                    removed += 1
            else:
                seen.add(label)
        return removed

    def create_banner(self, label: str, file_path: str | Path, **fields: Any) -> dict:
        values = _prepare_banner_fields({"label": label, "file_path": file_path, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO banners
                (label, file_path, original_filename, mime_type, width, height, duration_sec,
                 position, x, y, opacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["file_path"],
                values["original_filename"],
                values["mime_type"],
                values["width"],
                values["height"],
                values["duration_sec"],
                values["position"],
                values["x"],
                values["y"],
                values["opacity"],
            ),
        )
        return self.get_banner(cur.lastrowid)

    def list_banners(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM banners ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_banner(self, banner_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM banners WHERE id = ?", (banner_id,))
        if not row:
            raise KeyError(f"banner not found: {banner_id}")
        return dict(row)

    def update_banner(self, banner_id: int, **fields: Any) -> dict:
        self.get_banner(banner_id)
        self._update_record("banners", banner_id, _prepare_banner_fields(fields, partial=True))
        return self.get_banner(banner_id)

    def delete_banner(self, banner_id: int) -> None:
        self.get_banner(banner_id)
        self._delete_record("banners", banner_id)

    # ---- «Файлы для монтажа» -------------------------------------------------

    def create_montage_asset(self, **f: Any) -> dict:
        cur = self.db.execute(
            """
            INSERT INTO montage_assets (kind, label, description, tags, file_path, original_filename,
                                        mime_type, duration_sec, width, height, has_audio, size_bytes, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f["kind"], str(f["label"]).strip()[:120] or "файл", str(f.get("description") or "")[:500],
                str(f.get("tags") or "")[:200], str(f["file_path"]), f.get("original_filename") or "",
                f.get("mime_type") or "", float(f.get("duration_sec") or 0), int(f.get("width") or 0),
                int(f.get("height") or 0), int(bool(f.get("has_audio"))), int(f.get("size_bytes") or 0),
                int(f["project_id"]) if f.get("project_id") is not None else None,
            ),
        )
        return self.get_montage_asset(cur.lastrowid)

    def list_montage_assets(self, *, project_id: int | None = None, visible_to: Any = None) -> list[dict]:
        """Все файлы; ``project_id`` — только файлы этого проекта; ``visible_to`` (id проектов, () — ни одного) —
        общие плюс файлы этих проектов: так подбирают файл для клипа (app/asset_scope.py)."""
        where, params = "", ()
        if project_id is not None:
            where, params = "WHERE project_id = ?", (int(project_id),)
        elif visible_to is not None:
            ids = tuple(int(p) for p in visible_to)
            marks = ", ".join("?" for _ in ids)
            where, params = f"WHERE project_id IS NULL OR project_id IN ({marks})" if ids else "WHERE project_id IS NULL", ids
        rows = self.db.query_all(f"SELECT * FROM montage_assets {where} ORDER BY id DESC", params)
        return [_decode_asset(dict(r)) for r in rows]

    def get_montage_asset(self, asset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM montage_assets WHERE id = ?", (asset_id,))
        if not row:
            raise KeyError(f"montage asset not found: {asset_id}")
        return _decode_asset(dict(row))

    def update_montage_asset(self, asset_id: int, **fields: Any) -> dict:
        self.get_montage_asset(asset_id)
        updates: dict[str, Any] = {}
        if "label" in fields and str(fields["label"]).strip():
            updates["label"] = str(fields["label"]).strip()[:120]
        if "description" in fields:
            updates["description"] = str(fields["description"] or "")[:500]
        if "tags" in fields:
            updates["tags"] = str(fields["tags"] or "")[:200]
        if "people_check" in fields:
            value = fields["people_check"]
            updates["people_check"] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else ""
        if "project_id" in fields:  # None — в общие
            updates["project_id"] = int(fields["project_id"]) if fields["project_id"] is not None else None
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            self.db.execute(f"UPDATE montage_assets SET {sets} WHERE id = ?", (*updates.values(), asset_id))
        return self.get_montage_asset(asset_id)

    def delete_montage_asset(self, asset_id: int) -> dict:
        asset = self.get_montage_asset(asset_id)
        self.db.execute("DELETE FROM montage_assets WHERE id = ?", (asset_id,))
        try:
            Path(asset["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        return asset

    def create_audio_track(self, label: str, file_path: str | Path, **fields: Any) -> dict:
        values = _prepare_audio_track_fields({"label": label, "file_path": file_path, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO audio_tracks
                (label, file_path, original_filename, mime_type, duration_sec, volume)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["file_path"],
                values["original_filename"],
                values["mime_type"],
                values["duration_sec"],
                values["volume"],
            ),
        )
        return self.get_audio_track(cur.lastrowid)

    def list_audio_tracks(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM audio_tracks ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_audio_track(self, track_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM audio_tracks WHERE id = ?", (track_id,))
        if not row:
            raise KeyError(f"audio track not found: {track_id}")
        return dict(row)

    def update_audio_track(self, track_id: int, **fields: Any) -> dict:
        self.get_audio_track(track_id)
        self._update_record("audio_tracks", track_id, _prepare_audio_track_fields(fields, partial=True))
        return self.get_audio_track(track_id)

    def delete_audio_track(self, track_id: int) -> None:
        self.get_audio_track(track_id)
        self._delete_record("audio_tracks", track_id)

    # ---- Бренды: шаблон оформления роликов одного заказчика (app/brand_kit.py) ----

    def create_brand(self, name: str, **fields: Any) -> dict:
        from app.brand_kit import brand_problems, normalize_brand

        kit = normalize_brand({**fields, "name": name})
        problems = brand_problems(self, kit)
        if problems:
            raise ValueError("; ".join(problems))
        cur = self.db.execute(
            "INSERT INTO brands (name, kit_json) VALUES (?, ?)",
            (kit["name"], json.dumps(kit, ensure_ascii=False)),
        )
        return self.get_brand(cur.lastrowid)

    def list_brands(self) -> list[dict]:
        return [_decode_brand(dict(r)) for r in self.db.query_all("SELECT * FROM brands ORDER BY name COLLATE NOCASE, id")]

    def get_brand(self, brand_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM brands WHERE id = ?", (brand_id,))
        if not row:
            raise KeyError(f"brand not found: {brand_id}")
        return _decode_brand(dict(row))

    def update_brand(self, brand_id: int, **fields: Any) -> dict:
        from app.brand_kit import brand_problems, merge_brand

        current = self.get_brand(brand_id)
        kit = merge_brand({k: v for k, v in current.items() if k not in ("id", "created_at", "updated_at")}, fields)
        problems = brand_problems(self, kit)
        if problems:
            raise ValueError("; ".join(problems))
        self._update_record("brands", brand_id, {"name": kit["name"], "kit_json": json.dumps(kit, ensure_ascii=False)})
        return self.get_brand(brand_id)

    def delete_brand(self, brand_id: int) -> None:
        self.get_brand(brand_id)
        self._delete_record("brands", brand_id)

    def create_subtitle_profile(self, label: str, **fields: Any) -> dict:
        values = _prepare_subtitle_profile_fields({"label": label, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO subtitle_profiles
                (label, provider, model, language, font_family, font_size, primary_color,
                 active_word_color, outline_color, back_color, alignment, margin_v,
                 max_words_per_line, uppercase, timing_offset_sec, outline_width, shadow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["provider"],
                values["model"],
                values["language"],
                values["font_family"],
                values["font_size"],
                values["primary_color"],
                values["active_word_color"],
                values["outline_color"],
                values["back_color"],
                values["alignment"],
                values["margin_v"],
                values["max_words_per_line"],
                values["uppercase"],
                values["timing_offset_sec"],
                values["outline_width"],
                values["shadow"],
            ),
        )
        return self.get_subtitle_profile(cur.lastrowid)

    def list_subtitle_profiles(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM subtitle_profiles ORDER BY id DESC")
        return [self._subtitle_profile_from_row(row) for row in rows]

    def get_subtitle_profile(self, profile_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM subtitle_profiles WHERE id = ?", (profile_id,))
        if not row:
            raise KeyError(f"subtitle profile not found: {profile_id}")
        return self._subtitle_profile_from_row(row)

    def update_subtitle_profile(self, profile_id: int, **fields: Any) -> dict:
        self.get_subtitle_profile(profile_id)
        self._update_record(
            "subtitle_profiles",
            profile_id,
            _prepare_subtitle_profile_fields(fields, partial=True),
        )
        return self.get_subtitle_profile(profile_id)

    def delete_subtitle_profile(self, profile_id: int) -> None:
        self.get_subtitle_profile(profile_id)
        self._delete_record("subtitle_profiles", profile_id)

    def _subtitle_profile_from_row(self, row) -> dict:
        data = dict(row)
        data["uppercase"] = bool(data["uppercase"])
        data["timing_offset_sec"] = float(data.get("timing_offset_sec") or 0)
        data["outline_width"] = float(data.get("outline_width") if data.get("outline_width") is not None else 5)
        data["shadow"] = float(data.get("shadow") if data.get("shadow") is not None else 1)
        return data
