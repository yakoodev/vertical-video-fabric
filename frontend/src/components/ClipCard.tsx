import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { Clip } from "@/api/types";
import { clipsApi } from "@/api/clips";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Badge, formatDuration } from "@/components/ui";

export function ClipCard({
  clip,
  actions,
  selected,
  onSelect,
}: {
  clip: Clip;
  actions?: ReactNode;
  /** Batch selection (publish several): undefined = card is not selectable. */
  selected?: boolean;
  onSelect?: (next: boolean) => void;
}) {
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
    <div className={`panel clip-card${selected ? " clip-card--selected" : ""}`}>
      <div className="clip-video">
        {/* The cover (if the clip has one) instead of a black first frame. */}
        <video
          src={`/media/clips/${clip.id}`}
          poster={clip.cover_url ?? undefined}
          controls
          preload="metadata"
          playsInline
        />
        <span className="clip-ov clip-ov--status">
          <Badge status={clip.status} />
        </span>
        {onSelect ? (
          <label className="clip-ov clip-ov--select" title="Выбрать для публикации пачкой">
            <input type="checkbox" checked={Boolean(selected)} onChange={(e) => onSelect(e.target.checked)} />
          </label>
        ) : null}
        {clip.published_targets_count ? (
          <span className="clip-ov clip-ov--pub">опубликовано: {clip.published_targets_count}</span>
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
      <div className="muted mono" style={{ fontSize: 12, display: "flex", gap: 6 }}>
        <span>{formatDuration(clip.duration_sec)}</span>
        {clip.width ? <span>· {clip.width}×{clip.height}</span> : null}
      </div>
      {clip.qc ? (
        <div
          className={`clip-qc${clip.qc.ok ? " clip-qc--ok" : " clip-qc--warn"}`}
          title={clip.qc.issues.length ? clip.qc.issues.map((i) => i.text).join("\n") : "Замечаний нет"}
        >
          {clip.qc.ok
            ? "✓ качество в порядке"
            : `⚠ ${clip.qc.issues.filter((i) => i.level === "warn").length} замечан.: ${clip.qc.issues.find((i) => i.level === "warn")?.text ?? ""}`}
          {clip.qc.loudness_lufs != null ? <span className="mono"> · {clip.qc.loudness_lufs.toFixed(0)} LUFS</span> : null}
        </div>
      ) : null}
      {clip.error ? <div style={{ color: "var(--danger)", fontSize: 12.5 }}>{clip.error}</div> : null}
      {actions ? <div className="clip-actions">{actions}</div> : null}
    </div>
  );
}
