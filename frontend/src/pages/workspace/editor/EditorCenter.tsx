import { MissingSourceNotice } from "@/components/MissingSourceNotice";
import { frameSize } from "@/motion";
import { FRAME_SEC, formatTimecode } from "@/pages/workspace/editor/editorHelpers";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";
import { MontagePlayer } from "@/pages/workspace/preview/MontagePlayer";

/** Середина редактора: кадр как на телефоне и управление плеером. */
export function EditorCenter({ session }: { session: DraftsEditorSession }) {
  const {
    toast, phoneRef, phoneOverlayRef, tcRef, recordMissing, proxyReady, sourceMissing, playRanges, togglePlay,
    reframedView, inlineCancelRef, planSubs, autofocus, setFixedFrame, activePlan, selectedPreset, lookCss, loop,
    setLoop, rate, setRate, subsOn, bannerOn, bannerId, mirror, setLayers, setCamera, aspect, layersFmt,
    setLayersFmt, inlineEdit, setInlineEdit, showZones, bannerHeightPct, bannerPosPct, subPosPct, pickingPoint,
    moduleOn, source, isCanvas, selectedSegment, stepClip, openWatch, onInsertPointerDown, onInsertPointerMove,
    onOverlayDoubleClick, commitInlineEdit, onInsertPointerUp, planDur, planRender, montageRootRef, montageMounted,
    montageShown, montageWant, montageFull, montageStartAt, montageTotal, srcPlaying, setMontageMedia,
    montageOnRevision, montageRelease, montageEnterFull, montageExitFull,
  } = session;
  const full = montageFull !== "none";
  const playing = montageWant || srcPlaying;
  const canPlay = Boolean(activePlan?.segments.length);
  return (
    <div className="ed-center">
      <div className="ed-phone" title={`Так будет выглядеть клип ${aspect}`}>
          <div
            className="ed-phone-screen"
            data-fmt={aspect}
            style={{ "--fmt-ar": `${frameSize(aspect).w} / ${frameSize(aspect).h}`, "--fmt-k": frameSize(aspect).w / frameSize(aspect).h } as React.CSSProperties}
          >
            <canvas
              ref={phoneRef}
              width={frameSize(aspect).w / 2}
              height={frameSize(aspect).h / 2}
              style={{
                filter: lookCss || undefined,
                transform: mirror ? "scaleX(-1)" : undefined,
              }}
            />
            <canvas
              ref={phoneOverlayRef}
              className={`ed-phone-overlay${pickingPoint ? " picking" : ""}`}
              width={frameSize(aspect).w}
              height={frameSize(aspect).h}
              style={{ pointerEvents: "auto", touchAction: "none" }}
              title="Слой или мем можно двигать мышкой, тянуть за уголок, крутить за кружок; двойной клик по тексту — править"
              onPointerDown={onInsertPointerDown}
              onPointerMove={onInsertPointerMove}
              onPointerUp={onInsertPointerUp}
              onPointerCancel={onInsertPointerUp}
              onDoubleClick={onOverlayDoubleClick}
            />
            {inlineEdit ? (
              <textarea
                className="ed-inline-text"
                autoFocus
                defaultValue={inlineEdit.text}
                style={{ left: `${inlineEdit.left}%`, top: `${inlineEdit.top}%`, width: `${inlineEdit.width}%` }}
                onFocus={(e) => e.currentTarget.select()}
                onBlur={(e) => commitInlineEdit(e.currentTarget.value)}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === "Escape") {
                    // Отмена: blur от размонтирования поля не должен сохранить текст.
                    inlineCancelRef.current = true;
                    setInlineEdit(null);
                  }
                  else if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    commitInlineEdit(e.currentTarget.value);
                  }
                }}
              />
            ) : null}
            {selectedPreset?.vignette ? (
              <div className="stage-vignette" style={{ opacity: Math.min(1, selectedPreset.vignette) }} />
            ) : null}
            {showZones && bannerOn && !bannerId ? (
              <div className="safe-zone safe-zone--banner" style={{ top: `${bannerPosPct}%`, height: `${bannerHeightPct}%` }}>
                <span>Баннер</span>
              </div>
            ) : null}
            {showZones && subsOn && !planSubs.data?.words?.length ? (
              <div className="safe-zone safe-zone--subs" style={{ bottom: `${subPosPct}%` }}>
                <span>Субтитры</span>
              </div>
            ) : null}
            {sourceMissing && activePlan ? (
              planRender(activePlan.id) ? (
                <video
                  key={planRender(activePlan.id)!.id}
                  className="ed-phone-render"
                  src={`/media/clips/${planRender(activePlan.id)!.id}`}
                  poster={`/media/clips/${planRender(activePlan.id)!.id}/thumb`}
                  controls
                  preload="metadata"
                />
              ) : (
                <div className="src-missing-stage ed-phone-render">Рендера этого клипа нет</div>
              )
            ) : null}
            <span className="ed-phone-tag mono">{aspect}</span>
            {/* Весь монтаж прямо здесь: ролик поверх кадра правки, пока играет; на весь экран — он же. */}
            {!sourceMissing && activePlan ? (
              <div
                ref={montageRootRef}
                className={`ed-montage${montageShown || full ? " is-on" : ""}${montageFull === "fallback" ? " is-full" : ""}`}
                onClick={(e) => {
                  if (!full && e.target instanceof HTMLVideoElement) togglePlay();
                }}
              >
                {montageMounted ? (
                  <MontagePlayer
                    key={activePlan.id}
                    clipPlanId={activePlan.id}
                    autoPlay={montageWant}
                    startAt={montageStartAt}
                    onMedia={setMontageMedia}
                    onRevision={montageOnRevision}
                    bare={!full}
                    controls={full}
                    keys={full}
                    onClose={full ? montageExitFull : undefined}
                    rootRef={montageRootRef}
                    title={activePlan.title || `Клип #${activePlan.id}`}
                    className={full ? "mplayer--watch" : "mplayer--inline"}
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      {recordMissing ? <MissingSourceNotice source={source} compact proxyReady={proxyReady} /> : null}
      {reframedView ? (
        <div className="ed-reframe-note" role="status">
          <span>
            Слои размечены в <b>{layersFmt}</b> — превью показывает их перенос в <b>{aspect}</b>, как сделает рендер
            (одним масштабом по сценам). Двигать мышью — после разметки.
          </span>
          <button
            type="button"
            className="btn sm"
            title={`Сохранить перенесённые слои и камеру как разметку ${aspect}: дальше слои правятся прямо в этом кадре. Остальные форматы будут переноситься уже из ${aspect}`}
            onClick={() => {
              setLayers(reframedView.layers);
              setCamera(reframedView.camera);
              setLayersFmt(aspect);
              toast.push(`Слои размечены в ${aspect}: теперь их можно двигать в этом кадре (Ctrl+Z в истории клипа — откат)`, "success");
            }}
          >
            Разметить слои в этом формате
          </button>
        </div>
      ) : null}
      {sourceMissing && activePlan ? (
        <div className="ed-transport">
          <button className="btn primary sm" onClick={() => void openWatch()} title="Записи нет: покажет готовый рендер и скажет, что с монтажом">
            ▶ Смотреть монтаж
          </button>
        </div>
      ) : null}
      {sourceMissing ? null : <div className="ed-transport">
        <button
          className="btn primary sm ed-watch-btn ed-play-btn"
          disabled={!canPlay}
          onClick={togglePlay}
          aria-label={playing ? "Пауза" : "Смотреть монтаж"}
          title={
            playing
              ? "Пауза (пробел): кадр остаётся, можно править"
              : "Весь монтаж прямо здесь: склейки, переходы, стоп-кадры, музыка — как выйдет. Пробел — плей/пауза, клик по таймлайну — перемотка"
          }
        >
          {playing ? "⏸ Пауза" : "▶ Смотреть"}
        </button>
        <div className="ed-tc" title="Время ролика · ←/→ — кадр, Shift+←/→ — секунда, Home — в начало, пробел — плей/пауза">
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-1e6)} aria-label="В начало клипа" title="В начало (Home)">
            ⏮
          </button>
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-FRAME_SEC)} aria-label="Кадр назад" title="Кадр назад (←)">
            ◂
          </button>
          <span className="ed-tc-now mono" ref={tcRef}>
            00:00.00
          </span>
          <span className="ed-tc-total mono muted">/ {formatTimecode(montageTotal ?? (activePlan ? planDur(activePlan) : 0))}</span>
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(FRAME_SEC)} aria-label="Кадр вперёд" title="Кадр вперёд (→)">
            ▸
          </button>
        </div>
        <button
          className="btn sm"
          disabled={!selectedSegment}
          title={selectedSegment ? "Только выбранный кусок, как он лежит в записи: без переходов и стоп-кадров" : "Выберите кусок на таймлайне"}
          onClick={() => {
            if (!selectedSegment) return;
            montageRelease();
            playRanges([{ start: selectedSegment.start_sec, end: selectedSegment.end_sec }]);
          }}
        >
          ▶ Сегмент
        </button>
        <label className="switch">
          <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
          <span className="switch-track" />
          <span>Зациклить</span>
        </label>
        <div className="seg speed-seg" role="radiogroup" aria-label="Скорость">
          {[0.5, 1, 1.5, 2].map((r) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={rate === r}
              className={`seg-item${rate === r ? " active" : ""}`}
              title="Скорость воспроизведения"
              onClick={() => setRate(r)}
            >
              {r}×
            </button>
          ))}
        </div>
        <button
          className="btn sm ghost ed-full-btn"
          disabled={!canPlay}
          onClick={(e) => {
            // Иначе после выхода пробел снова «нажмёт» эту кнопку, а не плей/паузу.
            e.currentTarget.blur();
            montageEnterFull();
          }}
          aria-label="На весь экран"
          title="Монтаж на весь экран с той же секунды · Esc — обратно сюда"
        >
          ⛶
        </button>
        {/* Холст — кадр 9:16 целиком: рамку двигать некуда. */}
        {activePlan && !isCanvas
          ? (() => {
              const fx = activePlan.segments[0]?.focus ?? [];
              const constant = fx.length > 0 && fx.every((p) => Math.abs(p.x - fx[0].x) < 0.001);
              const mx = constant ? fx[0].x : null;
              return (
                <div className="ed-fixed">
                  <span className="muted">Фикс. рамка</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round((mx ?? 0.5) * 100)}
                    onChange={(e) => setFixedFrame(activePlan.id, Number(e.target.value) / 100)}
                    title="Зафиксировать кадр 9:16 по горизонтали (или тащи рамку на превью). Пишет точки фокуса, не конфликтует с кейфреймами."
                  />
                  <span className="mono ed-fixed-val">{mx == null ? "трек" : `${Math.round(mx * 100)}%`}</span>
                  {moduleOn("autofocus") ? (
                    <button
                      className="btn sm"
                      disabled={autofocus.isPending || !activePlan.segments.length}
                      onClick={() => autofocus.mutate(activePlan.segments.map((s) => s.id))}
                      title="Пересчитать автофокус для этого клипа"
                    >
                      Авто
                    </button>
                  ) : null}
                </div>
              );
            })()
          : null}
      </div>}
    </div>
  );
}
