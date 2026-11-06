import { qk } from "@/api/keys";
import { SubtitleEditor } from "@/pages/workspace/SubtitleEditor";
import { Group } from "@/pages/workspace/InspectorParts";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Вкладка «Субтитры»: включение, стиль, положение и правка строк. */
export function InspectorSubsPane({ session }: { session: DraftsEditorSession }) {
  const {
    sourceId, qc, subLinesRef, subWordsRef, subs, activePlan, setSubLines, subsOn, setSubsOn, subId, setSubId,
    subEngine, setSubEngine, setSettingsNonce, setSubsWide, subPosPct, setSubPosPct, isCanvas, clipTimeNow,
    seekClipTime, inspActive,
  } = session;
  return (
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
  );
}
