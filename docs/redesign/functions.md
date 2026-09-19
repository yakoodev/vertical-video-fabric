# Redesign checklist — every function that must survive

Each redesign step is checked against this list. Nothing here may disappear;
moving/regrouping is fine.

## Global / shell
- Sidebar: Проекты, Клипы | Авто, Публикации, Аккаунты | Задачи; bottom: Помощь, Настройки, Выйти
- ActivityCenter 🔔 (badge count): per-task status, progress % (download), отменить (download/analysis/render), открыть →, скрыть ×, Очистить, «Все задачи →»
- Toasts; background "готово/ошибка" notifications; legacy URL redirects

## Projects `/projects`
- Add source: 📤 file upload, URL input (Enter), quality select (авто/макс/1080/720/480/360), 🎞 «Серия и озвучка» (PlayerOptionsDialog: плеер/сезон/серия/озвучка), Добавить
- Status chips Все/Обработка/Готово/Ошибка (counts), search (>6 projects)
- Card: storyboard cover (hover slideshow + scrub), title + ✎ rename, status, W×H, N моментов, N клипов, 🗑 delete (confirm)

## Workspace `/projects/:id`
- ← Все проекты; title; status, duration, W×H; tabs Исходник / Моменты / Клипы / Смонтированные; hidden `segments` route

### Исходник
- Player + crop overlay; meta (duration, W×H, fps, size, type), original URL link
- «Статус анализа»: моментов, клипов, ср. качество, популярные темы
- «Кадр источника»: 🔍 Найти полосы, 4 sliders (верх/низ/лево/право 0–45%), Сохранить/Сбросить
- «Запустить анализ»: пресет, провайдер (action/polza/gemini/artemox/mock), свой промпт, ☐ транскрипт (+cached count), Анализировать
- Анализы list: status, #id, N кандид., provider·model·date, Отменить (active) / 🗑; failed collapsed

### Моменты (triage)
- Source player + meta + N моментов
- Search, ★ Избранное filter, скрытые (N), скрыть дубли, Выбрать все/Снять все
- Analysis chips (show/hide per analysis, «Вручную»)
- Groups per analysis (header provider·model, count, group select)
- Plan chip: quality 0–100, ☐ include-in-render, title (+дубль), duration · N сегм., open editor, ☆/★, ✕/↩

### Редактор
- Stage: video with look CSS + vignette, mirror, meta; CropFrame (drag 9:16 = fixed frame), safe zones Баннер/Субтитры
- ▶ Превью клипа, ▶ Сегмент, ⏹ Стоп, ☐ Зациклить
- Фикс. рамка slider (% / «трек»), 🎯 Авто
- Рендер: Лук (preset select, LookPicker hover frames, ☐ Зеркало); Субтитры (☐, движок, стиль, положение 2–40%); Баннер (☐, картинка, высота 6–30, положение 0–80); Музыка (☐, трек); ☐ Зоны на превью; ▶ Рендерить выбранные (N)
- Умный кадр: стратегия, пресет детекции, ☐ Gemini refine, 🎯 Авто-фокус все клипы, Только этот план
- Границы клипов: strategy select, Пересчитать
- Timeline: zoom −/Fit/+, Ctrl+wheel, ruler seek, segment select/drag/resize with snapping, playhead
- FocusEditor: центр slider, 🎯 Детектор, + Точка, Сохранить фокус, Очистить, point chips (seek / ×)

### Клипы / Смонтированные
- Clip grid (ClipCard: player, status, опубликовано N, ✎ rename, duration, W×H, error); Опубликовать, Удалить (confirm); link «Загрузить клип →»

## Clips `/clips`
- 📤 Загрузить клип; all-clips grid (same card actions)
- PublishDialog: заголовок, описание, account checkboxes, приватность, расписание, В очередь

## Авто `/automation`
- Form: URL, пресет, провайдер, макс. клипов, приватность, интервал ч, ☐ транскрипт
- Оформление: render preset, subtitles (engine/style/pos), banner (img/height/pos), mirror, music
- Account checkboxes; ▶ Запустить
- Runs list: status, message, планов/клипов/постов, проект →, error

## Публикации `/publications`
- Table #, Статус, Заголовок, Аккаунтов, Расписание, Создано; detail: status, schedule, error, targets (платформа, аккаунт, статус, ссылка, ошибка)

## Аккаунты `/accounts`
- Add: платформа (youtube/tiktok/instagram), метка, прокси, cookies; cards: status (готов/re-auth), cookies N, прокси, не хватает, Удалить

## Задачи `/tasks`
- Filter chips Все/Активные/С ошибкой/Скачивание/Анализ/Рендер/Публикация; rows with status, error, timings, открыть →

## Настройки `/settings`
- Пресеты рендера (smart-reframe toggle, delete), Баннеры (upload, delete), Музыка (upload, volume, player, delete), Субтитры (list, delete), Промпты (create: задача/название/промпт/default; list, delete), По умолчанию (providers/models, default banner/subtitles, global proxy), Экспорт/импорт (☐ accounts, download bundle, upload bundle)

## Помощь `/help`
- TOC + static sections (update: «Кандидаты»→«Моменты», accounts live on /accounts)

## Added after the codex audit (2026-09-20)
- Shell: «＋ Создать» (N) → new-project dialog (same AddSource: file, URL, quality, «Серия и озвучка»); sidebar Проекты · Клипы · Публикации · Авто · Очередь (badge = active tasks) · Аккаунты; Горячие клавиши (?), Помощь, Настройки, Выйти
- Top bar: «Поиск или команда…» (Ctrl/⌘ K palette: actions, sections, this project's tabs + moments → монтаж, projects incl. «твич/ютуб», clips), «N активн.» pill → /tasks, ActivityCenter bell
- Keys: Ctrl K, N, ?, 1–5 project tabs, F ★ / X hide / E edit (moment under pointer), Esc back from the editor
- Project header: summary «N моментов · N ★ · N клипов»
- Моменты: sticky filter toolbar with «✂ Монтаж · N ★» and «▶ Рендер · N»; AI: «✨ ИИ выберет лучшие» (count, goal, chain ИИ-монтаж/render), «🤖 ИИ-монтаж всех ★» (goal, render, progress, ↩ per clip); card badge «✨ score» + reason
- Editor: «🤖 ИИ-монтаж» dialog, «↩ Откатить ИИ-монтаж»; inspector footer rows: saved-state + «Зоны», «{ } Файл клипа» + «Ко всем ★», render (1) / Выбранные
- Publish: «✨ Сгенерировать» (single), «✨ ИИ-заголовки…» toggle (batch + Авто); Авто: «🤖 ИИ-монтаж каждого клипа перед рендером»
- Empty states with next-step buttons (Публикации, Авто, Клипы)
- «Файлы для монтажа» /assets: drag-drop multi-upload (image/GIF/video/audio ≤300 MB), card preview, label, «когда уместно», tags (autosave on blur), delete (confirm); sidebar + palette entry
- Editor «Вставки (мемы)» group: per insert file select, at (clip s), duration, mode (весь кадр / окном / звук), ⏱ сейчас, ▶ к месту, ✕; + Вставка на текущий момент; AI reason shown; «Ко всем ★» keeps each clip's own inserts
- 🤖 ИИ-монтаж may add 0–3 inserts from the library (shown in «было → стало»)
