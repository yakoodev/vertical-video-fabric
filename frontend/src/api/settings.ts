import { api } from "@/api/client";

export interface AppSettings {
  default_ai_provider: string;
  default_ai_model: string;
  default_subtitle_provider: string;
  default_subtitle_model: string;
  default_banner_id: number;
  default_subtitle_profile_id: number;
  global_proxy_configured: boolean;
  global_proxy_display: string;
  banners: { id: number; label: string }[];
  subtitle_profiles: { id: number; label: string }[];
}

export interface DefaultsInput {
  default_ai_provider: string;
  default_ai_model: string;
  default_subtitle_provider: string;
  default_subtitle_model: string;
  global_proxy_url: string;
  default_banner_id: number;
  default_subtitle_profile_id: number;
}

export const settingsApi = {
  get: () => api.get<AppSettings>("/api/settings"),
  saveDefaults: (input: DefaultsInput) => {
    const fd = new FormData();
    for (const [k, v] of Object.entries(input)) {
      // Omitting an empty proxy leaves the saved value untouched (backend treats a
      // missing field as "don't change" but an empty string as "clear").
      if (k === "global_proxy_url" && !String(v).trim()) continue;
      fd.append(k, String(v));
    }
    fd.append("next", "/settings/defaults");
    return api.form<unknown>("/ui/settings/defaults", fd);
  },
};
