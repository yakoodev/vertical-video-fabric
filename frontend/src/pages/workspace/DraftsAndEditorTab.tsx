import { EmptyState, ErrorState, Loading } from "@/components/ui";
import { DraftsScreen } from "@/pages/workspace/drafts/DraftsScreen";
import { buildDraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";
import { ClipDialogs, SubtitlesWideDialog } from "@/pages/workspace/editor/EditorDialogs";
import { EditorScreen, EditorViewBar } from "@/pages/workspace/editor/EditorScreen";
import { useDraftsEditorState } from "@/pages/workspace/editor/useDraftsEditorState";

/**
 * Вкладка видео «Черновики»: список черновиков или редактор открытого клипа (?clip=<id>).
 * Экраны меняются по адресу, а состояние живёт здесь, над ними: см. useDraftsEditorState.
 */
export function DraftsAndEditorTab({ sourceId }: { sourceId: string }) {
  const state = useDraftsEditorState(sourceId);
  const { query, plans, view } = state;
  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!plans.length) {
    return <EmptyState icon="🎯" title="Черновиков нет" hint="Черновики появляются после анализа" />;
  }
  const session = buildDraftsEditorSession(state, query.data!);
  return (
    <div className="editor">
      <ClipDialogs session={session} />
      {view === "editor" ? <EditorViewBar session={session} /> : null}
      <SubtitlesWideDialog session={session} />
      {view === "triage" && <DraftsScreen session={session} />}
      {view === "editor" && <EditorScreen session={session} />}
    </div>
  );
}
