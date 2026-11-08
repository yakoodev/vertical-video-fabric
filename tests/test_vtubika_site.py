"""Скриншоты сайта во вставке покупки (app/vtubika_site.py): поле плана site_shots → телефон «site» с шагами;
нет снимков — остаётся «shop» с нейтральной плиткой; телефоны подряд (to_next) меняют экран на месте."""

import copy
from pathlib import Path

from app import vtubika_plan as P
from app import vtubika_site as SITE
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
TRIAL = ROOT / "tools" / "vtubika_plans" / "trial.json"


def _spec(plan: dict) -> dict:
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.9 + i * 0.4, 3), "end": round(0.9 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.6, 3)
    return to_vrm_spec(plan, P.build(plan, words, total), words, total)


def _with_shots(tmp_path: Path) -> dict:
    plan = P.load_plan(TRIAL)
    for name in ("catalog_3d.png", "product_mafuyu.png"):
        (tmp_path / name).write_bytes(b"\x89PNG\r\n")
    plan["site_shots"] = {**plan["site_shots"], "dir": str(tmp_path)}
    return plan


def test_shots_need_both_pages_on_disk(tmp_path):
    plan = _with_shots(tmp_path)
    got = SITE.shots(plan)
    assert got["catalog"]["url"] == "/site/catalog_3d.png" and got["product"]["button"] == [16, 1479, 358, 52]
    (tmp_path / "product_mafuyu.png").unlink()
    assert SITE.shots(plan) is None
    assert SITE.shots({}) is None


def test_trial_purchase_uses_real_site_with_steps_in_order(tmp_path):
    spec = _spec(_with_shots(tmp_path))
    phones = [o for o in spec["overlays"] if o["kind"] == "phone"]
    site = [o for o in phones if o["screen"] == "site"]
    assert len(site) == 1 and not any(o["screen"] == "shop" for o in phones)
    steps = [s["step"] for s in site[0]["site"]["steps"]]
    assert steps == ["product", "tap", "paid", "delivered"]
    ts = [s["t"] for s in site[0]["site"]["steps"]]
    assert ts == sorted(ts) and site[0]["t0"] < ts[0] and ts[-1] < site[0]["t1"]
    assert site[0]["site"]["title"].startswith("Mafuyu") and site[0]["site"]["price"] == "5 090 ₽"
    assert spec["siteShots"] == ["/site/catalog_3d.png", "/site/product_mafuyu.png"] and spec["siteDir"] == str(tmp_path)
    # Телефон выше субтитров (y = 0.8 кадра): низ корпуса — 440·scale + рамка от центра.
    assert site[0]["y"] + (440 + 14) * site[0]["scale"] < 0.8 * 1920 - 40


def test_to_next_phones_swap_screen_in_place():
    plan = copy.deepcopy(P.load_plan(TRIAL))
    plan.pop("site_shots")
    s5 = plan["sentences"][4]
    s5["do"][0]["phone"]["to_next"] = True
    s5["do"].append({"at": "end", "phone": {"screen": "messenger", "message": {"file": "a.zip"}}, "span": 0, "dt_end": 1.0})
    phones = [o for o in _spec(plan)["overlays"] if o["kind"] == "phone"]
    first, second = phones[0], phones[1]
    assert first["cutOut"] and second["cutIn"] and first["t1"] == second["t0"]
