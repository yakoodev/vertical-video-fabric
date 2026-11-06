import { MissingSourceNotice } from "@/components/MissingSourceNotice";
import { AiBatchPanel, AiBatchReport } from "@/pages/workspace/AiBatchPanel";
import { EmptyState, formatDuration, plural } from "@/components/ui";
import { DraftPickHint } from "@/pages/workspace/DraftPickHint";
import { CropFrame } from "@/pages/workspace/editor/CropFrame";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Экран «Черновики»: плеер, фильтры и сетка черновиков видео. Состояние — в useDraftsEditorState. */
export function DraftsScreen({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, videoRef, heatHeadRef, subPosPctRef, triagePhoneRef, recordMissing, proxyReady,
    sourceMissing, openEditor, hoverPlanRef, plans, selected, compile, batchAuto, batch, setPlanFlag, activePlan,
    planGroups, shownCount, favCount, hiddenCount, analysisChips, analysisMeta, mediaSrc, setChosen, watchOpen,
    hideDuplicates, setHideDuplicates, candidateSearch, setCandidateSearch, hiddenAnalyses, setHiddenAnalyses,
    onlyFavorites, setOnlyFavorites, showHidden, setShowHidden, 
    autoProgress, moduleOn, source, toggleChosen, visiblePlans, planDur, planRender, planThumb, playPlan,
  } = session;
  return (
    <>
      {recordMissing ? <MissingSourceNotice source={source} proxyReady={proxyReady} /> : null}
      <div className="mo-top">
        <div className="panel mo-player">
          <div className="mo-player-frame">
            {/*
              Сцена ровно по размеру кадра источника: рамка 9:16 ставится в
              процентах от родителя, и при чёрных полях по бокам (видео уже
              рамки плеера) она съезжала бы с картинки.
            */}
            <div
              className="mo-stage"
              style={
                source.width && source.height
                  ? {
                      aspectRatio: `${source.width} / ${source.height}`,
                      width: `min(100%, calc(46vh * ${source.width / source.height}))`,
                    }
                  : undefined
              }
            >
              <video ref={videoRef} src={sourceMissing || watchOpen ? undefined : mediaSrc ?? undefined} controls={!sourceMissing} preload="metadata" style={sourceMissing ? { display: "none" } : undefined} />
              {sourceMissing ? (
                activePlan && planRender(activePlan.id) ? (
                  <video
                    key={planRender(activePlan.id)!.id}
                    className="mo-stage-render"
                    src={`/media/clips/${planRender(activePlan.id)!.id}`}
                    poster={`/media/clips/${planRender(activePlan.id)!.id}/thumb`}
                    controls
                    preload="metadata"
                  />
                ) : (
                  <div className="src-missing-stage">У этого клипа нет готового рендера</div>
                )
              ) : null}
              {source.width && source.height && !sourceMissing ? (
                <CropFrame
                  srcW={source.width}
                  srcH={source.height}
                  crop={source.content_crop}
                  segments={visiblePlans.flatMap((p) => p.segments)}
                  videoRef={videoRef}
                  showBanner={false}
                  bannerHeightPct={0}
                  bannerPosPct={0}
                  showSubs={false}
                  subPosPct={0}
                  focusPreset={source.focus_preset}
                  phoneRef={triagePhoneRef}
                  subPosPctRef={subPosPctRef}
                />
              ) : null}
            </div>
          </div>
          {activePlan ? (
            <div className="mo-now">
              <span className="mo-now-title" title={activePlan.title || ""}>
                {activePlan.title || `Клип #${activePlan.id}`}
              </span>
              <button
                type="button"
                className={`btn mo-now-fav${activePlan.favorite ? " fav" : ""}`}
                onClick={() => setPlanFlag(activePlan.id, { favorite: !activePlan.favorite })}
                title="Избранные черновики попадают в редактор"
              >
                {activePlan.favorite ? "★ В избранном" : "☆ В избранное"}
              </button>
            </div>
          ) : null}
          {source.duration_sec ? (
            <div className="mo-heat" title="Где в видео найдены клипы — клик перематывает">
              <span ref={heatHeadRef} className="mo-heat-head" />
              {visiblePlans.map((p) =>
                p.segments.map((s) => (
                  <button
                    key={`${p.id}-${s.id}`}
                    className={`mo-heat-tick${p.favorite ? " fav" : ""}${p.id === activePlan?.id ? " active" : ""}`}
                    style={{
                      left: `${(s.start_sec / source.duration_sec) * 100}%`,
                      width: `max(3px, ${((s.end_sec - s.start_sec) / source.duration_sec) * 100}%)`,
                    }}
                    title={`${p.title || `Клип #${p.id}`} · ${formatDuration(s.start_sec)}`}
                    onClick={() => playPlan(p.id)}
                  />
                )),
              )}
            </div>
          ) : null}
          <div className="mo-heat-scale mono">
            {[0, 0.25, 0.5, 0.75, 1].map((f) => (
              <span key={f}>{formatDuration(source.duration_sec * f)}</span>
            ))}
          </div>
        </div>

        <div className="panel mo-stats">
          {/*
            Так будет выглядеть вертикальный клип: рамка на плеере двигается по
            треку фокуса момента под курсором, а сюда рисуется то, что в неё
            попадает. Раньше это было только в редакторе — выбирать момент
            вслепую, не видя кадрирования, неудобно.
          */}
          {sourceMissing ? null : (
            <div className="mo-phone" title="Так будет выглядеть вертикальный клип">
              <div className="ed-phone-screen mo-phone-screen">
                <canvas ref={triagePhoneRef} width={270} height={480} />
                <span className="ed-phone-tag mono">9:16</span>
              </div>
            </div>
          )}
          <div className="mo-stats-line">
            <b>{plural(plans.length, "клип", "клипа", "клипов")}</b>
            <span>· {plural(plans.filter((p) => p.status !== "rendered").length, "черновик", "черновика", "черновиков")}</span>
            <span>· {favCount} в работе ★</span>
            <span>· {hiddenCount} скрыто</span>
          </div>
          <div className="mo-stats-meta muted mono">
            {formatDuration(source.duration_sec)}
            {source.width ? ` · ${source.width}×${source.height}` : ""}
            {` · ${analysisChips.filter(([a]) => a).length} анализ(а)`}
          </div>
          <button
            className="btn primary mo-big"
            onClick={() => openEditor()}
            title={favCount ? "Работать над избранными черновиками" : "Отметьте ★ черновики, чтобы работать над ними в редакторе"}
          >
            Открыть редактор{favCount ? ` (${favCount} ★)` : ""}
          </button>
          <button
            className="btn mo-big"
            disabled={batch.isPending || !selected.size || recordMissing}
            onClick={() => batch.mutate(undefined)}
            title={recordMissing ? "Для рендера нужна запись стрима" : "Рендер отмеченных «в рендер» с настройками из редактора"}
          >
            {batch.isPending ? "Запуск…" : `▶ Рендерить выбранные (${selected.size})`}
          </button>
          {moduleOn("ai_montage") ? <AiBatchPanel sourceId={sourceId} favCount={favCount} /> : null}
          <p className="muted mo-stats-hint">
            ☆ — взять в работу, ☐ — в рендер, ✕ — скрыть лишнее. Клик по кадру — посмотреть клип.
          </p>
        </div>
      </div>

      <AiBatchReport sourceId={sourceId} onOpenClip={(id) => openEditor(id)} />

      <DraftPickHint projectId={source.project_id ?? null} />

      <div className="mo-toolbar">
        <div className="mo-search">
          <span aria-hidden>⌕</span>
          <input
            className="input"
            placeholder="Поиск по названию…"
            value={candidateSearch}
            onChange={(e) => setCandidateSearch(e.target.value)}
          />
        </div>
        <button
          className={`chip${onlyFavorites ? " active" : ""}`}
          onClick={() => setOnlyFavorites((v) => !v)}
          title="Показать только избранные"
        >
          ★ Избранное · {favCount}
        </button>
        {hiddenCount ? (
          <label className="switch">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            <span className="switch-track" />
            <span>скрытые ({hiddenCount})</span>
          </label>
        ) : null}
        {plans.some((p) => p.duplicate_of != null) ? (
          <label className="switch">
            <input type="checkbox" checked={hideDuplicates} onChange={(e) => setHideDuplicates(e.target.checked)} />
            <span className="switch-track" />
            <span>скрыть дубли</span>
          </label>
        ) : null}
        <button
          className="btn sm"
          onClick={() => setChosen(new Set(selected.size === plans.length ? [] : plans.map((p) => p.id)))}
        >
          {selected.size === plans.length ? "Снять все" : "Выбрать все"}
        </button>
        {analysisChips.map(([aid, count]) => {
          const shown = !hiddenAnalyses.has(aid);
          return (
            <button
              key={aid}
              className={`chip${shown ? " active" : " off"}`}
              title={shown ? "Скрыть этот анализ" : "Показать этот анализ"}
              onClick={() =>
                setHiddenAnalyses((prev) => {
                  const next = new Set(prev);
                  next.has(aid) ? next.delete(aid) : next.add(aid);
                  return next;
                })
              }
            >
              {shown ? "" : "🚫 "}
              {aid ? `Анализ #${aid}` : "Вручную"} · {count}
            </button>
          );
        })}
        {candidateSearch.trim() ? <span className="muted mo-count">{shownCount} из {plans.length}</span> : null}
        <span className="mo-toolbar-acts">
          <button
            className="btn sm primary"
            onClick={() => openEditor()}
            title="Монтаж избранных (E — черновик под курсором)"
          >
            ✂ Монтаж{favCount ? ` · ${favCount} ★` : ""}
          </button>
          <button
            className="btn sm"
            disabled={!favCount || batchAuto.isPending}
            onClick={() => batchAuto.mutate(plans.filter((p) => p.favorite).map((p) => p.id))}
            title="Каждому избранному клипу: распознать речь (если ещё нет) и ⚡ автомонтаж — хук, субтитры, эмодзи, наезды. Без ИИ."
          >
            {batchAuto.isPending ? `⚡ ${autoProgress}` : `⚡ Автомонтаж ★${favCount ? ` · ${favCount}` : ""}`}
          </button>
          {moduleOn("compilation") ? (
            <button
              className="btn sm"
              disabled={favCount < 2 || compile.isPending}
              onClick={() => compile.mutate(plans.filter((p) => p.favorite).map((p) => p.id))}
              title="Склеить избранные в один клип «ТОП-N»: номера обратным отсчётом, названия, субтитры, whoosh между клипами"
            >
              {compile.isPending ? "Собираю…" : "🧩 Компиляция ★"}
            </button>
          ) : null}
          <button
            className="btn sm"
            disabled={batch.isPending || !selected.size}
            onClick={() => batch.mutate(undefined)}
            title="Рендер отмеченных «в рендер»"
          >
            ▶ Рендер · {selected.size}
          </button>
        </span>
      </div>

      {!shownCount ? (
        <EmptyState icon="🔎" title="Ничего не показано" hint="Снимите фильтры или включите скрытые анализы" />
      ) : null}

      {planGroups.map(([analysisId, group]) => (
        <section key={analysisId} className="mo-group">
          <div className="mo-group-head">
            <span className="mo-group-name">{analysisMeta(analysisId)}</span>
            <span className="mo-group-count">· {group.length}</span>
            <button
              className="mo-group-sel"
              onClick={() =>
                setChosen((prev) => {
                  const next = new Set(prev ?? []);
                  const ids = group.map((p) => p.id);
                  const allOn = ids.every((id) => next.has(id));
                  ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)));
                  return next;
                })
              }
            >
              · {group.every((p) => selected.has(p.id)) ? "снять" : "выбрать"}
            </button>
          </div>
          <div className="mo-grid">
            {group.map((p) => (
              <article
                key={p.id}
                onMouseEnter={() => (hoverPlanRef.current = p.id)}
                onMouseLeave={() => (hoverPlanRef.current = null)}
                className={`mo-card${p.id === activePlan?.id ? " active" : ""}${p.hidden ? " is-hidden" : ""}${
                  p.favorite ? " fav" : ""
                }`}
              >
                <button className="mo-thumb" onClick={() => playPlan(p.id)} title="Посмотреть черновик в плеере">
                  {planThumb(p)}
                  {typeof p.quality === "number" ? (
                    <span
                      className="mo-q"
                      title={`Оценка качества ${(p.quality * 100).toFixed(0)}% (речь + длительность + оценка модели)`}
                    >
                      {(p.quality * 100).toFixed(0)}
                    </span>
                  ) : null}
                  {p.duplicate_of != null ? <span className="mo-dup">дубль</span> : null}
                  {p.ai_pick ? (
                    <span className="mo-ai" title={`✨ ИИ: ${p.ai_pick.score}/10 — ${p.ai_pick.reason}`}>
                      ✨ {p.ai_pick.score}
                    </span>
                  ) : null}
                  <span className="mo-play">▶</span>
                  <span className="mo-dur mono">
                    {formatDuration(planDur(p))}
                    {p.segments.length > 1 ? ` · ${p.segments.length} сегм.` : ""}
                  </span>
                </button>
                <button
                  className="mo-title"
                  onClick={() => openEditor(p.id)}
                  title="Открыть в редакторе"
                >
                  {p.title || `Клип #${p.id}`}
                </button>
                {p.ai_pick?.reason ? <div className="mo-why">✨ {p.ai_pick.reason}</div> : null}
                <div className="mo-stateline">
                  {p.status === "rendered" ? (
                    <span className="mo-state ok">готов</span>
                  ) : p.owner_picked ? (
                    <span className="mo-state work" title="Ты выбрал этот черновик: агент может его монтировать">черновик, в работе</span>
                  ) : (
                    <span className="mo-state">черновик</span>
                  )}
                </div>
                <div className="mo-foot">
                  <label className="check" title="Включить в рендер">
                    <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggleChosen(p.id)} />
                    <span>в рендер</span>
                  </label>
                  <button
                    className={`mo-act${p.favorite ? " fav" : ""}`}
                    title={p.favorite ? "Убрать из работы" : "Взять в работу: агент сможет монтировать этот черновик"}
                    onClick={() => setPlanFlag(p.id, { favorite: !p.favorite })}
                  >
                    {p.favorite ? "★" : "☆"}
                  </button>
                  <button
                    className="mo-act"
                    title={p.hidden ? "Вернуть черновик" : "Скрыть черновик"}
                    onClick={() => setPlanFlag(p.id, { hidden: !p.hidden })}
                  >
                    {p.hidden ? "↩" : "✕"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
