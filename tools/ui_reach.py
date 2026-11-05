"""Проверка «ни одна функция не пропала»: каждый контрол из docs/redesign/functions.md
достижим в SPA (видим после описанных кликов: вкладка, группа, «Ещё…»).

Только против локальной копии с демо-базой (tools/ui_shots.py — как поднять), не против
живого сервиса. Пример:

    VVF_UI=http://127.0.0.1:5297 python tools/ui_reach.py

Код выхода 1, если что-то не нашлось; список недостижимого — в конце вывода.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

from playwright.sync_api import Page, sync_playwright

UI = os.environ.get("VVF_UI", "http://127.0.0.1:5297").rstrip("/")
EDITOR = "/v/1/candidates?clip=1"


@dataclass
class Check:
    name: str  # что ищем (подпись/плейсхолдер/title)
    steps: list[tuple[str, str]] = field(default_factory=list)  # ("tab"|"group"|"more"|"click"|"key", arg)
    sel: str = ""  # css-селектор, если подпись не годится
    kind: str = "text"  # text | placeholder | title | css


def T(name: str, *steps: tuple[str, str], sel: str = "", kind: str = "text") -> Check:
    return Check(name, list(steps), sel, kind)


tab = lambda s: ("tab", s)  # noqa: E731 - вкладка инспектора редактора
grp = lambda s: ("group", s)  # noqa: E731
more = ("more", "")
click = lambda s: ("click", s)  # noqa: E731
key = lambda s: ("key", s)  # noqa: E731

SCREENS: dict[str, tuple[str, list[Check]]] = {
    "shell": ("/projects", [
        *(T(n, sel=".side") for n in ["Проекты", "Новый проект", "Входящие", "Публикации и аккаунты", "Общие файлы", "Клавиши", "Помощь", "Настройки", "Выйти"]),
        *(T(n, sel=".lad") for n in ["Проект", "Видео", "Клип"]),
        T("Поиск", sel=".tools"), T("Общий чат", sel=".tools"), T("задач", sel=".tools"),
        T("панель задач", click(".activity .tool"), sel=".activity-panel", kind="css"),
        T("палитра Ctrl K", key("Control+k"), sel=".cmdk input", kind="css"),
        T("горячие клавиши ?", key("?"), sel=".keys-modal", kind="css"),
        T("новое видео N", key("n"), sel=".create-modal", kind="css"),
        T("общий чат", click(".tools .tool >> text=Общий чат"), sel=".drawer", kind="css"),
    ]),
    "projects": ("/projects", [
        T("Загрузить файл"), T("ссылка", kind="placeholder"), T("Качество скачивания", kind="title"), T("Добавить"), T("Новый холст"),
        T("Все"), T("Обработка"), T("Готово"), T("Ошибка"),
        T("demo-stream.mp4"), T("переименовать", sel=".pcard-edit, [title*=Переимен]", kind="css"),
        T("удалить видео", sel="[title*=Удалить], .pcard-del", kind="css"),
        T("Новый проект", sel=".series-strip"),
    ]),
    "source": ("/v/1/source", [
        *(T(n, sel=".pipeline") for n in ["Исходник", "Моменты", "Монтаж", "Клипы", "Смонтированные"]),
        T("Статус анализа"), T("Кадр источника"), T("Найти полосы"), T("Сверху"), T("Снизу"),
        T("Запустить анализ"), T("Пресет анализа"), T("Провайдер"), T("Свой промпт", kind="placeholder"), T("Транскрипт"),
        T("Анализировать"), T("Анализы"),
    ]),
    "moments": ("/v/1/candidates", [
        T("Открыть редактор"), T("Рендерить выбранные"), T("ИИ выберет лучшие"), T("ИИ-монтаж всех"),
        T("Поиск по названию", kind="placeholder"), T("Избранное"), T("Выбрать все|Снять все", kind="regex"), T("Mock short #1"),
    ]),
    "editor": (EDITOR, [
        T("Назад к моментам"), T("Шаг назад"), T("ИИ-монтаж", sel=".ed-viewbar"),
        *(T(n, sel=".ed-tabs") for n in ["Моменты", "Кадр", "ИИ", "Шаблоны", "Текст", "Стикеры", "Звуки", "Мемы", "Создать", "Вставки"]),
        T("Превью клипа"), T("Сегмент", sel=".ed-transport"), T("Стоп", sel=".ed-transport"), T("Зациклить"),
        T("Фикс. рамка"), T("Авто", sel=".ed-fixed"),
        # Клип
        T("Формат", tab("Клип"), sel=".insp-pane:not([hidden])"), T("Стоп-кадр в конце", tab("Клип")),
        T("Автомонтаж", tab("Клип")), T("Найти мемы", tab("Клип")), T("Применить бренд", tab("Клип")),
        T("Пресет", tab("Клип"), grp("Картинка (лук)"), sel=".insp-pane:not([hidden])"), T("Зеркало", tab("Клип"), grp("Картинка (лук)")),
        T("Раскладка", tab("Клип"), grp("Раскладка кадра"), sel=".insp-pane:not([hidden]) .rgroup.open"),
        T("Склейка кусков", tab("Клип"), grp("Переходы")), T("Звук на стыке", tab("Клип"), grp("Переходы")),
        T("Сделать обложку", tab("Клип"), grp("Обложка")), T("Кадр из клипа", tab("Клип"), grp("Обложка")), T("Своя картинка", tab("Клип"), grp("Обложка")),
        T("Накладывать баннер", tab("Клип"), grp("Баннер")),
        T("Паузы и слова-паразиты", tab("Клип")),
        T("Стратегия", tab("Клип"), more, grp("Умный кадр")), T("Пресет детекции", tab("Клип"), more, grp("Умный кадр")),
        T("Уточнять кадр через Gemini", tab("Клип"), more, grp("Умный кадр")),
        T("Автофокус: все клипы", tab("Клип"), more, grp("Умный кадр")), T("Только этот план", tab("Клип"), more, grp("Умный кадр")),
        T("Пересчитать", tab("Клип"), more, grp("Границы клипов")),
        T(".otio", tab("Клип"), more, grp("Отдать в монтажку")), T(".edl", tab("Клип"), more, grp("Отдать в монтажку")), T(".xml", tab("Клип"), more, grp("Отдать в монтажку")),
        # Слои
        T("Слои клипа", tab("Слои")), T("Хук", tab("Слои"), grp("Слои клипа"), sel=".ed-layer-list"),
        T("Вставки (мемы)", tab("Слои")), T("Камера (наезды)", tab("Слои")),
        T("Текст", tab("Слои"), grp("Слои клипа"), click(".ed-layer-row"), sel=".insp-pane:not([hidden])"),
        # Кусок
        T("Кусок 1 из 1", tab("Кусок")), T("Скорость куска", tab("Кусок")), T("Цвет куска", tab("Кусок")),
        T("Детектор", tab("Кусок"), grp("Фокус кадра")), T("Точка", tab("Кусок"), grp("Фокус кадра")),
        T("Сохранить фокус", tab("Кусок"), grp("Фокус кадра")), T("Очистить", tab("Кусок"), grp("Фокус кадра"), click(".focus-ed button >> text=Точка")),
        # Звук
        T("Фоновый трек", tab("Звук"), grp("Музыка")), T("Заглушить мат", tab("Звук"), grp("Тишина вместо звука")),
        T("Громче у курсора", tab("Звук"), grp("Громкость исходника")), T("Звуковые дорожки", tab("Звук")),
        # Субтитры
        T("Накладывать субтитры", tab("Субтитры")), T("Править строки широко", tab("Субтитры")),
        T("Распознать", tab("Субтитры")), T("Голоса", tab("Субтитры"), sel=".subed-top"), T("Ключевые", tab("Субтитры")),
        T("Движок", tab("Субтитры"), click(".insp-pane:not([hidden]) .switch"), sel=".insp-pane:not([hidden])"),
        # Проверка
        T("Проверить", tab("Проверка")), T("по рендеру", tab("Проверка")),
        # Подвал и таймлайн
        T("Зоны", sel=".ed-insp-foot"), T("Файл клипа"), T("Ко всем"), T("Рендерить (1)"), T("Выбранные (", sel=".ed-insp-foot"),
        T("Fit"), T("Разделить"), T("Заглушить", sel=".ctl-tools, .ed-bottom"), T("К долям"),
    ]),
    "canvas": ("/v/2/candidates?clip=2", [
        T("Фон холста", tab("Клип")), T("Музыка", tab("Звук")),
    ]),
    "clips": ("/pubs?tab=ready", [
        T("Загрузить клип"), T("Опубликовать", sel=".clip-actions"), T("Удалить", sel=".clip-actions"),
        T("Сгенерировать", click(".clip-actions >> text=Опубликовать")), T("Заголовок", click(".clip-actions >> text=Опубликовать")),
        T("Описание", click(".clip-actions >> text=Опубликовать")), T("Сделать 3 варианта", click(".clip-actions >> text=Опубликовать")),
        T("Демо YouTube", click(".clip-actions >> text=Опубликовать")), T("unlisted", click(".clip-actions >> text=Опубликовать")),
        T("Расписание", click(".clip-actions >> text=Опубликовать")), T("В очередь", click(".clip-actions >> text=Опубликовать")),
    ]),
    "ws-clips": ("/v/1/clips", [T("Опубликовать")]),
    "ws-montaged": ("/v/1/montaged", [T("Загрузить клип")]),
    "automation": ("/automation", [
        T("Ссылка на видео"), T("Пресет анализа"), T("Провайдер"), T("Макс. клипов"), T("Приватность"), T("Интервал"),
        T("Транскрипт"), T("ИИ-монтаж каждого клипа"), T("Оформление клипов"), T("Субтитры"), T("Баннер"), T("Зеркало"), T("Музыка"),
        T("Запустить"),
    ]),
    "publications": ("/pubs?tab=pubs", [T("Публикации", sel="h1")]),
    "accounts": ("/pubs?tab=accounts", [T("Демо YouTube"), T("Удалить"), T("cookies", sel="body")]),
    "tasks": ("/inbox?tab=tasks", [T(n) for n in ["Все", "Активные", "С ошибкой", "Скачивание", "Анализ", "Рендер", "Публикация"]]),
    "assets": ("/files", [T("Общие файлы", sel="h1")]),
    "approvals": ("/inbox?tab=approve", [T(n, sel=".pipeline") for n in ["Ждут", "На переделке", "Одобрено", "Мусор", "Все"]]),
    "series": ("/p/1", [T(n, sel=".ptabs") for n in ["Видео", "Клипы", "Вопросы", "Файлы", "Чат", "Лор и правила", "Статистика", "Автопилот"]]),
    "series-lore": ("/p/1?tab=brief", [T(n, sel=".pills") for n in ["Резюме и лор", "Правила", "Гипотезы"]]),
    "aivideo": ("/p/2?tab=aiv", [T(n, sel=".aiv-subtabs") for n in ["Сценарий", "Персонажи", "Локации", "Раскадровка", "Сборка"]]),
    # ---- Проход 2: объяснялки, ИИ-видео v3, звук (сведение, роль/эффекты слоя) ----
    "explainer": ("/p/3?tab=expl", [
        T("Новый ролик"), T("Тема:", kind="placeholder"), T("Длина:"), T("сразу рендер и обложка"), T("Сделать ролик"),
        T("Ролики", sel="h3"), T("Открыть клип"), T("Продолжить"), T("Пересобрать"), T("Удалить", sel=".expl-video-actions"),
        T("Сценарий и биты"), T("биты ролика", click(".expl-toggle >> nth=-1"), sel=".expl-beat", kind="css"),
        T("Маскот «Ух»"), T("Загрузить лист"), T("Перерисовать", sel=".expl-sheet"), T("Имя", kind="placeholder"),
        T("Карта личности", kind="placeholder"), T("Характер для сценариста", kind="placeholder"), T("Стиль:", kind="placeholder"),
        T("акцент"), T("чипы"), T("Музыка под голосом", kind="title"), T("Чем озвучивать", kind="title"),
        T("Голос Gemini", kind="title"), T("как говорить", kind="placeholder"), T("Проба"), T("Сохранить", sel=".expl"),
        T("Библиотека поз"), T("Найти по тегам", kind="placeholder"), T("Добавить позу"),
        T("Своя картинка (иначе нарисовать)", click(".expl-lib-tools .btn")),
        T("Добавить и нарисовать", click(".expl-lib-tools .btn")), T("Теги через запятую", click(".expl-lib-tools .btn"), kind="placeholder"),
        T("Новая версия", kind="title"), T("Убрать из библиотеки", kind="title"), T("Новый маскот"),
    ]),
    "explainer-empty": ("/p/2?tab=expl", [
        T("Маскота пока нет"), T("Импортировать пилот"), T("Новый маскот"), T("Создать", sel=".expl"),
    ]),
    "aivideo3": ("/p/2?tab=aiv&sub=board", [
        T("1. Блокинг"), T("2. Пары кадров"), T("Проверить кадры"), T("3. Видео планов"), T("Серия (черновик)"),
        T("Старт", sel=".aiv-shot"), T("Конец", sel=".aiv-shot"), T("Проверить сходство"), T("Видео", sel=".aiv-shot .series-row"),
        T("Проверить видео"), T("Кухня ночью", sel=".aiv-shot"), T("Сделать (0 ₽)|всё уже есть", click(".aiv-steps .btn >> nth=0"), kind="regex"),
        T("Скрыть", click(".aiv-steps .btn >> nth=0"), sel=".aiv-est"),
    ]),
    "aivideo3-blocking": ("/p/2?tab=aiv&sub=blocking", [
        T("Блокинг серии"), T("блокинг: старт"), T("кадр: старт"), T("Пересоздать раскладку"), T("Править раскладку вручную"),
        T("Сохранить и отрисовать", click(".aiv-more summary")), T("Сантиметры", click(".aiv-more summary")),
    ]),
    "aivideo3-characters": ("/p/2?tab=aiv&sub=characters", [
        T("Листы персонажа"), T("Сохранить", sel=".series-panel-head"), T("физика", kind="placeholder"),
        T("Манекен в блокинге", kind="title"), T("Как персонаж «говорит»", kind="title"), T("подписи к звукам"),
        T("Чем озвучивать", kind="title"), T("Проба"), T("Листы и референсы"), T("Своя картинка"), T("разворот"), T("Облик", sel=".series-section .btn"),
    ]),
    "aivideo3-script": ("/p/2?tab=aiv&sub=script", [
        T("Модель видео планов", kind="title"), T("видео (оживить план)"), T("продолжает прошлый план"),
        T("движение:", kind="placeholder"), T("звук: фон", kind="placeholder"), T("Состояние кадра"),
        T("Начало плана", click(".aiv-shot-edit .aiv-more summary")),
    ]),
    "editor-audio2": (EDITOR, [
        T("Сведение и эффекты", tab("Звук")), T("Проверить сведение", tab("Звук"), grp("Сведение")), T("Автосведение", tab("Звук"), grp("Сведение")),
        T("Сбросить", tab("Звук"), grp("Сведение"), sel=".insp-pane:not([hidden]) .rgroup.open"),
        T("Голос (звук исходника)", tab("Звук"), grp("Сведение")), T("Пресет", tab("Звук"), grp("Сведение"), sel=".insp-pane:not([hidden]) .fx-chain select", kind="css"),
        T("Компрессор", tab("Звук"), grp("Сведение"), sel=".fx-link"),
        T("Порог", tab("Звук"), grp("Сведение"), click(".insp-pane:not([hidden]) .fx-link summary"), sel=".fx-params"),
        T("Роль в сведении", tab("Слои"), grp("Слои клипа"), click(".ed-layer-row >> text=/звук|whoosh|вжух/i")),
        T("fx слоя", tab("Слои"), grp("Слои клипа"), click(".ed-layer-row >> text=/звук|whoosh|вжух/i"), sel=".insp-pane:not([hidden]) .fx-chain", kind="css"),
        T("Проверить", tab("Проверка"), sel=".insp-pane:not([hidden]) .lint-panel button, .insp-pane:not([hidden]) button", kind="css"),
    ]),
    "chat": ("/chat/1", [T("Новый чат"), T("Напиши агенту", kind="placeholder")]),
    "help": ("/help", [T("Помощь", sel="h1")]),
    "settings": ("/settings/render", [
        T(n, sel=".settings-rail") for n in ["Пресеты рендера", "Баннеры", "Музыка", "Субтитры", "Бренды", "Промпты", "По умолчанию", "Модули", "Прокси", "Telegram", "Экспорт/импорт"]
    ]),
}


def visible(page: Page, c: Check) -> bool:
    if c.kind == "css":
        loc = page.locator(c.sel)
    elif c.kind == "placeholder":
        loc = page.locator(f"[placeholder*='{c.name}' i]")
    elif c.kind == "regex":
        loc = (page.locator(c.sel) if c.sel else page.locator("body")).get_by_text(re.compile(c.name, re.I))
    elif c.kind == "title":
        loc = page.locator(f"[title*='{c.name}']")
    else:
        scope = page.locator(c.sel) if c.sel else page.locator("body")
        loc = scope.get_by_text(re.compile(re.escape(c.name), re.I))
    try:
        loc.first.wait_for(state="visible", timeout=5000)
        return True
    except Exception:  # noqa: BLE001
        n = loc.count()
        return any(loc.nth(i).is_visible() for i in range(min(n, 20)))


def run_steps(page: Page, steps: list[tuple[str, str]]) -> None:
    for kind, arg in steps:
        if kind == "tab":
            page.locator(".insp-tab", has_text=arg).first.click()
        elif kind == "group":
            head = page.locator(".insp-pane:not([hidden]) .rgroup-head", has_text=arg).first
            if head.get_attribute("aria-expanded") != "true":
                head.click()
        elif kind == "more":
            head = page.locator(".insp-pane:not([hidden]) .insp-more-head").first
            if head.get_attribute("aria-expanded") != "true":
                head.click()
        elif kind == "click":
            target = page.locator(arg).first
            try:
                target.click(timeout=8000)
            except Exception:  # noqa: BLE001 - липкая шапка перекрыла кнопку после прокрутки
                target.dispatch_event("click")
        elif kind == "key":
            page.keyboard.press(arg)
        page.wait_for_timeout(250)


def main() -> int:
    missing: list[str] = []
    total = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("PW_CHROME") or None)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        only = set(sys.argv[1:])  # можно перечислить экраны: python tools/ui_reach.py source editor
        for screen, (url, checks) in SCREENS.items():
            if only and screen not in only:
                continue
            for c in checks:
                total += 1
                # Каждая проверка — с чистой страницы: шаги не зависят от предыдущих.
                page.goto(UI + url)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(500)
                try:
                    run_steps(page, c.steps)
                    ok = visible(page, c)
                except Exception as e:  # noqa: BLE001
                    ok = False
                    print(f"  step error {screen}/{c.name}: {str(e).splitlines()[0]}")
                print(("ok  " if ok else "MISS") + f" {screen}: {c.name}", flush=True)
                if not ok:
                    missing.append(f"{screen}: {c.name}")
        browser.close()
    print(f"\n{total - len(missing)}/{total} reachable")
    for m in missing:
        print("  MISSING", m)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
