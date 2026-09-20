# Редактор клипа «Монтаж»

Документ описывает редактор 2.0 для Fabric - Vertical Video. Цель: один power user должен быстро делать десятки вертикальных клипов в день, видеть результат до рендера и править монтаж как объекты на таймлайне, а не как набор полей в правой панели.

Проверенные live screenshots:

- `shots/live-moments-1600x1000.png` - текущая вкладка «Моменты».
- `shots/live-editor-368-1600x1000.png` - текущий editor view на `/projects/50/candidates?clip=368`.

Новые reference images:

- `ref-editor-1.png` - весь редактор: stage, phone preview, inspector и многодорожечный timeline.
- `ref-editor-2.png` - режим редактирования субтитров: список строк, waveform, слова караоке.
- `ref-editor-3.png` - вставки/мемы: drawer библиотеки, drag на timeline, настройки вставки.

## 1. Диагноз

Текущий `CandidatesTab.tsx` уже содержит сильное ядро редактора: source stage с `CropFrame`, live phone canvas preview, transport, `Timeline`, `FocusEditor`, per-clip autosave и footer с рендером. Но ощущается это как форма настроек, потому что главные монтажные объекты не живут на сцене и таймлайне.

Конкретно:

- `Timeline.tsx` - одна дорожка `tl-track` для сегментов исходника. Она умеет drag/resize/snap/zoom, но показывает время всего source video. В live screenshot открытый клип длится 0:20, а ruler растянут по исходнику 4:10:50. Монтажный пользователь думает в clip time 0:00-0:20, а интерфейс заставляет думать в source time.
- `Group` в правом `ed-inspector` является главным местом работы: «Картинка», «Субтитры», «Баннер», «Переходы», «Обложка», «Вставки», «Музыка», «Умный кадр», «Фокус кадра», «Границы клипов». Это настройки рендера, а не прямое редактирование объектов.
- `Group title="Субтитры"` дает только `subs_on`, `sub_engine`, `sub_id`, `sub_pos_pct`. Реальные субтитры создаются в `ClipRenderService._render_subtitles`, затем `write_ass_subtitles` сразу прожигает ASS в видео. До рендера нельзя увидеть текст, исправить опечатку, разрезать строку или подвинуть слово.
- `Group title="Вставки (мемы)"` редактирует `MontageInsert` через select и number inputs: `asset_id`, `at`, `duration`, `mode`. Вставка не видна на timeline, ее нельзя перетащить, обрезать ручками, выбрать на preview или понять по thumbnail, что именно вставлено.
- `drawInserts` уже умеет preview на phone canvas, а `app/montage_assets.py` умеет full/pip/sound/duck на рендере. Но UI не превращает это в монтажную операцию. Пользователь видит не клип с объектами, а список чисел.
- `FocusEditor` хорош как ручной keyframe editor, но спрятан в инспекторе и зависит от выбора сегмента. Фокус должен быть отдельной дорожкой, где видна кривая камеры, точки, cuts и выбранный keyframe.
- `AiMontageDialog` работает через `vvf.clip/1` и показывает «было -> стало», но живет как модалка отдельно от timeline. Применение меняет clip file, однако пользователь не видит предложение как editable preview на дорожках.
- Footer `ed-insp-foot` содержит saved state, «Зоны», «{ } Файл клипа», «Ко всем ★», «Рендерить (1)», «Выбранные». Это правильные команды, но они конкурируют с длинным accordion inspector и на 1000 px высоты воспринимаются как пристегнутый хвост формы.

Вывод: в новом редакторе timeline должен стать центром правки. Inspector должен только объяснять и уточнять выбранный объект.

## 2. Функции нового редактора

Статус: `[есть]` - уже есть в текущем продукте, но может переехать в новый layout; `[новое]` - нужно добавить или существенно переработать.

### Вход в редактор и shell

- `[есть]` Открытие редактора из «Моментов» через `/projects/:id/candidates?clip=:clipId`.
- `[есть]` `Esc` или «Назад к моментам» возвращает к triage.
- `[есть]` Левая глобальная навигация: Проекты, Клипы, Файлы для монтажа, Публикации, Авто, Очередь, Аккаунты, Помощь, Настройки, Выйти.
- `[есть]` Top command/search bar `Ctrl K`, activity pill, notification bell.
- `[есть]` ActivityCenter: статусы задач, progress, отмена download/analysis/render, открыть, скрыть, очистить, «Все задачи».
- `[новое]` Режим focus editor с опциональным collapsible sidebar: на 1600 px sidebar остается, но stage/timeline получают приоритет; на 2560 px sidebar не мешает.
- `[новое]` В editor view всегда показывать project breadcrumb, clip title, saved state, AI actions и render status в одной верхней строке.

### Навигация по клипам

- `[есть]` Левая rail избранных клипов с thumbnails и длительностью.
- `[есть]` Работа в редакторе по избранным ★; открытый клип добавляется в rail даже если еще не favorite.
- `[новое]` Rail показывает производственный статус: `13 ★`, `5 готовы`, `2 в рендере`, `1 ошибка`, текущий clip highlighted.
- `[новое]` `[` / `]` переходят к предыдущему/следующему клипу в rail.
- `[новое]` Быстрый mini-map по клипу: duration, количество кусков, есть ли субтитры, вставки, музыка, cover.

### Stage и preview

- `[есть]` Source stage с видео, look CSS, vignette, mirror preview.
- `[есть]` Draggable 9:16 `CropFrame`, fixed frame slider, safe zones «Баннер» и «Субтитры».
- `[есть]` Phone canvas preview того, что попадет в 9:16 render.
- `[есть]` Preview clip, preview segment, stop, loop, playback speed.
- `[новое]` Stage selection: клик по subtitle overlay, insert overlay или crop keyframe выбирает соответствующий объект на timeline и в inspector.
- `[новое]` Overlay handles на phone preview для `pip` insert: двигать, масштабировать, выбрать anchor/preset position.
- `[новое]` Preview mode toggles: `Исходник`, `Итог 9:16`, `Субтитры`, `Вставки`, `Без зон`.
- `[новое]` Время stage и phone всегда в clip time; source time показывается как вторичная подсказка.

### Многодорожечный timeline

- `[есть]` Zoom `- / Fit / +`, `Ctrl+wheel`, ruler seek, playhead.
- `[есть]` Segment select, drag, resize, snapping, commit на release.
- `[новое]` Timeline работает в clip time: 0:00 - duration of montage. Source time виден в tooltip и внутри segment metadata.
- `[новое]` Дорожки: `Клипы`, `Фокус`, `Субтитры`, `Слова караоке`, `Вставки`, `Звуки`, `Музыка`.
- `[новое]` Track labels, mute/solo/lock/collapse controls для дорожек, где это применимо.
- `[новое]` Split at playhead, blade tool, duplicate, delete, ripple trim для video pieces.
- `[новое]` Snapping к playhead, edges, subtitle boundaries, word boundaries, insert edges, beat markers/music transients.
- `[новое]` Marquee/multi-select, `Shift` add selection, `Alt-drag` duplicate inserts/subtitle blocks.
- `[новое]` Undo/redo для timeline operations.
- `[новое]` Autosave indicator attached to timeline and top bar, not hidden only in inspector footer.

### Куски клипа

- `[есть]` Clip plan состоит из ordered `segments` with `start_sec`, `end_sec`, title, focus.
- `[есть]` `vvf.clip/1` can add/remove/reorder pieces and validate all-or-nothing.
- `[новое]` Timeline pieces are editable montage objects: reorder by dragging, trim handles, split at playhead, remove gap, create jump cut.
- `[новое]` Piece inspector: title, source time, duration, focus mode, transition into/out of piece, replace from source at same duration.
- `[новое]` Context command «Открыть в исходнике» seeks source stage to original timestamp.

### Фокус и кадрирование

- `[есть]` `FocusEditor`: center slider, detector, add point at current frame, save, clear, point chips.
- `[есть]` `focus` keyframes `{t, x, y?, cut?}` render through `_segment_reframe_x`.
- `[есть]` Auto-focus for all clips or only this plan; strategy, detection preset, Gemini refine.
- `[новое]` Focus track always visible under video pieces as curve with draggable keyframes.
- `[новое]` Keyframe inspector follows selected point: `t`, `x`, optional `y`, easing, `cut`.
- `[новое]` CropFrame drag can create/update focus keyframe at playhead, not only overwrite fixed frame.
- `[новое]` `A` runs autofocus for selected piece or selected range; `Shift A` for whole clip.

### Субтитры

- `[есть]` Render settings already store `subs_on`, `sub_id`, `sub_engine`, `sub_pos_pct`.
- `[есть]` Providers exist: Whisper, Gemini, mock; `SubtitleResult` stores `segments` and `words`.
- `[есть]` ASS karaoke burn exists through `write_ass_subtitles`.
- `[новое]` `Сгенерировать субтитры` happens inside editor before render.
- `[новое]` Subtitle line list with editable rows: text, start, end, confidence/status, speaker optional, split/merge.
- `[новое]` Subtitle blocks on timeline can be moved, trimmed, split, merged and snapped.
- `[новое]` Word lane shows karaoke word timing; word pills can be nudged and used as snap anchors.
- `[новое]` Text correction preserves word timings when possible and marks mismatches as `нужна синхронизация`.
- `[новое]` Render uses approved subtitle draft if present; if no draft exists, current render-time generation remains fallback.
- `[новое]` Subtitle preview on phone uses the same page/line grouping rules as ASS: no surprise after render.

### Вставки, мемы, звуки

- `[есть]` `/assets` stores image/GIF/video/audio with label, description, tags, duration, preview URL.
- `[есть]` `MontageInsert`: `{asset_id, at, duration, mode: full|pip|sound, volume, duck, reason?}`.
- `[есть]` Render overlay supports full, pip, sound, volume, duck and max 6 inserts.
- `[есть]` Phone preview already draws inserts in `drawInserts`.
- `[новое]` Library drawer inside editor: search, filters, kind chips, tags, duration, preview.
- `[новое]` Drag asset onto `Вставки` or `Звуки` lane creates insert at drop time.
- `[новое]` Insert blocks show thumbnail/waveform, mode badge, duration and reason.
- `[новое]` Inserts can be dragged, trimmed, duplicated, deleted, snapped and selected.
- `[новое]` PIP insert can be positioned/resized on phone preview with handles.
- `[новое]` Inspector follows selected insert: asset preview, mode, start, duration, volume, duck, position/size, replace file.
- `[новое]` AI can propose a missing image/sticker, save it into «Файлы для монтажа», then place it.

### Музыка и audio

- `[есть]` Background music track toggle, track select, render pass with ducking/fade in render settings.
- `[есть]` Existing audio tracks live in settings.
- `[новое]` Music lane with waveform or compact bar.
- `[новое]` Volume automation/ducking preview as visible envelope.
- `[новое]` Sound-only inserts appear on `Звуки` lane, not mixed into the same form as image inserts.

### Оформление и render settings

- `[есть]` Look preset picker with hover frames, mirror toggle.
- `[есть]` Banner on/off, asset, height, position.
- `[есть]` Transitions: type, duration, audio smooth/hard, sfx, sfx volume.
- `[есть]` Cover: none, frame, uploaded image, burn first frame, burn duration.
- `[есть]` `Файл клипа` JSON dialog for `vvf.clip/1`.
- `[есть]` `Ко всем ★` copies style settings to other favorites while preserving each clip's own inserts.
- `[есть]` `Рендерить (1)` and `Выбранные`.
- `[новое]` Inspector modes/tabs: `Оформление`, `Кадр`, `Монтаж`, `Экспорт`; selected object overrides mode automatically.
- `[новое]` Render footer is sticky and separate from object inspector.
- `[новое]` Render readiness checklist: subtitles generated? missing assets? render settings saved? duration/format OK?

### AI в редакторе

- `[есть]` `🤖 ИИ-монтаж` proposes spec, diff, rationale; apply, apply+render; undo.
- `[есть]` Batch AI montage and AI pick from moments.
- `[есть]` AI montage sees clip pieces, transcript windows, QC and montage asset library.
- `[новое]` AI proposal appears as a pending overlay on timeline: changed pieces, inserted memes, subtitle toggles, cover frame.
- `[новое]` Apply options per change: apply all, accept selected, reject selected, preview before/after.
- `[новое]` `ИИ-субтитры`: generate, fix typos, shorten lines, resync selected range, translate/slang normalization only after confirmation.
- `[новое]` `ИИ добыть картинку`: generate original asset or import confirmed URL into library, then place as insert.

### Связанные функции из checklist, которые не должны исчезнуть

- `[есть]` Projects: upload, URL, quality, «Серия и озвучка», status chips, search, cards, rename/delete.
- `[есть]` Workspace: Исходник / Моменты / Монтаж / Клипы / Смонтированные, hidden segments route.
- `[есть]` Исходник: player, crop overlay, metadata, analysis status, content crop sliders, analysis form, transcript toggle, analyses list/cancel/delete.
- `[есть]` Моменты: search, favorites filter, hidden, hide duplicates, select all, analysis chips, grouped plans, quality, include-in-render, title, open editor, favorite, hide/restore.
- `[есть]` Клипы / Смонтированные: clip grid, player, status, rename, publish, delete, uploaded clips.
- `[есть]` Global `/clips`: upload, grid, PublishDialog.
- `[есть]` Авто: URL, preset, provider, max clips, privacy, interval, transcript, render styling, accounts, AI montage before render.
- `[есть]` Публикации, Аккаунты, Очередь, Настройки, Помощь keep their current functions and stay outside the editor.

## 3. Раскладка

### 1600x1000

Canvas with current global shell:

- Global sidebar: 240-256 px, stays visible by default.
- Top app bar: 56-64 px with command search and activity.
- Editor top bar inside content: 52 px. Always visible: `Назад к моментам`, clip title/source, saved state, undo AI, `ИИ-монтаж`, render activity.
- Left clip rail: 112-128 px. Always visible on desktop; can collapse to icons/thumbnails.
- Main stage column: about 620-720 px. Contains source preview with crop frame.
- Phone preview: 220-260 px beside stage. Always visible because vertical output is the product.
- Inspector: 340-380 px. Sticky render footer at bottom.
- Timeline: fixed bottom band 290-330 px across rail + main + phone, not under inspector footer. It is always visible.

At this size, only one side drawer can be open at a time:

- Library drawer overlays the left side of stage/timeline.
- Subtitle line list can replace the left rail or expand above timeline in subtitle mode.
- Clip file JSON and AI montage remain modal/drawer because they are not continuous editing controls.

### 2560 wide

Wide layout should not simply stretch the video. It should expose more production surfaces:

- Global sidebar: 256 px.
- Editor workspace: 2300 px.
- Clip rail: 132 px.
- Stage + phone preview group: 1250-1450 px, centered with stable max sizes.
- Inspector: 420 px.
- Optional right secondary panel: 420-520 px for library, AI proposal diff, subtitle line list or QC/render checklist.
- Timeline: full width below stage, with more visible seconds and less horizontal scrolling.

On 2560, library drawer and inspector can both be visible: library as asset browser, inspector as selected-object settings.

### Always visible

- Back to moments.
- Clip title, source, duration, saved state.
- Stage with crop frame.
- Phone 9:16 preview.
- Transport.
- Timeline with at least `Клипы`, `Фокус`, `Субтитры`, `Вставки`, `Музыка`.
- Object inspector header.
- Render footer: zones toggle, clip file, apply-to-favorites, render one, render selected.

### On demand

- Full asset library drawer.
- Subtitle line table.
- AI montage proposal details.
- Advanced style controls for look/banner/cover/transitions.
- `vvf.clip/1` JSON dialog.
- QC/render history.

## 4. Многодорожечный timeline

Timeline is the editor. It uses clip time as the ruler.

### Дорожка «Клипы»

Objects: ordered video pieces from `clip_plan.segments`.

Actions:

- Drag piece to reorder.
- Trim start/end with handles.
- Split at playhead.
- Delete selected piece.
- Duplicate as jump cut.
- Ripple trim on/off.
- Snap to previous/next piece, playhead, subtitle/word boundary.
- Tooltip shows source time: `58:23.4 -> 58:31.8`.
- Inspector: title, source start/end, clip position, duration, transition before/after, focus summary.

### Дорожка «Фокус»

Objects: keyframes from `segment.focus`.

Actions:

- Click curve to add keyframe at playhead.
- Drag keyframe in time and x position.
- Mark keyframe as `cut`.
- Smooth/hold mode per point.
- Run detector for selected piece/range.
- Stage crop drag updates selected keyframe or creates one.
- Inspector: x/y, t, cut, easing, detector result.

### Дорожка «Субтитры»

Objects: subtitle lines in clip time.

Actions:

- Generate draft.
- Select line.
- Edit text inline.
- Trim line start/end.
- Split at caret or playhead.
- Merge with previous/next.
- Delete line.
- Drag line to nudge timing.
- Snap to word boundaries and waveform transients.
- Inspector: text, start/end, style override, confidence, AI fix.

### Дорожка «Слова караоке»

Objects: word timings inside selected line or expanded all lines.

Actions:

- Nudge word start/end.
- Merge/split token.
- Lock word timing.
- Mark uncertain word.
- Use word boundary as cut/snap target.

This lane is collapsed by default and expands automatically in subtitle mode.

### Дорожка «Вставки»

Objects: image/GIF/video inserts with `mode=full|pip`.

Actions:

- Drag from library to create.
- Move on timeline.
- Trim duration.
- Drag/resize PIP on phone preview.
- Replace asset.
- Duplicate.
- Delete.
- Snap to subtitle lines, word boundaries, clip cuts, playhead.
- Inspector: asset, mode, start, duration, position/size, volume if video has audio, duck.

### Дорожка «Звуки»

Objects: audio inserts and sound from video/GIF inserts in `sound` mode.

Actions:

- Drag audio asset from library.
- Move/trim.
- Volume control.
- Duck original under sound or not.
- Quick audition.

### Дорожка «Музыка»

Objects: one background music bed for the clip.

Actions:

- Enable/disable.
- Select track.
- Set volume.
- Show waveform/envelope.
- Optional future: fade handles and duck depth.

### Selection model

Single selection:

- Clicking object selects it on timeline, highlights it on stage/phone, inspector switches to object settings.
- `Esc` clears selection; in editor with no selection, `Esc` returns to moments.

Multi-selection:

- `Shift-click` adds/removes object.
- Drag marquee selects blocks on unlocked tracks.
- Inspector shows shared operations: move, delete, align, group, apply style.

Inspector follows selection:

- Video piece selected -> piece/cut/focus summary.
- Focus keyframe selected -> frame coordinates and detector tools.
- Subtitle line selected -> text/timing/style.
- Word selected -> word timing and spelling.
- Insert selected -> asset/mode/position/volume.
- Nothing selected -> clip-level inspector with `Оформление`, `Кадр`, `Монтаж`, `Экспорт`.

## 5. Субтитры в редакторе

Owner request: «вынести создание субтитров на монтаж, чтобы их можно было отредактировать до рендера».

### User flow

1. User opens editor and sees subtitle track empty or existing draft.
2. Presses `Сгенерировать` in subtitle mode or inspector.
3. Backend builds/uses the current clip audio without music and without montage inserts, matching existing render order.
4. Provider returns `SubtitleResult`: `segments` and `words`.
5. UI shows:
   - line table with time/text/confidence,
   - subtitle blocks on timeline,
   - word lane for karaoke timings,
   - phone preview with the selected style and position.
6. User edits:
   - fixes text,
   - splits/merges lines,
   - trims/moves line timings,
   - nudges words,
   - changes style/position.
7. Render burns subtitles from the approved draft. If draft is absent, old render-time transcription remains fallback.

### UI

Reference: `ref-editor-2.png`.

Subtitle mode has:

- Header: `Субтитры`, count, language, status `черновик сохранен`, engine/model.
- Primary button: `Сгенерировать` or `Перегенерировать диапазон`.
- Table columns: `#`, `Время`, `Текст`, `Уверенность`, actions.
- Selected line row has play button, text field, split/merge actions.
- Timeline:
  - `Субтитры` blocks show short text.
  - `Слова караоке` lane shows word pills.
  - waveform underneath for timing.
- Inspector:
  - line text textarea,
  - start/end inputs,
  - `Разбить`, `Склеить`, `Сдвиг`,
  - style controls,
  - `ИИ-субтитры`.

### Data model

Use a per-clip subtitle draft, separate from rendered `subtitle_tracks`.

Suggested shape:

```json
{
  "schema": "vvf.subtitles/1",
  "clip_plan_id": 368,
  "source_revision": "segments+render-settings hash",
  "provider": "whisper",
  "model": "large-v3",
  "language": "ru",
  "duration": 20.4,
  "status": "draft",
  "lines": [
    {
      "id": "ln_001",
      "start": 0.12,
      "end": 2.84,
      "text": "Этот мир создан",
      "words": [
        { "id": "w_001", "word": "Этот", "start": 0.12, "end": 0.46 },
        { "id": "w_002", "word": "мир", "start": 0.48, "end": 0.72 },
        { "id": "w_003", "word": "создан", "start": 0.78, "end": 1.24 }
      ],
      "confidence": 0.98,
      "dirty": false
    }
  ],
  "style": {
    "profile_id": 3,
    "position_pct": 12
  }
}
```

Store can be a new `clip_subtitle_drafts` table or a versioned blob attached to `clip_plans`. Prefer a new table once edits/history matter; for first implementation, a JSON field is acceptable if migration is cheap.

### API needed

- `GET /api/clip-plans/{id}/subtitles` - return current draft or `null`.
- `POST /api/clip-plans/{id}/subtitles/generate` - create draft from current clip pieces. Body: `{engine, profile_id, range?, force?}`. Returns job or draft.
- `PATCH /api/clip-plans/{id}/subtitles` - patch lines/words/style. Small patches for autosave.
- `PUT /api/clip-plans/{id}/subtitles` - replace full draft, validate all timings.
- `POST /api/clip-plans/{id}/subtitles/ai-fix` - fix spelling/line breaks for selected line/range; does not move timings unless requested.
- `POST /api/clip-plans/{id}/subtitles/align` - re-align selected line/range when text changed too much.
- `DELETE /api/clip-plans/{id}/subtitles` - clear draft.

Render changes:

- `settings_to_render_kwargs` keeps current `subtitle_profile_id` behavior.
- Before `_render_subtitles` transcribes, render checks for an approved draft on the clip plan.
- If draft exists, render converts draft to `SubtitleResult` and calls `write_ass_subtitles`.
- If no draft exists, current provider transcription path stays as fallback.

### Preserving karaoke word timing

Rules:

- Words are canonical timing objects.
- Editing line text only changes text until word count diverges.
- If edit keeps same number of tokens, keep each word's start/end and replace `word`.
- If edit changes token count:
  - line remains valid for block timing,
  - word lane marks `нужна синхронизация`,
  - user can choose `Распределить по строке` for rough timing or `Синхронизировать` for provider/AI alignment.
- Split line at word boundary moves word objects into two lines without changing word timestamps.
- Merge lines concatenates words and recomputes line start/end from first/last word.
- Dragging a line moves all words by the same offset unless `lock words` is enabled.
- Trimming a line clamps visible line boundaries, but does not silently delete word timings; hidden out-of-range words are shown as warnings.

## 6. Вставки и мемы

Owner request: «монтаж с картинками» должен стать нормальным монтажом, а не number fields.

### Library drawer

Reference: `ref-editor-3.png`.

Drawer title: `Файлы для монтажа`.

Controls:

- Search by label, tags, description.
- Filters: `Все`, `Стикеры`, `Реакции`, `Звуки`, `Видео`, `Избранное`.
- Cards: thumbnail/player, kind, duration, tags, AI description.
- Card actions: preview, add at playhead, drag to timeline, edit metadata, delete.

### Drag onto timeline

Behavior:

- Drag image/GIF/video to `Вставки` lane.
- Drag audio to `Звуки` lane.
- While dragging, show drop marker and snap targets.
- Drop creates normalized `MontageInsert`:
  - `asset_id`,
  - `at` in clip time,
  - `duration` default from asset duration or 1.5 sec,
  - `mode` default `pip` for stickers/reactions, `full` for cutaways, `sound` for audio,
  - `volume`,
  - `duck`.
- If max insert count is still 6, show remaining slots. If limit reached, drawer offers replace/remove.

### Editing

- Move block horizontally to change `at`.
- Trim handles to change `duration`.
- Resize/position PIP on phone preview.
- Double-click insert opens source asset preview.
- `Delete` removes selected insert.
- `Alt-drag` duplicates insert.
- Inspector updates the same `inserts` list currently stored in render settings.

### Preview

- Phone preview uses existing `drawInserts` behavior but adds selection handles.
- Full-screen insert dims/replaces base video in the phone preview at active time.
- Sound-only insert displays audio badge and waveform on timeline, not a fake visual overlay.

## 7. ИИ в редакторе

### 🤖 ИИ-монтаж

Current behavior is good: model sees clip file, transcript windows, QC and library, then returns a validated spec. Keep that contract.

New UI:

- AI result appears as a `Предложение` layer on timeline:
  - new/changed pieces,
  - inserts,
  - cover frame,
  - transition choice,
  - subtitle on/off.
- Side panel shows:
  - `Было` and `Стало` durations,
  - changed cuts,
  - rationale,
  - warnings from validation.
- Actions:
  - `Применить все`,
  - `Принять выбранное`,
  - `Отклонить выбранное`,
  - `Еще вариант`,
  - `Применить и рендерить`.
- After apply, existing `↩ Откатить ИИ-монтаж` stays in top bar.

### ИИ-субтитры

Functions:

- Generate subtitles with chosen engine.
- Fix spelling for selected line/range.
- Re-split long lines by reading speed and pauses.
- Shorten text for karaoke while keeping meaning.
- Resync selected line/range if text and audio diverge.

Safety:

- AI text changes are previewed as diff.
- Timing changes are shown on timeline before apply.
- Existing word timings are preserved unless action explicitly says it will resync.

### ИИ добывает картинку

Owner request: AI can obtain missing meme/sticker and save it into «Файлы для монтажа» for reuse.

Safest sane flow:

1. User selects a timeline moment or asks in inspector: `нужна картинка: удивление / пауза / фейл`.
2. AI proposes one of two paths:
   - `Сгенерировать оригинал` - default and recommended. Creates a new original sticker/cutaway, no real people, no logos, no recognizable characters.
   - `Импортировать по URL` - only if user provides/approves the URL.
3. Before saving, show preview and metadata:
   - label,
   - description «когда уместно»,
   - tags,
   - kind,
   - duration if video/audio.
4. User clicks `Сохранить в библиотеку`.
5. Backend creates `montage_asset` exactly like upload:
   - stores file in `data/montage_assets`,
   - probes media,
   - fills label/description/tags,
   - records provenance: `ai_generated` or `url_imported`.
6. UI inserts it at playhead or leaves it in drawer.

URL import guardrails:

- No silent scraping by agent.
- Only direct URL approved by user.
- Server validates MIME, size, extension and max bytes.
- No private/authenticated URLs.
- Store original URL/provenance for audit.
- If source looks copyrighted/brand/recognizable character, show warning and require manual confirmation or prefer generated original.

Suggested API:

- `POST /api/montage-assets/ai-generate` with `{prompt, kind, style, transparent?, duration?}` -> returns pending preview asset.
- `POST /api/montage-assets/import-url` with `{url, label?, description?, tags?}` -> validates and creates asset.
- `POST /api/montage-assets/{id}/insert-suggestion` optional helper -> returns default insert settings for selected clip time.

## 8. Горячие клавиши

Keep existing:

- `Ctrl/Cmd K` - command palette.
- `?` - shortcuts.
- `1...5` - project tabs.
- `F` / `А` - favorite active/hovered moment or open clip.
- `X` / `Ч` - hide hovered moment in triage.
- `E` / `У` - open editor from hovered moment.
- `Esc` - close modal or return from editor to moments.
- `Space` - play/pause.

Add editor keys:

- `[` / `]` - previous/next clip in rail.
- `J` / `K` / `L` - back, pause, forward playback.
- `←` / `→` - nudge playhead by frame or 0.1 sec.
- `Shift ←` / `Shift →` - nudge selected object by 0.1 sec.
- `Alt ←` / `Alt →` - nudge selected object by 1 frame.
- `B` - blade/split at playhead.
- `Delete` / `Backspace` - delete selected object.
- `M` - mute selected audio/music/insert sound.
- `S` - split selected subtitle line at caret/playhead.
- `Cmd/Ctrl J` - merge selected subtitle with next.
- `I` - open inserts drawer.
- `A` - autofocus selected piece/range.
- `Shift A` - AI action menu for selection.
- `R` - render current clip.
- `Shift R` - render selected clips.
- `Cmd/Ctrl S` - flush autosave now.
- `Cmd/Ctrl Z` / `Cmd/Ctrl Shift Z` - undo/redo editor operations.

## 9. Этапы внедрения

Каждый шаг рассчитан максимум на один рабочий день и дает видимый прирост.

1. **Clip-time timeline shell**: перевести ruler/editor timeline на clip time, добавить track labels, сохранить текущий segment drag/resize через source mapping.
2. **Selection-driven inspector**: единая модель selected object; inspector переключается между clip-level, segment, focus point.
3. **Focus track**: вынести `FocusEditor` в timeline curve, оставить текущий inspector как detail panel.
4. **Insert lane v1**: показывать существующие `render_settings.inserts` на timeline, move/trim/delete, inspector для выбранной вставки.
5. **Library drawer v1**: открыть «Файлы для монтажа» внутри editor, drag/drop asset на insert/sound lane.
6. **Phone PIP handles**: для selected pip insert двигать/масштабировать overlay на phone preview и сохранять position/size после расширения `MontageInsert`.
7. **Subtitle draft API v1**: `GET/POST/PATCH /clip-plans/{id}/subtitles`, генерация draft из текущего clip audio, хранение JSON.
8. **Subtitle timeline v1**: line blocks, text edit, split/merge, move/trim, phone preview from draft.
9. **Render from subtitle draft**: render pass burns approved draft through existing `write_ass_subtitles`; old render-time generation остается fallback.
10. **Word lane**: show/nudge karaoke word timings, preserve timings through text edits.
11. **AI montage overlay**: показывать proposal на timeline до apply, accept/reject selected changes.
12. **AI subtitles**: spelling fix, line re-split, selected range resync with preview diff.
13. **AI asset generation/import**: original image generation first, URL import with confirmation, save into montage library, then insert.
14. **Keyboard and undo/redo**: blade/delete/nudge/split/merge/render shortcuts and command palette entries.
15. **Wide layout**: 2560 mode with persistent library/AI secondary panel, while keeping 1600 mode compact.

Build first for maximum effect:

1. Insert lane + library drawer, because it directly fixes the «картинки на монтаже» pain.
2. Subtitle draft generation/editing before render, because it removes the biggest render-loop waste.
3. Clip-time timeline, because every other object becomes understandable only when the ruler is the clip, not the 4-hour source.

