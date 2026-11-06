// Разделы инспектора: время слоя, появление и исчезание, реакция на звук, тень и наложение.
import type { InOutType, Layer, LoopType, ReactProp, ReactSource } from "@/motion";
import { Color, Num, r2, Section, Toggle } from "./fields";

const IN_TYPES: { v: InOutType; label: string }[] = [
  { v: "none", label: "Нет" },
  { v: "fade", label: "Затухание" },
  { v: "pop", label: "Поп" },
  { v: "zoom", label: "Зум" },
  { v: "slideUp", label: "Снизу" },
  { v: "slideDown", label: "Сверху" },
  { v: "slideLeft", label: "Справа" },
  { v: "slideRight", label: "Слева" },
  { v: "drop", label: "Падение" },
  { v: "spin", label: "Вращение" },
  { v: "blur", label: "Размытие" },
  { v: "wipe", label: "Шторка" },
  { v: "typewriter", label: "Печатная машинка" },
  { v: "words", label: "По словам" },
  { v: "rise", label: "Из-под маски" },
  { v: "draw", label: "Прорисовка" },
  { v: "glitch", label: "Глитч (текст)" },
  { v: "scramble", label: "Расшифровка (текст)" },
  { v: "stomp", label: "Удар" },
];
const LOOP_TYPES: { v: LoopType; label: string }[] = [
  { v: "none", label: "Нет" },
  { v: "pulse", label: "Пульс" },
  { v: "float", label: "Парение" },
  { v: "shake", label: "Тряска" },
  { v: "wiggle", label: "Покачивание" },
  { v: "spin", label: "Вращение" },
  { v: "blink", label: "Мигание" },
  { v: "glitch", label: "Глитч (текст)" },
  { v: "wave", label: "Волна букв (текст)" },
];
/** У эффекта кадра — только проявление: остальное к нему не применимо. */
const FX_IN_TYPES = IN_TYPES.filter((o) => o.v === "none" || o.v === "fade");
const REACT_SOURCES_UI: { v: ReactSource; label: string }[] = [
  { v: "voice", label: "Голос" },
  { v: "music", label: "Музыка" },
  { v: "mix", label: "Весь звук" },
];
const REACT_PROPS_UI: { v: ReactProp; label: string }[] = [
  { v: "scale", label: "Масштаб" },
  { v: "glow", label: "Свечение" },
  { v: "opacity", label: "Прозрачность" },
  { v: "y", label: "Подскок" },
];

export function TimeSection({
  layer,
  t,
  clipDuration,
  onChange,
  onSeek,
}: {
  layer: Layer;
  t: number;
  clipDuration: number;
  onChange: (next: Layer) => void;
  onSeek: (clipSec: number) => void;
}) {
  return (
    <Section title="Время">
      <div className="li-row">
        <Num
          label="Начало"
          value={layer.start}
          step={0.1}
          min={0}
          max={Math.max(0, layer.end - 0.1)}
          onChange={(start) => onChange({ ...layer, start: r2(start) })}
        />
        <Num
          label="Конец"
          value={layer.end}
          step={0.1}
          min={layer.start + 0.1}
          max={clipDuration || undefined}
          onChange={(end) => onChange({ ...layer, end: r2(end) })}
        />
        <em className="muted li-dur">{r2(layer.end - layer.start)} с</em>
      </div>
      <div className="li-row">
        <button
          type="button"
          className="btn ghost sm"
          onClick={() => {
            const len = layer.end - layer.start;
            onChange({ ...layer, start: r2(t), end: r2(Math.min(clipDuration || t + len, t + len)) });
          }}
        >
          ⇥ Начать с курсора
        </button>
        <button type="button" className="btn ghost sm" onClick={() => t > layer.start && onChange({ ...layer, end: r2(t) })}>
          ⇤ Закончить на курсоре
        </button>
        <button type="button" className="btn ghost sm" onClick={() => onSeek(layer.start + 0.05)}>
          ▶ К слою
        </button>
      </div>
    </Section>
  );
}

export function AnimationSection({ layer, onChange }: { layer: Exclude<Layer, { type: "audio" }>; onChange: (next: Layer) => void }) {
  return (
    <Section title="Анимация">
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Появление</span>
          <select
            className="input"
            value={layer.in?.type ?? "none"}
            onChange={(e) => onChange({ ...layer, in: { type: e.target.value as InOutType, duration: layer.in?.duration ?? 0.35 } })}
          >
            {(layer.type === "fx" ? FX_IN_TYPES : IN_TYPES).map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <Num
          label="с"
          value={layer.in?.duration ?? 0.35}
          step={0.05}
          min={0.05}
          max={3}
          onChange={(duration) => onChange({ ...layer, in: { type: layer.in?.type ?? "fade", duration } })}
        />
      </div>
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Исчезание</span>
          <select
            className="input"
            value={layer.out?.type ?? "none"}
            onChange={(e) => onChange({ ...layer, out: { type: e.target.value as InOutType, duration: layer.out?.duration ?? 0.25 } })}
          >
            {(layer.type === "fx" ? FX_IN_TYPES : IN_TYPES).map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <Num
          label="с"
          value={layer.out?.duration ?? 0.25}
          step={0.05}
          min={0.05}
          max={3}
          onChange={(duration) => onChange({ ...layer, out: { type: layer.out?.type ?? "fade", duration } })}
        />
      </div>
      {layer.type !== "fx" ? (
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Пока виден</span>
          <select
            className="input"
            value={layer.loop?.type ?? "none"}
            onChange={(e) => onChange({ ...layer, loop: { type: e.target.value as LoopType, speed: layer.loop?.speed ?? 1, amount: layer.loop?.amount ?? 1 } })}
          >
            {LOOP_TYPES.map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {layer.loop && layer.loop.type !== "none" ? (
          <>
            <Num label="Скорость" value={layer.loop.speed ?? 1} step={0.1} min={0.1} max={5} onChange={(speed) => onChange({ ...layer, loop: { ...layer.loop!, speed } })} />
            <Num label="Сила" value={layer.loop.amount ?? 1} step={0.1} min={0} max={3} onChange={(amount) => onChange({ ...layer, loop: { ...layer.loop!, amount } })} />
          </>
        ) : null}
      </div>
      ) : null}
      <div className="li-row" title="Слой дышит громкостью клипа: огибающую считает сервер, превью и рендер совпадают. У эффекта кадра «Масштаб» — его сила">
        <label className="li-grow">
          <span className="li-cap">От звука</span>
          <select
            className="input"
            value={layer.react ? layer.react.source ?? "voice" : ""}
            onChange={(e) =>
              onChange({
                ...layer,
                react: e.target.value ? { prop: "scale", amount: 1, smooth: 0.15, ...layer.react, source: e.target.value as ReactSource } : null,
              })
            }
          >
            <option value="">Нет</option>
            {REACT_SOURCES_UI.map((o) => (
              <option key={o.v} value={o.v}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {layer.react ? (
          <>
            <label>
              <span className="li-cap">Что</span>
              <select className="input" value={layer.react.prop ?? "scale"} onChange={(e) => onChange({ ...layer, react: { ...layer.react!, prop: e.target.value as ReactProp } })}>
                {REACT_PROPS_UI.map((o) => (
                  <option key={o.v} value={o.v}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <Num label="Сила" value={layer.react.amount ?? 1} step={0.1} min={0} max={3} onChange={(amount) => onChange({ ...layer, react: { ...layer.react!, amount } })} />
            <Num label="Плавность" value={layer.react.smooth ?? 0.15} step={0.05} min={0} max={1} onChange={(smooth) => onChange({ ...layer, react: { ...layer.react!, smooth } })} />
          </>
        ) : null}
      </div>
    </Section>
  );
}

export function ShadowSection({ layer, onChange }: { layer: Exclude<Layer, { type: "audio" | "fx" }>; onChange: (next: Layer) => void }) {
  return (
    <Section title="Тень и наложение" open={false}>
      <Toggle
        label="Тень"
        checked={Boolean(layer.shadow)}
        onChange={(on) => onChange({ ...layer, shadow: on ? { color: "#000000", blur: 24, x: 0, y: 8, opacity: 0.6 } : null })}
      />
      {layer.shadow ? (
        <div className="li-row">
          <Color label="Цвет" value={layer.shadow.color ?? "#000000"} onChange={(color) => onChange({ ...layer, shadow: { ...layer.shadow!, color } })} />
          <Num label="Размытие" value={layer.shadow.blur ?? 0} min={0} max={200} onChange={(blur) => onChange({ ...layer, shadow: { ...layer.shadow!, blur } })} />
          <Num label="Сдвиг Y" value={layer.shadow.y ?? 0} min={-200} max={200} onChange={(y) => onChange({ ...layer, shadow: { ...layer.shadow!, y } })} />
        </div>
      ) : null}
      <label>
        <span className="li-cap">Наложение</span>
        <select className="input" value={layer.blend ?? "normal"} onChange={(e) => onChange({ ...layer, blend: e.target.value as Layer["blend"] })}>
          <option value="normal">Обычное</option>
          <option value="screen">Осветление</option>
          <option value="multiply">Умножение</option>
          <option value="overlay">Перекрытие</option>
          <option value="add">Сложение (свечение)</option>
        </select>
      </label>
    </Section>
  );
}
