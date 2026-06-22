import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Clip } from "@/api/types";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Badge, formatDuration } from "@/components/ui";

export function ClipCard({ clip, actions }: { clip: Clip; actions?: ReactNode }) {
  const qc = useQueryClient();
  const toast = useToast();
  const name = clip.title || `Клип #${clip.id}`;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);

  const rename = useMutation({
    mutationFn: (t: string) => clipsApi.rename(clip.id, t),
    onSuccess: () => {
      toast.success("Клип переименован");
      qc.invalidateQueries({ queryKey: qk.clips() });
      if (clip.source_id) qc.invalidateQueries({ queryKey: qk.clips(clip.source_id) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось переименовать"),
  });

  const commit = () => {
    const t = value.trim();
    setEditing(false);
    if (t && t !== name) rename.mutate(t);
    else setValue(name);
  };

  return (
    <div className="panel clip-card">
      <div className="clip-video">
        <video src={`/media/clips/${clip.id}`} controls preload="metadata" playsInline />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <Badge status={clip.status} />
        {clip.published_targets_count ? (
          <span className="badge ok">
            <span className="dot" />
            опубликовано: {clip.published_targets_count}
          </span>
        ) : null}
      </div>
      {editing ? (
        <input
          className="input"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setEditing(false);
              setValue(name);
            }
          }}
        />
      ) : (
        <div className="pcard-titlerow">
          <strong style={{ flex: 1, fontSize: 14, wordBreak: "break-word" }} title={name}>
            {name}
          </strong>
          <button
            className="pcard-edit"
            title="Переименовать"
            onClick={() => {
              setValue(name);
              setEditing(true);
            }}
          >
            ✎
          </button>
        </div>
      )}
      <div className="muted" style={{ fontSize: 12.5, display: "flex", gap: 12 }}>
        <span>{formatDuration(clip.duration_sec)}</span>
        {clip.width ? <span>{clip.width}×{clip.height}</span> : null}
      </div>
      {clip.error ? <div style={{ color: "var(--danger)", fontSize: 12.5 }}>{clip.error}</div> : null}
      {actions ? <div className="clip-actions">{actions}</div> : null}
    </div>
  );
}
