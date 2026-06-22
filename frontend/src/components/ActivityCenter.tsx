import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { isActive, useActiveTasks } from "@/hooks/useActiveTasks";
import type { ActiveTask } from "@/api/types";
import { Badge } from "@/components/ui";

const DISMISS_KEY = "vvf-dismissed-tasks";
const KIND_RU: Record<string, string> = { job: "Публикация", clip: "Рендер", analysis: "Анализ" };

function fmtAgo(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z");
  const sec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (sec < 60) return `${sec}с назад`;
  if (sec < 3600) return `${Math.floor(sec / 60)}м назад`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}ч назад`;
  return d.toLocaleDateString("ru-RU");
}

function taskLink(t: ActiveTask): string | null {
  if (t.kind === "job") return `/publications/${t.id}`;
  if (t.source_id) return `/projects/${t.source_id}`;
  return null;
}

function loadDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(window.localStorage.getItem(DISMISS_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

export function ActivityCenter() {
  const query = useActiveTasks();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(loadDismissed);

  useEffect(() => {
    window.localStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissed]));
  }, [dismissed]);

  const tasks = query.data ?? [];
  const activeCount = useMemo(() => tasks.filter((t) => isActive(t.status)).length, [tasks]);

  // Visible = all active tasks + terminal tasks the user hasn't dismissed (keyed
  // by updated_at so a status change re-surfaces them).
  const visible = useMemo(
    () => tasks.filter((t) => isActive(t.status) || !dismissed.has(`${t.kind}:${t.id}:${t.updated_at}`)),
    [tasks, dismissed],
  );

  const dismiss = (t: ActiveTask) =>
    setDismissed((prev) => new Set(prev).add(`${t.kind}:${t.id}:${t.updated_at}`));
  const dismissAllTerminal = () =>
    setDismissed((prev) => {
      const next = new Set(prev);
      tasks.filter((t) => !isActive(t.status)).forEach((t) => next.add(`${t.kind}:${t.id}:${t.updated_at}`));
      return next;
    });

  return (
    <div className="activity">
      <button className="activity-bell" onClick={() => setOpen((v) => !v)} aria-label="Уведомления">
        🔔
        {activeCount > 0 ? <span className="activity-badge">{activeCount}</span> : null}
      </button>
      {open ? (
        <>
          <div className="activity-backdrop" onClick={() => setOpen(false)} />
          <div className="activity-panel">
            <div className="activity-head">
              <strong>Активность</strong>
              {visible.some((t) => !isActive(t.status)) ? (
                <button className="btn ghost sm" onClick={dismissAllTerminal}>
                  Очистить
                </button>
              ) : null}
            </div>
            {!visible.length ? (
              <div className="muted" style={{ padding: "18px 4px", textAlign: "center" }}>
                Нет активных задач
              </div>
            ) : (
              <div className="activity-list">
                {visible.map((t) => {
                  const link = taskLink(t);
                  return (
                    <div key={`${t.kind}:${t.id}:${t.updated_at}`} className="activity-item">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <strong style={{ fontSize: 13 }}>
                          {KIND_RU[t.kind] ?? t.kind} #{t.id}
                        </strong>
                        <Badge status={t.status} />
                      </div>
                      {t.label ? <div className="muted" style={{ fontSize: 12 }}>{t.label}</div> : null}
                      {t.error ? <div style={{ color: "var(--danger)", fontSize: 12 }}>{t.error}</div> : null}
                      <div className="activity-foot mono">
                        <span>{fmtAgo(t.updated_at)}</span>
                        {link ? (
                          <Link to={link} onClick={() => setOpen(false)} className="task-link">
                            открыть →
                          </Link>
                        ) : null}
                      </div>
                      {!isActive(t.status) ? (
                        <button className="activity-x" onClick={() => dismiss(t)} aria-label="Скрыть">
                          ×
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
            <Link to="/tasks" onClick={() => setOpen(false)} className="activity-all">
              Все задачи →
            </Link>
          </div>
        </>
      ) : null}
    </div>
  );
}
