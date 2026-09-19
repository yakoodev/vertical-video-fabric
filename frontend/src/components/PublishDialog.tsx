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

  const suggest = useMutation({
    mutationFn: () => clipsApi.publishMeta(clip.id),
    onSuccess: (m) => {
      setTitle(m.title);
      setDescription([m.description, m.hashtags.join(" ")].filter(Boolean).join("\n\n"));
      toast.success(m.has_transcript ? "Сгенерировано по речи клипа" : "Сгенерировано по описанию момента (транскрипта нет)");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сгенерировать"),
  });

  const toggle = (id: number) =>
    setTargets((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal pub-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pub-head">
          <h3>Опубликовать клип</h3>
          <button className="pub-x" title="Закрыть" onClick={onClose} disabled={publish.isPending}>
            ×
          </button>
        </div>
        <div className="pub-body">
        <div className="pub-preview">
          <video src={`/media/clips/${clip.id}`} controls preload="metadata" playsInline />
        </div>
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <div className="pub-gen">
            <button
              type="button"
              className="btn sm pub-gen-btn"
              disabled={suggest.isPending}
              onClick={() => suggest.mutate()}
              title="Заголовок, описание и хэштеги по речи и сути клипа (Gemini). Стиль — пресет «Публикация» в Настройках → Промпты."
            >
              {suggest.isPending ? "Генерирую…" : "✨ Сгенерировать"}
            </button>
            <span className="muted">заголовок · описание · хэштеги</span>
          </div>
          <label className="field">
            <span>Заголовок <span className="muted mono">{title.length}/100</span></span>
            <input className="input" maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>Описание</span>
            <textarea className="input" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="field">
            <span>Аккаунты</span>
            {accounts.isLoading ? (
              <Loading />
            ) : !accounts.data?.length ? (
              <span className="muted">Нет аккаунтов — добавьте на странице «Аккаунты»</span>
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
          <div className="field">
            <span>Приватность</span>
            <div className="seg" role="radiogroup" aria-label="Приватность">
              {["public", "unlisted", "private"].map((p) => (
                <button
                  key={p}
                  type="button"
                  role="radio"
                  aria-checked={privacy === p}
                  className={`seg-item${privacy === p ? " active" : ""}`}
                  onClick={() => setPrivacy(p)}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
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
