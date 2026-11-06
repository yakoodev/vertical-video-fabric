import { useEffect, useRef, useState } from "react";
import { type AiSegment, type ColorFix, type SpeedAudio, segLen, segSpeed } from "@/api/types";

// Инспектор куска: скорость, реверс, звук и цвет.

const SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];

export function speedBadge(seg: AiSegment): string {
  const bits = [seg.reverse ? "реверс" : "", segSpeed(seg) !== 1 ? `×${segSpeed(seg)}` : ""].filter(Boolean);
  return bits.join(" ") || "×1";
}

/** Инспектор куска: скорость, реверс и звук (POST /segment-speed = set_segment_speed агента). */
export function SegmentSpeedControl({
  segment,
  index,
  busy,
  onChange,
}: {
  segment: AiSegment;
  index: number;
  busy: boolean;
  onChange: (body: { piece: number; speed: number; reverse: boolean; audio?: SpeedAudio }) => void;
}) {
  const speed = segSpeed(segment);
  const reverse = Boolean(segment.reverse);
  const audio: SpeedAudio = segment.speed_audio ?? (reverse ? "mute" : "tempo");
  const sourceLen = segment.end_sec - segment.start_sec;
  const send = (next: { speed?: number; reverse?: boolean; audio?: SpeedAudio }) => {
    const r = next.reverse ?? reverse;
    let a: SpeedAudio | undefined = next.audio ?? audio;
    if (a === "reverse" && !r) a = undefined;
    onChange({ piece: index, speed: next.speed ?? speed, reverse: r, audio: a });
  };
  return (
    <div style={{ display: "grid", gap: 8, fontSize: 12.5 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        Скорость
        <select
          value={String(speed)}
          disabled={busy || index < 0}
          onChange={(e) => send({ speed: Number(e.target.value) })}
        >
          {SPEEDS.map((v) => (
            <option key={v} value={String(v)}>
              {v === 1 ? "×1 (как есть)" : v < 1 ? `×${v} — слоу-мо` : `×${v} — быстрее`}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }} title={sourceLen > 10 ? "Реверс — кусок до 10 с видео" : ""}>
        <input
          type="checkbox"
          checked={reverse}
          disabled={busy || index < 0 || (!reverse && sourceLen > 10)}
          onChange={(e) => send({ reverse: e.target.checked, audio: e.target.checked ? undefined : audio === "reverse" ? undefined : audio })}
        />
        Задом наперёд{sourceLen > 10 && !reverse ? " (кусок длиннее 10 с)" : ""}
      </label>
      {speed !== 1 || reverse ? (
        <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
          Звук
          <select value={audio} disabled={busy} onChange={(e) => send({ audio: e.target.value as SpeedAudio })}>
            <option value="tempo">речь без смены тона</option>
            <option value="pitch">как плёнка (тон меняется)</option>
            <option value="mute">без звука</option>
            {reverse ? <option value="reverse">задом наперёд</option> : null}
          </select>
        </label>
      ) : null}
      <p className="muted" style={{ margin: 0 }}>
        На таймлайне {segLen(segment).toFixed(1)} с (в видео {sourceLen.toFixed(1)} с). Слои и субтитры после куска
        сдвигаются сами; у реверса субтитры куска скрыты, превью играет его вперёд.
      </p>
    </div>
  );
}

const COLOR_FIELDS: { key: keyof ColorFix; label: string; min: number; max: number; step: number; neutral: number; hint: string }[] = [
  { key: "exposure", label: "Экспозиция", min: -2, max: 2, step: 0.05, neutral: 0, hint: "ступени EV: +1 — вдвое светлее" },
  { key: "contrast", label: "Контраст", min: 0.5, max: 1.5, step: 0.01, neutral: 1, hint: "1 — как есть" },
  { key: "saturation", label: "Насыщенность", min: 0, max: 2, step: 0.01, neutral: 1, hint: "0 — ч/б, 1 — как есть" },
  { key: "temperature", label: "Температура", min: -1, max: 1, step: 0.02, neutral: 0, hint: "+ теплее, − холоднее" },
  { key: "tint", label: "Оттенок", min: -1, max: 1, step: 0.02, neutral: 0, hint: "+ пурпурнее, − зеленее" },
];

export function colorBadge(seg: AiSegment): string {
  const n = Object.keys(seg.color_fix ?? {}).length;
  return n ? `${n} попр.` : "как есть";
}

/**
 * Инспектор куска: цветокоррекция (POST /piece-color = set_piece_color агента) и подгонка
 * остальных кусков к этому (POST /piece-color/match = match_piece_colors). Ползунки шлют
 * значение с задержкой — не запрос на каждый пиксель движения.
 */
export function PieceColorControl({
  segment,
  index,
  pieces,
  busy,
  onChange,
  onMatch,
}: {
  segment: AiSegment;
  index: number;
  pieces: number;
  busy: boolean;
  onChange: (body: { piece: number; reset?: boolean } & ColorFix) => void;
  onMatch: (reference: number) => void;
}) {
  const [draft, setDraft] = useState<ColorFix>(segment.color_fix ?? {});
  const timer = useRef<number | null>(null);
  useEffect(() => setDraft(segment.color_fix ?? {}), [segment.id, JSON.stringify(segment.color_fix ?? {})]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);
  const set = (key: keyof ColorFix, value: number) => {
    const next = { ...draft, [key]: value };
    setDraft(next);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => onChange({ piece: index, [key]: value }), 350);
  };
  return (
    <div style={{ display: "grid", gap: 6, fontSize: 12.5 }}>
      {COLOR_FIELDS.map((f) => {
        const value = draft[f.key] ?? f.neutral;
        return (
          <label key={f.key} className="field" title={f.hint} style={{ margin: 0 }}>
            <span>
              {f.label} — {f.key === "exposure" || f.key === "temperature" || f.key === "tint" ? (value > 0 ? "+" : "") : "×"}
              {value.toFixed(2)}
            </span>
            <input
              type="range"
              min={f.min}
              max={f.max}
              step={f.step}
              value={value}
              disabled={busy || index < 0}
              onChange={(e) => set(f.key, Number(e.target.value))}
              onDoubleClick={() => set(f.key, f.neutral)}
            />
          </label>
        );
      })}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button type="button" className="btn sm" disabled={busy || index < 0} onClick={() => onChange({ piece: index, reset: true })}>
          Сбросить
        </button>
        <button
          type="button"
          className="btn sm"
          disabled={busy || index < 0 || pieces < 2}
          title="Замерить кадры всех кусков и свести их яркость, баланс и насыщенность к этому куску"
          onClick={() => onMatch(index)}
        >
          🎨 Подогнать остальные к этому
        </button>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Поправка рендерится до лука пресета; превью — приближённо. Двойной клик по ползунку — нейтраль.
      </p>
    </div>
  );
}
