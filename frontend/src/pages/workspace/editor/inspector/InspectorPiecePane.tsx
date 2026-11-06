import { Group } from "@/pages/workspace/InspectorParts";
import { FocusEditor } from "@/pages/workspace/editor/FocusEditor";
import { colorBadge, PieceColorControl, SegmentSpeedControl, speedBadge } from "@/pages/workspace/editor/PieceControls";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Кусок»: скорость, цвет и фокус выбранного куска. */
export function InspectorPiecePane({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, videoRef, segmentSpeed, pieceColor, matchColors, activePlan, setTlSelected, setSelectedSeg,
    selectedSegment, inspActive, selectedPieceIndex,
  } = session;
  return (
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
  );
}
