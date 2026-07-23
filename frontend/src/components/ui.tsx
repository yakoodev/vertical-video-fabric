import type { ReactNode } from "react";
import { ApiError } from "@/api/client";

export function PageHead({ title, sub, actions }: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub ? <p className="sub">{sub}</p> : null}
      </div>
      {actions ? <div style={{ display: "flex", gap: 10 }}>{actions}</div> : null}
    </header>
  );
}

export function Loading({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div className="loading-row">
      <span className="spinner" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : "Что-то пошло не так";
  return (
    <div className="empty">
      <strong style={{ color: "var(--danger)" }}>{message}</strong>
      {onRetry ? (
        <button className="btn ghost sm" onClick={onRetry}>
          Повторить
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ icon = "📭", title, hint }: { icon?: string; title: string; hint?: string }) {
  return (
    <div className="empty">
      <div style={{ fontSize: 40 }}>{icon}</div>
      <strong>{title}</strong>
      {hint ? <span className="muted">{hint}</span> : null}
    </div>
  );
}

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  return (
    <span className={`badge ${status}`}>
      <span className="dot" />
      {children ?? status}
    </span>
  );
}

// Russian pluralization: plural(2, "клип", "клипа", "клипов") -> "2 клипа".
export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n) % 100;
  const d = abs % 10;
  const word = abs > 10 && abs < 20 ? many : d === 1 ? one : d >= 2 && d <= 4 ? few : many;
  return `${n} ${word}`;
}

export function formatDuration(sec: number): string {
  if (!sec || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}
