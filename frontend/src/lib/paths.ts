/**
 * Адреса лестницы Проект → Видео → Клип. Клип пока открывается на экране монтажа внутри видео;
 * /c/:token занят ссылкой на обложку, страница клипа будет на /clips/:id.
 */
export const projectPath = (id: number | string, tab?: string) => `/p/${id}${tab ? `?tab=${tab}` : ""}`;
export const videoPath = (id: number | string, seg?: string) => `/v/${id}${seg ? `/${seg}` : ""}`;
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
