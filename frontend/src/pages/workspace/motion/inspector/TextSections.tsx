// Разделы инспектора: текст с подсветкой слов, счётчик, эмодзи.
import type { Highlight } from "@/motion";
import type { MotionFont } from "@/api/motion";
import { Color, Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";
import { TextStyleFields } from "./TextStyleFields";

/**
 * Подсветка слов титра: клик по слову — в подсветку и обратно; вид — плашка или маркер,
 * «Прорисовка» — за сколько секунд плашка/маркер ложатся слева направо (0 — сразу).
 */
function HighlightFields({ highlight, text, onChange }: { highlight: Highlight | null; text: string; onChange: (h: Highlight | null) => void }) {
  const words = text.split(/\s+/).filter(Boolean).slice(0, 100);
  const on = new Set(highlight?.words ?? []);
  const marker = highlight?.style === "marker";
  const toggle = (i: number) => {
    const next = on.has(i) ? [...on].filter((x) => x !== i) : [...on, i].sort((a, b) => a - b);
    onChange(next.length ? { color: "#ffd400", bg: null, ...highlight, words: next } : null);
  };
  return (
    <>
      <span className="li-cap">Подсветка слов</span>
      <div className="li-words-list">
        {words.map((w, i) => (
          <button key={i} type="button" className={`li-word${on.has(i) ? " on" : ""}`} onClick={() => toggle(i)}>
            {w}
          </button>
        ))}
      </div>
      {highlight ? (
        <div className="li-row">
          <div className="seg" role="radiogroup" aria-label="Вид подсветки">
            {(["color", "plate", "marker"] as const).map((v) => {
              const active = v === "marker" ? marker : v === "plate" ? !marker && Boolean(highlight.bg) : !marker && !highlight.bg;
              return (
                <button
                  key={v}
                  type="button"
                  className={`seg-item${active ? " active" : ""}`}
                  onClick={() =>
                    onChange(
                      v === "marker"
                        ? { ...highlight, style: "marker", bg: highlight.bg ?? "#ffd400", color: highlight.color === "#ffd400" ? undefined : highlight.color }
                        : v === "plate"
                          ? { ...highlight, style: undefined, bg: highlight.bg ?? "#7c5cff" }
                          : { ...highlight, style: undefined, bg: null, color: highlight.color ?? "#ffd400" },
                    )
                  }
                >
                  {v === "marker" ? "Маркер" : v === "plate" ? "Плашка" : "Цвет"}
                </button>
              );
            })}
          </div>
          {highlight.bg ? <Color label={marker ? "Маркер" : "Плашка"} value={highlight.bg} onChange={(bg) => onChange({ ...highlight, bg })} /> : null}
          <Color label="Буквы" value={highlight.color ?? "#ffffff"} onChange={(color) => onChange({ ...highlight, color })} />
          {highlight.bg ? (
            <Num
              label="Прорисовка, с"
              value={highlight.draw ?? (marker ? 0.45 : 0)}
              min={0}
              max={3}
              step={0.05}
              onChange={(draw) => onChange({ ...highlight, draw })}
            />
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function TextSection({ layer, fonts, refWidth, patchProps }: { layer: LayerOf<"text">; fonts: MotionFont[]; refWidth: number; patchProps: PatchProps }) {
  return (
    <Section title="Текст">
      <textarea
        className="input li-text"
        rows={3}
        value={layer.props.text}
        onChange={(e) => patchProps({ text: e.target.value })}
        placeholder="Текст титра"
      />
      <TextStyleFields style={layer.props} fonts={fonts} onPatch={patchProps} refWidth={refWidth} />
      <Toggle
        label="Затемнение за текстом"
        checked={Boolean(layer.props.backdrop)}
        onChange={(on) => patchProps({ backdrop: on ? { color: "#000000", opacity: 0.55 } : null })}
      />
      {layer.props.backdrop ? (
        <div className="li-row">
          <Num label="Плотность" value={layer.props.backdrop.opacity ?? 0.55} min={0} max={1} step={0.05} onChange={(opacity) => patchProps({ backdrop: { ...layer.props.backdrop, opacity } })} />
          <Num label="Поля" value={layer.props.backdrop.spread ?? Math.round((layer.props.size ?? 96) * 0.9)} min={0} max={800} step={10} onChange={(spread) => patchProps({ backdrop: { ...layer.props.backdrop, spread } })} />
        </div>
      ) : null}
      <HighlightFields
        highlight={layer.props.highlight ?? null}
        text={layer.props.text}
        onChange={(highlight) => patchProps({ highlight })}
      />
    </Section>
  );
}

export function CounterSection({ layer, fonts, refWidth, patchProps }: { layer: LayerOf<"counter">; fonts: MotionFont[]; refWidth: number; patchProps: PatchProps }) {
  return (
    <Section title="Счётчик">
      <div className="li-row">
        <Num label="От" value={layer.props.from ?? 0} onChange={(from) => patchProps({ from })} />
        <Num label="До" value={layer.props.to ?? 100} onChange={(to) => patchProps({ to })} />
        <Num label="Знаков" value={layer.props.decimals ?? 0} min={0} max={3} onChange={(decimals) => patchProps({ decimals })} />
      </div>
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Перед</span>
          <input className="input" value={layer.props.prefix ?? ""} onChange={(e) => patchProps({ prefix: e.target.value })} />
        </label>
        <label className="li-grow">
          <span className="li-cap">После</span>
          <input className="input" value={layer.props.suffix ?? ""} onChange={(e) => patchProps({ suffix: e.target.value })} />
        </label>
      </div>
      <TextStyleFields style={layer.props} fonts={fonts} onPatch={patchProps} refWidth={refWidth} />
    </Section>
  );
}

export function EmojiSection({ layer, patchProps }: { layer: LayerOf<"emoji">; patchProps: PatchProps }) {
  return (
    <Section title="Эмодзи">
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Символ</span>
          <input className="input li-emoji" value={layer.props.char} onChange={(e) => patchProps({ char: e.target.value })} />
        </label>
        <Num label="Размер" value={layer.props.size ?? 220} min={20} max={1200} step={10} onChange={(size) => patchProps({ size })} />
      </div>
    </Section>
  );
}
