import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi, type DefaultsInput } from "@/api/settings";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";

const AI_PROVIDERS = ["action", "polza", "gemini", "artemox", "mock"];
const SUB_PROVIDERS = ["whisper", "polza", "gemini", "artemox", "mock"];

const EMPTY: DefaultsInput = {
  default_ai_provider: "action",
  default_ai_model: "",
  default_subtitle_provider: "whisper",
  default_subtitle_model: "",
  global_proxy_url: "",
  default_banner_id: 0,
  default_subtitle_profile_id: 0,
};

export function DefaultsSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: qk.settings, queryFn: settingsApi.get });
  const [form, setForm] = useState<DefaultsInput>(EMPTY);

  useEffect(() => {
    if (!q.data) return;
    setForm({
      default_ai_provider: q.data.default_ai_provider || "action",
      default_ai_model: q.data.default_ai_model || "",
      default_subtitle_provider: q.data.default_subtitle_provider || "whisper",
      default_subtitle_model: q.data.default_subtitle_model || "",
      global_proxy_url: "",
      default_banner_id: q.data.default_banner_id || 0,
      default_subtitle_profile_id: q.data.default_subtitle_profile_id || 0,
    });
  }, [q.data]);

  const save = useMutation({
    mutationFn: () => settingsApi.saveDefaults(form),
    onSuccess: () => {
      toast.success("Настройки сохранены");
      qc.invalidateQueries({ queryKey: qk.settings });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const set = (patch: Partial<DefaultsInput>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <div className="panel" style={{ display: "grid", gap: 14, maxWidth: 640 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Провайдер анализа</span>
          <select className="input" value={form.default_ai_provider} onChange={(e) => set({ default_ai_provider: e.target.value })}>
            {AI_PROVIDERS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Модель анализа</span>
          <input className="input" value={form.default_ai_model} onChange={(e) => set({ default_ai_model: e.target.value })} placeholder="напр. gemini-3.1-flash-lite" />
        </label>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Провайдер субтитров</span>
          <select className="input" value={form.default_subtitle_provider} onChange={(e) => set({ default_subtitle_provider: e.target.value })}>
            {SUB_PROVIDERS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Модель субтитров</span>
          <input className="input" value={form.default_subtitle_model} onChange={(e) => set({ default_subtitle_model: e.target.value })} />
        </label>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Баннер по умолчанию</span>
          <select className="input" value={form.default_banner_id} onChange={(e) => set({ default_banner_id: Number(e.target.value) })}>
            <option value={0}>— нет —</option>
            {q.data?.banners.map((b) => (
              <option key={b.id} value={b.id}>{b.label}</option>
            ))}
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 160 }}>
          <span>Профиль субтитров по умолчанию</span>
          <select className="input" value={form.default_subtitle_profile_id} onChange={(e) => set({ default_subtitle_profile_id: Number(e.target.value) })}>
            <option value={0}>— нет —</option>
            {q.data?.subtitle_profiles.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>Глобальный прокси для публикации {q.data?.global_proxy_configured ? "(сейчас: настроен)" : "(сейчас: прямое подключение)"}</span>
        <input className="input" value={form.global_proxy_url} onChange={(e) => set({ global_proxy_url: e.target.value })} placeholder="http://user:pass@host:port — пусто, чтобы не менять" />
      </label>
      <div>
        <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Сохранение…" : "Сохранить"}
        </button>
      </div>
    </div>
  );
}
