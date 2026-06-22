import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { promptsApi } from "@/api/prompts";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";

const TASKS = [
  { value: "analysis", label: "Анализ" },
  { value: "publishing", label: "Публикация" },
  { value: "subtitle", label: "Субтитры" },
];

export function PromptsSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: qk.promptPresets, queryFn: promptsApi.list });
  const [task, setTask] = useState("analysis");
  const [label, setLabel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [makeDefault, setMakeDefault] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.promptPresets });
  const create = useMutation({
    mutationFn: () => promptsApi.create(task, label.trim(), prompt.trim(), makeDefault),
    onSuccess: () => {
      toast.success("Пресет сохранён");
      setLabel("");
      setPrompt("");
      setMakeDefault(false);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => promptsApi.remove(id),
    onSuccess: () => {
      toast.success("Пресет удалён");
      invalidate();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"),
  });

  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="panel" style={{ display: "grid", gap: 10, maxWidth: 680 }}>
        <strong>Новый промпт-пресет</strong>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label className="field" style={{ width: 160 }}>
            <span>Задача</span>
            <select className="input" value={task} onChange={(e) => setTask(e.target.value)}>
              {TASKS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 180 }}>
            <span>Название</span>
            <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
          </label>
        </div>
        <label className="field">
          <span>Промпт</span>
          <textarea className="input" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={makeDefault} onChange={(e) => setMakeDefault(e.target.checked)} />
          <span>Сделать пресетом по умолчанию для этой задачи</span>
        </label>
        <div>
          <button className="btn primary" disabled={create.isPending || !label.trim() || !prompt.trim()} onClick={() => create.mutate()}>
            {create.isPending ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      </div>

      {TASKS.map((t) => {
        const items = (q.data ?? []).filter((p) => p.task === t.value);
        if (!items.length) return null;
        return (
          <div key={t.value} style={{ display: "grid", gap: 8 }}>
            <span className="muted" style={{ fontSize: 12.5, fontWeight: 700, textTransform: "uppercase" }}>
              {t.label}
            </span>
            {items.map((p) => (
              <div key={p.id} className="ws-row" style={{ alignItems: "flex-start" }}>
                <div style={{ flex: 1, display: "grid", gap: 3 }}>
                  <strong>
                    {p.label} {p.is_default ? <span className="badge ok">по умолчанию</span> : null}
                  </strong>
                  <span className="muted" style={{ fontSize: 12.5, whiteSpace: "pre-wrap", maxHeight: 60, overflow: "hidden" }}>
                    {p.prompt}
                  </span>
                </div>
                <button className="btn ghost sm" onClick={() => remove.mutate(p.id)}>
                  🗑
                </button>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
