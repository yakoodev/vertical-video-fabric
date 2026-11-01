/**
 * Детали правой панели редактора (инспектора): вкладки, сворачиваемые группы и «Ещё…».
 *
 * Свёрнуто/развёрнуто и выбранная вкладка запоминаются в localStorage этого браузера —
 * у каждого своё. Хранилище может быть недоступно (приватное окно, запрет сайта):
 * тогда всё просто работает без памяти.
 */
import { useEffect, useState, type ReactNode } from "react";

export function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* приватное окно — просто не запоминаем */
  }
}

/** Свёрнутость, которую помнит браузер; fallback — пока человек её не трогал. */
function usePersistedOpen(key: string, fallback: boolean): [boolean, (next: boolean) => void] {
  const [open, setOpen] = useState(() => {
    const saved = readPref(key);
    return saved == null ? fallback : saved === "1";
  });
  const set = (next: boolean) => {
    setOpen(next);
    writePref(key, next ? "1" : "0");
  };
  return [open, set];
}

/**
 * Сворачиваемая группа инспектора. `forceOpen` — раскрыть, когда это стало нужно
 * (выбрали на таймлайне отрезок тишины или ключ камеры), даже если её свернули.
 */
export function Group({
  title,
  badge,
  defaultOpen = false,
  forceOpen = false,
  children,
}: {
  title: string;
  badge?: string;
  defaultOpen?: boolean;
  forceOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = usePersistedOpen(`vvf.ed.group.${title}`, defaultOpen);
  useEffect(() => {
    if (forceOpen && !open) setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceOpen]);
  return (
    <div className={`rgroup${open ? " open" : ""}`}>
      <button className="rgroup-head" onClick={() => setOpen(!open)} type="button" aria-expanded={open}>
        <span className="rgroup-caret" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span className="rgroup-title">{title}</span>
        {badge ? <span className="rgroup-badge">{badge}</span> : null}
      </button>
      {open ? <div className="rgroup-body">{children}</div> : null}
    </div>
  );
}

/** «Ещё…» — редкие настройки под одной строкой, раскрываются по клику. */
export function MoreSection({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = usePersistedOpen(`vvf.ed.more.${id}`, false);
  return (
    <div className={`insp-more${open ? " open" : ""}`}>
      <button type="button" className="insp-more-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="insp-more-label">
          {open ? "Скрыть" : label}
          {open ? null : "…"}
        </span>
        {hint && !open ? <span className="insp-more-hint">{hint}</span> : null}
        <span className="rgroup-caret" aria-hidden>
          {open ? "▴" : "▾"}
        </span>
      </button>
      {open ? <div className="insp-more-body">{children}</div> : null}
    </div>
  );
}

export type InspTabDef<K extends string> = { key: K; label: string; count?: number; hint: string };

/** Вкладки инспектора: одна строка, без иконок, счётчик — сколько там всего. */
export function InspectorTabs<K extends string>({
  tabs,
  active,
  onPick,
}: {
  tabs: InspTabDef<K>[];
  active: K;
  onPick: (key: K) => void;
}) {
  return (
    <div className="insp-tabs" role="tablist" aria-label="Панель клипа">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={active === t.key}
          className={`insp-tab${active === t.key ? " active" : ""}`}
          title={t.hint}
          onClick={() => onPick(t.key)}
        >
          {t.label}
          {t.count ? <span className="insp-tab-count">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
