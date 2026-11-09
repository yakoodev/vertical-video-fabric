import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clipGroupKeys } from "@/api/clipGroups";
import { clipMarksApi, pubLine, type ClipPubBrief } from "@/api/clipMarks";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";

/**
 * Заметка и «опубликовано ×N» у плитки клипа (app/clip_marks.py).
 * Владелец выкладывает сам и с телефона: список публикаций открывается нажатием, не только наведением.
 */

/** После правки заметки или публикации: сетки, экран клипа, готовые файлы, статистика проекта. */
export function useMarksRefresh() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: clipGroupKeys.all });
    void qc.invalidateQueries({ queryKey: clipGroupKeys.anyView });
    void qc.invalidateQueries({ queryKey: qk.clips() });
    void qc.invalidateQueries({ queryKey: ["channel-projects"] });
  };
}

/** Чип «опубликовано ×N»: наведение — подсказка, нажатие — список (площадка · аккаунт · дата). */
export function PubBadge({ count, pubs }: { count: number; pubs: ClipPubBrief[] }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);
  if (!count) return null;
  const lines = pubs.map(pubLine);
  return (
    <span className="pubb">
      <button
        type="button"
        className="cchip ok pubb-chip"
        title={lines.join("\n")}
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        опубликовано ×{count}
      </button>
      {open ? (
        <span className="pubb-pop" role="dialog" aria-label="Где выложен" onClick={(e) => e.stopPropagation()}>
          {pubs.map((p) => (
            <span key={`${p.kind}-${p.id}`} className="pubb-row">
              {p.url ? (
                <a href={p.url} target="_blank" rel="noreferrer">{pubLine(p)}</a>
              ) : (
                <span>{pubLine(p)}</span>
              )}
              {p.kind === "service" ? <span className="muted"> · сервисом</span> : null}
            </span>
          ))}
        </span>
      ) : null}
    </span>
  );
}

/** Поле заметки: сохраняется, когда уходишь с поля (или Ctrl+Enter); Esc — отменить. */
export function NoteEditor({
  planId,
  text,
  rows = 3,
  autoFocus = false,
  onDone,
}: {
  planId: number;
  text: string;
  rows?: number;
  autoFocus?: boolean;
  onDone?: () => void;
}) {
  const toast = useToast();
  const refresh = useMarksRefresh();
  const [value, setValue] = useState(text);
  const [saved, setSaved] = useState(false);
  const known = useRef(text);
  const cancelled = useRef(false);
  // Заметка сменилась на сервере (другая вкладка, агент) — перечитать, если её сейчас не правят.
  useEffect(() => {
    if (value === known.current) setValue(text);
    known.current = text;
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (next: string) => clipMarksApi.saveNote(planId, next),
    onSuccess: (note) => {
      known.current = note.text;
      setSaved(true);
      refresh();
      onDone?.();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить заметку"),
  });
  const dirty = value.trim() !== text.trim();
  const commit = () => {
    if (cancelled.current) return;
    if (dirty) save.mutate(value);
    else onDone?.();
  };
  return (
    <div className="cnote-ed">
      <textarea
        className="input"
        rows={rows}
        value={value}
        autoFocus={autoFocus}
        placeholder="Например: выложил в TikTok с телефона, в Shorts — завтра"
        aria-label="Заметка к клипу"
        onChange={(e) => {
          setValue(e.target.value);
          setSaved(false);
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) (e.target as HTMLTextAreaElement).blur();
          if (e.key === "Escape") {
            cancelled.current = true;
            setValue(text);
            onDone?.();
          }
        }}
      />
      <span className="muted cnote-state" role="status" aria-live="polite">
        {save.isPending ? "Сохраняю…" : dirty ? "Сохранится, когда уйдёте с поля" : saved ? "Сохранено" : ""}
      </span>
    </div>
  );
}

/** Заметка на плитке: две строки текста, нажатие — правка на месте. */
export function TileNote({ planId, text, editing, onEditing }: { planId: number; text: string; editing: boolean; onEditing: (on: boolean) => void }) {
  if (editing) return <NoteEditor planId={planId} text={text} rows={3} autoFocus onDone={() => onEditing(false)} />;
  if (!text.trim()) return null;
  return (
    <button type="button" className="ctile-note" title={`${text}\n\nНажмите, чтобы поправить`} onClick={() => onEditing(true)}>
      <span>{text}</span>
    </button>
  );
}
