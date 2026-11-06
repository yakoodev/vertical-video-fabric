// Поля инспектора слоя: число с черновиком, цвет, переключатель, раздел, образцы цветов.
import { useState } from "react";
import type { Layer } from "@/motion";

/** Слой нужного вида: раздел инспектора показывается только для него. */
export type LayerOf<T extends Layer["type"]> = Extract<Layer, { type: T }>;
export type PatchProps = (patch: Record<string, unknown>) => void;

export const r2 = (v: number) => Math.round(v * 100) / 100;

/**
 * Поле числа с черновиком: пока печатаешь, значение не зажимается и не
 * сохраняется (иначе «12» при минимуме 5 превращалось в 5.12, а «−» не набирался);
 * применяется на Enter или при уходе из поля.
 */
export function NumInput({
  value,
  onCommit,
  step = 1,
  min,
  max,
  className = "input",
}: {
  value: number;
  onCommit: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  className?: string;
}) {
  const shown = Number.isFinite(value) ? String(r2(value)) : "0";
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft == null) return;
    const v = Number(draft.replace(",", "."));
    setDraft(null);
    if (draft.trim() === "" || !Number.isFinite(v)) return;
    const clamped = Math.min(max ?? v, Math.max(min ?? v, v));
    if (clamped !== value) onCommit(clamped);
  };
  return (
    <input
      className={className}
      type="text"
      inputMode="decimal"
      value={draft ?? shown}
      onFocus={(e) => e.currentTarget.select()}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit();
          (e.currentTarget as HTMLInputElement).blur();
        } else if (e.key === "Escape") {
          setDraft(null);
          (e.currentTarget as HTMLInputElement).blur();
        } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const base = Number((draft ?? shown).replace(",", "."));
          const next = (Number.isFinite(base) ? base : 0) + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1);
          setDraft(null);
          onCommit(Math.min(max ?? next, Math.max(min ?? next, r2(next))));
        }
      }}
    />
  );
}

export function Num({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  return (
    <label className="li-num">
      <span>{label}</span>
      <NumInput value={value} onCommit={onChange} step={step} min={min} max={max} />
      {suffix ? <em>{suffix}</em> : null}
    </label>
  );
}

export function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="li-color" title={label}>
      <span>{label}</span>
      <input type="color" value={(value || "#ffffff").slice(0, 7)} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="switch li-switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" />
      <span>{label}</span>
    </label>
  );
}

export function Section({ title, children, open = true }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details className="li-sec" open={open}>
      <summary>{title}</summary>
      <div className="li-sec-body">{children}</div>
    </details>
  );
}

const SWATCHES = ["#ffffff", "#ffd600", "#ff3b30", "#7c5cff", "#22d3ee", "#22c55e", "#000000"];

export function Swatches({ value, onPick }: { value?: string; onPick: (c: string) => void }) {
  return (
    <div className="li-swatches">
      {SWATCHES.map((c) => (
        <button
          key={c}
          type="button"
          className={`li-swatch${value?.toLowerCase() === c ? " on" : ""}`}
          style={{ background: c }}
          onClick={() => onPick(c)}
          aria-label={c}
        />
      ))}
    </div>
  );
}
