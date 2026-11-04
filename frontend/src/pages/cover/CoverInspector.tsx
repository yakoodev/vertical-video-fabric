import { useMemo, type ReactNode } from "react";
import type { CoverFont } from "@/api/coverDocs";
import type { CoverGradient, CoverImageLayer, CoverLayer, CoverShadow, CoverShapeLayer, CoverStroke, CoverTextLayer } from "@/motion/coverdoc";

export type LayerPatch = Partial<CoverImageLayer> | Partial<CoverTextLayer> | Partial<CoverShapeLayer>;
export type UpdateLayer = (patch: LayerPatch, coalesce?: string) => void;

function Num({ label, value, onChange, min, max, step = 1 }: {
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

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
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
function Optional({ label, on, onToggle, children }: { label: string; on: boolean; onToggle: (v: boolean) => void; children: ReactNode }) {
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

function Segmented<T extends string>({ label, value, options, onChange }: {
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

function StrokeFields({ value, update }: { value: CoverStroke; update: (v: CoverStroke, key?: string) => void }) {
  return (
    <Optional label="Обводка" on={!!value} onToggle={(on) => update(on ? { color: "#000000", width: 10 } : null)}>
      {value ? (
        <div className="cov-row">
          <ColorField label="Цвет" value={value.color} onChange={(color) => update({ ...value, color }, "stroke-color")} />
          <Num label="Толщина" value={value.width} min={0} max={120} onChange={(width) => update({ ...value, width }, "stroke-width")} />
        </div>
      ) : null}
    </Optional>
  );
}

function ShadowFields({ value, update }: { value: CoverShadow; update: (v: CoverShadow, key?: string) => void }) {
  return (
    <Optional label="Тень" on={!!value} onToggle={(on) => update(on ? { color: "#000000b3", blur: 30, x: 0, y: 12 } : null)}>
      {value ? (
        <>
          <ColorField label="Цвет" value={value.color} onChange={(color) => update({ ...value, color }, "shadow-color")} />
          <div className="cov-row three">
            <Num label="Размытие" value={value.blur} min={0} max={300} onChange={(blur) => update({ ...value, blur }, "shadow-blur")} />
            <Num label="Сдвиг X" value={value.x} min={-300} max={300} onChange={(x) => update({ ...value, x }, "shadow-x")} />
            <Num label="Сдвиг Y" value={value.y} min={-300} max={300} onChange={(y) => update({ ...value, y }, "shadow-y")} />
          </div>
        </>
      ) : null}
    </Optional>
  );
}

function GradientFields({ value, update, fallback }: { value: CoverGradient; update: (v: CoverGradient, key?: string) => void; fallback: string }) {
  return (
    <Optional label="Градиент" on={!!value} onToggle={(on) => update(on ? { from: fallback.slice(0, 7), to: "#000000", angle: 90 } : null)}>
      {value ? (
        <>
          <div className="cov-row">
            <ColorField label="От" value={value.from} onChange={(from) => update({ ...value, from }, "grad-from")} />
            <ColorField label="До" value={value.to} onChange={(to) => update({ ...value, to }, "grad-to")} />
          </div>
          <Num label="Угол" value={value.angle} min={-360} max={360} onChange={(angle) => update({ ...value, angle }, "grad-angle")} />
        </>
      ) : null}
    </Optional>
  );
}

function TextFields({ layer, update, fonts, textRef }: {
  layer: CoverTextLayer;
  update: UpdateLayer;
  fonts: CoverFont[];
  textRef: React.Ref<HTMLTextAreaElement>;
}) {
  const families = useMemo(() => Array.from(new Set(fonts.map((f) => f.family))).sort((a, b) => a.localeCompare(b)), [fonts]);
  const family = layer.font || "Montserrat";
  const weights = useMemo(() => {
    const set = new Set<number>();
    for (const f of fonts) {
      if (f.family !== family) continue;
      if (f.variable) {
        const [lo, hi] = f.weight_range ?? [100, 900];
        for (let w = 100; w <= 900; w += 100) if (w >= lo && w <= hi) set.add(w);
      } else if (f.weight) {
        set.add(f.weight);
      }
    }
    if (!set.size) set.add(layer.weight);
    return Array.from(set).sort((a, b) => a - b);
  }, [fonts, family, layer.weight]);
  const plate = layer.plate;
  return (
    <>
      <label className="field">
        <span>Текст</span>
        <textarea ref={textRef} className="input cov-text" rows={3} value={layer.text} maxLength={400} onChange={(e) => update({ text: e.target.value }, "text")} />
      </label>
      <label className="field">
        <span>Шрифт</span>
        <select className="input" value={family} onChange={(e) => update({ font: e.target.value })}>
          {families.includes(family) ? null : <option value={family}>{family}</option>}
          {families.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </label>
      <div className="cov-row">
        <label className="cov-num">
          <span>Жирность</span>
          <select className="input" value={layer.weight} onChange={(e) => update({ weight: Number(e.target.value) })}>
            {weights.includes(layer.weight) ? null : <option value={layer.weight}>{layer.weight}</option>}
            {weights.map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
        </label>
        <Num label="Размер" value={layer.size} min={8} max={800} onChange={(size) => update({ size }, "size")} />
      </div>
      <Segmented label="Выравнивание" value={layer.align} options={[["left", "Слева"], ["center", "По центру"], ["right", "Справа"]]} onChange={(align) => update({ align })} />
      <div className="cov-row">
        <Num label="Межстрочный" value={layer.lineHeight} step={0.02} min={0.6} max={3} onChange={(lineHeight) => update({ lineHeight }, "line")} />
        <Num label="Трекинг" value={layer.letterSpacing} min={-40} max={200} onChange={(letterSpacing) => update({ letterSpacing }, "track")} />
      </div>
      <div className="cov-row">
        <label className="cov-check"><input type="checkbox" checked={layer.uppercase} onChange={(e) => update({ uppercase: e.target.checked })} /><span>Заглавные</span></label>
        <label className="cov-check"><input type="checkbox" checked={layer.italic} onChange={(e) => update({ italic: e.target.checked })} /><span>Курсив</span></label>
      </div>
      <ColorField label="Цвет" value={layer.color} onChange={(color) => update({ color }, "color")} />
      <GradientFields value={layer.gradient} fallback={layer.color} update={(gradient, key) => update({ gradient }, key)} />
      <StrokeFields value={layer.stroke} update={(stroke, key) => update({ stroke }, key)} />
      <ShadowFields value={layer.shadow} update={(shadow, key) => update({ shadow }, key)} />
      <Optional label="Свечение" on={!!layer.glow} onToggle={(on) => update({ glow: on ? { color: "#ffe600", blur: 40 } : null })}>
        {layer.glow ? (
          <div className="cov-row">
            <ColorField label="Цвет" value={layer.glow.color} onChange={(color) => update({ glow: { ...layer.glow!, color } }, "glow-color")} />
            <Num label="Сила" value={layer.glow.blur} min={0} max={300} onChange={(blur) => update({ glow: { ...layer.glow!, blur } }, "glow-blur")} />
          </div>
        ) : null}
      </Optional>
      <Optional
        label="Плашка"
        on={!!plate}
        onToggle={(on) => update({ plate: on ? { color: "#000000", padX: 28, padY: 10, radius: 12, perLine: true } : null })}
      >
        {plate ? (
          <>
            <ColorField label="Цвет" value={plate.color} onChange={(color) => update({ plate: { ...plate, color } }, "plate-color")} />
            <div className="cov-row three">
              <Num label="Поля X" value={plate.padX} min={0} max={300} onChange={(padX) => update({ plate: { ...plate, padX } }, "plate-x")} />
              <Num label="Поля Y" value={plate.padY} min={0} max={300} onChange={(padY) => update({ plate: { ...plate, padY } }, "plate-y")} />
              <Num label="Скругление" value={plate.radius} min={0} max={300} onChange={(radius) => update({ plate: { ...plate, radius } }, "plate-r")} />
            </div>
            <label className="cov-check"><input type="checkbox" checked={plate.perLine} onChange={(e) => update({ plate: { ...plate, perLine: e.target.checked } })} /><span>На каждую строку</span></label>
          </>
        ) : null}
      </Optional>
    </>
  );
}

function ImageFields({ layer, update, assets, assetUrl, onUpload }: {
  layer: CoverImageLayer;
  update: UpdateLayer;
  assets: string[];
  assetUrl: (name: string) => string;
  onUpload: (file: File, layerId?: string) => void;
}) {
  return (
    <>
      <div className="field">
        <span>Картинка</span>
        <div className="cov-assets small">
          {assets.map((name) => (
            <button key={name} type="button" className={name === layer.src ? "on" : ""} onClick={() => update({ src: name })} aria-label="Поставить эту картинку">
              <img src={assetUrl(name)} alt="" loading="lazy" />
            </button>
          ))}
          <label className="cov-upload">
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUpload(file, layer.id);
              e.target.value = "";
            }} />
            Загрузить
          </label>
        </div>
      </div>
      <Segmented label="Вписать" value={layer.fit} options={[["cover", "Заполнить рамку"], ["contain", "Целиком"]]} onChange={(fit) => update({ fit })} />
      <Segmented label="Форма" value={layer.mask} options={[["none", "Как есть"], ["round", "Скруглить"], ["ellipse", "Круг"]]} onChange={(mask) => update({ mask })} />
      {layer.mask === "round" ? <Num label="Скругление" value={layer.radius} min={0} max={2000} onChange={(radius) => update({ radius }, "radius")} /> : null}
      <Optional label="Вырезать часть" on={!!layer.crop} onToggle={(on) => update({ crop: on ? { x: 0.1, y: 0.1, w: 0.8, h: 0.8 } : null })}>
        {layer.crop ? (
          <div className="cov-row four">
            {(["x", "y", "w", "h"] as const).map((k) => (
              <Num
                key={k}
                label={{ x: "Слева", y: "Сверху", w: "Ширина", h: "Высота" }[k]}
                value={layer.crop![k] * 100}
                min={0}
                max={100}
                onChange={(v) => update({ crop: { ...layer.crop!, [k]: Math.min(1, Math.max(0.01, v / 100)) } }, `crop-${k}`)}
              />
            ))}
          </div>
        ) : null}
      </Optional>
      <label className="cov-check"><input type="checkbox" checked={layer.flipX} onChange={(e) => update({ flipX: e.target.checked })} /><span>Отразить</span></label>
      <StrokeFields value={layer.stroke} update={(stroke, key) => update({ stroke }, key)} />
      <ShadowFields value={layer.shadow} update={(shadow, key) => update({ shadow }, key)} />
    </>
  );
}

function ShapeFields({ layer, update }: { layer: CoverShapeLayer; update: UpdateLayer }) {
  return (
    <>
      <Segmented label="Фигура" value={layer.shape} options={[["rect", "Прямоугольник"], ["ellipse", "Эллипс"]]} onChange={(shape) => update({ shape })} />
      {layer.shape === "rect" ? <Num label="Скругление" value={layer.radius} min={0} max={2000} onChange={(radius) => update({ radius }, "radius")} /> : null}
      <Optional label="Заливка" on={!!layer.fill} onToggle={(on) => update({ fill: on ? "#ffe600" : null })}>
        {layer.fill ? <ColorField label="Цвет" value={layer.fill} onChange={(fill) => update({ fill }, "fill")} /> : null}
      </Optional>
      <GradientFields value={layer.gradient} fallback={layer.fill ?? "#7b2cff"} update={(gradient, key) => update({ gradient }, key)} />
      <StrokeFields value={layer.stroke} update={(stroke, key) => update({ stroke }, key)} />
      <ShadowFields value={layer.shadow} update={(shadow, key) => update({ shadow }, key)} />
    </>
  );
}

export function CoverInspector({ layer, update, fonts, assets, assetUrl, onUpload, textRef }: {
  layer: CoverLayer;
  update: UpdateLayer;
  fonts: CoverFont[];
  assets: string[];
  assetUrl: (name: string) => string;
  onUpload: (file: File, layerId?: string) => void;
  textRef: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <div className="cov-inspector">
      <label className="field">
        <span>Название слоя</span>
        <input className="input" value={layer.name} maxLength={60} onChange={(e) => update({ name: e.target.value }, "name")} />
      </label>
      {layer.type === "text" ? <TextFields layer={layer} update={update} fonts={fonts} textRef={textRef} /> : null}
      {layer.type === "image" ? <ImageFields layer={layer} update={update} assets={assets} assetUrl={assetUrl} onUpload={onUpload} /> : null}
      {layer.type === "shape" ? <ShapeFields layer={layer} update={update} /> : null}
      <div className="cov-row four">
        <Num label="X" value={layer.x} onChange={(x) => update({ x }, "x")} />
        <Num label="Y" value={layer.y} onChange={(y) => update({ y }, "y")} />
        <Num label="Ширина" value={layer.w} min={1} onChange={(w) => update({ w }, "w")} />
        <Num label="Высота" value={layer.h} min={1} onChange={(h) => update({ h }, "h")} />
      </div>
      <div className="cov-row">
        <Num label="Поворот" value={layer.rotation} min={-360} max={360} onChange={(rotation) => update({ rotation }, "rotation")} />
        <Num label="Непрозрачность, %" value={layer.opacity * 100} min={0} max={100} onChange={(v) => update({ opacity: Math.min(1, Math.max(0, v / 100)) }, "opacity")} />
      </div>
    </div>
  );
}
