import { LintPanel } from "@/pages/workspace/LintPanel";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Проверка»: проверка перед сдачей. */
export function InspectorCheckPane({ session }: { session: DraftsEditorSession }) {
  const {
    flushSettings, activePlan, seekClipTime, inspActive,
  } = session;
  return (
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
  );
}
