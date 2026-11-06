import type { NavState } from "@/api/nav";
import type { useWhere } from "@/components/shell/Ladder";
import { HIDDEN_SOURCE_TYPES } from "@/lib/videoKinds";

/**
 * К чему привязан чат из верхней панели: то, что открыто сейчас, и ступени над ним.
 * Клип — его чат с экрана клипа (и холст тоже: проект холста знает сам клип). Видео — чат видео.
 * Проект — основная переписка проекта. Вне проектов — общий чат.
 */
export type ChatLevel =
  | { kind: "clip"; planId: number; name: string }
  | { kind: "video"; sourceId: number; name: string }
  | { kind: "project"; projectId: number; name: string }
  | { kind: "general" };

export type ChatPlace = {
  /** От самого узкого к общему: первый — чат этого экрана, последний — всегда общий. */
  levels: ChatLevel[];
  /** Экран ещё не знает, где он: кнопка ждёт, а не открывает не тот чат. */
  pending: boolean;
};

const GENERAL: ChatLevel = { kind: "general" };

/** Подпись кнопки и заголовок панели. */
export function levelTitle(level: ChatLevel): string {
  if (level.kind === "clip") return `Чат клипа ${level.name}`;
  if (level.kind === "video") return `Чат видео ${level.name}`;
  if (level.kind === "project") return `Чат проекта ${level.name}`;
  return "Общий чат";
}

/** Короткая подпись переключателя в панели. */
export function levelTab(level: ChatLevel): string {
  if (level.kind === "clip") return "Клип";
  if (level.kind === "video") return "Видео";
  if (level.kind === "project") return `Проект ${level.name}`;
  return "Общий";
}

export function chatPlace(where: ReturnType<typeof useWhere>, nav: NavState | undefined, clipChatOn: boolean): ChatPlace {
  const { head, parts, videoId, source, clipId, editClipId, clip, projectId, hiddenVideo } = where;
  const planAsked = clipId || editClipId;
  if (head !== "p" && !planAsked && !videoId) return { levels: [GENERAL], pending: false };
  if ((planAsked && clip.isLoading) || (videoId && source.isLoading) || !nav) return { levels: [GENERAL], pending: true };

  const levels: ChatLevel[] = [];
  const view = clip.data;
  if (view && clipChatOn) {
    // На экране клипа — главный план группы (его чат там и показан), в монтаже — открытый план.
    levels.push({ kind: "clip", planId: editClipId ? Number(editClipId) : view.id, name: view.name });
  }
  if (view && !HIDDEN_SOURCE_TYPES.includes(view.source.source_type)) {
    levels.push({ kind: "video", sourceId: view.source.id, name: view.source.name });
  } else if (!view && videoId && source.data && !hiddenVideo) {
    levels.push({
      kind: "video",
      sourceId: source.data.id,
      name: source.data.display_name || source.data.original_filename || `#${source.data.id}`,
    });
  }
  const project = nav.projects.find((p) => p.id === (head === "p" ? Number(parts[1]) : projectId));
  if (project) levels.push({ kind: "project", projectId: project.id, name: project.name });
  return { levels: [...levels, GENERAL], pending: false };
}
