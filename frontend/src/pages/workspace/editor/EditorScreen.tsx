import { Link } from "react-router-dom";
import { clipPlansApi } from "@/api/clipPlans";
import { qk } from "@/api/keys";
import { type Aspect, type MontageInsert, type TransitionSettings, segSpeed } from "@/api/types";
import { FxChainEditor } from "@/pages/workspace/FxChainEditor";
import { ApiError } from "@/api/client";
import { MissingSourceNotice } from "@/components/MissingSourceNotice";
import { SubtitleEditor } from "@/pages/workspace/SubtitleEditor";
import { Group, InspectorTabs, MoreSection } from "@/pages/workspace/InspectorParts";
import { type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { clipAccent, formatRefSize, frameSize, sourceStrategy, type Layer } from "@/motion";
import { duplicateLayer, LAYER_ICON, layerTitle } from "@/pages/workspace/motion/edit";
import { LayerInspector } from "@/pages/workspace/motion/LayerInspector";
import { CameraPanel } from "@/pages/workspace/motion/CameraPanel";
import { AgentPanel } from "@/pages/workspace/motion/AgentPanel";
import { clipPath } from "@/lib/paths";
import { LayoutPanel } from "@/pages/workspace/LayoutPanel";
import { SourceAudioPanel } from "@/pages/workspace/SourceAudioPanel";
import { CutsPanel } from "@/pages/workspace/CutsPanel";
import { LintPanel } from "@/pages/workspace/LintPanel";
import { BackdropLive, canvasLabel } from "@/pages/workspace/canvasClip";
import { cutsTotal } from "@/api/cuts";
import { CENSOR_STYLES, muteTotal, type CensorStyle } from "@/api/mute";
import { GAIN_MAX_DB, GAIN_MIN_DB, normalizeGain } from "@/api/gain";
import { playbackApi } from "@/api/playback";
import { GeneratePanel, MemesPanel, SoundsPanel, StickersPanel, TemplatesPanel, TextPanel } from "@/pages/workspace/motion/LibraryPanels";
import { ClipTimeline } from "@/pages/workspace/ClipTimeline";
import { AssetDrawer } from "@/pages/workspace/AssetDrawer";
import { formatDuration } from "@/components/ui";
import { clipTimeOf, focusWindowPos, focusXAt, FRAME_SEC, formatTimecode, LIB_TABS } from "@/pages/workspace/editor/editorHelpers";
import { CropFrame } from "@/pages/workspace/editor/CropFrame";
import { FocusEditor } from "@/pages/workspace/editor/FocusEditor";
import { LookPicker } from "@/pages/workspace/editor/LookPicker";
import { colorBadge, PieceColorControl, SegmentSpeedControl, speedBadge } from "@/pages/workspace/editor/PieceControls";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Шапка редактора: назад к клипу или черновикам, название, шаг назад, ИИ-монтаж. */
export function EditorViewBar({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, toast, stopPlayback, backToMoments, flushSettings, undoEdit, activePlan, setAiMontageOpen,
    setSettingsNonce, setAiGoal, moduleOn, source,
  } = session;
  return (
    <div className="ed-viewbar">
      {activePlan ? (
        <Link className="btn primary ed-back" to={clipPath(activePlan.id)} onClick={stopPlayback} title="На экран клипа: версии, описание, превью, публикация">
          <span className="ed-back-arrow" aria-hidden>←</span>
          К клипу
        </Link>
      ) : null}
      <button className="btn ed-back" onClick={backToMoments} title="Вернуться к списку черновиков (или кнопка «Назад» браузера)">
        <span className="ed-back-arrow" aria-hidden>←</span>
        Назад к черновикам
      </button>
      <div className="ed-crumb">
        <span className="ed-crumb-title">{activePlan?.title || (activePlan ? `Клип #${activePlan.id}` : "")}</span>
        <span className="ed-crumb-src muted">
          {source.display_name || source.original_filename || source.original_url || `Видео #${source.id}`}
        </span>
      </div>
      <button
        className="btn ed-undo-btn"
        disabled={!activePlan || undoEdit.isPending}
        onClick={() => activePlan && undoEdit.mutate(activePlan.id)}
        title="Шаг назад по последней правке клипа (Ctrl+Z): настройки, куски, субтитры, мемы"
      >
        {undoEdit.isPending ? "…" : "↩ Шаг назад"}
      </button>
      {activePlan?.has_montage_backup ? (
        <button
          className="btn ed-undo-btn"
          onClick={async () => {
            try {
              await flushSettings();
              await clipPlansApi.aiMontageUndo(activePlan.id);
              toast.success("Откатил к версии до ИИ-монтажа");
              await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
              setSettingsNonce((n) => n + 1);
            } catch (e) {
              toast.error(e instanceof ApiError ? e.message : "Не удалось откатить");
            }
          }}
          title="Вернуть клип к версии до ИИ-монтажа"
        >
          ↩ Откатить ИИ-монтаж
        </button>
      ) : null}
      {moduleOn("ai_montage") ? (
        <button
          className="btn ed-ai-btn"
          disabled={!activePlan}
          onClick={async () => {
            await flushSettings();
            setAiGoal("");
            setAiMontageOpen(true);
          }}
          title="ИИ перемонтирует клип: хук в начало, без пауз и воды, переход, обложка — с предпросмотром «было → стало»"
        >
          🤖 ИИ-монтаж
        </button>
      ) : null}
    </div>
  );
}

/** Экран редактора: библиотека, превью, инспектор и таймлайн. Состояние — в useDraftsEditorState. */
export function EditorScreen({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, toast, libTabs, videoRef, phoneRef, phoneOverlayRef, tcRef, subLinesRef, subWordsRef, bannerImgRef,
    bannerGeomRef, subStyleRef, subPosPctRef, inspScrollRef, recordMissing, proxyReady, sourceMissing, 
    banners, tracks, subs, assetsQuery, pickableAssets, navigate, playbackRef, playRanges, stopPlayback, openEditor,
    musicPreviewRef, musicFromUrl, patchTransition, reframedView, motionViewRef, inlineCancelRef, motionFonts,
    clipNow, patchCover, planWave, planSubs, plans, selected, transitionOpts, agentRunningRef, flushSettings,
    applyToFavorites, composeCover, autoEdit, mixReport, autoMix, censorMat, brands, applyBrand, segmentSpeed,
    snapBeats, pieceColor, matchColors, batch, focusOptions, setFocus, cutStrategies, refineCuts, autofocus, commit,
    pickPointRef, setFixedFrame, setPlanFlag, activePlan, uniquePresets, selectedPreset, lookCss, lookFrames,
    tcSegsRef, libTab, setLibTab, tlSelected, setTlSelected, subLines, setSubLines, mediaSrc, activePlanId,
    setSelectedSeg, loop, setLoop, rate, setRate, presetId, setPresetId, subsOn, setSubsOn, subId, setSubId,
    subEngine, setSubEngine, bannerOn, setBannerOn, bannerId, setBannerId, musicOn, setMusicOn, trackId, setTrackId,
    musicStartSec, setMusicStartSec, musicUrl, setMusicUrl, mirror, setMirror, transition, cover, inserts,
    setInserts, layers, setLayers, camera, setCamera, layout, setLayout, cuts, setCuts, backdrop, setBackdrop, mute,
    setMute, gain, setGain, audioEdit, setAudioEdit, mixSettings, setMixSettings, aspect, setAspect, layersFmt,
    setLayersFmt, endHoldSec, setEndHoldSec, motionBlur, setMotionBlur, inlineEdit, setInlineEdit, coverUploading,
    setCoverUploading, setClipFileOpen, watchOpen, setAiMontageOpen, setSettingsNonce, setAiGoal, setSubsWide,
    useVlmFocus, setUseVlmFocus, packagePieces, setPackagePieces, showZones, setShowZones, bannerHeightPct,
    setBannerHeightPct, bannerPosPct, setBannerPosPct, subPosPct, setSubPosPct, coverTitle, setCoverTitle,
    coverStyle, setCoverStyle, censorStyle, setCensorStyle, brandPick, setBrandPick, snapMode, setSnapMode,
    pickingPoint, setPickingPoint, moduleOn, source, isCanvas, selectedSegment, clipTimeNow, seekClipTime, stepClip,
    openWatch, insertsPreview, clipTimeExact, selectedLayerId, captionWords, onInsertPointerDown,
    onInsertPointerMove, onOverlayDoubleClick, commitInlineEdit, onInsertPointerUp, splitAtClip, deleteSelected,
    clipDuration, addLayers, addAtCursor, dropLayersAt, selectOnTimeline, inspTabs, inspActive, pickInspTab,
    selectedPieceIndex, railPlans, planDur, planRender, planThumb,
  } = session;
  return (
    <div className={`ed${libTab === "frame" ? " ed--wide-lib" : ""}`}>
      <nav className="ed-tabs" aria-label="Библиотека">
        {libTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`ed-tab${libTab === tab.key ? " active" : ""}`}
            onClick={() => setLibTab(tab.key)}
            title={tab.hint}
          >
            <span className="ed-tab-ico" aria-hidden>
              {tab.icon}
            </span>
            <span className="ed-tab-label">{tab.label}</span>
          </button>
        ))}
      </nav>

      <aside className="ed-lib" aria-label={LIB_TABS.find((t) => t.key === libTab)?.label}>
        {libTab === "moments" ? (
          <>
            <div className="ed-lib-head">
              <strong>{railPlans.some((p) => p.favorite) ? "★ Избранное" : "Черновики"}</strong>
              <span className="muted">{railPlans.length}</span>
            </div>
            <div className="ed-lib-moments">
              {railPlans.map((p) => (
          <button
            key={p.id}
            className={`ed-rail-item${p.id === activePlan?.id ? " active" : ""}`}
            onClick={() => openEditor(p.id)}
            title={p.title || `Клип #${p.id}`}
          >
            {planThumb(p)}
            {p.favorite ? <span className="ed-rail-star">★</span> : null}
            <span className="ed-rail-dur mono">{formatDuration(planDur(p))}</span>
          </button>
        ))}
            </div>
          </>
        ) : null}
        {/* Исходник с рамкой 9:16 живёт всегда: из его <video> рисуется превью.
            На других вкладках он «припаркован» — невидим, но играет. */}
        <div className={`ed-lib-frame${libTab === "frame" ? "" : " parked"}`}>
          <div className="ed-lib-head">
            <strong>{isCanvas ? "Холст" : "Кадр видео"}</strong>
            <span className="muted">
              {isCanvas ? "видео нет — весь кадр фон; сменить — «Фон холста» справа" : `тащи рамку — так встанет кадр ${aspect}`}
            </span>
          </div>
          <div className="ed-stage">
            <div
              className={`stage-frame${mirror ? " mirrored" : ""}`}
              style={{
                ...(source.width && source.height
                  ? {
                      aspectRatio: `${source.width} / ${source.height}`,
                      // Cap by height too, or max-height would squash the frame and
                      // the % -positioned crop window would drift off the video.
                      maxWidth: `calc(52vh * ${source.width} / ${source.height})`,
                      margin: "0 auto",
                    }
                  : {}),
                // Mirror the whole stage so the crop window and zones stay aligned
                // with what the render actually produces (hflip after the crop).
                transform: mirror ? "scaleX(-1)" : undefined,
              }}
            >
              <video
                ref={videoRef}
                src={sourceMissing || watchOpen ? undefined : mediaSrc ?? undefined}
                controls={!isCanvas && !sourceMissing}
                preload="metadata"
                style={isCanvas ? { opacity: 0 } : lookCss ? { filter: lookCss } : undefined}
              />
              {isCanvas ? (
                // Основа холста играет невидимой (по ней идёт время), видно — фон клипа.
                <BackdropLive
                  className="stage-canvas-bg"
                  bg={backdrop}
                  accent={clipAccent(layers)}
                  clipTime={() => clipTimeOf(activePlan?.segments ?? [], videoRef.current?.currentTime ?? 0) ?? 0}
                />
              ) : null}
              {selectedPreset?.vignette ? (
                <div className="stage-vignette" style={{ opacity: Math.min(1, selectedPreset.vignette) }} />
              ) : null}
              <CropFrame
                srcW={source.width}
                srcH={source.height}
                crop={source.content_crop}
                segments={activePlan?.segments ?? []}
                videoRef={videoRef}
                showBanner={showZones && bannerOn}
                bannerHeightPct={bannerHeightPct}
                bannerPosPct={bannerPosPct}
                showSubs={showZones && subsOn}
                subPosPct={subPosPct}
                focusPreset={source.focus_preset}
                onManualX={activePlan && !isCanvas ? (x) => setFixedFrame(activePlan.id, x) : undefined}
                mirror={mirror}
                phoneRef={phoneRef}
                insertsPreview={insertsPreview}
                overlayRef={phoneOverlayRef}
                subLinesRef={subLinesRef}
                subWordsRef={subWordsRef}
                subStyleRef={subStyleRef}
                bannerRef={bannerImgRef}
                bannerGeomRef={bannerGeomRef}
                subPosPctRef={subPosPctRef}
                motionRef={motionViewRef}
                layout={layout}
                chromaKey={source.chroma_key}
                backdrop={backdrop}
                format={aspect}
                suspended={watchOpen}
              />
            </div>
            <div className="editor-stage-meta mono">
              {isCanvas
                ? `${canvasLabel(backdrop)} · ${aspect} · ${Math.round(clipDuration)} с`
                : `${formatDuration(source.duration_sec)} · ${source.width}×${source.height} → ${aspect}${
                    sourceStrategy(aspect, source.width, source.height, source.content_crop) === "fit" ? " (целиком на подложке)" : ""
                  }`}
            </div>
          </div>
        </div>
        {libTab === "ai" && activePlan && moduleOn("editor_agent") ? (
          <AgentPanel
            clipPlanId={activePlan.id}
            onRunningChange={(on) => {
              agentRunningRef.current = on;
              if (on) void flushSettings();
            }}
            onChanged={async () => {
              await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
              setSettingsNonce((n) => n + 1);
            }}
          />
        ) : null}
        {libTab === "templates" ? <TemplatesPanel onAdd={addAtCursor} format={layers.length || camera.length ? layersFmt : aspect} /> : null}
        {libTab === "text" ? (
          <TextPanel
            onAdd={addAtCursor}
            onCaptions={(style) => {
              // Один слой субтитров на клип: повторный выбор меняет стиль.
              const existing = layers.find((l) => l.type === "captions");
              if (existing) {
                setLayers((prev) =>
                  prev.map((l) => (l.id === existing.id ? ({ ...l, hidden: false, props: { ...l.props, style } } as Layer) : l)),
                );
                setTlSelected({ kind: "layer", id: existing.id });
              } else {
                addLayers(
                  [{ id: "new", type: "captions", start: 0, end: clipDuration, x: 0.5, y: 0.68, props: { style } } as Layer],
                  0,
                );
              }
              if (!planSubs.data?.words?.length) toast.push("Нужны субтитры клипа: «Распознать речь» в группе «Субтитры»", "info");
            }}
          />
        ) : null}
        {libTab === "stickers" ? <StickersPanel onAdd={addAtCursor} /> : null}
        {libTab === "sounds" ? <SoundsPanel onAdd={addAtCursor} clipDuration={clipDuration} /> : null}
        {libTab === "memes" ? <MemesPanel onAdd={addAtCursor} assets={pickableAssets} clipId={activePlanId} /> : null}
        {libTab === "generate" && libTabs.some((t) => t.key === "generate") ? <GeneratePanel onAdd={addAtCursor} /> : null}
        {libTab === "media" ? (
          <div className="ed-lib-media">
            <div className="ed-shelf-head">
            <span className="ed-shelf-title">Полка вставок</span>
            <span className="muted ed-shelf-hint">перетащи файл на дорожку «Вставки» или кликни дважды — встанет под курсором</span>
            {moduleOn("ai_montage") ? (
              <button
                className="btn sm"
                disabled={!activePlan}
                onClick={async () => {
                  await flushSettings();
                  setAiGoal("Клип не перекраивай: оставь куски как есть, только расставь мемы из библиотеки по лучшим моментам.");
                  setAiMontageOpen(true);
                }}
                title="ИИ разложит мемы по клипу, не трогая куски"
              >
                ✨ ИИ подберёт мемы
              </button>
            ) : null}
          </div>
          <AssetDrawer
            clipId={activePlanId}
            onAdd={(assetId) => {
              const asset = assetsQuery.data?.find((a) => a.id === assetId);
              setInserts((prev) => [
                ...prev,
                {
                  asset_id: assetId,
                  at: clipTimeNow(),
                  duration: Math.min(4, Math.max(1, asset?.duration_sec || 1.5)),
                  mode: asset?.kind === "audio" ? "sound" : "full",
                  volume: 1,
                  duck: asset?.kind !== "audio",
                },
              ]);
            }}
          />
          </div>
        ) : null}
      </aside>

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

      <aside className="ed-inspector">
        <InspectorTabs tabs={inspTabs} active={inspActive} onPick={pickInspTab} />
        <div className="ed-insp-scroll" ref={inspScrollRef}>
          <div className="insp-pane" hidden={inspActive !== "clip"} role="tabpanel" aria-label="Клип">
            <Group title="Формат и концовка" badge={aspect} defaultOpen>
              <div className="sub-options">
              <label
                className="field"
                title="Формат готового ролика: видео кадрируется окном нужной пропорции, слои сами переносятся в кадр одним масштабом, фон — на весь кадр. Превью сразу показывает кадр этого формата"
              >
                <span>Формат</span>
                <select
                  className="input"
                  value={aspect}
                  onChange={(e) => {
                    const next = e.target.value as Aspect;
                    setAspect(next);
                    // Слоёв ещё нет — новые сразу размечаются в кадре нового формата.
                    if (!layers.length && !camera.length) setLayersFmt(next);
                  }}
                >
                  <option value="9:16">9:16 — вертикаль</option>
                  <option value="1:1">1:1 — квадрат</option>
                  <option value="4:5">4:5 — лента</option>
                  <option value="16:9">16:9 — горизонталь</option>
                </select>
              </label>
              <label className="field" title="Последний кадр стоит, пока затухает звук (музыка доигрывает на нём). Мему — «без стоп-кадра»: ролик обрывается на последнем кадре">
                <span>Стоп-кадр в конце</span>
                <select
                  className="input"
                  value={endHoldSec == null ? "" : String(endHoldSec)}
                  onChange={(e) => setEndHoldSec(e.target.value === "" ? null : Number(e.target.value))}
                >
                  <option value="">как у сервиса</option>
                  <option value="0">без стоп-кадра</option>
                  <option value="0.25">0.25 с</option>
                  <option value="0.5">0.5 с</option>
                  <option value="1">1 с</option>
                  <option value="1.5">1.5 с</option>
                </select>
              </label>
              <label
                className="field"
                title="Motion blur: быстрые слои, наезды и whip камеры смазываются по пути движения (затвор — доля кадра; 50% — киношные 180°). В покое кадр не меняется. Свой у слоя — в его инспекторе, у ключа камеры — в панели камеры"
              >
                <span>Motion blur · {motionBlur ? `${Math.round(motionBlur * 100)}%` : "выкл"}</span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={Math.round((motionBlur ?? 0) * 100)}
                  onChange={(e) => setMotionBlur(Number(e.target.value) ? Number(e.target.value) / 100 : null)}
                />
              </label>
              </div>
            </Group>
            {/* Быстрые действия: «🤖 ИИ-монтаж» — в шапке редактора, автофокус клипа — «Авто»
                под превью (и в «Ещё… → Умный кадр»): здесь их больше не дублируем. */}
            <div className="ed-quick">
              <span className="ed-quick-title">Быстрые действия</span>
              <div className="ed-quick-row">
                <button
                  className="btn sm"
                  disabled={!activePlan || autoEdit.isPending}
                  onClick={() => activePlan && autoEdit.mutate(activePlan.id)}
                  title="Без ИИ, за секунду: хук-титр, анимированные субтитры по словам (ключевые цветом, другие голоса отдельно), наезды в героя и звуки на ударные слова. Ctrl+Z откатит."
                >
                  {autoEdit.isPending ? "Собираю…" : "Автомонтаж"}
                </button>
                <a
                  className="btn sm"
                  href="/files"
                  target="_blank"
                  rel="noreferrer"
                  title="Библиотека мемов (откроется в новой вкладке): ИИ ищет картинки в интернете и описывает файлы"
                >
                  Найти мемы ↗
                </a>
              </div>
              {brands.data?.length ? (
                <div className="ed-quick-row">
                  <select
                    className="input"
                    style={{ flex: "1 1 120px", minWidth: 0 }}
                    value={brandPick || brands.data[0].id}
                    onChange={(e) => setBrandPick(Number(e.target.value))}
                    title="Бренд заказчика (Настройки → Бренды)"
                  >
                    {brands.data.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                  <button
                    className="btn sm"
                    disabled={!activePlan || applyBrand.isPending}
                    onClick={() =>
                      activePlan &&
                      applyBrand.mutate({ clipPlanId: activePlan.id, brandId: brandPick || brands.data![0].id })
                    }
                    title="Субтитры и хук в цветах бренда, логотип в углу, призыв на последние секунды, музыка и фон бренда. Делай после монтажа; повтор не дублирует. Ctrl+Z откатит."
                  >
                    {applyBrand.isPending ? "Применяю…" : "Применить бренд"}
                  </button>
                </div>
              ) : null}
            </div>
            <Group title="Картинка (лук)" badge={selectedPreset?.label ?? "по умолчанию"} defaultOpen>
              <label className="field">
                <span>Пресет</span>
                <select className="input" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))}>
                  <option value={0}>По умолчанию</option>
                  {uniquePresets.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
              </label>
              {/* Кадры основы холста — зелёные: смотреть лук не на чем. */}
              <LookPicker frames={isCanvas ? [] : lookFrames} presets={uniquePresets} value={presetId} onPick={setPresetId} />
              <label className="switch" title="Отразить видео по горизонтали (поменять лево и право) — например чтобы репост отличался от оригинала">
                <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
                <span className="switch-track" />
                <span>Зеркало (лево↔право)</span>
              </label>
            </Group>
            <Group
              title={isCanvas ? "Фон холста" : "Раскладка кадра"}
              badge={
                isCanvas
                  ? canvasLabel(backdrop).replace("Холст · ", "")
                  : { single: "обычная", split: "вебка + игра", pip_cam: "вебка в углу", meme: "мем: видео сверху", avatar_full: "только аватар" }[layout.mode] ?? "обычная"
              }
              defaultOpen={isCanvas}
            >
              <LayoutPanel
                noSourceFile={sourceMissing}
                sourceId={source.id}
                layout={layout}
                onChange={setLayout}
                frameAt={videoRef.current?.currentTime ?? 0}
                videoRef={videoRef}
                segments={activePlan?.segments ?? []}
                onSeekSource={(src) => {
                  const v = videoRef.current;
                  if (!v) return;
                  v.pause();
                  playbackRef.current = null;
                  v.currentTime = src;
                }}
                chromaKey={source.chroma_key}
                backdrop={backdrop}
                onBackdrop={setBackdrop}
                accent={clipAccent(layers)}
                canvas={isCanvas}
              />
            </Group>
            <Group
              title="Переходы"
              badge={transitionOpts.data?.types.find((t) => t.key === transition.type)?.label ?? "встык"}
            >
              {(activePlan?.segments.length ?? 0) < 2 ? (
                <p className="muted field-hint">
                  В этом клипе один кусок — склеивать нечего. Переход сработает, когда кусков станет больше.
                </p>
              ) : null}
              <label className="field">
                <span>Склейка кусков</span>
                <select
                  className="input"
                  value={transition.type}
                  onChange={(e) => patchTransition({ type: e.target.value as TransitionSettings["type"] })}
                >
                  {(transitionOpts.data?.types ?? [{ key: "cut", label: "Встык" }]).map((t) => (
                    <option key={t.key} value={t.key}>{t.label}</option>
                  ))}
                </select>
              </label>
              {transition.type !== "cut" ? (
                <label className="switch" title="Внутри одной сцены (вырезанная пауза) склейка остаётся встык — эффект тратится только на смену сцены.">
                  <input
                    type="checkbox"
                    checked={transition.smart !== false}
                    onChange={(e) => patchTransition({ smart: e.target.checked })}
                  />
                  <span className="switch-track" />
                  <span>умные склейки: внутри сцены — встык</span>
                </label>
              ) : null}
              {transition.type !== "cut" && !(transitionOpts.data?.snap ?? {})[transition.type] ? (
                <label className="field">
                  <span>Длительность — {transition.duration.toFixed(2)} с</span>
                  <input
                    type="range"
                    min={0.1}
                    max={1.2}
                    step={0.05}
                    value={transition.duration}
                    onChange={(e) => patchTransition({ duration: Number(e.target.value) })}
                  />
                </label>
              ) : null}
              <label className="field">
                <span>Звук на стыке</span>
                <select
                  className="input"
                  value={transition.audio}
                  onChange={(e) => patchTransition({ audio: e.target.value as TransitionSettings["audio"] })}
                  title="Плавно — звук перетекает (на резкой склейке — без щелчка). Резко — обрывается точно на стыке."
                >
                  {(transitionOpts.data?.audio ?? []).map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Звук-эффект на каждом стыке</span>
                <select
                  className="input"
                  value={transition.sfx}
                  onChange={(e) => patchTransition({ sfx: e.target.value as TransitionSettings["sfx"] })}
                >
                  {(transitionOpts.data?.sfx ?? []).map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              {transition.sfx !== "none" ? (
                <label className="field">
                  <span>Громкость эффекта — {Math.round(transition.sfx_volume * 100)}%</span>
                  <input
                    type="range"
                    min={0.05}
                    max={1}
                    step={0.05}
                    value={transition.sfx_volume}
                    onChange={(e) => patchTransition({ sfx_volume: Number(e.target.value) })}
                  />
                </label>
              ) : null}
            </Group>
            <Group
              title="Обложка"
              badge={cover.mode === "none" ? "нет" : cover.mode === "frame" ? "кадр" : "картинка"}
            >
              <div className="cover-compose">
                <span className="li-cap">Обложка с заголовком — кадр под курсором + крупный текст</span>
                <div className="li-row">
                  <input
                    className="input li-grow"
                    placeholder={activePlan?.title ? activePlan.title.toUpperCase() : "ЗАГОЛОВОК"}
                    value={coverTitle}
                    onChange={(e) => setCoverTitle(e.target.value)}
                    maxLength={80}
                  />
                  <select className="input" value={coverStyle} onChange={(e) => setCoverStyle(e.target.value)}>
                    <option value="yellow">Жёлтый</option>
                    <option value="fire">Огонь</option>
                    <option value="white">Белый</option>
                    <option value="plate">Плашка</option>
                  </select>
                </div>
                {moduleOn("covers") ? (
                  <button
                    type="button"
                    className="btn sm"
                    disabled={!activePlan || composeCover.isPending}
                    onClick={() => composeCover.mutate()}
                  >
                    {composeCover.isPending ? "Рисую…" : "Сделать обложку"}
                  </button>
                ) : null}
              </div>
              <div className="seg-toggle" role="radiogroup" aria-label="Источник обложки">
                {([
                  ["none", "Нет"],
                  ["frame", "Кадр из клипа"],
                  ["image", "Своя картинка"],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={cover.mode === key}
                    className={cover.mode === key ? "active" : ""}
                    onClick={() => patchCover({ mode: key })}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {cover.mode === "frame" && activePlan ? (() => {
                const pieces = activePlan.segments;
                const idx = Math.min(cover.piece, Math.max(0, pieces.length - 1));
                const seg = pieces[idx];
                const len = seg ? seg.end_sec - seg.start_sec : 0;
                const off = Math.min(cover.offset, Math.max(0, len - 0.05));
                const t = seg ? seg.start_sec + off : 0;
                return (
                  <>
                    {pieces.length > 1 ? (
                      <label className="field">
                        <span>Кусок</span>
                        <select
                          className="input"
                          value={idx}
                          onChange={(e) => patchCover({ piece: Number(e.target.value), offset: 0 })}
                        >
                          {pieces.map((p, i) => (
                            <option key={p.id} value={i}>
                              {i + 1}. {p.title}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    <label className="field">
                      <span>Момент — {off.toFixed(1)} с от начала куска</span>
                      <input
                        type="range"
                        min={0}
                        max={Math.max(0.1, len - 0.05)}
                        step={0.1}
                        value={off}
                        onChange={(e) => patchCover({ offset: Number(e.target.value) })}
                      />
                    </label>
                    {sourceMissing ? (
                      <p className="muted field-hint">Кадр обложки берётся из записи стрима: её нет.</p>
                    ) : seg ? (
                      aspect === "9:16" ? (
                        <img
                          className="cover-preview"
                          alt="Кадр для обложки"
                          src={`/api/sources/${source.id}/frame?t=${t.toFixed(2)}&x=${focusXAt(seg, off).toFixed(3)}`}
                        />
                      ) : (
                        // Другой формат: полный кадр, обрезанный окном формата у той же точки фокуса.
                        <img
                          className="cover-preview"
                          alt="Кадр для обложки"
                          src={`/api/sources/${source.id}/frame?t=${t.toFixed(2)}&w=640`}
                          style={{
                            aspectRatio: `${frameSize(aspect).w} / ${frameSize(aspect).h}`,
                            maxWidth: frameSize(aspect).w > frameSize(aspect).h ? 320 : undefined,
                            objectPosition: `${(focusWindowPos(focusXAt(seg, off), source.width, source.height, aspect) * 100).toFixed(1)}% 50%`,
                          }}
                        />
                      )
                    ) : null}
                    <p className="muted field-hint">В рендере кадр будет уже с цветом, субтитрами и баннером.</p>
                  </>
                );
              })() : null}

              {cover.mode === "image" ? (
                <>
                  <label className="btn cover-upload">
                    {coverUploading ? "Загружаю…" : cover.image ? "Заменить картинку…" : "Выбрать картинку…"}
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      disabled={coverUploading}
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (!f) return;
                        setCoverUploading(true);
                        try {
                          const res = await clipPlansApi.uploadCover(f);
                          patchCover({ image: res.image });
                        } catch (err) {
                          toast.error(err instanceof ApiError ? err.message : "Не удалось загрузить картинку");
                        } finally {
                          setCoverUploading(false);
                        }
                      }}
                    />
                  </label>
                  {cover.image ? (
                    <img
                      className="cover-preview"
                      alt="Обложка"
                      src={`/media/covers/${cover.image}`}
                      style={aspect === "9:16" ? undefined : { aspectRatio: `${frameSize(aspect).w} / ${frameSize(aspect).h}` }}
                    />
                  ) : (
                    <p className="muted field-hint">Картинка подгонится под 1080×1920: заполнит кадр, лишнее обрежется.</p>
                  )}
                </>
              ) : null}

              {cover.mode !== "none" ? (
                <>
                  <label
                    className="switch"
                    title="Площадки, которые берут превью с первого кадра (Reels, лента TikTok), покажут обложку"
                  >
                    <input type="checkbox" checked={cover.burn} onChange={(e) => patchCover({ burn: e.target.checked })} />
                    <span className="switch-track" />
                    <span>Вшить первым кадром</span>
                  </label>
                  {cover.burn ? (
                    <label className="field">
                      <span>Сколько держать — {cover.burn_sec.toFixed(2)} с</span>
                      <input
                        type="range"
                        min={0.04}
                        max={0.5}
                        step={0.02}
                        value={cover.burn_sec}
                        onChange={(e) => patchCover({ burn_sec: Number(e.target.value) })}
                      />
                    </label>
                  ) : null}
                </>
              ) : null}
            </Group>
            <Group title="Баннер" badge={bannerOn ? "вкл" : "выкл"}>
              <label className="switch">
                <input type="checkbox" checked={bannerOn} onChange={(e) => setBannerOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Накладывать баннер</span>
              </label>
              {bannerOn ? (
                <>
                  <label className="field">
                    <span>Картинка</span>
                    <select className="input" value={bannerId} onChange={(e) => setBannerId(Number(e.target.value))}>
                      <option value={0}>по умолчанию</option>
                      {banners.data?.map((b) => (
                        <option key={b.id} value={b.id}>{b.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field range">
                    <span>Высота · {bannerHeightPct}%</span>
                    <input
                      type="range" min={6} max={30} value={bannerHeightPct}
                      onChange={(e) => setBannerHeightPct(Number(e.target.value))}
                    />
                  </label>
                  <label className="field range">
                    <span>Положение · {bannerPosPct}% сверху</span>
                    <input
                      type="range" min={0} max={80} value={bannerPosPct}
                      onChange={(e) => setBannerPosPct(Number(e.target.value))}
                    />
                  </label>
                </>
              ) : null}
            </Group>
            <Group
              title="Паузы и слова-паразиты"
              badge={cuts.length ? `−${cutsTotal(cuts).toFixed(1)} с` : "нет"}
            >
              {activePlan && !moduleOn("jump_cuts") ? (
                <div className="muted li-note">Модуль «Джамп-каты» выключен (Настройки → Модули).</div>
              ) : activePlan ? (
                <CutsPanel
                  clipPlanId={activePlan.id}
                  cuts={cuts}
                  onChange={(next) => {
                    // Панель уже сохранила вырезы; автосохранение повторит то же самое — не страшно.
                    setCuts(next);
                    void qc.invalidateQueries({ queryKey: qk.source(sourceId) });
                  }}
                  onSeek={(t) => seekClipTime(t, false)}
                />
              ) : null}
            </Group>
            <MoreSection id="clip" label="Ещё" hint="умный кадр, границы клипов, экспорт в монтажку">
              {/* Холст: умный кадр не нужен — кадр 9:16 целиком. */}
              {isCanvas ? null : (
              <Group
                title="Умный кадр"
                badge={focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.label ?? ""}
              >
                <label className="field">
                  <span>Стратегия — как ведёт себя кадр</span>
                  <select
                    className="input"
                    value={source.focus_strategy || "shot"}
                    onChange={(e) => setFocus.mutate({ focus_strategy: e.target.value })}
                    disabled={setFocus.isPending}
                  >
                    {focusOptions.data?.strategies.map((s) => (
                      <option key={s.key} value={s.key}>{s.label}</option>
                    ))}
                  </select>
                </label>
                <span className="muted" style={{ fontSize: 11 }}>
                  {focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.hint ?? ""}
                </span>
                <label className="field">
                  <span>Пресет детекции — под тип видео</span>
                  <select
                    className="input"
                    value={source.focus_preset || "balanced"}
                    onChange={(e) => setFocus.mutate({ focus_preset: e.target.value })}
                    disabled={setFocus.isPending}
                  >
                    {focusOptions.data?.presets.map((p) => (
                      <option key={p.key} value={p.key}>{p.label}</option>
                    ))}
                  </select>
                </label>
                <label
                  className="check"
                  title="Детектор находит границы планов, а Gemini по одному кадру каждого плана решает, где главный объект. Точнее на сложном контенте, но тратит токены."
                >
                  <input type="checkbox" checked={useVlmFocus} onChange={(e) => setUseVlmFocus(e.target.checked)} />
                  <span>🤖 Уточнять кадр через Gemini (1 кадр на план)</span>
                </label>
                {moduleOn("autofocus") ? <div className="ed-btn-row">
                  <button
                    className="btn primary sm"
                    disabled={autofocus.isPending || !plans.length}
                    title="Детектор фокуса для всех черновиков этого видео (займёт время)"
                    onClick={() => autofocus.mutate([])}
                  >
                    {autofocus.isPending ? "Анализ кадров…" : "Автофокус: все клипы"}
                  </button>
                  <button
                    className="btn sm"
                    disabled={autofocus.isPending || !activePlan?.segments.length}
                    title="Только сегменты этого клипа"
                    onClick={() => activePlan && autofocus.mutate(activePlan.segments.map((s) => s.id))}
                  >
                    Только этот клип
                  </button>
                </div> : <div className="muted li-note">Модуль «Автофокус» выключен (Настройки → Модули).</div>}
              </Group>
              )}
              {/* Холст: границ клипов по речи нет — основа немая. */}
              {isCanvas ? null : (
              <Group title="Границы клипов" badge={cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.label ?? ""}>
                <select
                  className="input"
                  value={source.cut_strategy || "phrase"}
                  onChange={(e) => refineCuts.mutate(e.target.value)}
                  disabled={refineCuts.isPending}
                >
                  {cutStrategies.data?.map((s) => (
                    <option key={s.key} value={s.key}>{s.label}</option>
                  ))}
                </select>
                <span className="muted" style={{ fontSize: 11.5 }}>
                  {cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.hint ??
                    "Подгоняет начало/конец клипов к фразам речи. Нужен транскрипт."}
                </span>
                <button
                  className="btn sm"
                  disabled={refineCuts.isPending}
                  onClick={() => refineCuts.mutate(source.cut_strategy || "phrase")}
                >
                  {refineCuts.isPending ? "Считаю…" : "Пересчитать"}
                </button>
              </Group>
              )}
              <Group title="Отдать в монтажку" badge="OTIO · пакет">
                <span className="muted" style={{ fontSize: 11.5 }}>
                  Пакет для Premiere и DaVinci Resolve — одной кнопкой и без рендера: монтаж с дорожками, субтитры,
                  картинки, звуки и музыка отдельными файлами, README на русском — что доехало, что нет и как открыть.
                </span>
                <div className="ed-foot-tools">
                  <a
                    className="btn sm primary"
                    href={activePlan ? playbackApi.packageUrl(activePlan.id, packagePieces) : undefined}
                    download
                    onClick={() => void flushSettings()}
                    title="Zip: FCP7 .xml + .otio, .srt, файлы мемов и звуков. Запись стрима в архив не кладём"
                  >
                    ⬇ Пакет для монтажки (.zip)
                  </a>
                  <label className="switch" title="Положить в архив короткие куски записи 720p, чтобы монтаж открылся сразу. Без галочки — только ссылка на запись и инструкция перелинковки">
                    <input type="checkbox" checked={packagePieces} onChange={(e) => setPackagePieces(e.target.checked)} />
                    <span className="switch-track" />
                    <span>с кусками видео</span>
                  </label>
                </div>
                <span className="muted" style={{ fontSize: 11.5 }}>
                  Только куски и таймкоды, без файлов (субтитры, мемы и кадрирование туда не переносятся):
                </span>
                <div className="ed-foot-tools">
                  <a
                    className="btn sm"
                    href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "otio") : undefined}
                    download
                    title="OpenTimelineIO — Kdenlive 25.04+ открывает нативно"
                  >
                    ⬇ .otio
                  </a>
                  <a
                    className="btn sm"
                    href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "edl") : undefined}
                    download
                    title="CMX 3600 EDL — понимают почти все монтажки"
                  >
                    ⬇ .edl
                  </a>
                  <a
                    className="btn sm"
                    href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "fcpxml") : undefined}
                    download
                    title="FCP7 XML — DaVinci Resolve, Premiere"
                  >
                    ⬇ .xml
                  </a>
                </div>
              </Group>
            </MoreSection>
          </div>
          <div className="insp-pane" hidden={inspActive !== "layers"} role="tabpanel" aria-label="Слои">
            {selectedLayerId && layers.find((l) => l.id === selectedLayerId) ? (
              <LayerInspector
                key={selectedLayerId}
                layer={layers.find((l) => l.id === selectedLayerId)!}
                words={captionWords}
                fonts={motionFonts.data ?? []}
                now={clipNow}
                clipDuration={clipDuration}
                refWidth={formatRefSize(layersFmt).w}
                onChange={(next) => setLayers((prev) => prev.map((l) => (l.id === next.id ? next : l)))}
                onDelete={() => {
                  setLayers((prev) => prev.filter((l) => l.id !== selectedLayerId));
                  setTlSelected(null);
                }}
                onDuplicate={() => {
                  const r = duplicateLayer(layers, selectedLayerId, clipDuration);
                  setLayers(r.layers);
                  if (r.id) setTlSelected({ kind: "layer", id: r.id });
                }}
                onSeek={(t) => seekClipTime(t, false)}
                keyed={Boolean(source.chroma_key)}
                hasCamera={camera.length > 0}
                planId={activePlan?.id}
                onPickPoint={(cb) => {
                  pickPointRef.current = cb;
                  setPickingPoint(Boolean(cb));
                }}
              />
            ) : null}
            <Group title="Слои клипа" badge={layers.length ? String(layers.length) : "нет"} defaultOpen={layers.length > 0}>
              {layers.length ? (
                <div className="ed-layer-list">
                  {[...layers]
                    .sort((a, b) => a.start - b.start)
                    .map((l) => (
                      <div
                        key={l.id}
                        className={`ed-layer-row${selectedLayerId === l.id ? " on" : ""}${l.hidden ? " off" : ""}`}
                        onClick={() => {
                          setTlSelected({ kind: "layer", id: l.id });
                          const now = clipTimeExact();
                          if (now < l.start || now > l.end) seekClipTime(l.start + Math.min(0.6, (l.end - l.start) / 2), false);
                        }}
                      >
                        <span className="ed-layer-ico">{LAYER_ICON[l.type]}</span>
                        <span className="ed-layer-name">{layerTitle(l)}</span>
                        <span className="mono muted ed-layer-time">{formatDuration(l.start)}</span>
                        <button
                          type="button"
                          className="btn ghost sm"
                          title={l.hidden ? "Показать" : "Скрыть"}
                          onClick={(e) => {
                            e.stopPropagation();
                            setLayers((prev) => prev.map((x) => (x.id === l.id ? ({ ...x, hidden: !x.hidden } as Layer) : x)));
                          }}
                        >
                          {l.hidden ? "🙈" : "👁"}
                        </button>
                      </div>
                    ))}
                </div>
              ) : (
                <div className="muted li-note">Слоёв пока нет — возьмите шаблон, текст или стикер в библиотеке слева, или попросите ИИ.</div>
              )}
            </Group>
            <Group title="Вставки (мемы)" badge={inserts.length ? String(inserts.length) : "нет"} defaultOpen>
              {!pickableAssets.length ? (
                <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                  Библиотека пуста — закиньте мемы и звуки в{" "}
                  <a href="/files" onClick={(e) => { e.preventDefault(); navigate("/files"); }}>
                    «Файлы для монтажа»
                  </a>
                  . 🤖 ИИ-монтаж вставит подходящие сам.
                </p>
              ) : null}
              {inserts.map((ins, i) => {
                const asset = assetsQuery.data?.find((a) => a.id === ins.asset_id);
                const upd = (patchIns: Partial<MontageInsert>) =>
                  setInserts((prev) => prev.map((x, j) => (j === i ? { ...x, ...patchIns } : x)));
                return (
                  <div key={i} className="ins-row">
                    <select
                      className="input"
                      value={ins.asset_id}
                      onChange={(e) => upd({ asset_id: Number(e.target.value) })}
                    >
                      {!asset ? <option value={ins.asset_id}>файл удалён</option> : null}
                      {assetsQuery.data?.filter((a) => !a.in_clip_only || a.id === ins.asset_id).map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label} · {a.kind}
                        </option>
                      ))}
                    </select>
                    <div className="ins-grid">
                      <label className="field">
                        <span>с, от начала клипа</span>
                        <input
                          className="input"
                          type="number"
                          min={0}
                          step={0.1}
                          value={ins.at}
                          onChange={(e) => upd({ at: Number(e.target.value) })}
                        />
                      </label>
                      <label className="field">
                        <span>длит., с</span>
                        <input
                          className="input"
                          type="number"
                          min={0.3}
                          max={8}
                          step={0.1}
                          value={ins.duration}
                          onChange={(e) => upd({ duration: Number(e.target.value) })}
                        />
                      </label>
                      <label className="field">
                        <span>как</span>
                        <select
                          className="input"
                          value={asset?.kind === "audio" ? "sound" : ins.mode}
                          disabled={asset?.kind === "audio"}
                          onChange={(e) => upd({ mode: e.target.value as MontageInsert["mode"], duck: e.target.value === "full" })}
                        >
                          <option value="full">на весь кадр</option>
                          <option value="pip">окном</option>
                          <option value="sound">только звук</option>
                        </select>
                      </label>
                    </div>
                    <div className="ins-foot">
                      <button type="button" className="btn sm" title="Поставить на текущий момент плеера" onClick={() => upd({ at: clipTimeNow() })}>
                        ⏱ сейчас
                      </button>
                      <button
                        type="button"
                        className="btn sm"
                        title="Проиграть клип с этого места"
                        onClick={() => seekClipTime(ins.at)}
                      >
                        ▶ к месту
                      </button>
                      <button type="button" className="btn ghost sm" title="Убрать вставку" onClick={() => setInserts((prev) => prev.filter((_, j) => j !== i))}>
                        ✕
                      </button>
                    </div>
                    {ins.reason ? <span className="ins-why">🤖 {ins.reason}</span> : null}
                  </div>
                );
              })}
              {pickableAssets.length ? (
                <button
                  type="button"
                  className="btn sm"
                  disabled={inserts.length >= 6}
                  onClick={() =>
                    setInserts((prev) => [
                      ...prev,
                      {
                        asset_id: pickableAssets[0].id,
                        at: clipTimeNow(),
                        duration: 1.5,
                        mode: pickableAssets[0].kind === "audio" ? "sound" : "full",
                        volume: 1,
                        duck: pickableAssets[0].kind !== "audio",
                      },
                    ])
                  }
                >
                  + Вставка на текущий момент
                </button>
              ) : null}
            </Group>
            <Group title="Камера (наезды)" badge={camera.length ? `${camera.length} ключ.` : "нет"} forceOpen={tlSelected?.kind === "cam"}>
              <CameraPanel
                words={(planSubs.data?.words ?? []) as SubtitleWord[]}
                peaks={planWave.data?.peaks ?? []}
                camera={camera}
                selectedIndex={tlSelected?.kind === "cam" ? tlSelected.index : null}
                now={clipNow}
                clipDuration={clipDuration}
                onChange={setCamera}
                onSelect={(i) => setTlSelected(i == null ? null : { kind: "cam", index: i })}
              />
            </Group>
          </div>
          {/* Холст: кусок один и без исходника — скорость, цвет и фокус не нужны. */}
          {isCanvas ? null : (
          <div className="insp-pane" hidden={inspActive !== "piece"} role="tabpanel" aria-label="Кусок">
            {/* Какой кусок правим: номера кусков — быстрый выбор без таймлайна. */}
            <div className="insp-piece-head">
              <span className="insp-piece-title">
                {selectedSegment && activePlan
                  ? `Кусок ${selectedPieceIndex + 1} из ${activePlan.segments.length}`
                  : "Кусок не выбран"}
              </span>
              {activePlan && activePlan.segments.length > 1 ? (
                <div className="seg insp-piece-pick" role="radiogroup" aria-label="Кусок клипа">
                  {activePlan.segments.map((s, i) => (
                    <button
                      key={s.id}
                      type="button"
                      role="radio"
                      aria-checked={selectedSegment?.id === s.id}
                      className={`seg-item${selectedSegment?.id === s.id ? " active" : ""}`}
                      title={s.title || `Кусок ${i + 1}`}
                      onClick={() => {
                        setSelectedSeg(s.id);
                        setTlSelected({ kind: "piece", index: i });
                      }}
                    >
                      {i + 1}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            {selectedSegment && activePlan ? (
              <>
                <Group title="Скорость куска" badge={speedBadge(selectedSegment)}>
                  <SegmentSpeedControl
                    segment={selectedSegment}
                    index={selectedPieceIndex}
                    busy={segmentSpeed.isPending}
                    onChange={(body) => segmentSpeed.mutate({ planId: activePlan.id, ...body })}
                  />
                </Group>
                <Group title="Цвет куска" badge={colorBadge(selectedSegment)}>
                  <PieceColorControl
                    segment={selectedSegment}
                    index={selectedPieceIndex}
                    pieces={activePlan.segments.length}
                    busy={pieceColor.isPending || matchColors.isPending}
                    onChange={(body) => pieceColor.mutate({ planId: activePlan.id, ...body })}
                    onMatch={(reference) => matchColors.mutate({ planId: activePlan.id, reference })}
                  />
                </Group>
                <Group title="Фокус кадра" badge={`${selectedSegment.focus?.length ?? 0} точ.`} defaultOpen>
                  <FocusEditor segment={selectedSegment} sourceId={sourceId} videoRef={videoRef} />
                </Group>
              </>
            ) : (
              <div className="insp-empty">
                <p>Выберите кусок на таймлайне (дорожка «Клипы»): скорость и реверс, цвет, точки фокуса кадра.</p>
                {activePlan?.segments.length ? (
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => {
                      setSelectedSeg(activePlan.segments[0].id);
                      setTlSelected({ kind: "piece", index: 0 });
                    }}
                  >
                    Выбрать первый кусок
                  </button>
                ) : null}
              </div>
            )}
          </div>
          )}
          <div className="insp-pane" hidden={inspActive !== "audio"} role="tabpanel" aria-label="Звук">
            <Group title="Музыка" badge={musicOn ? "вкл" : "выкл"}>
              <label className="switch">
                <input type="checkbox" checked={musicOn} onChange={(e) => setMusicOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Фоновый трек</span>
              </label>
              {musicOn ? (
                <>
                  <div className="lay-row">
                    <label className="field lay-grow">
                      <span>Трек</span>
                      <select
                        className="input"
                        value={trackId}
                        onChange={(e) => {
                          setTrackId(Number(e.target.value));
                          setMusicStartSec(0);
                        }}
                      >
                        <option value={0}>по умолчанию</option>
                        {tracks.data?.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.label}
                            {t.duration_sec ? ` · ${formatDuration(t.duration_sec)}` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="btn ghost sm music-refresh"
                      title="Обновить список треков"
                      disabled={tracks.isFetching}
                      onClick={() => void tracks.refetch()}
                    >
                      {tracks.isFetching ? "…" : "↻"}
                    </button>
                  </div>
                  {trackId ? (
                    <>
                      <audio
                        ref={musicPreviewRef}
                        className="music-preview"
                        src={`/media/audio-tracks/${trackId}`}
                        controls
                        preload="none"
                        // Прослушка с выбранного старта: так и зазвучит на 0 с клипа.
                        onPlay={(e) => {
                          const a = e.currentTarget;
                          if (musicStartSec > 0 && a.currentTime < musicStartSec - 0.25) a.currentTime = musicStartSec;
                        }}
                      />
                      <label className="field range">
                        <span title="Музыка на 0 с клипа играет с этой секунды трека — пропустить тихое вступление и начать сразу с барабанов">
                          Старт трека · {musicStartSec.toFixed(1)} с
                        </span>
                        <input
                          type="range"
                          min={0}
                          max={Math.max(1, Math.floor(tracks.data?.find((t) => t.id === trackId)?.duration_sec || 300))}
                          step={0.1}
                          value={musicStartSec}
                          onChange={(e) => setMusicStartSec(Number(e.target.value))}
                        />
                      </label>
                      <div className="lay-row">
                        <button
                          type="button"
                          className="btn ghost sm"
                          title="Нашли на слух, где вступают барабаны, — поставьте плеер на паузу там и нажмите"
                          onClick={() => setMusicStartSec(Math.round((musicPreviewRef.current?.currentTime ?? 0) * 10) / 10)}
                        >
                          ⏱ Старт отсюда (из плеера)
                        </button>
                        {musicStartSec > 0 ? (
                          <button type="button" className="btn ghost sm" onClick={() => setMusicStartSec(0)}>
                            С начала
                          </button>
                        ) : null}
                      </div>
                    </>
                  ) : null}
                  <div className="lay-row">
                    <input
                      className="input lay-grow"
                      type="url"
                      placeholder="Ссылка на трек: YouTube, SoundCloud…"
                      value={musicUrl}
                      disabled={musicFromUrl.isPending}
                      onChange={(e) => {
                        setMusicUrl(e.target.value);
                        musicFromUrl.reset();
                      }}
                      onKeyDown={(e) => e.key === "Enter" && musicUrl.trim() && musicFromUrl.mutate()}
                    />
                    <button
                      type="button"
                      className="btn sm"
                      disabled={musicFromUrl.isPending || !/^https?:\/\//i.test(musicUrl.trim())}
                      onClick={() => musicFromUrl.mutate()}
                      title="Сервер скачает только звук (до 15 минут) и добавит трек в список"
                    >
                      {musicFromUrl.isPending ? "Скачиваю…" : "Добавить по ссылке"}
                    </button>
                  </div>
                  {musicFromUrl.isPending ? (
                    <div className="lay-note ok">Скачиваю звук — это может занять минуту-другую, окно можно не трогать.</div>
                  ) : musicFromUrl.isError ? (
                    <div className="lay-note warn">
                      {musicFromUrl.error instanceof ApiError ? musicFromUrl.error.message : "Не удалось скачать трек"}
                    </div>
                  ) : null}
                </>
              ) : null}
            </Group>
            {/* Холст: звука исходника нет — глушить, громкость и дорожки не нужны. */}
            {isCanvas ? null : (
            <>
            <Group
              title="Тишина вместо звука"
              badge={mute.length ? `${mute.length} · ${muteTotal(mute).toFixed(1)} с` : "нет"}
              forceOpen={tlSelected?.kind === "mute"}
            >
              <div className="muted li-note">
                Звук видео глушится, картинка остаётся (вшитый эффект, шум, мат); звуки слоёв и музыка играют.
                Протяните мышью по дорожке «Звук» или нажмите «🔇 Заглушить» над таймлайном.
              </div>
              <div className="ed-quick-row">
                <button
                  type="button"
                  className="btn sm"
                  disabled={!activePlan || censorMat.isPending}
                  onClick={() => activePlan && censorMat.mutate(activePlan.id)}
                  title="По словам субтитров: каждое матерное слово глушится серединой (человек поймёт, автосубтитры площадок — нет), в субтитрах — «б****». Ручные отрезки не трогает, повторное нажатие заменяет только свои. Ctrl+Z откатит."
                >
                  {censorMat.isPending ? "Ищу мат…" : "Заглушить мат"}
                </button>
                <select
                  className="input"
                  style={{ flex: "1 1 120px", minWidth: 0 }}
                  value={censorStyle}
                  onChange={(e) => setCensorStyle(e.target.value as CensorStyle | "")}
                  title="Чем заполнить заглушённую середину слова. Превью глушит тишиной — как звучит, слышно в рендере"
                >
                  <option value="">как раньше у клипа (новый — задом наперёд)</option>
                  {(Object.keys(CENSOR_STYLES) as CensorStyle[]).map((s) => (
                    <option key={s} value={s}>{CENSOR_STYLES[s]}</option>
                  ))}
                </select>
              </div>
              {mute.length ? (
                <div className="ed-layer-list">
                  {mute.map((m, i) => (
                    <div
                      key={`${m.start}-${m.end}-${m.by ?? ""}`}
                      className={`ed-layer-row${tlSelected?.kind === "mute" && tlSelected.index === i ? " on" : ""}`}
                      onClick={() => {
                        setTlSelected({ kind: "mute", index: i });
                        seekClipTime(Math.max(0, m.start - 1), false);
                      }}
                      title={
                        m.by === "censor"
                          ? `Мат, уровень ${m.level ?? 1}, ${CENSOR_STYLES[m.style ?? "silence"]} (кнопка «Заглушить мат» заменит). Перейти на секунду раньше`
                          : "Перейти на секунду раньше отрезка — послушать стык"
                      }
                    >
                      <span className="ed-layer-ico">{m.by === "censor" ? "🤬" : "🔇"}</span>
                      <span className="ed-layer-name mono">
                        {formatTimecode(m.start)} – {formatTimecode(m.end)}
                      </span>
                      <span className="mono muted ed-layer-time">{(m.end - m.start).toFixed(1)} с</span>
                      <button
                        type="button"
                        className="btn ghost sm"
                        title="Вернуть звук"
                        onClick={(e) => {
                          e.stopPropagation();
                          setMute((prev) => prev.filter((_, j) => j !== i));
                          setTlSelected(null);
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </Group>
            <Group title="Громкость видео" badge={gain.length ? `${gain.length} отр.` : "как есть"}>
              <div className="muted li-note">
                Поднять тихий кусок (голос под игрой) или приглушить громкий: звук видео на отрезке громче/тише
                на столько дБ, картинка та же; звуки слоёв и музыка не меняются. Тихий микрофон по всему ролику —
                громкость дорожки в звуке проекта.
              </div>
              {gain.length ? (
                <div className="ed-layer-list">
                  {gain.map((g, i) => (
                    <div key={`${g.start}-${g.end}`} className="ed-layer-row" title="Перейти на секунду раньше отрезка">
                      <span className="ed-layer-ico">🔊</span>
                      <span
                        className="ed-layer-name mono"
                        onClick={() => seekClipTime(Math.max(0, g.start - 1), false)}
                        style={{ cursor: "pointer" }}
                      >
                        {formatTimecode(g.start)} – {formatTimecode(g.end)}
                      </span>
                      <input
                        type="number"
                        className="input mono"
                        style={{ width: 70 }}
                        step={1}
                        min={GAIN_MIN_DB}
                        max={GAIN_MAX_DB}
                        value={g.gain_db}
                        title="дБ: плюс — громче, минус — тише"
                        onChange={(e) => {
                          const db = Number(e.target.value);
                          if (!Number.isFinite(db)) return;
                          const clamped = Math.round(Math.min(GAIN_MAX_DB, Math.max(GAIN_MIN_DB, db)) * 10) / 10;
                          setGain((prev) => prev.map((x, j) => (j === i ? { ...x, gain_db: clamped } : x)));
                        }}
                      />
                      <span className="muted">дБ</span>
                      <button
                        type="button"
                        className="btn ghost sm"
                        title="Убрать отрезок"
                        onClick={() => setGain((prev) => prev.filter((_, j) => j !== i))}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                className="btn ghost sm"
                title="Отрезок 3 с от курсора, +6 дБ — потом поправьте дБ"
                onClick={() => {
                  const v = videoRef.current;
                  const t = v ? clipTimeOf(tcSegsRef.current, v.currentTime) : null;
                  const at = t ?? 0;
                  setGain((prev) => normalizeGain([...prev, { start: at, end: at + 3, gain_db: 6 }]));
                }}
              >
                + Громче у курсора
              </button>
            </Group>
            <Group
              title="Звуковые дорожки"
              badge={
                (source.audio_tracks?.length ?? 0) > 1
                  ? `смесь ${source.audio_tracks!.map((t) => t + 1).join("+")}`
                  : source.audio_tracks?.length
                    ? `дорожка ${source.audio_tracks[0] + 1}`
                    : "по умолчанию"
              }
            >
              <SourceAudioPanel sourceId={source.id} />
            </Group>
            </>
            )}
            {/* Сведение — последний шаг звука: после музыки, тишины, громкости и дорожек. */}
            <Group
              title="Сведение и эффекты"
              badge={mixSettings.auto ? "авто" : mixSettings.music_db != null || mixSettings.voice_fx || mixSettings.music_fx ? "своё" : undefined}
            >
              <div
                className="muted li-note"
                title="«Проверить» меряет по стемам (как сведёт рендер); «Автосведение» выставит громкость музыки, ducking по фразам и звуки под голос"
              >
                Цель: музыка под речью на 12–18 дБ тише голоса, звуки не закрывают слова, ролик −14 LUFS.
              </div>
              <div className="lay-row">
                <button
                  type="button"
                  className="btn sm"
                  disabled={!activePlan || mixReport.isPending}
                  onClick={() => activePlan && mixReport.mutate(activePlan.id)}
                >
                  {mixReport.isPending ? "Слушаю…" : "Проверить сведение"}
                </button>
                <button
                  type="button"
                  className="btn sm"
                  disabled={!activePlan || autoMix.isPending}
                  onClick={() => activePlan && autoMix.mutate(activePlan.id)}
                  title="Громкость музыки под голосом, ducking на каждую фразу, звуки слоёв под голос, −14 LUFS"
                >
                  {autoMix.isPending ? "Свожу…" : "Автосведение"}
                </button>
              </div>
              {mixReport.data ? (
                <div className="mix-report">
                  <div className={mixReport.data.verdicts.some((v) => v.severity === "warn") ? "lay-note warn" : "lay-note ok"}>
                    {mixReport.data.summary}
                  </div>
                  {mixReport.data.timeline?.line ? (
                    <div className="mix-line" title={mixReport.data.timeline.line_scale}>
                      {mixReport.data.timeline.line}
                    </div>
                  ) : null}
                  {mixReport.data.verdicts.map((v, i) => (
                    <div key={i} className={`lay-note ${v.severity === "warn" ? "warn" : ""}`}>
                      {v.message}
                      {v.fix ? <span className="muted"> → {v.fix}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {mixSettings.music_db != null ? (
                <div className="lay-row">
                  <span className="muted li-note">
                    Музыка: {mixSettings.music_db.toFixed(1)} дБ к треку
                    {mixSettings.sidechain === false ? " · ducking по фразам" : ""}
                    {mixSettings.layer_gains ? ` · поправок звуков: ${Object.keys(mixSettings.layer_gains).length}` : ""}
                  </span>
                  <button
                    type="button"
                    className="btn ghost sm"
                    title="Снять сведение: музыка снова подгоняется под голос"
                    onClick={() => setMixSettings(({ voice_fx, music_fx }) => ({ ...(voice_fx ? { voice_fx } : {}), ...(music_fx ? { music_fx } : {}) }))}
                  >
                    Сбросить
                  </button>
                </div>
              ) : null}
              <div className="rgroup-sub">Эффекты</div>
              <FxChainEditor
                title={isCanvas ? "Голос (слои-озвучка)" : "Голос (звук видео)"}
                chain={mixSettings.voice_fx}
                onChange={(voice_fx) => setMixSettings((m) => ({ ...m, voice_fx }))}
              />
              {musicOn ? (
                <FxChainEditor
                  title="Музыка"
                  chain={mixSettings.music_fx}
                  onChange={(music_fx) => setMixSettings((m) => ({ ...m, music_fx }))}
                />
              ) : null}
            </Group>
          </div>
          <div className="insp-pane" hidden={inspActive !== "subs"} role="tabpanel" aria-label="Субтитры">
            <Group title="Субтитры на ролике" badge={subsOn ? "вкл" : "выкл"} defaultOpen>
              <label className="switch">
                <input type="checkbox" checked={subsOn} onChange={(e) => setSubsOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Накладывать субтитры</span>
              </label>
              {subsOn ? (
                <>
                  <div className="sub-options">
                    <label className="field">
                      <span>Движок</span>
                      <select className="input" value={subEngine} onChange={(e) => setSubEngine(e.target.value)}>
                        <option value="">из стиля</option>
                        <option value="whisper">Whisper (локально)</option>
                        <option value="gemini">Gemini</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Стиль</span>
                      <select className="input" value={subId} onChange={(e) => setSubId(Number(e.target.value))}>
                        <option value={0}>по умолчанию</option>
                        {subs.data?.map((s) => (
                          <option key={s.id} value={s.id}>{s.label}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <label className="field range">
                    <span>Положение · {subPosPct}% снизу</span>
                    <input
                      type="range" min={2} max={40} value={subPosPct}
                      onChange={(e) => setSubPosPct(Number(e.target.value))}
                    />
                  </label>
                </>
              ) : null}
            </Group>
            {/* Текст речи нужен и выжженным субтитрам, и слою «Анимированные субтитры»
                (автомонтаж выключает первые и ставит слой) — правка и «Голоса» видны всегда.
                Не в сворачиваемой группе: редактор строк кормит таймлайн и превью. */}
            <section className="insp-section">
              <div className="insp-section-head">
                <span className="insp-section-title">Строки и речь</span>
                {activePlan ? (
                  <button
                    className="btn ghost sm"
                    onClick={() => setSubsWide(true)}
                    title="Открыть строки на всё окно: широкий текст и таймкоды"
                  >
                    ⛶ Править строки широко
                  </button>
                ) : null}
              </div>
              {activePlan ? (
                <SubtitleEditor
                  key={activePlan.id}
                  clipPlanId={activePlan.id}
                  silent={isCanvas}
                  clipTime={clipTimeNow}
                  onSeek={seekClipTime}
                  onLines={(lines) => {
                    subLinesRef.current = lines;
                    setSubLines(lines);
                  }}
                  onWords={(words) => {
                    subWordsRef.current = words;
                  }}
                  onServerChange={async () => {
                    await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
                    setSettingsNonce((n) => n + 1);
                  }}
                />
              ) : null}
            </section>
          </div>
          <div className="insp-pane" hidden={inspActive !== "check"} role="tabpanel" aria-label="Проверка">
            <section className="insp-section">
              <div className="insp-section-head">
                <span className="insp-section-title">Проверка перед сдачей</span>
              </div>
              {activePlan ? (
                <LintPanel
                  clipPlanId={activePlan.id}
                  beforeRun={flushSettings}
                  onSeek={(t) => seekClipTime(t, false)}
                />
              ) : null}
            </section>
          </div>
        </div>

        <div className="ed-insp-foot">
          <div className="ed-foot-row">
            <span
              className={`ed-saved${activePlan?.render_settings ? " on" : ""}`}
              title={
                activePlan?.render_settings
                  ? "Настройки сохранены в этот клип — при рендере возьмутся они."
                  : "У клипа пока нет своих настроек — возьмутся текущие с панели."
              }
            >
              {activePlan?.render_settings ? "● настройки клипа сохранены" : "○ настройки с панели"}
            </span>
            <label className="switch" title="Показывать на превью, где окажутся баннер и субтитры">
              <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} />
              <span className="switch-track" />
              <span>Зоны</span>
            </label>
          </div>
          <div className="ed-foot-tools">
            <button
              type="button"
              className="btn ed-apply-fav"
              disabled={!activePlan}
              onClick={async () => {
                // Unsaved panel edits go in first, so the file shows what is really saved.
                await flushSettings();
                setClipFileOpen(true);
              }}
              title="Весь клип одним JSON-файлом: править руками, скачать, отдать агенту, загрузить"
            >
              {"{ }"} Файл клипа
            </button>
            <button
              type="button"
              className="btn ed-apply-fav"
              onClick={() => void applyToFavorites()}
              title="Скопировать настройки этой панели во все остальные избранные клипы"
            >
              Ко всем ★
            </button>
          </div>
          <div className="ed-render-btns">
            <button
              className="btn primary"
              disabled={batch.isPending || !activePlan || recordMissing}
              onClick={() => activePlan && batch.mutate([activePlan.id])}
              title={recordMissing ? "Для рендера нужна запись стрима" : "Рендер только этого клипа с текущими настройками"}
            >
              {batch.isPending ? "Запуск…" : "▶ Рендерить (1)"}
            </button>
            <button
              className="btn"
              disabled={batch.isPending || !selected.size || recordMissing}
              onClick={() => batch.mutate(undefined)}
              title={recordMissing ? "Для рендера нужна запись стрима" : "Рендер всех отмеченных «в рендер» на странице черновиков"}
            >
              Выбранные ({selected.size})
            </button>
          </div>
        </div>
      </aside>

      <div className="panel editor-timeline ed-bottom">
          <div className="ed-tl-head">
            <strong>{activePlan ? activePlan.title || `Клип #${activePlan.id}` : ""}</strong>
            <span className="muted mono">
              {activePlan ? `${formatDuration(planDur(activePlan))} · ${activePlan.segments.length} сегм.` : ""}
            </span>
            {activePlan ? (
              <button
                className={`mo-act${activePlan.favorite ? " fav" : ""}`}
                title={activePlan.favorite ? "Убрать из избранного" : "В избранное"}
                onClick={() => setPlanFlag(activePlan.id, { favorite: !activePlan.favorite })}
              >
                {activePlan.favorite ? "★" : "☆"}
              </button>
            ) : null}
            {activePlan ? (
              <span className="ed-snap">
                <select
                  className="input"
                  value={snapMode}
                  disabled={snapBeats.isPending}
                  onChange={(e) => setSnapMode(e.target.value as "beat" | "downbeat" | "joins")}
                  title="Что ставить на доли"
                >
                  <option value="beat">слои, ключи, камера → доли</option>
                  <option value="downbeat">… → сильные доли</option>
                  <option value="joins">+ склейки кусков → доли</option>
                </select>
                <button
                  className="btn sm"
                  disabled={snapBeats.isPending}
                  onClick={() => snapBeats.mutate({ planId: activePlan.id, mode: snapMode })}
                  title="Начала слоёв, ключевые кадры и наезды камеры (и склейки — в третьем режиме) — на ближайшую долю музыки клипа, если до неё ≤ 0.15 с. Сетка — трек «Музыка», без него — доли звука клипа. Субтитры и чат не двигаются. Отмена — «↩ Шаг назад»"
                >
                  {snapBeats.isPending ? "…" : "♪ К долям"}
                </button>
              </span>
            ) : null}
          </div>
          <ClipTimeline
            sourceId={source.id}
            noFrames={sourceMissing}
            canvas={isCanvas ? { bg: backdrop, accent: clipAccent(layers), tileAspect: frameSize(aspect).w / frameSize(aspect).h } : null}
            peaks={planWave.data?.peaks}
            pieces={activePlan?.segments ?? []}
            inserts={inserts}
            subLines={subLines}
            assets={assetsQuery.data ?? []}
            videoRef={videoRef}
            selected={tlSelected}
            onSelect={(sel) => {
              selectOnTimeline(sel);
              if (sel?.kind === "piece") {
                const seg = activePlan?.segments[sel.index];
                if (seg) setSelectedSeg(seg.id);
              }
            }}
            onSeekClip={(t) => seekClipTime(t, false)}
            onPieceCommit={(id, s, e) => commit.mutate({ id, s, e })}
            onSplitAt={splitAtClip}
            onDeleteSelected={deleteSelected}
            onInsertsChange={setInserts}
            layers={layers}
            camera={camera}
            onLayersChange={setLayers}
            onCameraChange={setCamera}
            onDropLayers={(payload, at, track) => dropLayersAt(payload, at, track)}
            mute={mute}
            onMuteChange={setMute}
            audioEdit={audioEdit}
            onAudioEditChange={setAudioEdit}
            onSubLinesChange={(next) => {
              setSubLines(next);
              subLinesRef.current = next;
              if (activePlan) void clipPlansApi.saveSubtitles(activePlan.id, next).catch(() => undefined);
            }}
            onDropAsset={(assetId, at) => {
              const asset = assetsQuery.data?.find((a) => a.id === assetId);
              setInserts((prev) => [
                ...prev,
                {
                  asset_id: assetId,
                  at,
                  duration: Math.min(4, Math.max(1, asset?.duration_sec || 1.5)),
                  mode: asset?.kind === "audio" ? "sound" : "full",
                  volume: 1,
                  duck: asset?.kind !== "audio",
                },
              ]);
              setTlSelected({ kind: "insert", index: inserts.length });
              toast.success(`«${asset?.label ?? "файл"}» на ${formatDuration(at)} — тяните блок, чтобы подвинуть`);
            }}
          />
      </div>
    </div>
  );
}
