import { api } from "@/api/client";

export interface PromptPreset {
  id: number;
  task: string;
  label: string;
  prompt: string;
  is_default: number;
}

export const promptsApi = {
  list: () => api.get<PromptPreset[]>("/api/prompt-presets"),
  create: (task: string, label: string, prompt: string, makeDefault: boolean) => {
    const fd = new FormData();
    fd.append("task", task);
    fd.append("label", label);
    fd.append("prompt", prompt);
    if (makeDefault) fd.append("make_default", "true");
    fd.append("next", "/settings/prompts");
    return api.form<unknown>("/ui/prompt-presets", fd);
  },
  remove: (id: number) => {
    const fd = new FormData();
    fd.append("next", "/settings/prompts");
    return api.form<unknown>(`/ui/prompt-presets/${id}/delete`, fd);
  },
};
