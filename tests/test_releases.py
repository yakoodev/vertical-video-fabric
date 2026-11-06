"""Выпуски (app/releases.py, решения 12, 14, 16): один список поверх разных таблиц, страница шагов,
пересборка как новая версия того же клипа, правки субтитров, старые адреса. Сети и денег нет: Gemini и Whisper поддельные,
Polza не вызывается."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import clip_groups as CG
from app import explainer as E
from app import explainer_gen as X
from app import nav
from app import projects as P
from app import releases as R
from app import stories as S
from app import stories_gen as SG
from app import word_fixes as WF
from app.clip_history import capture
from test_clip_groups import add_plan, add_source
from test_explainer import PLAIN, FakeGemini, _setup, env  # noqa: F401 - env — фикстура
from test_stories import STORY, _setup as _story_setup

ROOT = Path(__file__).resolve().parents[1]


def _words(text: str, start: float = 0.0) -> list[dict]:
    out, t = [], start
    for word in text.split():
        out.append({"word": word, "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 0.4
    return out


def _render(store, plan_id: int) -> dict:
    """Рендер без компоновщика: файл не нужен, нужна версия со снимком монтажа."""
    state = capture(store, plan_id)
    row = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (plan_id,))
    cur = store.db.execute(
        "INSERT INTO clips (source_id, clip_plan_id, title, status, output_path, duration_sec, width, height, plan_fingerprint) "
        "VALUES (?, ?, 'рендер', 'succeeded', 'x.mp4', 45, 1080, 1920, ?)",
        (row["source_id"], plan_id, CG.state_fingerprint(state)),
    )
    CG.add_snapshot(store, plan_id, state, clip_id=int(cur.lastrowid), kind="render")
    return store.get_clip(int(cur.lastrowid))


def _done_video(store, tmp) -> tuple[dict, dict]:
    ids = _setup(store, tmp)
    video = X.create(store, ids["mascot_id"], "сон", 45, script=PLAIN, run=True, client=FakeGemini())
    assert video["status"] == "done", video["error"]
    return ids, video


# ---- правки субтитров: чистые функции ----------------------------------------------------------------


def test_find_fixes_sees_text_not_tidying():
    base = _words("спишь восемь часов а встаёшь разбитым")
    same = _words("спишь восемь часов а встаёшь разбитым")
    assert WF.find_fixes(base, same) == {"fixes": [], "timing": 0}
    changed = [dict(w) for w in base]
    changed[1]["word"] = "девять"
    found = WF.find_fixes(base, changed)
    assert [(f["was"], [w["word"] for w in f["now"]]) for f in found["fixes"]] == [(["восемь"], ["девять"])]
    moved = [dict(w) for w in base]
    moved[2]["start"] += 0.5
    assert WF.find_fixes(base, moved)["timing"] == 1


def test_carry_fixes_survive_new_timing_and_changed_script():
    base = _words("спишь восемь часов а встаёшь разбитым")
    fixed = [dict(w) for w in base]
    fixed[1]["word"] = "девять"
    fixes = WF.find_fixes(base, fixed)["fixes"]
    # Биты поменялись, текст тот же: новое время, правка на месте.
    new = _words("спишь восемь часов а встаёшь разбитым", 1.0)
    words, carried, lost = WF.carry_fixes(base, fixes, new)
    assert [w["word"] for w in words][1] == "девять" and carried and not lost
    assert words[1]["start"] == new[1]["start"]
    # Сценарий переписали ровно в этом месте: правка потеряна, и это сказано словами.
    new = _words("спишь мало а встаёшь разбитым")
    words, carried, lost = WF.carry_fixes(base, fixes, new)
    assert "девять" not in [w["word"] for w in words] and not carried and len(lost) == 1
    note = WF.summary({"fixes": fixes, "timing": 0}, carried, lost)["note"]
    assert "Не перенесены" in note and "восемь → девять" in note


def test_carry_fixes_many_to_one_and_added_word():
    base = _words("всего двадцать пять минут в день")
    current = [*_words("всего 25 минут"), *_words("только в день", 2.0)]
    found = WF.find_fixes(base, current)
    labels = {WF.label(f) for f in found["fixes"]}
    assert "двадцать пять → 25" in labels and "добавлено: только" in labels
    words, carried, lost = WF.carry_fixes(base, found["fixes"], _words("всего двадцать пять минут в день", 3.0))
    assert [w["word"] for w in words] == ["всего", "25", "минут", "только", "в", "день"] and not lost and len(carried) == 2


# ---- пересборка: новая версия, а не новый клип --------------------------------------------------------


def test_reassembly_makes_a_new_version_of_the_same_clip(env):  # noqa: F811
    client, store, tmp = env
    _ids, video = _done_video(store, tmp)
    first = video["clip_id"]
    render = _render(store, first)
    before = store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"]

    r = client.post(f"/api/explainer/videos/{video['id']}/continue", json={"reassemble": True})
    assert r.status_code == 200, r.text
    again = E.get_video(store, video["id"])
    assert again["status"] == "done" and again["clip_id"] != first
    assert store.db.query_one("SELECT COUNT(*) AS n FROM clip_plans")["n"] == before + 1

    # Один клип с двумя планами: старый адрес открывает тот же экран, главный — новый план.
    members = CG.group_members(store, first)
    assert [int(m["id"]) for m in members] == [first, again["clip_id"]]
    view = client.get(f"/api/clip-view/{first}").json()
    assert view["id"] == again["clip_id"] and len(view["plans"]) == 2
    assert view["release"]["key"] == f"expl-{video['id']}"
    # Старая версия на месте: её рендер виден и не тронут.
    assert any(v["render_id"] == render["id"] and v["url"] for v in view["versions"])
    # В проекте всё ещё одна строка выпуска и один клип.
    rows = nav.project_videos(store, video["project_id"])["generated"]
    assert [g["key"] for g in rows] == [f"expl-{video['id']}"] and rows[0]["clip_count"] == 1
    assert again["timeline"]["version"]["linked"] is True

    # Откат: монтаж старой версии вернулся на главный план, ни одна версия не пропала.
    store.db.execute("UPDATE clip_plans SET render_settings_json = json_set(render_settings_json, '$.layers', json('[]')) WHERE id = ?",
                     (again["clip_id"],))
    assert not store.get_clip_plan(again["clip_id"])["render_settings"].get("layers")
    out = client.post(f"/api/clip-view/{first}/rollback", json={"render_id": render["id"]})
    assert out.status_code == 200, out.text
    assert out.json()["id"] == again["clip_id"]
    assert store.get_clip_plan(again["clip_id"])["render_settings"]["layers"]
    assert any(v["render_id"] == render["id"] for v in client.get(f"/api/clip-view/{first}").json()["versions"])


def test_failed_assembly_does_not_become_the_clip(env, monkeypatch):  # noqa: F811
    _client, store, tmp = env
    _ids, video = _done_video(store, tmp)
    first = video["clip_id"]
    E.set_status(store, video["id"], "assembling", ("done",))
    monkeypatch.setattr(X, "auto_mix_clip", lambda ctx, notes: (_ for _ in ()).throw(RuntimeError("сведение упало")))
    out = X.advance(store, video["id"])
    assert out["status"] == "failed" and "сведение упало" in out["error"]
    assert out["clip_id"] == first  # выпуск всё ещё показывает рабочий клип
    assert [int(m["id"]) for m in CG.group_members(store, first)] == [first]
    rows = nav.project_videos(store, video["project_id"])["generated"]
    assert len(rows) == 1 and rows[0]["head"] == first  # упавшая сборка не легла лишней строкой
    assert rows[0]["older"] == []  # и не считается старой версией клипа


def test_other_format_stays_a_separate_clip(env):  # noqa: F811
    _client, store, tmp = env
    _ids, video = _done_video(store, tmp)
    first = video["clip_id"]
    render = dict(store.get_clip_plan(first)["render_settings"])
    store.set_clip_plan_render_settings(first, {**render, "format": "16:9"})
    E.set_status(store, video["id"], "assembling", ("done",))
    again = X.advance(store, video["id"])
    assert again["status"] == "done" and again["clip_id"] != first
    assert again["timeline"]["version"]["linked"] is False
    assert [int(m["id"]) for m in CG.group_members(store, again["clip_id"])] == [again["clip_id"]]
    assert any("разные клипы" in n for n in again["notes"])


def test_clip_moved_to_another_project_keeps_new_versions_there(env):  # noqa: F811
    _client, store, tmp = env
    _ids, video = _done_video(store, tmp)
    other = P.create_project(store, "Сова", kind="other")["id"]
    P.assign_plan_project(store, video["clip_id"], other)
    E.set_status(store, video["id"], "assembling", ("done",))
    again = X.advance(store, video["id"])
    assert P.project_of_plan(store, again["clip_id"]) == other


def test_changing_beats_keeps_word_fixes_and_says_so(env):  # noqa: F811
    client, store, tmp = env
    ids, video = _done_video(store, tmp)
    first = video["clip_id"]
    subs = store.get_clip_plan_subtitles(first)
    words = [dict(w) for w in subs["words"]]
    assert words[0]["word"] != "Исправлено"
    original = words[0]["word"]
    words[0]["word"] = "Исправлено"
    store.set_clip_plan_subtitles(first, {**subs, "words": words})

    # До пересборки: страница выпуска и итог агента говорят, что будет.
    view = R.view(store, f"expl-{video['id']}")
    assert view["reassemble"]["subtitle_fixes"]["count"] == 1
    assert f"{original} → Исправлено" in view["reassemble"]["note"]
    assert "новая версия" in view["reassemble"]["note"].lower()

    poses = E.list_poses(store, ids["mascot_id"])
    changed = client.patch(f"/api/explainer/videos/{video['id']}/beats", json={"beats": [{"i": 0, "pose_id": poses[0]["id"]}]})
    assert changed.status_code == 200, changed.text
    # Правка битов не теряет клип и слова прошлой сборки.
    mid = E.get_video(store, video["id"])
    assert mid["clip_id"] == first and mid["words"]
    assert "Исправлено" in X.facts(store, video["id"], mid)["summary"]

    out = X.advance(store, video["id"], client=FakeGemini())
    assert out["status"] == "done", out["error"]
    assert out["clip_id"] != first
    new_words = [w["word"] for w in store.get_clip_plan_subtitles(out["clip_id"])["words"]]
    assert new_words[0] == "Исправлено" and original not in new_words[:1]
    fixes = out["timeline"]["subtitle_fixes"]
    assert fixes["count"] == 1 and not fixes["lost"]
    assert any("Правки субтитров перенесены" in n for n in out["notes"])
    assert "Правки субтитров перенесены" in X.facts(store, video["id"], out)["summary"]
    # Прошлая версия с правкой осталась нетронутой.
    assert store.get_clip_plan_subtitles(first)["words"][0]["word"] == "Исправлено"
    assert [int(m["id"]) for m in CG.group_members(store, first)] == [first, out["clip_id"]]


def test_rewritten_script_says_which_fix_is_lost(env):  # noqa: F811
    _client, store, tmp = env
    _ids, video = _done_video(store, tmp)
    first = video["clip_id"]
    subs = store.get_clip_plan_subtitles(first)
    words = [dict(w) for w in subs["words"]]
    words[0]["word"] = "Исправлено"
    store.set_clip_plan_subtitles(first, {**subs, "words": words})
    script = {**PLAIN, "hook": "Совсем другое начало ролика про бодрость?"}
    X.set_script(store, video["id"], script, "owner")
    assert E.get_video(store, video["id"])["clip_id"] == first
    out = X.advance(store, video["id"], client=FakeGemini())
    assert out["status"] == "done", out["error"]
    fixes = out["timeline"]["subtitle_fixes"]
    assert fixes["lost"] and "Не перенесены" in fixes["note"]
    assert any("Не перенесены" in n for n in out["notes"])


# ---- слой выпусков ---------------------------------------------------------------------------------


def test_owl_project_view_groups_old_assemblies_and_labels_canvases(env):  # noqa: F811
    from app.canvas import create_canvas_clip

    client, store, tmp = env
    ids, video = _done_video(store, tmp)
    pid = ids["project_id"]
    head = video["clip_id"]
    topic = E.get_video(store, video["id"])["topic"]
    # Сборка старых дней: отдельный план с тем же описанием, проекта нет.
    old = create_canvas_clip(store, 10, None, "Поел, а через час снова голодный", f"Объяснялка (Ух): {topic}")
    # Безымянный холст и холст с названием; черновик сценария; другое видео.
    unnamed = create_canvas_clip(store, 10, None, "", "")
    P.assign_plan_project(store, unnamed["id"], pid)
    named = create_canvas_clip(store, 10, None, "Пилот маскот: 3 ошибки сна", "")
    P.assign_plan_project(store, named["id"], pid)
    from app import aivideo as V

    draft = V.create_script(store, pid, brief="сова и идеальный баланс")

    rows = {r["key"]: r for r in nav.project_videos(store, pid)["generated"]}
    expl = rows[f"expl-{video['id']}"]
    assert expl["kind_key"] == "expl" and expl["to"] == f"/clips/{head}" and expl["steps_to"] == f"/r/expl-{video['id']}?steps=1"
    assert expl["clip_count"] == 1 and old["id"] in expl["older"]
    assert f"canvas-{old['id']}" not in rows  # старая сборка не лишняя строка
    assert rows[f"aiv-{draft['id']}"]["status"] == "empty" and rows[f"aiv-{draft['id']}"]["to"] == f"/r/aiv-{draft['id']}"
    hole = rows[f"canvas-{unnamed['id']}"]
    assert hole["unnamed"] is True and hole["name"].startswith("Холст без названия") and hole["to"] == f"/clips/{unnamed['id']}"
    assert rows[f"canvas-{named['id']}"]["unnamed"] is False and rows[f"canvas-{named['id']}"]["name"] == "Пилот маскот: 3 ошибки сна"
    # Счётчик панели и список — одно и то же.
    counted = {p["id"]: p for p in nav.nav_counts(store)["projects"]}[pid]
    assert counted["videos"] == len(rows) and counted["releases"] == len(rows)

    # Страница выпуска: шаги по порядку, всё сделано; старая сборка названа.
    r = client.get(f"/api/releases/expl-{video['id']}")
    assert r.status_code == 200, r.text
    view = r.json()
    assert [s["label"] for s in view["steps"]] == ["Тема", "Сценарий", "Позы", "Озвучка", "Сборка", "Клип"]
    assert all(s["state"] == "done" for s in view["steps"])
    assert view["older"][0]["id"] == old["id"] and view["clips"][0]["id"] == head and view["approval"] is None
    assert client.get("/api/releases/expl-99999").status_code == 404
    # Холст открывается по старому ключу, и когда главным стал другой план.
    assert client.get(f"/api/releases/canvas-{unnamed['id']}").json()["to"] == f"/clips/{unnamed['id']}"


def test_step_states_follow_the_pipeline(env):  # noqa: F811
    _client, store, tmp = env
    ids = _setup(store, tmp)
    video = E.create_video(store, ids["mascot_id"], "сон", 45, "", {}, "ui")
    key = f"expl-{video['id']}"

    def states(k):
        return {s["key"]: s["state"] for s in R.view(store, k)["steps"]}

    assert states(key)["script"] == "now" and states(key)["clip"] == "todo"
    X.set_script(store, video["id"], PLAIN, "owner")
    assert states(key)["script"] == "done" and states(key)["images"] == "now"
    X.plan_beats(store, video["id"], client=FakeGemini())
    E.set_status(store, video["id"], "failed", None, error="сборка: что-то сломалось")
    assert states(key)["build"] == "failed" and states(key)["voice"] == "done"
    assert R.view(store, key)["status"] == "failed"


def test_cost_card_is_shown_on_the_release(env):  # noqa: F811
    from app import aivideo_gen as G

    _client, store, tmp = env
    ids = _setup(store, tmp, provider="polza", voice={"provider": "polza", "voice": "liam", "model": "elevenlabs/eleven-v4"})
    video = X.create(store, ids["mascot_id"], "сон", 45, script=PLAIN, run=True, client=FakeGemini())
    assert video["status"] == "awaiting_approval", video["error"]
    view = R.view(store, f"expl-{video['id']}")
    assert view["status"] == "approval" and view["approval"]["amount_rub"] > 0
    # Покупать надо только голос: ожидание на шаге озвучки, позы из библиотеки уже есть.
    assert {s["key"]: s["state"] for s in view["steps"]}["voice"] == "wait"
    assert {s["key"]: s["state"] for s in view["steps"]}["images"] == "done"
    rows = nav.project_videos(store, ids["project_id"])["generated"]
    assert rows[0]["status"] == "approval" and rows[0]["estimate_rub"] > 0 and rows[0]["to"] == f"/r/expl-{video['id']}"
    # Отказ — и карточки больше нет, а денег не потрачено.
    assert G.spent_approval(store, video["approval_id"]) == 0


def test_ladder_project_of_a_clip_without_assignment_comes_from_release(env):  # noqa: F811
    client, store, tmp = env
    ids, video = _done_video(store, tmp)
    P.assign_plan_project(store, video["clip_id"], None)
    view = client.get(f"/api/clip-view/{video['clip_id']}").json()
    assert view["project"]["id"] == ids["project_id"]


# ---- истории: голос и субтитры как у объяснялок --------------------------------------------------------


def test_story_on_default_project_gets_real_voice_not_tone(env):  # noqa: F811
    _client, store, tmp = env
    ids = _story_setup(store, tmp, music=False, sounds=False)
    world = S.get_world(store, ids["world_id"])
    # Провайдер проекта none (значение по умолчанию): голос мира всё равно настоящий, тон только по явной опции.
    assert S.world_voice(store, world)["provider"] == "gemini"
    assert S.world_voice(store, world, True)["provider"] == "none"
    assert S.world_voice(store, {**world, "voice": {"provider": "none"}})["provider"] == "gemini"


def test_story_captions_are_the_script_on_speech_timing(env, monkeypatch):  # noqa: F811
    import test_stories as TS

    _client, store, tmp = env
    ids = TS._setup(store, tmp, music=False, sounds=False)
    ep = SG.create(store, ids["world_id"], "побег", 50, script=STORY, options={"dry_run": True}, run=True)
    assert ep["status"] == "done", ep["error"]
    assert ep["timeline"]["dry_run"] is True and ep["timeline"]["captions"]["timing"] == "text_length"
    assert store.get_clip_plan(ep["clip_id"])["title"].startswith(E.DRY_MARK)

    # Настоящая озвучка: Whisper ошибся в слове, в субтитрах остаётся слово сценария, время — распознанное.
    text = S.narration_text(ep)
    heard = [{"word": ("ОШИБКА" if i == 2 else w), "start": round(i * 0.4, 3), "end": round(i * 0.4 + 0.3, 3)}
             for i, w in enumerate(text.split())]
    monkeypatch.setattr(X, "transcribe_words", lambda path: heard)
    ref = S.narration_ref(store, ep)
    words, share = X.real_words(store, text, ref, Path("x"), S.StoryError, "эпизод")
    assert share >= X.VOICE_MIN_HEARD and "ОШИБКА" not in [w["word"] for w in words]
    # Whisper не встал: настоящий эпизод стоит с понятным текстом, а не молча раскладывает слова по длине текста.
    monkeypatch.setattr(X, "transcribe_words", lambda path: (_ for _ in ()).throw(RuntimeError("нет whisper")))
    with pytest.raises(S.StoryError, match="эпизод не собираю"):
        X.real_words(store, text, ref, Path("x"), S.StoryError, "эпизод")


def test_story_reassembly_is_a_new_version(env):  # noqa: F811
    import test_stories as TS

    _client, store, tmp = env
    ids = TS._setup(store, tmp, music=False, sounds=False)
    ep = SG.create(store, ids["world_id"], "побег", 50, script=STORY, options={"dry_run": True}, run=True)
    first = ep["clip_id"]
    again = SG.reassemble(store, ep["id"])
    assert again["status"] == "done", again["error"]
    assert [int(m["id"]) for m in CG.group_members(store, first)] == [first, again["clip_id"]]
    view = R.view(store, f"story-{ep['id']}")
    assert [s["label"] for s in view["steps"]][:3] == ["Завязка", "Сценарий", "Картинки и актёры"]
    assert view["clip_count"] == 1


# ---- данные старых дней: скрипт ------------------------------------------------------------------------


def _run_script(db: Path, *args: str) -> str:
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "link_release_versions.py"), "--db", str(db), *args],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout


def test_script_is_dry_by_default_and_idempotent(env):  # noqa: F811
    from app.canvas import create_canvas_clip

    _client, store, tmp = env
    old = create_canvas_clip(store, 10, None, "Старая сборка", "Объяснялка (Ух): сон")
    _ids, video = _done_video(store, tmp)
    head = video["clip_id"]
    P.assign_plan_project(store, head, None)  # клип старых дней без проекта
    db = Path(store.db.path) if hasattr(store.db, "path") else None
    assert db and db.exists()
    copy = tmp / "copy.sqlite"
    src = sqlite3.connect(db)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.close()
    query = "SELECT clip_group_id FROM clip_plans WHERE id = ?"

    out = _run_script(copy)
    assert "станут одним клипом" in out and "Ничего не записано" in out
    conn = sqlite3.connect(copy)
    assert conn.execute(query, (old["id"],)).fetchone()[0] is None
    conn.close()

    assert "Записано" in _run_script(copy, "--apply")
    conn = sqlite3.connect(copy)
    numbers = {conn.execute(query, (plan,)).fetchone()[0] for plan in (old["id"], head)}
    assert len(numbers) == 1 and None not in numbers
    assigned = {r[0] for r in conn.execute("SELECT clip_plan_id FROM plan_projects")}
    assert {old["id"], head} <= assigned
    conn.close()
    again = _run_script(copy)
    assert "Выпусков со старыми сборками: 0" in again and "Клипов без проекта: 0" in again


# ---- промо и ТОП-N из «Новое видео» ---------------------------------------------------------------------


def test_promo_builds_a_clip_and_refusal_leaves_no_row(env):  # noqa: F811
    from app.montage_assets import assets_dir, probe_asset

    client, store, tmp = env
    pid = P.create_project(store, "Промо", kind="other")["id"]
    path = assets_dir() / "rec.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x223344:s=180x320:d=8", str(path)], check=True)
    asset = store.create_montage_asset(kind="video", label="запись", description="", tags="", file_path=str(path),
                                       original_filename="rec.mp4", mime_type="video/mp4", size_bytes=path.stat().st_size,
                                       project_id=pid, **probe_asset(path))
    spec = {"brand": {"accent": "#ff6a1f"}, "hook": ["Ищешь коллаб?", "Свайпай."],
            "scenes": [{"asset_id": asset["id"], "from": 0, "to": 6, "title": "Лента — наших стримеров"}],
            "end": {"cta": "example.com", "lines": ["Уже можно"]}, "music": None}
    bad = client.post(f"/api/projects/{pid}/promo", json={"title": "Плохое", "spec": {**spec, "scenes": [{**spec["scenes"][0], "asset_id": 999999}]}})
    assert bad.status_code == 400 and "999999" in bad.text
    assert nav.project_videos(store, pid)["generated"] == []
    good = client.post(f"/api/projects/{pid}/promo", json={"title": "Приложение", "spec": spec})
    assert good.status_code == 200, good.text
    clip = good.json()["clip_id"]
    rows = nav.project_videos(store, pid)["generated"]
    assert [r["kind_key"] for r in rows] == ["promo"] and rows[0]["to"] == f"/clips/{clip}" and rows[0]["name"] == "Приложение"
    layers = store.get_clip_plan(clip)["render_settings"]["layers"]
    assert any(str(lay.get("name") or "").startswith("promo:") for lay in layers)


def test_compilation_is_listed_as_a_top_release(env):  # noqa: F811
    client, store, _tmp = env
    pid = P.create_project(store, "Мура", kind="other")["id"]
    source = add_source(store, project_id=pid)
    plan = add_plan(store, source, "ТОП-3 моментов стрима")
    store.update_clip_plan_text(plan["id"], plan["title"], "Компиляция: один · два · три", "")
    rows = nav.project_videos(store, pid)["generated"]
    assert [(r["kind_key"], r["to"]) for r in rows] == [("top", f"/clips/{plan['id']}")]
    view = client.get(f"/api/clip-view/{plan['id']}").json()
    assert view["release"]["kind"] == "top" and view["release"]["has_steps"] is False


# ---- вкладки и старые адреса ----------------------------------------------------------------------------


def test_old_project_tabs_only_redirect():
    """?tab=expl и другие остались как адреса, но вкладок больше нет."""
    tabs = (ROOT / "frontend" / "src" / "lib" / "tabs.ts").read_text(encoding="utf-8")
    block = tabs.split("export const PROJECT_TABS", 1)[1].split("];", 1)[0]
    assert not any(f'key: "{k}"' in block for k in ("aiv", "channel", "expl", "story"))
    assert 'key: "materials"' in block
    redirect = tabs.split("export function movedProjectTab", 1)[1].split("\n}\n", 1)[0]
    for old in ("expl", "story", "aiv", "channel"):
        assert f'"{old}"' in redirect
    page = (ROOT / "frontend" / "src" / "pages" / "SeriesPage.tsx").read_text(encoding="utf-8")
    assert "movedProjectTab" in page and "<Navigate" in page


def test_every_type_card_is_on_the_chooser():
    page = (ROOT / "frontend" / "src" / "pages" / "release" / "NewVideoPage.tsx").read_text(encoding="utf-8")
    for title in ("Ссылка или файл", "История", "ИИ-видео", "3D-серия", "Объяснялка", "Промо приложения", "Пустой холст", "ТОП-N"):
        assert f'title: "{title}"' in page
