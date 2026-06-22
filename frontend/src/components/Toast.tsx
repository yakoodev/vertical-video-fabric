import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

type ToastKind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  dedupeKey?: string;
}

interface ToastApi {
  push: (message: string, kind?: ToastKind, dedupeKey?: string) => void;
  success: (message: string, dedupeKey?: string) => void;
  error: (message: string, dedupeKey?: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

// errors stay until dismissed; everything else auto-clears.
const AUTO_DISMISS_MS = 4500;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const seenKeys = useRef<Set<string>>(new Set());

  const remove = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (message: string, kind: ToastKind = "info", dedupeKey?: string) => {
      if (dedupeKey) {
        if (seenKeys.current.has(dedupeKey)) return;
        seenKeys.current.add(dedupeKey);
      }
      const id = (seq.current += 1);
      setToasts((list) => [...list, { id, kind, message, dedupeKey }]);
      if (kind !== "error") {
        window.setTimeout(() => remove(id), AUTO_DISMISS_MS);
      }
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (m, k) => push(m, "success", k),
      error: (m, k) => push(m, "error", k),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} onClick={() => remove(t.id)} role="status">
            <span className="toast-ico">{t.kind === "success" ? "✓" : t.kind === "error" ? "✕" : "•"}</span>
            <span>{t.message}</span>
            <button className="toast-close" onClick={() => remove(t.id)} aria-label="Закрыть">
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
