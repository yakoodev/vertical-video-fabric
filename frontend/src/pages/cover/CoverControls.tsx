import type { ReactNode } from "react";

/* Общие поля инспектора: число, цвет с прозрачностью, необязательный блок, переключатель. */

export function Num({ label, value, onChange, min, max, step = 1 }: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="cov-num">
      <span>{label}</span>
      <input
        className="input"
        type="number"
        inputMode="decimal"
        value={Math.round(value * 100) / 100}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (e.target.value !== "" && Number.isFinite(v)) onChange(v);
        }}
      />
    </label>
  );
}

const hexPart = (color: string) => color.slice(0, 7);
const alphaOf = (color: string) => (color.length === 9 ? parseInt(color.slice(7), 16) / 255 : 1);
const withAlpha = (hex: string, alpha: number) =>
  alpha >= 1 ? hex : `${hex}${Math.round(alpha * 255).toString(16).padStart(2, "0")}`;

export function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  const alpha = alphaOf(value);
  return (
    <div className="cov-color">
      <span>{label}</span>
      <input type="color" aria-label={label} value={hexPart(value)} onChange={(e) => onChange(withAlpha(e.target.value, alpha))} />
      <input
        type="range"
        aria-label={`${label}: непрозрачность`}
        min={0}
        max={1}
        step={0.02}
        value={alpha}
        onChange={(e) => onChange(withAlpha(hexPart(value), Number(e.target.value)))}
      />
    </div>
  );
}

/** Необязательное свойство: галочка включает, поля появляются только когда включено. */
export function Optional({ label, on, onToggle, children }: { label: string; on: boolean; onToggle: (v: boolean) => void; children: ReactNode }) {
  return (
    <div className="cov-opt">
      <label className="cov-check">
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} />
        <span>{label}</span>
      </label>
      {on ? <div className="cov-opt-body">{children}</div> : null}
    </div>
  );
}

export function Segmented<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
}) {
  return (
    <div className="cov-seg" role="group" aria-label={label}>
      {options.map(([id, name]) => (
        <button key={id} type="button" className={value === id ? "on" : ""} aria-pressed={value === id} onClick={() => onChange(id)}>
          {name}
        </button>
      ))}
    </div>
  );
}
