import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { accountsApi } from "@/api/accounts";
import { clipsApi, type PublishRequest } from "@/api/clips";
import { qk } from "@/api/keys";
import type { Clip } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Loading } from "@/components/ui";

export function PublishDialog({ clip, onClose }: { clip: Clip; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const [title, setTitle] = useState(clip.title);
  const [description, setDescription] = useState(clip.description);
  const [privacy, setPrivacy] = useState("public");
  const [targets, setTargets] = useState<number[]>([]);
  const [scheduledAt, setScheduledAt] = useState("");

  const publish = useMutation({
    mutationFn: (body: PublishRequest) => clipsApi.publish(clip.id, body),
    onSuccess: (job) => {
      toast.success("Поставлено в очередь публикации");
      qc.invalidateQueries({ queryKey: qk.jobs });
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      onClose();
      navigate(`/publications/${job.id}`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось опубликовать"),
  });

  const toggle = (id: number) =>
    setTargets((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ margin: "0 0 14px" }}>Опубликовать клип</h3>
        <div style={{ display: "grid", gap: 12 }}>
          <label className="field">
            <span>Заголовок</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>Описание</span>
            <textarea className="input" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="field">
            <span>Аккаунты</span>
            {accounts.isLoading ? (
              <Loading />
            ) : !accounts.data?.length ? (
              <span className="muted">Нет аккаунтов — добавьте в Настройках</span>
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {accounts.data.map((a) => (
                  <label key={a.id} className="check">
                    <input type="checkbox" checked={targets.includes(a.id)} onChange={() => toggle(a.id)} />
                    <span>
                      {a.platform} · {a.label}
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <label className="field" style={{ flex: 1 }}>
              <span>Приватность</span>
              <select className="input" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                <option value="public">public</option>
                <option value="unlisted">unlisted</option>
                <option value="private">private</option>
              </select>
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Расписание (необязательно)</span>
              <input
                className="input"
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
              />
            </label>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 18 }}>
          <button className="btn ghost" onClick={onClose} disabled={publish.isPending}>
            Отмена
          </button>
          <button
            className="btn primary"
            disabled={publish.isPending || !targets.length}
            onClick={() =>
              publish.mutate({
                title: title.trim(),
                description: description.trim(),
                targets,
                privacy,
                scheduled_at: scheduledAt ? scheduledAt.replace("T", " ") : undefined,
              })
            }
          >
            {publish.isPending ? "…" : "В очередь"}
          </button>
        </div>
      </div>
    </div>
  );
}
