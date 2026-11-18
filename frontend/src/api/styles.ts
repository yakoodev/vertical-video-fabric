import { api } from "@/api/client";

/** Родной приём стиля: шаблон, переход, вход или инструмент — и к какому содержанию он подходит. */
export interface StyleMove {
  kind: "template" | "transition" | "entrance" | "tool";
  id: string;
  fits: string;
}

/** Карточка стиля ролика (app/style_cards.json): одна карточка — один вид ролика. */
export interface StyleCard {
  id: string;
  name: string;
  short: string;
  version: number;
  essence: string;
  card: {
    essence: string;
    not: string[];
    materials: { palette: string[]; fonts: string[]; texture: string };
    motion: string;
    camera: string;
    sound: { palette: string[]; note: string };
    native: StyleMove[];
    pitfalls: string[];
    vary: string[];
  };
  apply: {
    render_look: string;
    subtitle_profile: string;
    templates: string[];
    theme: string;
    entrances: Record<string, string>;
  };
}

export interface ApplyStyleResult {
  ok: boolean;
  style: { id: string; name: string; short: string; version: number };
  changes: string[];
  warnings: string[];
  project_default?: number;
}

export const stylesApi = {
  list: () => api.get<StyleCard[]>("/api/styles"),
  apply: (clipPlanId: number, style: string, projectDefault = false) =>
    api.post<ApplyStyleResult>(`/api/clip-plans/${clipPlanId}/apply-style`, {
      style,
      project_default: projectDefault,
    }),
};
