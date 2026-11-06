"""Скриншоты всех экранов SPA для UI-прохода (docs/redesign/audit-2026-11.md).

Только против локальной копии сервиса с демо-базой (никогда не против живого :8088 —
редактор автосохраняет). Пример:

    VVF_UI=http://127.0.0.1:5297 python tools/ui_shots.py D:/vvf-offload/tmp/uipass/before

Делает кадры 1440×900 (desktop-*.png) и 390×844 (mobile-*.png).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UI = os.environ.get("VVF_UI", "http://127.0.0.1:5297").rstrip("/")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
EDITOR = "/v/1/candidates?clip=1"
CANVAS = "/v/2/candidates?clip=2"


def settle(page: Page, ms: int = 900) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:  # noqa: BLE001 - SSE/long poll keep the network busy
        pass
    page.wait_for_timeout(ms)


def click_text(page: Page, selector: str, text: str) -> bool:
    loc = page.locator(selector, has_text=text)
    if loc.count():
        loc.first.scroll_into_view_if_needed()
        loc.first.click()
        page.wait_for_timeout(400)
        return True
    return False


def insp_tab(page: Page, label: str) -> bool:
    """После прохода: вкладка инспектора. До: нет — вернёт False."""
    return click_text(page, ".insp-tab", label)


def open_group(page: Page, title: str) -> None:
    head = page.locator(".rgroup-head", has_text=title)
    if not head.count():
        return
    grp = head.first.locator("xpath=..")
    if "open" not in (grp.get_attribute("class") or ""):
        head.first.click()
        page.wait_for_timeout(300)
    head.first.scroll_into_view_if_needed()
    page.wait_for_timeout(250)


def editor_layer(page: Page) -> None:
    insp_tab(page, "Слои")
    open_group(page, "Слои клипа")
    row = page.locator(".ed-layer-row")
    if row.count():
        row.first.click()
        page.wait_for_timeout(500)
    page.locator(".ed-insp-scroll").evaluate("e => e.scrollTop = 0")


def editor_subs(page: Page) -> None:
    insp_tab(page, "Субтитры")
    open_group(page, "Субтитры")


def editor_audio(page: Page) -> None:
    if not insp_tab(page, "Звук"):
        open_group(page, "Тишина вместо звука")
        open_group(page, "Громкость видео")
    open_group(page, "Музыка")


def editor_lint(page: Page) -> None:
    insp_tab(page, "Проверка")
    open_group(page, "Проверка перед сдачей")


def editor_piece(page: Page) -> None:
    piece = page.locator(".ctl-block--piece").first
    if piece.count():
        piece.click()
        page.wait_for_timeout(400)
    insp_tab(page, "Кусок")
    open_group(page, "Скорость куска")


def editor_full(page: Page) -> None:
    """Вся правая панель одним кадром (до: длинная лента групп)."""
    page.add_style_tag(
        content=".ed-inspector,.ed-insp-scroll{max-height:none!important;height:auto!important;overflow:visible!important}"
        ".ed{height:auto!important;max-height:none!important}"
    )
    page.wait_for_timeout(300)


def publish_dialog(page: Page) -> None:
    click_text(page, ".clip-actions button, .clip-actions .btn", "Опубликовать")
    page.wait_for_timeout(600)


def workspace_tab(label: str):
    def run(page: Page) -> None:
        click_text(page, "a, button", label)
        settle(page, 500)

    return run


def series_sub(tab: str, sub: str = "", then=None):
    """Вкладка серии (ИИ-видео / Объяснялки…) + подвкладка ИИ-видео + доп. действие."""

    def run(page: Page) -> None:
        click_text(page, ".ptabs a", tab)
        settle(page, 500)
        if sub:
            click_text(page, ".aiv-subtabs .tab", sub)
            settle(page, 600)
        if then:
            then(page)

    return run


def expl_open_video(page: Page) -> None:
    # До прохода 2 сценарий раскрывался кликом по названию (.expl-topic), после — кнопкой .expl-toggle.
    loc = page.locator(".expl-toggle")
    if not loc.count():
        loc = page.locator(".expl-topic")
    if loc.count():
        loc.last.click()
        settle(page, 800)


def expl_add_pose(page: Page) -> None:
    loc = page.locator(".expl-add summary")
    if loc.count():
        loc.first.scroll_into_view_if_needed()
        loc.first.click()
        page.wait_for_timeout(400)


def scroll_to(selector: str):
    def run(page: Page) -> None:
        loc = page.locator(selector)
        if loc.count():
            loc.first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)

    return run


def last_episode(page: Page) -> None:
    sel = page.locator(".aiv select.input").first
    if sel.count():
        opts = sel.locator("option")
        sel.select_option(index=opts.count() - 1)
        settle(page, 500)


def editor_mix(page: Page) -> None:
    insp_tab(page, "Звук")
    for g in ("Музыка", "Тишина вместо звука", "Громкость видео"):
        head = page.locator(".insp-pane:not([hidden]) .rgroup-head", has_text=g)
        if head.count() and head.first.get_attribute("aria-expanded") == "true":
            head.first.click()
    open_group(page, "Сведение")
    links = page.locator(".insp-pane:not([hidden]) .fx-link summary")
    if links.count():
        links.first.click()
    btn = page.locator(".insp-pane:not([hidden]) button", has_text="Проверить сведение")
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(6000)
    open_group(page, "Сведение")


def editor_audio_layer(page: Page) -> None:
    insp_tab(page, "Слои")
    open_group(page, "Слои клипа")
    row = page.locator(".ed-layer-row", has_text=re.compile("звук|whoosh|вжух", re.I))
    if row.count():
        row.first.click()
        page.wait_for_timeout(500)
    loc = page.locator(".insp-pane:not([hidden]) .fx-chain")
    if loc.count():
        loc.first.scroll_into_view_if_needed()
        page.wait_for_timeout(300)


def editor_cover(page: Page) -> None:
    insp_tab(page, "Клип")
    open_group(page, "Обложка")


def editor_lint_run(page: Page) -> None:
    editor_lint(page)
    btn = page.locator(".insp-pane:not([hidden]) button", has_text="Проверить")
    if btn.count():
        btn.first.click()
        page.wait_for_timeout(5000)


SCREENS: list[tuple[str, str, object]] = [
    ("projects", "/projects", None),
    ("source", "/projects/1/source", None),
    ("moments", "/projects/1/candidates", None),
    ("editor", EDITOR, None),
    ("editor-layer", EDITOR, editor_layer),
    ("editor-piece", EDITOR, editor_piece),
    ("editor-subs", EDITOR, editor_subs),
    ("editor-audio", EDITOR, editor_audio),
    ("editor-lint", EDITOR, editor_lint),
    ("editor-full", EDITOR, editor_full),
    ("editor-canvas", CANVAS, None),
    ("ws-clips", "/projects/1/clips", None),
    ("ws-montaged", "/projects/1/montaged", None),
    ("clips", "/clips", None),
    ("publish", "/clips", publish_dialog),
    ("assets", "/assets", None),
    ("automation", "/automation", None),
    ("approvals", "/approvals", None),
    ("publications", "/publications", None),
    ("accounts", "/accounts", None),
    ("tasks", "/tasks", None),
    ("help", "/help", None),
    ("chat", "/chat/1", None),
    ("series", "/series/1", None),
    ("series-autopilot", "/series/1", workspace_tab("Автопилот")),
    ("aiv-script", "/series/2", None),
    ("aiv-characters", "/series/2", workspace_tab("Персонажи")),
    ("aiv-locations", "/series/2", workspace_tab("Локации")),
    ("aiv-board", "/series/2", workspace_tab("Раскадровка")),
    ("aiv-assemble", "/series/2", workspace_tab("Сборка")),
    # Проход 2: объяснялки, ИИ-видео v3, звук (сведение, роль/эффекты слоя), обложка, проверка.
    ("expl", "/series/3", series_sub("Объяснялки")),
    ("expl-video", "/series/3", series_sub("Объяснялки", then=expl_open_video)),
    ("expl-poses", "/series/3", series_sub("Объяснялки", then=scroll_to(".expl-poses"))),
    ("expl-add-pose", "/series/3", series_sub("Объяснялки", then=expl_add_pose)),
    ("expl-empty", "/series/2", series_sub("Объяснялки")),
    ("aiv3-script", "/series/2", series_sub("ИИ-видео", "Сценарий", scroll_to(".aiv-shot-edit"))),
    ("aiv3-characters", "/series/2", series_sub("ИИ-видео", "Персонажи")),
    ("aiv3-blocking", "/series/2", series_sub("ИИ-видео", "Блокинг")),
    ("aiv3-board", "/series/2", series_sub("ИИ-видео", "Раскадровка")),
    ("aiv3-board-empty", "/series/2", series_sub("ИИ-видео", "Раскадровка", last_episode)),
    ("aiv3-assemble", "/series/2", series_sub("ИИ-видео", "Сборка")),
    ("expl-clip", "/projects/2/candidates?clip=4", None),
    ("editor-mix", EDITOR, editor_mix),
    ("editor-audio-layer", EDITOR, editor_audio_layer),
    ("editor-cover", EDITOR, editor_cover),
    ("editor-lint-run", EDITOR, editor_lint_run),
] + [
    (f"settings-{seg}", f"/settings/{seg}", None)
    for seg in ("render", "banners", "audio", "subtitles", "brands", "prompts", "defaults", "modules", "proxy", "telegram", "backup")
]

MOBILE = {"projects", "moments", "editor", "editor-layer", "editor-subs", "clips", "publish", "chat", "series", "aiv-script", "settings-modules", "automation",
          "expl", "expl-video", "expl-poses", "aiv3-characters", "aiv3-blocking", "aiv3-board", "editor-mix",
          "editor-audio-layer"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROME") or None)
        for kind, size in (("desktop", (1440, 900)), ("mobile", (390, 844))):
            ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(f"{kind}: {e}"))
            for name, url, action in SCREENS:
                if ONLY and name not in ONLY:
                    continue
                if kind == "mobile" and name not in MOBILE:
                    continue
                page.goto(UI + url)
                settle(page)
                if action:
                    try:
                        action(page)  # type: ignore[operator]
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{kind}/{name}: {e}")
                    settle(page, 500)
                full = name == "editor-full" or os.environ.get("VVF_FULL") == "1"
                page.screenshot(path=str(OUT / f"{kind}-{name}.png"), full_page=full)
                print(kind, name, flush=True)
            ctx.close()
        browser.close()
    for e in errors:
        print("ERR", e)


if __name__ == "__main__":
    main()
