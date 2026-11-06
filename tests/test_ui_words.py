"""Слова интерфейса (docs/redesign/concept-2026-11.md, «Словарь»): старые названия не должны вернуться.

Проверка статическая: комментарии убираем, в строках и подписях ищем запрещённое. Комментарии и имена
в коде (`/candidates`, таблицы базы) не считаются: адреса и таблицы не переименовываем.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"

BANNED = {
    "План #id": r"План #",
    "кандидат": r"[Кк]андид",
    "исходник": r"[Ии]сходник|\bисх\.",
    "Моменты как название вкладки": r"\bМоменты\b|«Момент[а-я]*»",
    "Кадр источника": r"Кадр источника",
    "путь к файлу кода": r"tools/\w+\.py|render_worker",
    "чип 480p в плеере": r">480p<",
}
# Имена компонентов, где в коде есть эти буквы: на экран не попадают.
CODE_NAMES = re.compile(r"\b[A-Za-z_]*[Cc]andidate[A-Za-z_]*\b")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", text)


def ui_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*") if p.suffix in {".ts", ".tsx"})


def test_strip_comments_keeps_strings_and_urls():
    text = 'const a = "https://x.y/z"; // коммент\n/* блок\nисходник */ const b = "текст";\n{/* ещё */}'
    out = strip_comments(text)
    assert "https://x.y/z" in out and "текст" in out
    assert "коммент" not in out and "исходник" not in out and "ещё" not in out


def test_old_words_do_not_come_back():
    found = []
    for path in ui_files():
        code = CODE_NAMES.sub("", strip_comments(path.read_text(encoding="utf-8")))
        for name, pattern in BANNED.items():
            for match in re.finditer(pattern, code):
                line = code[: match.start()].count("\n") + 1
                found.append(f"{path.relative_to(SRC)} (без комментариев, строка {line}): {name}: {match.group(0)}")
    assert not found, "старые слова в интерфейсе:\n" + "\n".join(found)


def test_module_descriptions_use_the_same_words():
    """Описания модулей видны в «Настройки → Модули»."""
    text = (ROOT / "app" / "modules.py").read_text(encoding="utf-8")
    assert not re.search(r"«Момент|«Исходник|[Кк]андидат", text)


def _labels(block: str) -> set[str]:
    return set(re.findall(r'label: "([^"]+)"', block))


def test_hints_name_only_existing_tabs():
    """Подсказки вида «на вкладке «X»» ведут только на вкладки, которые есть."""
    tabs = (SRC / "lib" / "tabs.ts").read_text(encoding="utf-8")
    workspace = SRC / "pages" / "workspace"
    inspector = (workspace / "editor" / "draftsEditorSession.tsx").read_text(encoding="utf-8")  # вкладки инспектора
    inspector += (workspace / "editor" / "editorHelpers.ts").read_text(encoding="utf-8")  # вкладки библиотеки
    known = _labels(tabs) | _labels(inspector) | {"Сценарий"}  # подвкладка ИИ-видео, на своём экране
    wrong = []
    for path in ui_files():
        code = strip_comments(path.read_text(encoding="utf-8"))
        for name in re.findall(r"[Вв]кладк[аеиуы]\s+«([^»]+)»", code):
            if name not in known:
                wrong.append(f"{path.relative_to(SRC)}: {name}")
    assert not wrong, "подсказки про вкладки, которых нет:\n" + "\n".join(wrong)


def test_tab_lists_live_in_one_module():
    """Вкладки видео и разделов не перечисляют заново: только lib/tabs.ts."""
    for rel in ("pages/VideoPage.tsx", "components/AppShell.tsx", "components/CommandPalette.tsx",
                "components/shell/Ladder.tsx", "pages/InboxPage.tsx", "pages/PubsPage.tsx", "pages/SettingsPage.tsx",
                "pages/project/ProjectPage.tsx"):
        code = strip_comments((SRC / rel).read_text(encoding="utf-8"))
        assert "@/lib/tabs" in code, rel
        for label in ('"Смонтированные"', '"Пресеты рендера"', '"По проектам"'):
            assert label not in code, f"{rel}: {label}"


def test_shortcut_help_comes_from_the_tab_list():
    shell = (SRC / "components" / "AppShell.tsx").read_text(encoding="utf-8")
    assert "tabShortcuts(on)" in shell
    assert "вкладки видео:" not in shell
