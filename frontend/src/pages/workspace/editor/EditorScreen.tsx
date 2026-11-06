import { Link } from "react-router-dom";
import { clipPlansApi } from "@/api/clipPlans";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { clipPath } from "@/lib/paths";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";
import { EditorLibrary } from "@/pages/workspace/editor/EditorLibrary";
import { EditorCenter } from "@/pages/workspace/editor/EditorCenter";
import { EditorTimeline } from "@/pages/workspace/editor/EditorTimeline";
import { EditorInspector } from "@/pages/workspace/editor/EditorInspector";

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
    libTab,
  } = session;
  return (
    <div className={`ed${libTab === "frame" ? " ed--wide-lib" : ""}`}>
      <EditorLibrary session={session} />

      <EditorCenter session={session} />

      <EditorInspector session={session} />

      <EditorTimeline session={session} />
    </div>
  );
}
