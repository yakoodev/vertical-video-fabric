/**
 * Кривая перехода между ключами: готовые кривые + свой cubic-bezier мышью.
 *
 * Значение — строка движка: имя кривой («outBack») или «bezier(x1,y1,x2,y2)».
 * y можно тянуть за 0..1 — так получается перелёт и отскок, как в After Effects.
 */
import { useRef } from "react";
import { easingFn, type Easing } from "@/motion";

const PRESETS: { label: string; value: Easing }[] = [
  { label: "Плавно", value: "inOutCubic" },
  { label: "Выход", value: "outCubic" },
  { label: "Вход", value: "inCubic" },
  { label: "Перелёт", value: "outBack" },
  { label: "Пружина", value: "outElastic" },
  { label: "Отскок", value: "outBounce" },
  { label: "Линейно", value: "linear" },
  { label: "Скачком", value: "step" },
];

const W = 200;
const H = 140;
const PAD = 22;
// Поле графика: x 0..1 по ширине, y −0.5..1.5 по высоте (место под перелёт).
const sx = (x: number) => PAD + x * (W - PAD * 2);
const sy = (y: number) => H - PAD - ((y + 0.5) / 2) * (H - PAD * 2);
const ux = (px: number) => Math.min(1, Math.max(0, (px - PAD) / (W - PAD * 2)));
const uy = (py: number) => Math.min(1.5, Math.max(-0.5, ((H - PAD - py) / (H - PAD * 2)) * 2 - 0.5));

function parseBezier(value: string): [number, number, number, number] | null {
  const m = /^bezier\(([^)]+)\)$/.exec(value.trim());
  if (!m) return null;
  const nums = m[1].split(",").map((v) => Number(v));
  return nums.length === 4 && nums.every(Number.isFinite) ? (nums as [number, number, number, number]) : null;
}

const r2 = (v: number) => Math.round(v * 100) / 100;

export function BezierEditor({ value, onChange }: { value: Easing; onChange: (next: Easing) => void }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<1 | 2 | null>(null);
  const bz = parseBezier(value) ?? [0.25, 0.1, 0.25, 1];
  const fn = easingFn(value);
  const path = Array.from({ length: 41 }, (_, i) => {
    const x = i / 40;
    return `${i ? "L" : "M"}${sx(x).toFixed(1)},${sy(fn(x)).toFixed(1)}`;
  }).join(" ");
  const custom = Boolean(parseBezier(value));
  const point = (e: React.PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ux(((e.clientX - r.left) / r.width) * W), y: uy(((e.clientY - r.top) / r.height) * H) };
  };
  const move = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const p = point(e);
    const next: [number, number, number, number] = [...bz];
    if (drag.current === 1) [next[0], next[1]] = [r2(p.x), r2(p.y)];
    else [next[2], next[3]] = [r2(p.x), r2(p.y)];
    onChange(`bezier(${next.join(",")})` as Easing);
  };
  return (
    <div className="bz">
      <div className="bz-presets">
        {PRESETS.map((p) => (
          <button key={p.value} type="button" className={`bz-chip${value === p.value ? " on" : ""}`} onClick={() => onChange(p.value)}>
            {p.label}
          </button>
        ))}
        <button
          type="button"
          className={`bz-chip${custom ? " on" : ""}`}
          onClick={() => !custom && onChange(`bezier(${bz.join(",")})` as Easing)}
          title="Своя кривая — тяни две точки"
        >
          Своя
        </button>
      </div>
      <svg
        ref={svgRef}
        className="bz-graph"
        viewBox={`0 0 ${W} ${H}`}
        onPointerMove={move}
        onPointerUp={() => (drag.current = null)}
        onPointerLeave={() => (drag.current = null)}
      >
        <rect x={sx(0)} y={sy(1)} width={sx(1) - sx(0)} height={sy(0) - sy(1)} className="bz-box" />
        <path d={path} className="bz-curve" />
        {custom ? (
          <>
            <line x1={sx(0)} y1={sy(0)} x2={sx(bz[0])} y2={sy(bz[1])} className="bz-arm" />
            <line x1={sx(1)} y1={sy(1)} x2={sx(bz[2])} y2={sy(bz[3])} className="bz-arm" />
            {[1, 2].map((k) => (
              <circle
                key={k}
                cx={sx(k === 1 ? bz[0] : bz[2])}
                cy={sy(k === 1 ? bz[1] : bz[3])}
                r={6}
                className="bz-handle"
                onPointerDown={(e) => {
                  drag.current = k as 1 | 2;
                  (e.currentTarget.ownerSVGElement as SVGSVGElement).setPointerCapture?.(e.pointerId);
                }}
              />
            ))}
          </>
        ) : null}
      </svg>
      {custom ? <div className="bz-value mono muted">{value}</div> : null}
    </div>
  );
}
