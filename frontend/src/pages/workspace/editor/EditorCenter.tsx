import { segSpeed } from "@/api/types";
import { MissingSourceNotice } from "@/components/MissingSourceNotice";
import { frameSize } from "@/motion";
import { FRAME_SEC, formatTimecode } from "@/pages/workspace/editor/editorHelpers";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Середина редактора: кадр как на телефоне и управление плеером. */
export function EditorCenter({ session }: { session: DraftsEditorSession }) {
  const {
    toast, phoneRef, phoneOverlayRef, tcRef, recordMissing, proxyReady, sourceMissing, playRanges, stopPlayback,
    reframedView, inlineCancelRef, planSubs, autofocus, setFixedFrame, activePlan, selectedPreset, lookCss, loop,
    setLoop, rate, setRate, subsOn, bannerOn, bannerId, mirror, setLayers, setCamera, aspect, layersFmt,
    setLayersFmt, inlineEdit, setInlineEdit, showZones, bannerHeightPct, bannerPosPct, subPosPct, pickingPoint,
    moduleOn, source, isCanvas, selectedSegment, stepClip, openWatch, onInsertPointerDown, onInsertPointerMove,
    onOverlayDoubleClick, commitInlineEdit, onInsertPointerUp, planDur, planRender,
  } = session;
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
          className="btn primary sm ed-watch-btn"
          disabled={!activePlan?.segments.length}
          onClick={() => void openWatch()}
          title="Весь клип со склейками, звуком и музыкой на весь экран, без рендера. Там: пробел — плей/пауза, ←/→ — кадр, Esc — закрыть"
        >
          ▶ Смотреть монтаж
        </button>
        <button
          className="btn sm"
          disabled={!activePlan?.segments.length}
          onClick={() =>
            activePlan && playRanges(activePlan.segments.map((s) => ({ start: s.start_sec, end: s.end_sec, speed: segSpeed(s) })))
          }
          title="Играть в телефоне слева: поверхность правки (без переходов и стоп-кадров). Весь монтаж — «Смотреть монтаж»"
        >
          ▶ Превью клипа
        </button>
        <button
          className="btn sm"
          disabled={!selectedSegment}
          title={selectedSegment ? "" : "Выберите сегмент на таймлайне"}
          onClick={() => selectedSegment && playRanges([{ start: selectedSegment.start_sec, end: selectedSegment.end_sec }])}
        >
          ▶ Сегмент
        </button>
        <button className="btn sm" onClick={stopPlayback}>
          ⏹ Стоп
        </button>
        <div className="ed-tc" title="Время клипа · ←/→ — кадр, Shift+←/→ — секунда, пробел — плей/пауза">
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-1e6)} aria-label="В начало клипа">
            ⏮
          </button>
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-FRAME_SEC)} aria-label="Кадр назад">
            ◂
          </button>
          <span className="ed-tc-now mono" ref={tcRef}>
            00:00.00
          </span>
          <span className="ed-tc-total mono muted">/ {formatTimecode(activePlan ? planDur(activePlan) : 0)}</span>
          <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(FRAME_SEC)} aria-label="Кадр вперёд">
            ▸
          </button>
        </div>
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
