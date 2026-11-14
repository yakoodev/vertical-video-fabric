import { qk } from "@/api/keys";
import { clipAccent, sourceStrategy, type Layer } from "@/motion";
import { AgentPanel } from "@/pages/workspace/motion/AgentPanel";
import { BackdropLive, canvasLabel } from "@/pages/workspace/canvasClip";
import { GeneratePanel, MemesPanel, SoundsPanel, StickersPanel, TemplatesPanel, TextPanel } from "@/pages/workspace/motion/LibraryPanels";
import { AssetDrawer } from "@/pages/workspace/AssetDrawer";
import { PagesPanel } from "@/pages/workspace/motion/PagesPanel";
import { formatDuration } from "@/components/ui";
import { clipTimeOf, LIB_TABS } from "@/pages/workspace/editor/editorHelpers";
import { CropFrame } from "@/pages/workspace/editor/CropFrame";
import { MemesTab } from "@/pages/workspace/editor/VideoMemePicker";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Левая часть редактора: вкладки и библиотека. Исходник с рамкой живёт здесь всегда, из него рисуется превью. */
export function EditorLibrary({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, toast, libTabs, videoRef, phoneRef, phoneOverlayRef, subLinesRef, subWordsRef, bannerImgRef,
    bannerGeomRef, subStyleRef, subPosPctRef, sourceMissing, assetsQuery, pickableAssets, openEditor, motionViewRef,
    planSubs, agentRunningRef, flushSettings, setFixedFrame, activePlan, selectedPreset, lookCss, libTab, setLibTab,
    setTlSelected, mediaSrc, activePlanId, subsOn, bannerOn, mirror, setInserts, layers, setLayers, camera, layout,
    backdrop, aspect, layersFmt, watchOpen, setAiMontageOpen, setSettingsNonce, setAiGoal, showZones,
    bannerHeightPct, bannerPosPct, subPosPct, moduleOn, source, isCanvas, clipTimeNow, insertsPreview, clipDuration,
    addLayers, addAtCursor, railPlans, planDur, planThumb, placeVideoMeme, placingMeme, clipNow,
  } = session;
  return (
    <>
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
        {libTab === "memes" ? (
          <MemesTab
            onPlace={(memeId, mode) => placeVideoMeme(memeId, mode, clipNow())}
            placing={placingMeme}
            disabled={!activePlan}
            images={<MemesPanel onAdd={addAtCursor} assets={pickableAssets} clipId={activePlanId} />}
          />
        ) : null}
        {libTab === "generate" && libTabs.some((t) => t.key === "generate") ? <GeneratePanel onAdd={addAtCursor} /> : null}
        {libTab === "pages" && libTabs.some((t) => t.key === "pages") ? <PagesPanel onAdd={addAtCursor} clipId={activePlanId} /> : null}
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
    </>
  );
}
