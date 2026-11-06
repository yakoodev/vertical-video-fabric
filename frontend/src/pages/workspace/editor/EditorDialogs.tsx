import { qk } from "@/api/keys";
import { ClipFileDialog } from "@/pages/workspace/ClipFileDialog";
import { AiMontageDialog } from "@/pages/workspace/AiMontageDialog";
import { SubtitleEditor } from "@/pages/workspace/SubtitleEditor";
import { WatchMontage } from "@/pages/workspace/preview/WatchMontage";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Окна клипа: ИИ-монтаж, «Смотреть монтаж», файл клипа. */
export function ClipDialogs({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, toast, activePlan, clipFileOpen, setClipFileOpen, watchOpen, setWatchOpen, aiMontageOpen,
    setAiMontageOpen, setSettingsNonce, aiGoal,
  } = session;
  return (
    <>
      {aiMontageOpen && activePlan ? (
        <AiMontageDialog
          initialGoal={aiGoal}
          clipPlanId={activePlan.id}
          clipTitle={activePlan.title || `Клип #${activePlan.id}`}
          onClose={() => setAiMontageOpen(false)}
          onApplied={async (changes, rendered) => {
            toast.success(
              (changes.length ? `ИИ-монтаж применён: ${changes.join(", ")}` : "Изменений нет") +
                (rendered ? " · рендер запущен" : ""),
            );
            await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
            if (rendered) {
              qc.invalidateQueries({ queryKey: qk.activeTasks });
              qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
            }
            setSettingsNonce((n) => n + 1);
          }}
        />
      ) : null}
      {watchOpen && activePlan ? (
        <WatchMontage clipPlanId={activePlan.id} title={activePlan.title || `Клип #${activePlan.id}`} onClose={() => setWatchOpen(false)} />
      ) : null}
      {clipFileOpen && activePlan ? (
        <ClipFileDialog
          clipPlanId={activePlan.id}
          onClose={() => setClipFileOpen(false)}
          onApplied={async (changes) => {
            toast.success(changes.length ? `Применено: ${changes.join(", ")}` : "Ничего не поменялось");
            await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
            setSettingsNonce((n) => n + 1);
          }}
        />
      ) : null}
    </>
  );
}

/** Строки субтитров на всё окно. */
export function SubtitlesWideDialog({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, subLinesRef, subWordsRef, activePlan, setSubLines, setSettingsNonce, subsWide,
    setSubsWide, isCanvas, clipTimeNow, seekClipTime,
  } = session;
  if (!(subsWide && activePlan)) return null;
  return (
    <div className="modal-backdrop" onClick={() => setSubsWide(false)}>
      <div className="modal subs-wide" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pub-head">
          <h3>Субтитры клипа · {activePlan.title || `Клип #${activePlan.id}`}</h3>
          <button className="pub-x" title="Закрыть" onClick={() => setSubsWide(false)}>
            ×
          </button>
        </div>
        <SubtitleEditor
          key={`wide-${activePlan.id}`}
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
      </div>
    </div>
  );
}
