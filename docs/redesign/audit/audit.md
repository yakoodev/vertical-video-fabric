# UX-аудит Fabric - Vertical Video

Аудит сделан по коду `frontend/src`, чеклисту `docs/redesign/functions.md`, референсам `docs/redesign/ref-s*.png` и живым скриншотам из `docs/redesign/audit/shots/` при viewport 1600x1000.

## Самые болезненные проблемы

1. **Глобальная навигация не показывает работу системы.** В `AppShell.tsx` наверху есть только `ActivityCenter`, без поиска, команд, активной очереди и контекста проекта. Для пользователя, который весь день запускает анализы, рендеры и публикации, это заставляет постоянно прыгать между `/tasks`, колокольчиком и текущим проектом.

2. **Повторяемый путь "Моменты -> Монтаж -> Рендер -> Публикация" распадается на несколько ментальных моделей.** В `ProjectWorkspace.tsx` вкладка «Монтаж» фактически ведёт в `/candidates?clip=...`, а кнопка «Открыть редактор» живёт внутри `CandidatesTab.tsx`. Это правильно технически, но визуально надо яснее показать: «Моменты» это триаж, «Монтаж» это фокус-режим выбранного клипа, возврат только к моментам.

3. **Первый экран «Моментов» слишком тяжёлый и поздно отдаёт главное действие.** На `/projects/50/candidates` большой плеер, телефон-превью и правый блок занимают почти весь viewport; сетка моментов начинается ниже. Пользователь пришёл выбрать лучшие моменты, но видит состояние загрузки/плеера, а не управляемую очередь карточек.

4. **Редактор мощный, но перегружает правую инспектор-панель и теряет нижние действия.** В `CandidatesTab.tsx` фокус-режим уже прячет project chrome, но при 1600x1000 нижняя часть `ed-inspector` обрезается, футер с зонами, файлом клипа, применением и рендером борется с группами настроек. Ключевые команды должны быть sticky и предсказуемыми.

5. **Публикация и задачи разнесены, но связь между ними слабая.** `ClipCard`, `PublishDialog`, `BatchPublishDialog`, `/publications` и `/tasks` выполняют правильные функции, однако пользователь не видит сквозной статус: какой клип уже в очереди, какой аккаунт упал, что делать дальше.

6. **Пустые и редкие состояния занимают много места, но мало помогают.** `/automation`, `/publications`, `/accounts` и часть настроек выглядят как большие пустые полотна. В этих местах нужны короткие next-best actions: добавить аккаунт, открыть клипы, запустить конвейер, посмотреть задачи.

7. **Контролы визуально разноязычные.** Кнопки, chips, switches и иконки смешивают emoji, текст, разную плотность и разные статусы. В референсах стиль более профессиональный: меньше случайных emoji, больше устойчивых иконок, одинаковые формы и роли.

## Навигация и IA

### Что сейчас хорошо

- Базовые группы sidebar в `AppShell.tsx` совпадают с доменом: монтаж, постинг, мониторинг.
- Проектные вкладки в `ProjectWorkspace.tsx` отражают конвейер: «Исходник», «Моменты», «Монтаж», «Клипы», «Смонтированные».
- `ActivityCenter.tsx` уже умеет показывать статусы, прогресс скачивания, отмену download/analysis/render, переход «открыть» и ссылку «Все задачи».
- Редактор в `CandidatesTab.tsx` уже имеет правильный фокус-бар: «Назад к моментам», название клипа, AI-монтаж и откат.

### Предлагаемая глобальная структура

Sidebar:

1. **Создать**: заметная кнопка сверху, открывает единый ingest-dialog с текущими функциями `AddSource`: загрузка файла, URL, качество, «Серия и озвучка».
2. **Проекты**: список исходников и вход в workspace.
3. **Клипы**: глобальная библиотека готовых и загруженных клипов.
4. **Публикация**: очередь и история публикаций. Оставить `/publications`, но назвать по задаче пользователя: «Публикация» или «Очередь публикаций».
5. **Авто**: конвейер и сохранённые запуски.
6. **Аккаунты**: подключение cookie-сессий и их готовность.
7. **Очередь**: переименовать видимый пункт «Задачи» в «Очередь» или «Активность», маршрут `/tasks` оставить. Badge с активными задачами должен быть виден всегда.
8. Bottom: **Помощь**, **Настройки**, **Выйти**.

Top bar:

- Слева: глобальный поиск/command palette `Ctrl K`: проекты, клипы, команды «анализировать», «рендер выбранных», «открыть задачи».
- Справа: компактный `ActivityCenter` как pill «3 активные», колокольчик, health/status, быстрый переход в `/tasks`.
- Внутри проекта top bar дополнительно показывает project quick switcher и текущую активную задачу проекта.

### Предлагаемая структура проекта

Project header:

- `← Проекты`, название + rename, статус, длительность, размер, краткая производственная сводка: «300 моментов», «10 ★», «7 клипов», «2 публикации».
- Если есть running task: progress strip в header и ссылка в Activity.

Tabs:

1. **Исходник**: видео, crop, анализы.
2. **Моменты**: triage, выбор, AI pick, batch render.
3. **Монтаж**: открывает последний активный/первый избранный clip editor. Если избранного нет, disabled state с CTA «Отметьте ★ на Моментах».
4. **Клипы**: отрендеренные клипы проекта.
5. **Смонтированные**: монтажные загруженные клипы.

Редактор:

- Остаётся по URL `/projects/:id/candidates?clip=:clipId`, чтобы browser Back возвращал к моментам.
- Project chrome скрыт, но глобальная оболочка может оставаться. Добавить отдельный полноэкранный режим, который сворачивает sidebar.
- Top focus bar: только «← Назад к моментам», clip title, project source label, `↩ Откатить ИИ-монтаж`, `🤖 ИИ-монтаж`, activity pill.

### Быстрые клавиши

- `Ctrl/Cmd K`: command palette.
- `/`: поиск на текущем экране.
- `1...5`: вкладки проекта.
- `N`: создать проект.
- `A`: анализировать исходник.
- `F`: toggle ★ для активного момента.
- `X`: скрыть/вернуть момент.
- `Space`: play/pause.
- `J/K/L`: назад/стоп/вперёд в плеере.
- `[` / `]`: предыдущий/следующий момент или клип в редакторе.
- `E`: открыть редактор активного момента.
- `R`: рендер активного клипа, `Shift R`: рендер выбранных.
- `P`: открыть публикацию готового клипа.
- `Shift A`: «ИИ выберет лучшие».
- `Shift M`: «ИИ-монтаж всех ★».
- `Esc`: закрыть модалку или вернуться из редактора к моментам.
- `?`: список shortcuts.

## Экранные рекомендации

### Global shell: `components/AppShell.tsx`, `components/ActivityCenter.tsx`, `styles/global.css`

- Добавить top bar с command palette, activity pill и быстрым status area. Сейчас `.topbar` содержит только `ActivityCenter`; в референсах владелец явно выбрал верхний поиск как главный рабочий инструмент.
- Сделать sidebar менее emoji-зависимым: заменить случайные emoji на стабильные иконки, сохранить русские labels. Emoji можно оставить только в AI-экшенах, где они уже стали семантикой продукта.
- Добавить badge активных задач на пункт «Очередь/Задачи» и в `ActivityCenter`. Не заставлять пользователя открывать колокольчик, чтобы понять, что сейчас идёт.
- `ActivityCenter`: в раскрытии показывать не только terminal item, но и группировку «Активные / Ошибки / Недавние», progress для analysis/render/job, не только download. Функции отмены и «Все задачи →» сохранить.

### Проекты: `pages/ProjectsPage.tsx`

- `AddSource` лучше вынести из большой hero-карты в компактную ingest-панель или глобальную кнопку «Создать». Функции не теряются: upload, URL Enter, quality select, PlayerOptionsDialog и «Добавить» остаются в одном диалоге.
- Status chips «Все / Обработка / Готово / Ошибка» показывать всегда, даже с нулём, чтобы не прыгала панель фильтров.
- В карточках `ProjectCard` добавить явное состояние «нет превью / превью грузится / аудио-only» вместо чёрных карточек с маленьким `[adult]`.
- Progress обработки лучше показывать на cover и в metadata: процент, тип операции, кнопка «открыть задачу». Сейчас progress bar без числа не объясняет, где смотреть детали.
- Search для проектов оставить, но визуально поднять в общий top bar; локальный search может оставаться как scoped filter.

### Исходник: `pages/workspace/SourceTab.tsx`

- Сохранить текущий двухколоночный layout, но сделать правую колонку «Статус анализа» sticky до завершения анализа. Это главный feedback long-running операции.
- `AnalysisProgress` хороший по сути: реальные шаги и counted windows. Добавить ETA, provider/model и кнопку «Открыть моменты по мере появления», если уже есть кандидаты.
- Блок «Кадр источника» сейчас уходит ниже first viewport. Для потокового сценария crop нужен до анализа: сделать компактный crop strip под плеером с раскрытием advanced sliders.
- В списке «Анализы» разделить успешные, активные и упавшие визуально. Сейчас failed прячутся, это правильно, но кнопка должна объяснять влияние удаления: «удалит кандидатов этого анализа».

### Моменты: `pages/workspace/CandidatesTab.tsx`, `pages/workspace/AiBatchPanel.tsx`

- Верхний блок `mo-top` должен быть режимом «просмотр активного момента», а не главным экраном. Сетка моментов должна начинаться выше, или иметь split: слева карточки, справа sticky preview/AI actions.
- Сделать sticky action bar: `Открыть редактор`, `Рендер выбранных`, `ИИ выберет лучшие`, `ИИ-монтаж всех ★`, selected count. Эти функции уже есть, но сейчас часть живёт в правом блоке `mo-stats`, часть в `AiBatchPanel`.
- Фильтры `mo-toolbar`: сгруппировать в две строки или меню: поиск, favorites, hidden, duplicates, analysis chips, select all. Сейчас при росте анализов chips могут занять слишком много ширины.
- Карточка момента: увеличить click targets для ★, hide, checkbox; добавить keyboard focus state. Функции `favorite`, `hidden`, `include-in-render`, open editor остаются там же.
- AI pick result (`mo-ai`, `mo-why`) визуально отделить от quality score. Сейчас оба конкурируют в маленькой карточке.
- Для live progress AI batch показывать мини-очередь прямо в sticky bar и дублировать в ActivityCenter.

### Монтаж / редактор: `pages/workspace/CandidatesTab.tsx`

- `ed-viewbar` уже соответствует новой модели. Усилить: добавить маленький project breadcrumb и activity pill, но не возвращать project tabs.
- Правую `ed-inspector` разделить на режимы: «Оформление», «Кадр», «Монтаж», «Экспорт». Все текущие группы сохранить: лук, субтитры, баннер, переходы, обложка, музыка, умный кадр, фокус, границы.
- `ed-render-btns` сделать постоянно видимыми внизу правой панели, но без перекрытия контента. Сейчас футер панели визуально съедает нижние группы на 1000px высоты.
- `Файл клипа` и «Применить ко всем ★» оставить рядом с render footer, но убрать из текстового абзаца `ed-settings-note`: это команды, а не hint.
- Левую rail избранных (`ed-rail`) снабдить счётчиком и статусом: «10 ★ / 4 готовы / 2 в рендере». Это поможет при массовом монтаже.
- Timeline: добавить видимые дорожки labels и состояние autosave. Пользователь должен понимать, что drag/resize уже сохранён или ещё сохраняется.
- Добавить fullscreen focus toggle: скрыть sidebar и расширить stage, не ломая текущий back-to-moments flow.

### Клипы: `pages/ClipsPage.tsx`, `pages/workspace/ClipsTab.tsx`, `components/ClipCard.tsx`

- Глобальная `/clips` нуждается в search, sort, фильтре по проекту и batch selection. Сейчас есть только status chips и upload.
- `ClipCard`: сохранить publish/delete/rename/qc/published count, но добавить source/project link и publication status summary. Для массовой работы важно видеть происхождение клипа.
- На project `ClipsTab` toolbar уже умеет batch publish. Перенести аналогичный pattern в глобальную `/clips`.
- Quality warning в карточке хорошо работает; сделать warning более сканируемым: icon + short issue + tooltip/detail drawer.

### Публикация: `components/PublishDialog.tsx`, `components/BatchPublishDialog.tsx`, `pages/PublicationsPage.tsx`

- `PublishDialog` с AI metadata хорош: оставить `✨ Сгенерировать`, но показать до/после или undo для сгенерированного текста.
- Account checkboxes лучше оформить как account chips с платформой, label, readiness. Если аккаунтов нет, CTA «Добавить аккаунт» ведёт на `/accounts`.
- `BatchPublishDialog`: сохранить interval, privacy, AI metadata, QC warnings. Добавить summary: «N клипов x M аккаунтов = K публикаций».
- `/publications` в empty state должен давать CTA «Открыть клипы» и «Добавить аккаунт». Таблица при данных должна показывать platform target statuses без захода в detail для каждого job.

### Авто: `pages/AutomationPage.tsx`

- Верхняя схема конвейера полезна, но занимает много места. Сделать её compact progress legend, а не hero.
- Разделить левую форму на две зоны: «Что взять» и «Как выпускать». Все текущие поля сохранить: URL, preset, provider, max clips, privacy, interval, transcript, AI montage, render preset, subtitles, banner, mirror, music, accounts, AI metadata.
- Если аккаунтов нет, disabled `Запустить` должен объяснять причину и предлагать `/accounts`.
- Runs list: даже пустое состояние должно быть компактным. При данных показывать run stage, project link, error, counts и thumbnails как сейчас.
- В будущем: сохранить auto recipes, чтобы power user не собирал оформление заново.

### Аккаунты: `pages/AccountsPage.tsx`, `pages/settings/AccountsSettings.tsx`

- Форма добавления аккаунта слишком «техническая» без помощи рядом. Добавить inline checklist required cookies по платформе и warning про безопасность cookies.
- Карточки аккаунтов должны показывать readiness, missing cookies, proxy display и last publish status. Текущие функции create/delete сохраняются.
- Не смешивать `/accounts` с `/settings/accounts`: сейчас компонент переиспользуется правильно, но в IA это должен быть самостоятельный глобальный раздел.

### Задачи: `pages/TasksPage.tsx`

- Переименовать видимый раздел в «Очередь» или «Активность», но оставить `/tasks`.
- Добавить pinned active group сверху, затем failed, затем history. Сейчас список сортирован как лог, но активные задачи могут теряться.
- Для rows добавить progress bars для download/analysis/render/job, source/clip thumbnail, bulk clear terminal. Фильтры из checklist сохранить.
- `ActivityCenter` и `/tasks` должны использовать одну терминологию статусов.

### Настройки: `pages/SettingsPage.tsx`, `pages/settings/*`

- `settings-rail` нормальный, но нужна категория «Производственные пресеты»: рендер, субтитры, баннеры, музыка, промпты. «По умолчанию» и «Экспорт/импорт» оставить ниже.
- `RenderPresetsSettings`: показать thumbnails/preview для луков, иначе список названий не помогает выбрать визуальный стиль.
- `PromptsSettings`: добавить task filters и default marker заметнее. Функции create/default/delete сохраняются.
- `BackupSettings`: warning про accounts уже есть; сделать его визуально сильнее и не emoji-зависимым.

### Помощь: `pages/HelpPage.tsx`

- Обновить терминологию: в тексте уже в основном «Моменты», но местами путь говорит «Редактор». Добавить отдельный раздел «Монтаж» как project tab и focus mode.
- Добавить shortcuts section и mini-map workflow: вручную, semi-auto, auto.
- TOC chips выглядят как filters; сделать их sticky anchor nav только на help page.

## Консистентность UI

- Primary button: только следующий главный шаг (`Анализировать`, `Открыть редактор`, `Рендерить`, `В очередь`, `Запустить`).
- Secondary button: batch/utility.
- Danger: delete/cancel. Не смешивать «Отменить операцию» и «Удалить файл» одним визуальным стилем.
- Chips: filters only. Status chips separate from filter chips.
- Switches: binary settings only. Для privacy и modes использовать segmented controls.
- Icons: перейти на lucide-style icons вместо emoji в navigation/action buttons. Emoji оставить в AI labels, если владелец хочет эту тональность.

## Quick wins, до 1 часа каждый

- В `AppShell.tsx` добавить topbar placeholder command palette и текстовую кнопку/пилюлю «Активность», оставив `ActivityCenter`.
- Всегда показывать четыре project status chips в `ProjectsPage.tsx`, включая нули.
- Переименовать visible sidebar «Задачи» в «Очередь», route не менять.
- В `ProjectWorkspace.tsx` для вкладки «Монтаж» добавить disabled tooltip/empty CTA, если нет starred moments.
- В `CandidatesTab.tsx` поднять `AiBatchPanel` actions в `mo-toolbar` или дублировать в sticky action bar.
- В `ed-insp-foot` разделить `Файл клипа`, `Применить ко всем ★` и render buttons на отдельные строки.
- Добавить empty-state CTA на `/publications`, `/automation`, `/accounts`.
- Добавить project/source link в `ClipCard`.

## Более крупные изменения

- Единая command palette: поиск проектов/клипов/публикаций и запуск команд.
- Новый global activity drawer: активные, ошибки, недавние, прогресс для всех типов задач.
- Пересборка workspace shell: compact header, project progress strip, sticky project actions.
- Новый layout «Моменты»: grid-first triage с правым sticky preview.
- Редактор 2.0: inspector modes, fullscreen focus, persistent render footer, autosave state.
- Batch publishing из глобальной `/clips` с тем же UX, что в project `ClipsTab`.
- Auto recipes: сохранить набор анализа, оформления, аккаунтов и расписания.

## Сохранение функций из checklist

Ни одна функция не должна исчезнуть. Предлагаемые переносы:

- **Add source**: upload, URL, quality, «Серия и озвучка», Добавить остаются; переместить в global «Создать» и оставить shortcut на `/projects`.
- **ActivityCenter**: все функции отмены, скрытия, очистки и «Все задачи» сохраняются; визуально усилить и связать с `/tasks`.
- **Project tabs**: «Исходник / Моменты / Монтаж / Клипы / Смонтированные» сохраняются. Hidden `segments` route остаётся скрытым/debug.
- **Исходник**: player, crop overlay, crop sliders, detect/save/reset, analyze form, transcript, analyses list и cancel/delete остаются.
- **Моменты**: search, favorite filter, hidden, duplicates, analysis chips, select all, group select, quality, include-in-render, title/open editor, favorite, hide/restore остаются.
- **AI на Моментах**: «ИИ выберет лучшие», «ИИ-монтаж всех ★» и live progress остаются; предлагается sticky placement.
- **Редактор**: stage, crop frame, safe zones, transport, fixed frame, look/subtitles/banner/music/mirror, smart frame, focus editor, cut boundaries, timeline, file clip, AI montage, undo AI montage, render selected сохраняются.
- **Клипы / Смонтированные**: ClipCard actions, batch publish, upload link for montaged remain.
- **Global Clips**: upload, filters, PublishDialog, delete remain; добавить batch/search без удаления.
- **Авто**: все поля и toggles сохраняются, только группируются.
- **Публикации**: table and detail remain; добавить summary/CTA.
- **Аккаунты**: platform, label, proxy, cookies, readiness and delete remain.
- **Задачи**: all filters and open links remain.
- **Настройки**: render presets, banners, music, subtitles, prompts, defaults, backup remain.
- **Помощь**: TOC and static sections remain, обновить под «Моменты» и «Монтаж».

## Новые reference images

- `docs/redesign/audit/ref-nav-1.png`: глобальная оболочка с улучшенным sidebar, top command/search bar, activity rail и проектной сеткой. Показывает, как связать «Проекты», «Очередь» и активные задачи в одном рабочем экране.
- `docs/redesign/audit/ref-nav-2.png`: project workspace: компактный project header, вкладки `Исходник / Моменты / Монтаж / Клипы / Смонтированные`, анализ progress справа и sticky project actions.
- `docs/redesign/audit/ref-nav-3.png`: editor focus mode: минимальный верхний бар «Назад к моментам», clip title, AI actions, центрированный stage, phone preview, timeline и компактный inspector.

Сгенерированные референсы имеют размер около 1586x992 и используют абстрактные превью без реальных лиц, персонажей и логотипов.
