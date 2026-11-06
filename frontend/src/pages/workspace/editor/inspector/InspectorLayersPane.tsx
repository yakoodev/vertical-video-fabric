import { type MontageInsert } from "@/api/types";
import { Group } from "@/pages/workspace/InspectorParts";
import { type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { formatRefSize, type Layer } from "@/motion";
import { duplicateLayer, LAYER_ICON, layerTitle } from "@/pages/workspace/motion/edit";
import { LayerInspector } from "@/pages/workspace/motion/LayerInspector";
import { CameraPanel } from "@/pages/workspace/motion/CameraPanel";
import { formatDuration } from "@/components/ui";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Слои»: выбранный слой, все слои клипа, вставки, камера. */
export function InspectorLayersPane({ session }: { session: DraftsEditorSession }) {
  const {
    assetsQuery, pickableAssets, navigate, motionFonts, clipNow, planWave, planSubs, pickPointRef, activePlan,
    tlSelected, setTlSelected, inserts, setInserts, layers, setLayers, camera, setCamera, layersFmt, setPickingPoint,
    source, clipTimeNow, seekClipTime, clipTimeExact, selectedLayerId, captionWords, clipDuration, inspActive,
  } = session;
  return (
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
  );
}
