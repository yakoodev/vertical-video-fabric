"""Хранилище: планы клипов, их куски, настройки рендера, субтитры и история правок. Часть ``AppStore``."""

from __future__ import annotations

import json
from typing import Any

from app.clip_score import clip_quality, words_in_ranges
from app.store_common import (
    _choice, _color_for_category, _segment_dict, CSS_HEX_RE, MIN_PIECE_DURATION_SEC, VALID_CLIP_PLAN_STATUSES,
)


def _same_layout(a: list, b: list) -> bool:
    def speed(x: list) -> float:
        return float(x[2]) if len(x) > 2 else 1.0

    try:
        return len(a) == len(b) and all(
            abs(float(x[0]) - float(y[0])) < 0.002 and abs(float(x[1]) - float(y[1])) < 0.002
            and abs(speed(x) - speed(y)) < 1e-4 for x, y in zip(a, b)
        )
    except (TypeError, ValueError, IndexError):
        return True  # мусорный штамп — не трогаем


def _decode_plan_settings(plan: dict) -> dict:
    """``render_settings_json`` → ``render_settings`` (None when never saved).

    None, not defaults: the batch render must tell "this clip was set up in the
    editor" from "nobody touched it" — only the latter falls back to the
    request's values.
    """
    plan["has_montage_backup"] = bool(plan.pop("montage_backup_json", ""))
    try:
        subs = json.loads(plan.pop("subtitles_json", "") or "null")
    except (TypeError, ValueError):
        subs = None
    # The editor needs the lines; the words only matter to the renderer.
    plan["subtitles"] = subs if isinstance(subs, dict) else None
    try:
        pick = json.loads(plan.pop("ai_pick_json", "") or "null")
    except (TypeError, ValueError):
        pick = None
    plan["ai_pick"] = pick if isinstance(pick, dict) else None
    raw = plan.pop("render_settings_json", None)
    settings = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed:
            settings = parsed
    plan["render_settings"] = settings
    return plan


class ClipPlansStore:
    def create_clip_plan(
        self,
        source_id: int,
        analysis_id: int | None,
        title: str,
        description: str = "",
        segment_ids: list[int] | None = None,
        score: float = 0,
        category: str = "",
        color: str = "",
        sort_order: int = 0,
        status: str = "candidate",
    ) -> dict:
        self.get_source(source_id)
        if analysis_id is not None:
            analysis = self.get_ai_analysis(analysis_id)
            if analysis["source_id"] != source_id:
                raise ValueError("analysis does not belong to source")
        segment_ids = segment_ids or []
        if not segment_ids:
            raise ValueError("clip plan requires at least one segment")
        segments = [self.get_ai_segment(segment_id) for segment_id in segment_ids]
        for segment in segments:
            if segment["source_id"] != source_id:
                raise ValueError("clip plan segment does not belong to source")
            if analysis_id is not None and segment["analysis_id"] != analysis_id:
                raise ValueError("clip plan segment does not belong to analysis")
        title = title.strip() or segments[0]["title"]
        description = description.strip() or segments[0]["description"]
        category = category.strip() or segments[0]["category"]
        color = color.strip() or segments[0]["color"] or _color_for_category(category)
        if not CSS_HEX_RE.match(color):
            color = _color_for_category(category)
        status = _choice(status, VALID_CLIP_PLAN_STATUSES, "status")
        cur = self.db.execute(
            """
            INSERT INTO clip_plans
                (source_id, analysis_id, status, title, description, score, category, color, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                analysis_id,
                status,
                title[:100],
                description[:500],
                max(0, min(1, float(score))),
                category[:40],
                color,
                int(sort_order),
            ),
        )
        plan_id = cur.lastrowid
        for index, segment_id in enumerate(segment_ids):
            self.db.execute(
                """
                INSERT INTO clip_plan_segments (clip_plan_id, segment_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (plan_id, segment_id, index),
            )
        return self.get_clip_plan(plan_id)

    def set_clip_plan_manual_focus(self, clip_plan_id: int, x: float | None) -> dict:
        """Fix the frame at ``x`` (full-frame horizontal centre, 0..1) on every
        segment of a clip plan.

        The focus track is the single source of truth for framing (the render
        reads only it): a fixed frame is a one-point track, exactly what the
        editor's «Фикс. рамка» writes. This endpoint used to set the legacy
        ``manual_focus_x`` column instead, which the render had stopped reading —
        the call "succeeded" and changed nothing. ``x=None`` only clears that
        legacy column: the old autofocus track is not recoverable from here
        (re-run autofocus for that).
        """
        self.get_clip_plan(clip_plan_id, include_segments=False)
        value = None if x is None else min(1.0, max(0.0, float(x)))
        rows = self.db.query_all(
            "SELECT segment_id FROM clip_plan_segments WHERE clip_plan_id = ?",
            (clip_plan_id,),
        )
        for row in rows:
            segment_id = int(row["segment_id"])
            self.db.execute("UPDATE ai_segments SET manual_focus_x = NULL WHERE id = ?", (segment_id,))
            if value is not None:
                self.update_ai_segment_focus(segment_id, [{"t": 0.0, "x": value}])
        return {"clip_plan_id": clip_plan_id, "manual_focus_x": value, "segments": len(rows)}

    def set_clip_plan_flags(
        self, clip_plan_id: int, *, favorite: bool | None = None, hidden: bool | None = None
    ) -> dict:
        """Toggle triage flags on a candidate: ⭐ favourite and/or hidden."""
        self.get_clip_plan(clip_plan_id, include_segments=False)
        updates: dict[str, int] = {}
        if favorite is not None:
            updates["favorite"] = 1 if favorite else 0
        if hidden is not None:
            updates["hidden"] = 1 if hidden else 0
        if updates:
            cols = ", ".join(f"{k} = ?" for k in updates)
            self.db.execute(
                f"UPDATE clip_plans SET {cols} WHERE id = ?", (*updates.values(), clip_plan_id)
            )
        return self.get_clip_plan(clip_plan_id, include_segments=False)

    def update_clip_plan_text(self, clip_plan_id: int, title: str, description: str, notes: str) -> dict:
        self.get_clip_plan(clip_plan_id, include_segments=False)
        self._update_record(
            "clip_plans", clip_plan_id, {"title": title, "description": description, "notes": notes}
        )
        return self.get_clip_plan(clip_plan_id, include_segments=False)

    def create_segment_for_plan(self, clip_plan_id: int, start_sec: float, end_sec: float, title: str) -> dict:
        """A new segment in the plan's analysis, NOT yet linked (see set_clip_plan_segments)."""
        plan = self.get_clip_plan(clip_plan_id, include_segments=False)
        analysis_id = plan["analysis_id"]
        if analysis_id is None:
            analysis_id = self.create_ai_analysis(plan["source_id"], "mock", status="succeeded")["id"]
        return self.create_ai_segment(
            analysis_id,
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title": title,
                "description": "Clip file edit",
                "score": plan.get("score") or 0.5,
                "category": plan["category"] or "manual",
                "color": plan["color"] or "#22D3EE",
                "reason": "Created from the clip file.",
            },
            min_duration=MIN_PIECE_DURATION_SEC,
        )

    def segment_plan_count(self, segment_id: int) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM clip_plan_segments WHERE segment_id = ?", (segment_id,)
        )
        return int(row["n"]) if row else 0

    def update_segment_title(self, segment_id: int, title: str) -> dict:
        self.get_ai_segment(segment_id)
        self._update_record("ai_segments", segment_id, {"title": title[:100]})
        return self.get_ai_segment(segment_id)

    def set_clip_plan_segments(self, clip_plan_id: int, segment_ids: list[int]) -> dict:
        """Make the plan consist of exactly these segments, in this order."""
        if not segment_ids:
            raise ValueError("clip plan must keep at least one segment")
        self.get_clip_plan(clip_plan_id, include_segments=False)
        self.db.execute("DELETE FROM clip_plan_segments WHERE clip_plan_id = ?", (clip_plan_id,))
        for order, segment_id in enumerate(segment_ids):
            self.db.execute(
                "INSERT INTO clip_plan_segments (clip_plan_id, segment_id, sort_order) VALUES (?, ?, ?)",
                (clip_plan_id, segment_id, order),
            )
        return self.get_clip_plan(clip_plan_id)

    def last_clip_plan_history_at(self, clip_plan_id: int) -> str | None:
        """Когда сделан последний снимок истории правок клипа (перед правкой) — или None."""
        row = self.db.query_one(
            "SELECT created_at FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 1",
            (clip_plan_id,),
        )
        return str(row["created_at"]) if row else None

    def get_clip_plan_subtitles(self, clip_plan_id: int) -> dict | None:
        row = self.db.query_one("SELECT subtitles_json FROM clip_plans WHERE id = ?", (clip_plan_id,))
        if not row:
            raise KeyError(f"clip plan not found: {clip_plan_id}")
        try:
            parsed = json.loads(row["subtitles_json"] or "null")
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        stamped = parsed.get("pieces")
        if isinstance(stamped, list) and stamped:
            current = self._plan_piece_layout(clip_plan_id)
            if current and not _same_layout(stamped, current):
                # Куски поменяли после распознавания — субтитры едут за своими словами.
                from app.clip_subtitles import remap_to_pieces

                parsed = remap_to_pieces(parsed, stamped, current)
                self.db.execute(
                    "UPDATE clip_plans SET subtitles_json = ? WHERE id = ?",
                    (json.dumps(parsed, ensure_ascii=False), clip_plan_id),
                )
        return parsed

    def _plan_piece_layout(self, clip_plan_id: int) -> list[list[float]]:
        """Штамп кусков для субтитров: ``[start, end]`` или ``[start, end, speed]`` у куска со
        скоростью — сменили скорость, слова куска растягиваются за ней (remap_to_pieces)."""
        from app.piece_speed import normalize_speed

        rows = self.db.query_all(
            """
            SELECT s.id, s.start_sec, s.end_sec FROM clip_plan_segments cps
            JOIN ai_segments s ON s.id = cps.segment_id
            WHERE cps.clip_plan_id = ? ORDER BY cps.sort_order
            """,
            (clip_plan_id,),
        )
        speeds: dict[int, float] = {}
        row = self.db.query_one("SELECT render_settings_json FROM clip_plans WHERE id = ?", (clip_plan_id,))
        if row and row["render_settings_json"]:
            try:
                rs = json.loads(row["render_settings_json"])
            except (TypeError, ValueError):
                rs = {}
            for entry in normalize_speed(rs.get("speed") if isinstance(rs, dict) else None):
                if entry.get("speed"):
                    speeds[entry["piece"]] = float(entry["speed"])
        out = []
        for r in rows:
            item = [round(float(r["start_sec"]), 3), round(float(r["end_sec"]), 3)]
            if int(r["id"]) in speeds:
                item.append(speeds[int(r["id"])])
            out.append(item)
        return out

    def set_clip_plan_subtitles(self, clip_plan_id: int, payload: dict | None) -> None:
        """Сохранить субтитры. Без штампа кусков они сняты по ТЕКУЩИМ кускам —
        штампуем их, чтобы при правке кусков перенести слова (см. get_clip_plan_subtitles)."""
        if payload and not payload.get("pieces"):  # свой штамп (снимок отмены) не трогаем
            layout = self._plan_piece_layout(clip_plan_id)
            if layout:
                payload = {**payload, "pieces": layout}
        if payload and payload.get("words"):
            # Слово под цензурой — всегда со звёздочками, кто бы ни переписал субтитры (app/censor.py).
            try:
                from app.censor import mask_censored_words

                payload = mask_censored_words(self, clip_plan_id, payload)
            except Exception:  # noqa: BLE001 - маска — удобство, сохранение важнее
                pass
            try:
                # Текст строк — как слова (маска мата, перераспределение слов, без строк-дублей).
                from app.clip_subtitles import sync_lines_with_words

                payload = sync_lines_with_words(payload)
            except Exception:  # noqa: BLE001
                pass
        self.db.execute(
            "UPDATE clip_plans SET subtitles_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False) if payload else "", clip_plan_id),
        )

    def set_clip_plan_ai_pick(self, clip_plan_id: int, pick: dict | None) -> None:
        self.db.execute(
            "UPDATE clip_plans SET ai_pick_json = ? WHERE id = ?",
            (json.dumps(pick, ensure_ascii=False) if pick else "", clip_plan_id),
        )

    def get_clip_plan_montage_backup(self, clip_plan_id: int) -> dict | None:
        row = self.db.query_one("SELECT montage_backup_json FROM clip_plans WHERE id = ?", (clip_plan_id,))
        if not row:
            raise KeyError(f"clip plan not found: {clip_plan_id}")
        try:
            parsed = json.loads(row["montage_backup_json"] or "null")
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_clip_plan_montage_backup(self, clip_plan_id: int, spec: dict | None) -> None:
        self.db.execute(
            "UPDATE clip_plans SET montage_backup_json = ? WHERE id = ?",
            (json.dumps(spec, ensure_ascii=False) if spec else "", clip_plan_id),
        )

    def set_clip_plan_render_settings(self, clip_plan_id: int, settings: dict) -> dict:
        """Save one clip's own render settings (normalized — junk never lands in the DB)."""
        from app.clip_settings import normalize_render_settings

        plan = self.get_clip_plan(clip_plan_id)
        clean = normalize_render_settings(settings)
        before = normalize_render_settings(plan.get("render_settings") or {})
        if (
            plan.get("render_settings")
            and clean["transition"] != before["transition"]
            and all(clean[k] == before[k] for k in ("layers", "camera", "inserts"))
        ):
            # Поменяли только переход — монтаж (во времени готового клипа) едет за
            # новыми наплывами, как субтитры. Правили слои в том же сохранении —
            # значит, их уже выставили под новый переход, не трогаем.
            from app.piece_remap import remap_for_transition

            clean = normalize_render_settings(
                remap_for_transition(clean, plan.get("segments") or [], before["transition"])
            )
        self.db.execute(
            "UPDATE clip_plans SET render_settings_json = ? WHERE id = ?",
            (json.dumps(clean, ensure_ascii=False), clip_plan_id),
        )
        if clean.get("format") != before.get("format"):
            # Вертикаль и горизонталь одного момента — разные клипы (app/clip_groups.py).
            try:
                from app.clip_groups import on_format_change

                on_format_change(self, clip_plan_id)
            except Exception:  # noqa: BLE001 - связь версий не должна срывать сохранение монтажа
                pass
        return self.get_clip_plan(clip_plan_id, include_segments=False)

    def list_clip_plans(
        self,
        source_id: int | None = None,
        analysis_id: int | None = None,
        include_segments: bool = True,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            where.append("source_id = ?")
            params.append(source_id)
        if analysis_id is not None:
            where.append("analysis_id = ?")
            params.append(analysis_id)
        sql = "SELECT * FROM clip_plans"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sort_order, id"
        plans = [_decode_plan_settings(dict(row)) for row in self.db.query_all(sql, params)]
        if include_segments:
            for plan in plans:
                plan["segments"] = self._plan_segments(plan["id"], plan.get("render_settings"))
            self._annotate_plan_quality(plans, source_id)
        return plans

    def _annotate_plan_quality(self, plans: list[dict], source_id: int | None) -> None:
        """Add a heuristic ``quality`` (0..1) to each plan and flag near-duplicates
        (``duplicate_of``) so a re-run's overlapping candidates are easy to skip."""
        from app.chat_signal import plan_score, source_signal

        cues_by_source: dict[int, list[dict]] = {}
        chat_by_source: dict[int, dict | None] = {}
        spans: list[tuple[float, float]] = []  # (start, end) of best-quality plans kept
        # Score first.
        for plan in plans:
            segs = plan.get("segments") or []
            ranges = [(float(s["start_sec"]), float(s["end_sec"])) for s in segs if s["end_sec"] > s["start_sec"]]
            total = sum(e - st for st, e in ranges)
            sid = plan.get("source_id")
            if sid not in cues_by_source:
                try:
                    cues_by_source[sid] = self.get_source_transcript(int(sid)) if sid else []
                except (KeyError, ValueError):
                    cues_by_source[sid] = []
            words = words_in_ranges(cues_by_source[sid], ranges)
            if sid not in chat_by_source:  # чат Twitch — мягкий вес, если импортирован (app/chat_signal.py)
                chat_by_source[sid] = source_signal(self, int(sid)) if sid else None
            chat = plan_score(chat_by_source[sid], plan)
            if chat is not None:
                plan["chat_score"] = chat
            plan["quality"] = clip_quality(plan.get("score", 0), total, words, chat)
            plan["_span"] = (min((r[0] for r in ranges), default=0.0), max((r[1] for r in ranges), default=0.0))
        # Flag duplicates: a lower-quality plan whose span heavily overlaps a kept one.
        for plan in sorted(plans, key=lambda p: p.get("quality", 0), reverse=True):
            s0, s1 = plan.pop("_span")
            if s1 <= s0:
                plan["duplicate_of"] = None
                continue
            dup = None
            for (k0, k1, kid) in spans:
                overlap = max(0.0, min(s1, k1) - max(s0, k0))
                if overlap > 0.6 * min(s1 - s0, k1 - k0):
                    dup = kid
                    break
            plan["duplicate_of"] = dup
            if dup is None:
                spans.append((s0, s1, plan["id"]))

    def get_clip_plan(self, clip_plan_id: int, include_segments: bool = True) -> dict:
        row = self.db.query_one("SELECT * FROM clip_plans WHERE id = ?", (clip_plan_id,))
        if not row:
            raise KeyError(f"clip plan not found: {clip_plan_id}")
        data = _decode_plan_settings(dict(row))
        if include_segments:
            data["segments"] = self._plan_segments(clip_plan_id, data.get("render_settings"))
        return data

    def _plan_segments(self, clip_plan_id: int, render_settings: Any) -> list[dict]:
        """Куски плана со скоростью/реверсом (app/piece_speed.py) и цветокоррекцией
        (app/piece_color.py) из его настроек."""
        from app.piece_color import attach as attach_color
        from app.piece_speed import attach

        return attach_color(attach(self._segments_for_clip_plan(clip_plan_id), render_settings), render_settings)

    def update_clip_plan_status(self, clip_plan_id: int, status: str) -> dict:
        self.get_clip_plan(clip_plan_id, include_segments=False)
        self._update_record(
            "clip_plans",
            clip_plan_id,
            {"status": _choice(status, VALID_CLIP_PLAN_STATUSES, "status")},
        )
        return self.get_clip_plan(clip_plan_id)

    def add_segment_to_clip_plan(
        self,
        clip_plan_id: int,
        start_sec: float,
        end_sec: float,
        title: str = "Segment",
    ) -> dict:
        plan = self.get_clip_plan(clip_plan_id)
        analysis_id = plan["analysis_id"]
        if analysis_id is None:
            analysis = self.create_ai_analysis(plan["source_id"], "mock", status="succeeded")
            analysis_id = analysis["id"]
        segment = self.create_ai_segment(
            analysis_id,
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title": title.strip() or f"Segment {len(plan['segments']) + 1}",
                "description": "Manual segment",
                "score": 0.5,
                "category": plan["category"] or "manual",
                "color": plan["color"] or "#22D3EE",
                "reason": "Created in studio preview.",
            },
            min_duration=MIN_PIECE_DURATION_SEC,
        )
        next_order = len(plan["segments"])
        self.db.execute(
            """
            INSERT INTO clip_plan_segments (clip_plan_id, segment_id, sort_order)
            VALUES (?, ?, ?)
            """,
            (clip_plan_id, segment["id"], next_order),
        )
        return self.get_clip_plan(clip_plan_id)

    def remove_segment_from_clip_plan(self, clip_plan_id: int, segment_id: int) -> dict:
        plan = self.get_clip_plan(clip_plan_id)
        if len(plan["segments"]) <= 1:
            raise ValueError("clip plan must keep at least one segment")
        self.db.execute(
            "DELETE FROM clip_plan_segments WHERE clip_plan_id = ? AND segment_id = ?",
            (clip_plan_id, segment_id),
        )
        self._renumber_clip_plan_segments(clip_plan_id)
        return self.get_clip_plan(clip_plan_id)

    def move_clip_plan_segment(self, clip_plan_id: int, segment_id: int, direction: str) -> dict:
        rows = self.db.query_all(
            """
            SELECT id, segment_id, sort_order
            FROM clip_plan_segments
            WHERE clip_plan_id = ?
            ORDER BY sort_order, id
            """,
            (clip_plan_id,),
        )
        items = [dict(row) for row in rows]
        index = next((idx for idx, item in enumerate(items) if item["segment_id"] == segment_id), -1)
        if index < 0:
            raise KeyError(f"segment not found in clip plan: {segment_id}")
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(items):
            return self.get_clip_plan(clip_plan_id)
        items[index], items[target] = items[target], items[index]
        for sort_order, item in enumerate(items):
            self.db.execute(
                "UPDATE clip_plan_segments SET sort_order = ? WHERE id = ?",
                (sort_order, item["id"]),
            )
        return self.get_clip_plan(clip_plan_id)

    def ensure_clip_plans_for_source(self, source_id: int) -> list[dict]:
        self.get_source(source_id)
        existing = self.list_clip_plans(source_id=source_id, include_segments=False)
        if existing:
            return existing
        segments = self.list_ai_segments(source_id=source_id)
        created: list[dict] = []
        for index, segment in enumerate(segments):
            created.append(
                self.create_clip_plan(
                    source_id,
                    segment["analysis_id"],
                    segment["title"],
                    description=segment["description"],
                    segment_ids=[segment["id"]],
                    score=segment["score"],
                    category=segment["category"],
                    color=segment["color"],
                    sort_order=index,
                )
            )
        return created

    def _renumber_clip_plan_segments(self, clip_plan_id: int) -> None:
        rows = self.db.query_all(
            """
            SELECT id FROM clip_plan_segments
            WHERE clip_plan_id = ?
            ORDER BY sort_order, id
            """,
            (clip_plan_id,),
        )
        for index, row in enumerate(rows):
            self.db.execute(
                "UPDATE clip_plan_segments SET sort_order = ? WHERE id = ?",
                (index, row["id"]),
            )

    # ---- История правок клипа: шаг назад (Ctrl+Z) ----------------------------

    def push_clip_plan_history(self, clip_plan_id: int, state: dict, label: str, depth: int = 20) -> None:
        """Положить снимок состояния. Одинаковый подряд не дублируем."""
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        last = self.db.query_one(
            "SELECT state_json FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 1",
            (clip_plan_id,),
        )
        if last and last["state_json"] == payload:
            return
        self.db.execute(
            "INSERT INTO clip_plan_history (clip_plan_id, state_json, label) VALUES (?, ?, ?)",
            (clip_plan_id, payload, str(label or "")[:120]),
        )
        self.db.execute(
            """
            DELETE FROM clip_plan_history
            WHERE clip_plan_id = ? AND id NOT IN (
                SELECT id FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (clip_plan_id, clip_plan_id, int(depth)),
        )

    def pop_clip_plan_history(self, clip_plan_id: int) -> dict | None:
        """Снять последний снимок (он же удаляется — иначе отмена топталась бы на месте)."""
        row = self.db.query_one(
            "SELECT id, state_json, label FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id DESC LIMIT 1",
            (clip_plan_id,),
        )
        if not row:
            return None
        self.db.execute("DELETE FROM clip_plan_history WHERE id = ?", (row["id"],))
        try:
            state = json.loads(row["state_json"])
        except (TypeError, ValueError):
            return None
        return {"state": state, "label": row["label"]}

    def count_clip_plan_history(self, clip_plan_id: int) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM clip_plan_history WHERE clip_plan_id = ?", (clip_plan_id,)
        )
        return int(row["n"]) if row else 0

    def delete_clip_plan(self, clip_plan_id: int) -> None:
        """Удалить клип-план (момент): куски уходят каскадом, готовые рендеры остаются
        (их могли опубликовать) — только отвязываются. История правок и переписка
        с агентом без внешних ключей — чистим сами."""
        self.get_clip_plan(clip_plan_id, include_segments=False)
        self.db.execute("DELETE FROM clip_plan_history WHERE clip_plan_id = ?", (clip_plan_id,))
        self.db.execute("DELETE FROM clip_plan_redo WHERE clip_plan_id = ?", (clip_plan_id,))
        self.db.execute("DELETE FROM clip_agent_messages WHERE clip_plan_id = ?", (clip_plan_id,))
        self._delete_record("clip_plans", clip_plan_id)

    def _segments_for_clip_plan(self, clip_plan_id: int) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT s.*, cps.sort_order AS clip_sort_order
            FROM clip_plan_segments cps
            JOIN ai_segments s ON s.id = cps.segment_id
            WHERE cps.clip_plan_id = ?
            ORDER BY cps.sort_order, cps.id
            """,
            (clip_plan_id,),
        )
        return [_segment_dict(row) for row in rows]
