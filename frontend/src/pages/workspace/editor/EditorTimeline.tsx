import { clipPlansApi } from "@/api/clipPlans";
import { clipAccent, frameSize } from "@/motion";
import { ClipTimeline } from "@/pages/workspace/ClipTimeline";
import { formatDuration } from "@/components/ui";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Низ редактора: заголовок клипа и таймлайн. */
export function EditorTimeline({ session }: { session: DraftsEditorSession }) {
  const {
    toast, videoRef, subLinesRef, sourceMissing, assetsQuery, planWave, snapBeats, commit,
    setPlanFlag, activePlan, tlSelected, setTlSelected, subLines, setSubLines, setSelectedSeg, inserts, setInserts,
    layers, setLayers, camera, setCamera, backdrop, mute, setMute, audioEdit, setAudioEdit, aspect, snapMode,
    setSnapMode, source, isCanvas, seekClipTime, splitAtClip, deleteSelected, dropLayersAt, selectOnTimeline,
    planDur,
  } = session;
  return (
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
  );
}
