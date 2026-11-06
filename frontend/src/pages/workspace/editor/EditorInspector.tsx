import { InspectorTabs } from "@/pages/workspace/InspectorParts";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";
import { InspectorClipPane } from "@/pages/workspace/editor/inspector/InspectorClipPane";
import { InspectorLayersPane } from "@/pages/workspace/editor/inspector/InspectorLayersPane";
import { InspectorPiecePane } from "@/pages/workspace/editor/inspector/InspectorPiecePane";
import { InspectorAudioPane } from "@/pages/workspace/editor/inspector/InspectorAudioPane";
import { InspectorSubsPane } from "@/pages/workspace/editor/inspector/InspectorSubsPane";
import { InspectorCheckPane } from "@/pages/workspace/editor/inspector/InspectorCheckPane";

/** Правая часть редактора: вкладки инспектора и подвал с рендером. */
export function EditorInspector({ session }: { session: DraftsEditorSession }) {
  const {
    inspScrollRef, recordMissing, selected, flushSettings, applyToFavorites, batch, activePlan, setClipFileOpen,
    showZones, setShowZones, isCanvas, inspTabs, inspActive, pickInspTab,
  } = session;
  return (
    <aside className="ed-inspector">
      <InspectorTabs tabs={inspTabs} active={inspActive} onPick={pickInspTab} />
      <div className="ed-insp-scroll" ref={inspScrollRef}>
        <InspectorClipPane session={session} />
        <InspectorLayersPane session={session} />
        {/* Холст: кусок один и без исходника — скорость, цвет и фокус не нужны. */}
        {isCanvas ? null : (
        <InspectorPiecePane session={session} />
        )}
        <InspectorAudioPane session={session} />
        <InspectorSubsPane session={session} />
        <InspectorCheckPane session={session} />
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
  );
}
