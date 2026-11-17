import type { CoverLook, CoverTextStyle } from "@/api/coverDocs";
import type { CoverFilters, CoverImageLayer, CoverShade, CoverTextLayer } from "@/motion/coverdoc";
import { ColorField, Num, Optional, Segmented } from "./CoverControls";
import type { UpdateLayer } from "./CoverInspector";

/* Фоторедактор: фильтры картинки и стили текста. Поля и границы — app/cover_doc_fx.py, отрисовка — coverfx.ts. */

type Filters = NonNullable<CoverFilters>;

const EMPTY: Filters = {
  brightness: 0, contrast: 0, saturation: 0, accent: null, blur: 0, sharpen: 0, vignette: 0, shade: [], shadeColor: "#050507",
};
const FIRE = [{ from: 10, to: 56, minSat: 0 }, { from: 344, to: 8, minSat: 0.55 }];
const ACCENT_PRESETS: [string, string, Filters["accent"]][] = [
  ["fire", "Огонь и красное", { ranges: FIRE, rest: 0.06 }],
  ["red", "Только красное", { ranges: [{ from: 340, to: 14, minSat: 0.45 }], rest: 0.06 }],
  ["blue", "Синее", { ranges: [{ from: 190, to: 250, minSat: 0.3 }], rest: 0.06 }],
  ["green", "Зелёное", { ranges: [{ from: 80, to: 160, minSat: 0.3 }], rest: 0.06 }],
];
const SIDE_NAMES: Record<CoverShade["side"], string> = { left: "Слева", right: "Справа", top: "Сверху", bottom: "Снизу" };

function Slider({ label, value, min, max, step, onChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="cov-slider">
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <output>{Math.round(value * 100) / 100}</output>
    </label>
  );
}

function accentId(accent: Filters["accent"]): string {
  if (!accent) return "";
  const hit = ACCENT_PRESETS.find(([, , a]) => JSON.stringify(a?.ranges) === JSON.stringify(accent.ranges));
  return hit ? hit[0] : "custom";
}

export function FiltersFields({ layer, update, looks }: { layer: CoverImageLayer; update: UpdateLayer; looks: CoverLook[] }) {
  const f: Filters = layer.filters ?? EMPTY;
  const set = (patch: Partial<Filters>, key?: string) => update({ filters: { ...f, ...patch } }, key ? `fx-${key}` : undefined);
  const setShade = (i: number, patch: Partial<CoverShade>, key: string) =>
    set({ shade: f.shade.map((s, j) => (j === i ? { ...s, ...patch } : s)) }, `${key}${i}`);
  return (
    <div className="cov-fx">
      <strong>Обработка кадра</strong>
      {looks.length ? (
        <div className="cov-chips" role="group" aria-label="Готовый вид">
          {looks.map((look) => (
            <button key={look.id} type="button" title={look.note} onClick={() => update({ filters: look.filters ?? null })}>
              {look.name}
            </button>
          ))}
        </div>
      ) : null}
      <Optional label="ЧБ с акцентом" on={!!f.accent} onToggle={(on) => set({ accent: on ? { ranges: FIRE, rest: 0.06 } : null })}>
        {f.accent ? (
          <>
            <div className="cov-chips" role="group" aria-label="Какой цвет оставить">
              {ACCENT_PRESETS.map(([id, name, accent]) => (
                <button key={id} type="button" className={accentId(f.accent) === id ? "on" : ""} onClick={() => set({ accent: { ...accent!, rest: f.accent!.rest } })}>
                  {name}
                </button>
              ))}
            </div>
            <Slider label="Цвет остального" value={f.accent.rest} min={0} max={1} step={0.02} onChange={(rest) => set({ accent: { ...f.accent!, rest } }, "rest")} />
          </>
        ) : null}
      </Optional>
      <Slider label="Яркость" value={f.brightness} min={-1} max={1} step={0.02} onChange={(brightness) => set({ brightness }, "bright")} />
      <Slider label="Контраст" value={f.contrast} min={-1} max={1} step={0.02} onChange={(contrast) => set({ contrast }, "contrast")} />
      <Slider label="Насыщенность" value={f.saturation} min={-1} max={1} step={0.02} onChange={(saturation) => set({ saturation }, "sat")} />
      <Slider label="Размытие" value={f.blur} min={0} max={40} step={0.5} onChange={(blur) => set({ blur }, "blur")} />
      <Slider label="Резкость" value={f.sharpen} min={0} max={3} step={0.05} onChange={(sharpen) => set({ sharpen }, "sharp")} />
      <Slider label="Виньетка" value={f.vignette} min={0} max={1} step={0.02} onChange={(vignette) => set({ vignette }, "vig")} />
      <div className="cov-fx-shades">
        <span>Затемнение под текст</span>
        {f.shade.map((s, i) => (
          <div key={i} className="cov-fx-shade">
            <Segmented label="С какой стороны" value={s.side} options={Object.entries(SIDE_NAMES) as [CoverShade["side"], string][]}
                       onChange={(side) => setShade(i, { side }, "side")} />
            <Slider label="Ширина" value={s.size} min={0.05} max={1} step={0.01} onChange={(size) => setShade(i, { size }, "size")} />
            <Slider label="Сила" value={s.strength} min={0} max={1} step={0.02} onChange={(strength) => setShade(i, { strength }, "str")} />
            <button type="button" className="btn sm ghost" onClick={() => set({ shade: f.shade.filter((_, j) => j !== i) })}>Убрать</button>
          </div>
        ))}
        {f.shade.length < 4 ? (
          <button type="button" className="btn sm" onClick={() => set({ shade: [...f.shade, { side: "left", size: 0.45, strength: 0.8, curve: 1.4, hold: 0 }] })}>
            Добавить затемнение
          </button>
        ) : null}
        {f.shade.length || f.vignette ? <ColorField label="Цвет затемнения" value={f.shadeColor} onChange={(shadeColor) => set({ shadeColor }, "shadecolor")} /> : null}
      </div>
      {layer.filters ? <button type="button" className="btn sm ghost" onClick={() => update({ filters: null })}>Сбросить обработку</button> : null}
    </div>
  );
}

/** Поля стиля при кегле 100 → под кегль слоя. */
export function scaleStyle(fields: Partial<CoverTextLayer>, size: number): Partial<CoverTextLayer> {
  const k = size / 100;
  const r = (v: number) => Math.round(v * k * 100) / 100;
  const out: Partial<CoverTextLayer> = { ...fields };
  if (typeof fields.letterSpacing === "number") out.letterSpacing = r(fields.letterSpacing);
  if (fields.stroke) out.stroke = { ...fields.stroke, width: r(fields.stroke.width) };
  if (fields.shadow) out.shadow = { ...fields.shadow, blur: r(fields.shadow.blur), x: r(fields.shadow.x), y: r(fields.shadow.y) };
  if (fields.glow) out.glow = { ...fields.glow, blur: r(fields.glow.blur) };
  if (fields.edge) out.edge = { ...fields.edge, offset: r(fields.edge.offset) };
  if (fields.underline) out.underline = { ...fields.underline, width: Math.max(1, r(fields.underline.width)), gap: r(fields.underline.gap), inset: r(fields.underline.inset) };
  delete (out as { size?: number }).size;
  return out;
}

export function TextStyleFields({ layer, update, styles }: { layer: CoverTextLayer; update: UpdateLayer; styles: CoverTextStyle[] }) {
  const edge = layer.edge ?? null;
  const line = layer.underline ?? null;
  return (
    <>
      {styles.length ? (
        <div className="field">
          <span>Готовый стиль</span>
          <div className="cov-chips" role="group" aria-label="Готовый стиль текста">
            {styles.map((st) => (
              <button key={st.id} type="button" title={st.note} onClick={() => update(scaleStyle(st.fields, layer.size))}>{st.name}</button>
            ))}
          </div>
        </div>
      ) : null}
      <Optional label="Кромка двумя цветами" on={!!edge} onToggle={(on) => update({ edge: on ? { left: "#9646ffe6", right: "#ff781ee6", offset: Math.round(layer.size * 0.037 * 10) / 10 } : null })}>
        {edge ? (
          <>
            <div className="cov-row">
              <ColorField label="Слева" value={edge.left} onChange={(left) => update({ edge: { ...edge, left } }, "edge-l")} />
              <ColorField label="Справа" value={edge.right} onChange={(right) => update({ edge: { ...edge, right } }, "edge-r")} />
            </div>
            <Num label="Сдвиг" value={edge.offset} min={0} max={200} onChange={(offset) => update({ edge: { ...edge, offset } }, "edge-o")} />
          </>
        ) : null}
      </Optional>
      <Optional label="Черта под строкой" on={!!line} onToggle={(on) => update({ underline: on ? { color: "#ff781e", width: Math.max(2, Math.round(layer.size * 0.032)), gap: Math.round(layer.size * 0.095), inset: Math.round(layer.size * 0.02) } : null })}>
        {line ? (
          <>
            <ColorField label="Цвет" value={line.color} onChange={(color) => update({ underline: { ...line, color } }, "ul-c")} />
            <div className="cov-row three">
              <Num label="Толщина" value={line.width} min={1} max={200} onChange={(width) => update({ underline: { ...line, width } }, "ul-w")} />
              <Num label="Отступ" value={line.gap} min={-400} max={400} onChange={(gap) => update({ underline: { ...line, gap } }, "ul-g")} />
              <Num label="С краёв" value={line.inset} min={-400} max={400} onChange={(inset) => update({ underline: { ...line, inset } }, "ul-i")} />
            </div>
          </>
        ) : null}
      </Optional>
    </>
  );
}
