/**
 * Адреса лестницы Проект → Видео или Выпуск → Клип. Экран клипа — /clips/:id (id любого плана его группы),
 * монтаж — редактор внутри видео. /c/:token занят ссылкой на обложку.
 */
export const projectPath = (id: number | string, tab?: string) => `/p/${id}${tab ? `?tab=${tab}` : ""}`;
export const videoPath = (id: number | string, seg?: string) => `/v/${id}${seg ? `/${seg}` : ""}`;
export const clipPath = (id: number | string) => `/clips/${id}`;
/** Выпуск (app/releases.py): expl-3, story-1, aiv-4. ?steps=1 — страница шагов, даже если клип один. */
export const releasePath = (key: string, steps = false) => `/r/${key}${steps ? "?steps=1" : ""}`;
/** «Новое видео»: выбор типа. В проекте — сразу в него. */
export const newVideoPath = (projectId?: number | string | null, type?: string) =>
  `${projectId ? `/p/${projectId}/new` : "/new"}${type ? `?type=${type}` : ""}`;
export const clipEditorPath = (sourceId: number | string, planId: number | string) =>
  `/v/${sourceId}/candidates?clip=${planId}`;

const LAST_PROJECT = "vvf-last-project";

export function rememberProject(id: number): void {
  try {
    window.localStorage.setItem(LAST_PROJECT, String(id));
  } catch {
    // без localStorage первый экран просто откроет первый проект
  }
}

export function lastProject(): number | null {
  try {
    return Number(window.localStorage.getItem(LAST_PROJECT)) || null;
  } catch {
    return null;
  }
}
