import { useState } from "react";
import { type FfmpegPreset } from "@/api/assets";
import { lookFilterCss } from "@/pages/workspace/preview/frame";

// Visual picker: one thumbnail per look preset, rendered from a real source frame
// with the preset's CSS approximation applied — pick a look by eye, not by name.
export function LookPicker({
  frames,
  presets,
  value,
  onPick,
}: {
  frames: string[];
  presets: FfmpegPreset[];
  value: number;
  onPick: (id: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!frames.length || !presets.length) return null;
  const items = [{ id: 0, label: "По умолчанию" } as Pick<FfmpegPreset, "id" | "label">, ...presets];
  const hovered = hover === null ? undefined : presets.find((x) => x.id === hover);
  const hoveredLabel = hover === null ? "" : items.find((i) => i.id === hover)?.label ?? "";
  const hoverCss = lookFilterCss(hovered);
  return (
    <div className="look-wrap" onMouseLeave={() => setHover(null)}>
      <div className="look-strip">
        {items.map((p) => {
          const preset = presets.find((x) => x.id === p.id);
          const css = lookFilterCss(preset);
          return (
            <button
              key={p.id}
              className={`look-item${value === p.id ? " active" : ""}`}
              onClick={() => onPick(p.id)}
              onMouseEnter={() => setHover(p.id)}
              onFocus={() => setHover(p.id)}
              title={`${p.label} — наведите, чтобы рассмотреть`}
            >
              <span className="look-thumb">
                <img src={frames[0]} alt="" style={css ? { filter: css } : undefined} />
                {preset?.vignette ? (
                  <span className="look-vig" style={{ opacity: Math.min(1, preset.vignette) }} />
                ) : null}
              </span>
              <span className="look-name">{p.label}</span>
            </button>
          );
        })}
      </div>
      {hover !== null ? (
        // Bigger, multi-frame look-through: one frame can lie (a dark shot hides a
        // colour grade), so show several moments of the actual video.
        <div className="look-pop">
          <div className="look-pop-head">{hoveredLabel}</div>
          <div className="look-pop-frames">
            {frames.map((f) => (
              <span key={f} className="look-pop-frame">
                <img src={f} alt="" style={hoverCss ? { filter: hoverCss } : undefined} />
                {hovered?.vignette ? (
                  <span className="look-vig" style={{ opacity: Math.min(1, hovered.vignette) }} />
                ) : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
