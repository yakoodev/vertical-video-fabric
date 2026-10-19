/**
 * Инспектор выделенного слоя: содержимое, стиль, положение с ключевыми кадрами,
 * время и анимации. Каждая правка отдаёт новый слой целиком — компонент
 * редактора кладёт его в список слоёв, а автосохранение и Ctrl+Z работают как
 * для остальных настроек клипа.
 */
import { useEffect, useState } from "react";
import {
  CAPTION_STYLES,
  interpolateKeys,
  type AnimatableProp,
  type CaptionWord,
  type ChartDatum,
  type Easing,
  type InOutType,
  type Layer,
  type LoopType,
  type TextStyle,
} from "@/motion";
import type { MotionFont } from "@/api/motion";
import { fontFamilies } from "@/pages/workspace/motion/runtime";
import { BezierEditor } from "@/pages/workspace/motion/BezierEditor";
import { LAYER_ICON, baseValue, keyAt, layerTitle, setAnimated, toggleKey } from "@/pages/workspace/motion/edit";

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
  { v: "draw", label: "Прорисовка" },
];
const LOOP_TYPES: { v: LoopType; label: string }[] = [
  { v: "none", label: "Нет" },
  { v: "pulse", label: "Пульс" },
  { v: "float", label: "Парение" },
  { v: "shake", label: "Тряска" },
  { v: "wiggle", label: "Покачивание" },
  { v: "spin", label: "Вращение" },
  { v: "blink", label: "Мигание" },
];
type Props = {
  layer: Layer;
  /** Слова клипа (для субтитров-слоя: подсветка слов и эмодзи над словами). */
  words?: CaptionWord[];
  fonts: MotionFont[];
  /** Текущее время клипа (для ◆ ключей), читается периодически. */
  now: () => number;
  clipDuration: number;
  onChange: (next: Layer) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onSeek: (clipSec: number) => void;
};

const r2 = (v: number) => Math.round(v * 100) / 100;

/**
 * Поле числа с черновиком: пока печатаешь, значение не зажимается и не
 * сохраняется (иначе «12» при минимуме 5 превращалось в 5.12, а «−» не набирался);
 * применяется на Enter или при уходе из поля.
 */
function NumInput({
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

function Num({
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

function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="li-color" title={label}>
      <span>{label}</span>
      <input type="color" value={(value || "#ffffff").slice(0, 7)} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="switch li-switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="switch-track" />
      <span>{label}</span>
    </label>
  );
}

function Section({ title, children, open = true }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details className="li-sec" open={open}>
      <summary>{title}</summary>
      <div className="li-sec-body">{children}</div>
    </details>
  );
}

const SWATCHES = ["#ffffff", "#ffd600", "#ff3b30", "#7c5cff", "#22d3ee", "#22c55e", "#000000"];

function Swatches({ value, onPick }: { value?: string; onPick: (c: string) => void }) {
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

/** Стиль текста — общий у «Текста» и «Счётчика». */
function TextStyleFields({
  style,
  fonts,
  onPatch,
}: {
  style: TextStyle;
  fonts: MotionFont[];
  onPatch: (patch: Partial<TextStyle>) => void;
}) {
  const families = fontFamilies(fonts);
  const fam = families.find((f) => f.family === style.font) ?? families[0];
  return (
    <>
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Шрифт</span>
          <select className="input" value={style.font ?? ""} onChange={(e) => onPatch({ font: e.target.value })}>
            {!style.font ? <option value="">По умолчанию</option> : null}
            {families.map((f) => (
              <option key={f.family} value={f.family} style={{ fontFamily: f.family }}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="li-cap">Толщина</span>
          <select className="input" value={style.weight ?? 800} onChange={(e) => onPatch({ weight: Number(e.target.value) })}>
            {(fam?.weights.length ? fam.weights : [400, 700, 800, 900]).map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field range">
        <span>Размер · {Math.round(style.size ?? 96)} px</span>
        <input type="range" min={20} max={300} value={style.size ?? 96} onChange={(e) => onPatch({ size: Number(e.target.value) })} />
      </label>
      <div className="li-row">
        <Color label="Цвет" value={style.color ?? "#ffffff"} onChange={(color) => onPatch({ color, gradient: null })} />
        <Swatches value={style.color} onPick={(color) => onPatch({ color, gradient: null })} />
      </div>
      <Toggle
        label="Градиент"
        checked={Boolean(style.gradient)}
        onChange={(on) => onPatch({ gradient: on ? [style.color ?? "#ffd600", "#ff3b30"] : null })}
      />
      {style.gradient ? (
        <div className="li-row">
          <Color label="От" value={style.gradient[0]} onChange={(c) => onPatch({ gradient: [c, style.gradient![1]] })} />
          <Color label="До" value={style.gradient[1]} onChange={(c) => onPatch({ gradient: [style.gradient![0], c] })} />
        </div>
      ) : null}
      <Toggle
        label="Обводка"
        checked={Boolean(style.stroke)}
        onChange={(on) => onPatch({ stroke: on ? { color: "#000000", width: 10 } : null })}
      />
      {style.stroke ? (
        <div className="li-row">
          <Color label="Цвет" value={style.stroke.color} onChange={(color) => onPatch({ stroke: { ...style.stroke!, color } })} />
          <Num label="Толщина" value={style.stroke.width} min={0} max={60} onChange={(width) => onPatch({ stroke: { ...style.stroke!, width } })} />
        </div>
      ) : null}
      <Toggle
        label="Плашка"
        checked={Boolean(style.bg)}
        onChange={(on) => onPatch({ bg: on ? { color: "#7c5cff", opacity: 1, radius: 18, padX: 28, padY: 14 } : null })}
      />
      {style.bg ? (
        <>
          <div className="li-row">
            <Color label="Цвет" value={style.bg.color} onChange={(color) => onPatch({ bg: { ...style.bg!, color } })} />
            <Swatches value={style.bg.color} onPick={(color) => onPatch({ bg: { ...style.bg!, color } })} />
          </div>
          <div className="li-row">
            <Num label="Скругл." value={style.bg.radius ?? 18} min={0} max={200} onChange={(radius) => onPatch({ bg: { ...style.bg!, radius } })} />
            <Num label="Скос°" value={style.bg.skew ?? 0} min={-30} max={30} onChange={(skew) => onPatch({ bg: { ...style.bg!, skew } })} />
            <Num
              label="Прозр."
              value={style.bg.opacity ?? 1}
              step={0.05}
              min={0}
              max={1}
              onChange={(opacity) => onPatch({ bg: { ...style.bg!, opacity } })}
            />
          </div>
        </>
      ) : null}
      <div className="li-row">
        <div className="seg" role="radiogroup" aria-label="Выравнивание">
          {(["left", "center", "right"] as const).map((a) => (
            <button
              key={a}
              type="button"
              className={`seg-item${(style.align ?? "center") === a ? " active" : ""}`}
              onClick={() => onPatch({ align: a })}
            >
              {a === "left" ? "⟸" : a === "center" ? "≡" : "⟹"}
            </button>
          ))}
        </div>
        <Toggle label="ЗАГЛАВНЫЕ" checked={Boolean(style.uppercase)} onChange={(uppercase) => onPatch({ uppercase })} />
      </div>
      <div className="li-row">
        <Num label="Ширина" value={style.maxWidth ?? 900} min={100} max={1080} step={10} onChange={(maxWidth) => onPatch({ maxWidth })} />
        <Num label="Интервал" value={style.lineHeight ?? 1.1} min={0.8} max={2} step={0.05} onChange={(lineHeight) => onPatch({ lineHeight })} />
        <Num label="Трекинг" value={style.letterSpacing ?? 0} min={-10} max={40} onChange={(letterSpacing) => onPatch({ letterSpacing })} />
      </div>
    </>
  );
}

const WORD_COLORS = ["#ffd600", "#22c55e", "#ff3b30", "#22d3ee", "#ff3b8d"];
const WORD_EMOJI = ["🔥", "😂", "💀", "😱", "💸", "❤️", "👀", "🤯", "😭", "🗿"];

/** Слова субтитров-слоя: клик — выбрать слово, дальше цвет или эмодзи над ним. */
function CaptionWords({
  layer,
  words,
  onChange,
  onSeek,
}: {
  layer: Extract<Layer, { type: "captions" }>;
  words: CaptionWord[];
  onChange: (next: Layer) => void;
  onSeek: (t: number) => void;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  const hl = new Map((layer.props.highlightWords ?? []).map((h) => [h.i, h.color]));
  const em = new Map((layer.props.emoji ?? []).map((e) => [e.i, e.char]));
  const inRange = words
    .map((w, i) => ({ w, i }))
    .filter(({ w }) => w.t0 >= layer.start - 0.05 && w.t1 <= layer.end + 0.05)
    .slice(0, 400);
  const setProps = (patch: Record<string, unknown>) => onChange({ ...layer, props: { ...layer.props, ...patch } } as Layer);
  const setColor = (i: number, color: string | null) =>
    setProps({
      highlightWords: [...(layer.props.highlightWords ?? []).filter((h) => h.i !== i), ...(color ? [{ i, color }] : [])].sort((a, b) => a.i - b.i),
    });
  const setEmoji = (i: number, char: string | null) =>
    setProps({ emoji: [...(layer.props.emoji ?? []).filter((e) => e.i !== i), ...(char ? [{ i, char }] : [])].sort((a, b) => a.i - b.i) });
  if (!inRange.length) return <div className="li-note muted">Слов нет — сначала «Распознать речь» в группе «Субтитры».</div>;
  return (
    <div className="li-words">
      <div className="li-words-list">
        {inRange.map(({ w, i }) => (
          <button
            key={i}
            type="button"
            className={`li-word${picked === i ? " on" : ""}`}
            style={hl.has(i) ? { color: hl.get(i), borderColor: hl.get(i) } : undefined}
            onClick={() => {
              setPicked(i);
              onSeek(w.t0 + 0.02);
            }}
            title={`${w.t0.toFixed(2)} с`}
          >
            {w.w}
            {em.has(i) ? <span className="li-word-emoji">{em.get(i)}</span> : null}
          </button>
        ))}
      </div>
      {picked != null ? (
        <div className="li-word-tools">
          <span className="li-cap">«{words[picked]?.w}»:</span>
          {WORD_COLORS.map((c) => (
            <button key={c} type="button" className="li-swatch" style={{ background: c }} onClick={() => setColor(picked, c)} aria-label={c} />
          ))}
          <button type="button" className="btn ghost sm" onClick={() => setColor(picked, null)} title="Без цвета">
            ⌀
          </button>
          <span className="li-word-sep" />
          {WORD_EMOJI.map((ch) => (
            <button key={ch} type="button" className="li-word-em" onClick={() => setEmoji(picked, em.get(picked) === ch ? null : ch)}>
              {ch}
            </button>
          ))}
        </div>
      ) : (
        <div className="li-note muted">Кликни слово — покрась его или поставь над ним эмодзи.</div>
      )}
    </div>
  );
}

export function LayerInspector({ layer, words = [], fonts, now, clipDuration, onChange, onDelete, onDuplicate, onSeek }: Props) {
  // Время перечитываем несколько раз в секунду: от него зависят ◆ и значения
  // анимированных свойств, а весь редактор ради этого перерисовывать не нужно.
  const [t, setT] = useState(now());
  useEffect(() => {
    const id = window.setInterval(() => setT(now()), 200);
    return () => window.clearInterval(id);
  }, [now]);
  const localT = Math.max(0, Math.min(layer.end - layer.start, t - layer.start));
  const inside = t >= layer.start && t <= layer.end;
  const patchProps = (patch: Record<string, unknown>) => onChange({ ...layer, props: { ...layer.props, ...patch } } as Layer);
  const valueOf = (prop: AnimatableProp) => interpolateKeys(layer.keys?.[prop], localT, baseValue(layer, prop));

  const transformRow = (prop: AnimatableProp, label: string, k: number, step: number, min: number, max: number, suffix = "") => {
    const has = Boolean(layer.keys?.[prop]?.length);
    const on = Boolean(keyAt(layer, prop, localT));
    return (
      <div className="li-tr" key={prop}>
        <button
          type="button"
          className={`li-key${on ? " on" : has ? " has" : ""}`}
          disabled={!inside}
          title={
            inside
              ? on
                ? "Убрать ключ в этом моменте"
                : "Поставить ключ: значение запомнится в этом моменте, между ключами — плавный переход"
              : "Курсор вне слоя — подведите его к слою"
          }
          onClick={() => onChange(toggleKey(layer, prop, localT, valueOf(prop)))}
        >
          ◆
        </button>
        <span className="li-tr-label">{label}</span>
<NumInput
          value={valueOf(prop) * k}
          step={step}
          min={min * k}
          max={max * k}
          onCommit={(v) => onChange(setAnimated(layer, prop, Math.min(max, Math.max(min, v / k)), localT))}
        />
        <em>{suffix}</em>
      </div>
    );
  };

  const keyEases = Object.values(layer.keys ?? {}).flat();
  const commonEase = keyEases.length ? keyEases[0]?.ease ?? "inOutCubic" : null;

  return (
    <div className="li">
      <div className="li-head">
        <span className="li-ico">{LAYER_ICON[layer.type]}</span>
        <input
          className="input li-name"
          value={layer.name ?? ""}
          placeholder={layerTitle(layer)}
          onChange={(e) => onChange({ ...layer, name: e.target.value || undefined })}
        />
        <button type="button" className="btn ghost sm" title={layer.hidden ? "Показать" : "Скрыть"} onClick={() => onChange({ ...layer, hidden: !layer.hidden })}>
          {layer.hidden ? "🙈" : "👁"}
        </button>
        <button type="button" className="btn ghost sm" title={layer.locked ? "Разблокировать" : "Заблокировать на кадре"} onClick={() => onChange({ ...layer, locked: !layer.locked })}>
          {layer.locked ? "🔒" : "🔓"}
        </button>
        <button type="button" className="btn ghost sm" title="Дублировать (Ctrl+D)" onClick={onDuplicate}>
          ⧉
        </button>
        <button type="button" className="btn ghost sm" title="Удалить (Del)" onClick={onDelete}>
          🗑
        </button>
      </div>

      {layer.type === "text" ? (
        <Section title="Текст">
          <textarea
            className="input li-text"
            rows={3}
            value={layer.props.text}
            onChange={(e) => patchProps({ text: e.target.value })}
            placeholder="Текст титра"
          />
          <TextStyleFields style={layer.props} fonts={fonts} onPatch={patchProps} />
        </Section>
      ) : null}

      {layer.type === "captions" ? (
        <Section title="Анимированные субтитры">
          <div className="li-cap-styles">
            {CAPTION_STYLES.map((st) => (
              <button
                key={st.id}
                type="button"
                className={`li-cap-style${(layer.props.style ?? "hormozi") === st.id ? " on" : ""}`}
                title={st.description}
                onClick={() =>
                  // Смена стиля сбрасывает ручные цвета/шрифт — иначе пресет «не включится».
                  onChange({
                    ...layer,
                    props: { style: st.id, highlightWords: layer.props.highlightWords, emoji: layer.props.emoji },
                  } as Layer)
                }
              >
                {st.name}
              </button>
            ))}
          </div>
          <div className="li-row">
            <Num label="Слов" value={layer.props.maxWords ?? 3} min={1} max={8} onChange={(maxWords) => patchProps({ maxWords })} />
            <Num label="Строк" value={layer.props.maxLines ?? 2} min={1} max={3} onChange={(maxLines) => patchProps({ maxLines })} />
            <Num label="Размер" value={layer.props.size ?? 90} min={30} max={220} onChange={(size) => patchProps({ size })} />
          </div>
          <div className="li-row">
            <Color label="Слова" value={layer.props.color ?? "#ffffff"} onChange={(color) => patchProps({ color })} />
            <Color label="Активное" value={layer.props.activeColor ?? "#ffd600"} onChange={(activeColor) => patchProps({ activeColor })} />
            <Toggle
              label="Плашка у слова"
              checked={Boolean(layer.props.activeBg)}
              onChange={(on) => patchProps({ activeBg: on ? "#7c5cff" : null })}
            />
            {layer.props.activeBg ? (
              <Color label="Плашка" value={layer.props.activeBg} onChange={(activeBg) => patchProps({ activeBg })} />
            ) : null}
          </div>
          <div className="li-row">
            <label className="li-grow">
              <span className="li-cap">Анимация слова</span>
              <select className="input" value={layer.props.animation ?? "pop"} onChange={(e) => patchProps({ animation: e.target.value })}>
                <option value="pop">Поп</option>
                <option value="bounce">Прыжок</option>
                <option value="fade">Проявление</option>
                <option value="karaoke">Караоке</option>
                <option value="typewriter">Печать</option>
                <option value="none">Без анимации</option>
              </select>
            </label>
            <Num label="Рост ×" value={layer.props.wordScale ?? 1.15} step={0.05} min={1} max={1.4} onChange={(wordScale) => patchProps({ wordScale })} />
          </div>
          <div className="li-row">
            <Toggle label="ЗАГЛАВНЫЕ" checked={Boolean(layer.props.uppercase)} onChange={(uppercase) => patchProps({ uppercase })} />
            <Toggle label="Знаки препинания" checked={Boolean(layer.props.punctuation)} onChange={(punctuation) => patchProps({ punctuation })} />
          </div>
          <CaptionWords layer={layer} words={words} onChange={onChange} onSeek={onSeek} />
        </Section>
      ) : null}

      {layer.type === "counter" ? (
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
          <TextStyleFields style={layer.props} fonts={fonts} onPatch={patchProps} />
        </Section>
      ) : null}

      {layer.type === "emoji" ? (
        <Section title="Эмодзи">
          <div className="li-row">
            <label className="li-grow">
              <span className="li-cap">Символ</span>
              <input className="input li-emoji" value={layer.props.char} onChange={(e) => patchProps({ char: e.target.value })} />
            </label>
            <Num label="Размер" value={layer.props.size ?? 220} min={20} max={1200} step={10} onChange={(size) => patchProps({ size })} />
          </div>
        </Section>
      ) : null}

      {layer.type === "image" ? (
        <Section title="Картинка">
          <div className="li-row">
            <Num label="Ширина" value={layer.props.w ?? 600} min={20} max={2400} step={10} onChange={(w) => patchProps({ w })} />
            <Num label="Скругл." value={layer.props.radius ?? 0} min={0} max={600} onChange={(radius) => patchProps({ radius })} />
          </div>
          <Toggle label="Отразить" checked={Boolean(layer.props.flipX)} onChange={(flipX) => patchProps({ flipX })} />
          <Toggle
            label="Рамка"
            checked={Boolean(layer.props.border)}
            onChange={(on) => patchProps({ border: on ? { color: "#ffffff", width: 8 } : null })}
          />
          {layer.props.border ? (
            <div className="li-row">
              <Color label="Цвет" value={layer.props.border.color} onChange={(color) => patchProps({ border: { ...layer.props.border!, color } })} />
              <Num label="Толщина" value={layer.props.border.width} min={0} max={60} onChange={(width) => patchProps({ border: { ...layer.props.border!, width } })} />
            </div>
          ) : null}
        </Section>
      ) : null}

      {layer.type === "shape" ? (
        <Section title="Фигура">
          <label>
            <span className="li-cap">Вид</span>
            <select className="input" value={layer.props.kind} onChange={(e) => patchProps({ kind: e.target.value })}>
              {[
                ["rect", "Прямоугольник"],
                ["ellipse", "Круг / овал"],
                ["line", "Линия"],
                ["arrow", "Стрелка"],
                ["triangle", "Треугольник"],
                ["star", "Звезда"],
                ["burst", "Взрыв"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <div className="li-row">
            <Num label="Ширина" value={layer.props.w ?? 400} min={4} max={2400} step={10} onChange={(w) => patchProps({ w })} />
            <Num label="Высота" value={layer.props.h ?? 400} min={4} max={2400} step={10} onChange={(h) => patchProps({ h })} />
          </div>
          <Toggle label="Заливка" checked={Boolean(layer.props.fill)} onChange={(on) => patchProps({ fill: on ? "#7c5cff" : null })} />
          {layer.props.fill ? (
            <div className="li-row">
              <Color label="Цвет" value={layer.props.fill} onChange={(fill) => patchProps({ fill })} />
              <Swatches value={layer.props.fill} onPick={(fill) => patchProps({ fill })} />
            </div>
          ) : null}
          <Toggle
            label="Контур"
            checked={Boolean(layer.props.stroke)}
            onChange={(on) => patchProps({ stroke: on ? { color: "#ff3b30", width: 14 } : null })}
          />
          {layer.props.stroke ? (
            <div className="li-row">
              <Color label="Цвет" value={layer.props.stroke.color} onChange={(color) => patchProps({ stroke: { ...layer.props.stroke!, color } })} />
              <Num label="Толщина" value={layer.props.stroke.width} min={0} max={120} onChange={(width) => patchProps({ stroke: { ...layer.props.stroke!, width } })} />
            </div>
          ) : null}
          <div className="li-row">
            {layer.props.kind === "rect" ? (
              <Num label="Скругл." value={layer.props.radius ?? 0} min={0} max={600} onChange={(radius) => patchProps({ radius })} />
            ) : null}
            {layer.props.kind === "star" || layer.props.kind === "burst" ? (
              <Num label="Лучей" value={layer.props.points ?? 5} min={3} max={24} onChange={(points) => patchProps({ points })} />
            ) : null}
            {layer.props.kind === "line" || layer.props.kind === "arrow" ? (
              <>
                <Num label="Изгиб" value={layer.props.bend ?? 0} min={-600} max={600} step={10} onChange={(bend) => patchProps({ bend })} />
                <Toggle label="Пунктир" checked={Boolean(layer.props.dash)} onChange={(dash) => patchProps({ dash })} />
              </>
            ) : null}
          </div>
        </Section>
      ) : null}

      {layer.type === "progress" ? (
        <Section title="Прогресс">
          <label className="field range">
            <span>Значение · {Math.round((layer.props.value ?? 0.75) * 100)}%</span>
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round((layer.props.value ?? 0.75) * 100)}
              onChange={(e) => patchProps({ value: Number(e.target.value) / 100 })}
            />
          </label>
          <div className="li-row">
            <Num label="Ширина" value={layer.props.w ?? 800} min={40} max={1080} step={10} onChange={(w) => patchProps({ w })} />
            <Num label="Высота" value={layer.props.h ?? 60} min={6} max={400} onChange={(h) => patchProps({ h })} />
          </div>
          <div className="li-row">
            <Color
              label="Заливка"
              value={Array.isArray(layer.props.fill) ? layer.props.fill[0] : layer.props.fill ?? "#7c5cff"}
              onChange={(fill) => patchProps({ fill })}
            />
            <Color label="Дорожка" value={layer.props.track ?? "#ffffff"} onChange={(track) => patchProps({ track })} />
            <Toggle label="Подпись %" checked={layer.props.label ?? true} onChange={(label) => patchProps({ label })} />
          </div>
        </Section>
      ) : null}

      {layer.type === "chart" ? (
        <Section title="График">
          <div className="li-row">
            <select className="input" value={layer.props.kind} onChange={(e) => patchProps({ kind: e.target.value })}>
              {[
                ["bar", "Столбцы"],
                ["hbar", "Полосы"],
                ["line", "Линия"],
                ["pie", "Пирог"],
                ["donut", "Кольцо"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
            <Toggle label="Значения" checked={layer.props.showValues ?? true} onChange={(showValues) => patchProps({ showValues })} />
          </div>
          <label>
            <span className="li-cap">Заголовок</span>
            <input className="input" value={layer.props.title ?? ""} onChange={(e) => patchProps({ title: e.target.value })} />
          </label>
          <div className="li-data">
            {(layer.props.data ?? []).map((d: ChartDatum, i: number) => (
              <div className="li-row" key={i}>
                <input
                  className="input li-grow"
                  value={d.label}
                  onChange={(e) => patchProps({ data: layer.props.data.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })}
                />
                <input
                  className="input li-val"
                  type="number"
                  value={d.value}
                  onChange={(e) =>
                    patchProps({ data: layer.props.data.map((x, j) => (j === i ? { ...x, value: Number(e.target.value) || 0 } : x)) })
                  }
                />
                <button
                  type="button"
                  className="btn ghost sm"
                  onClick={() => patchProps({ data: layer.props.data.filter((_, j) => j !== i) })}
                  aria-label="Убрать строку"
                >
                  ×
                </button>
              </div>
            ))}
            {(layer.props.data?.length ?? 0) < 12 ? (
              <button
                type="button"
                className="btn ghost sm"
                onClick={() => patchProps({ data: [...(layer.props.data ?? []), { label: "Новое", value: 10 }] })}
              >
                ＋ строка
              </button>
            ) : null}
          </div>
        </Section>
      ) : null}

      {layer.type === "callout" ? (
        <Section title="Плашка">
          <label>
            <span className="li-cap">Заголовок</span>
            <input className="input" value={layer.props.title} onChange={(e) => patchProps({ title: e.target.value })} />
          </label>
          <label>
            <span className="li-cap">Подпись</span>
            <input className="input" value={layer.props.subtitle ?? ""} onChange={(e) => patchProps({ subtitle: e.target.value })} />
          </label>
          <div className="li-row">
            <select className="input" value={layer.props.style ?? "bar"} onChange={(e) => patchProps({ style: e.target.value })}>
              <option value="bar">Полоса</option>
              <option value="box">Коробка</option>
              <option value="pill">Таблетка</option>
              <option value="neon">Неон</option>
            </select>
            <Color label="Акцент" value={layer.props.accent ?? "#7c5cff"} onChange={(accent) => patchProps({ accent })} />
            <label>
              <span className="li-cap">Значок</span>
              <input className="input li-emoji" value={layer.props.icon ?? ""} onChange={(e) => patchProps({ icon: e.target.value })} />
            </label>
          </div>
          <div className="li-row">
            <Num label="Ширина" value={layer.props.w ?? 860} min={200} max={1080} step={10} onChange={(w) => patchProps({ w })} />
            <Num label="Скос°" value={layer.props.skew ?? 0} min={-30} max={30} onChange={(skew) => patchProps({ skew })} />
          </div>
        </Section>
      ) : null}

      {layer.type === "audio" ? (
        <Section title="Звук">
          <label className="field range">
            <span>Громкость · {Math.round((layer.props.volume ?? 1) * 100)}%</span>
            <input
              type="range"
              min={0}
              max={200}
              value={Math.round((layer.props.volume ?? 1) * 100)}
              onChange={(e) => patchProps({ volume: Number(e.target.value) / 100 })}
            />
          </label>
          <div className="li-row">
            <Num label="Вход, с" value={layer.props.fadeIn ?? 0} min={0} max={5} step={0.05} onChange={(fadeIn) => patchProps({ fadeIn })} />
            <Num label="Выход, с" value={layer.props.fadeOut ?? 0} min={0} max={5} step={0.05} onChange={(fadeOut) => patchProps({ fadeOut })} />
            <Num label="С секунды" value={layer.props.offset ?? 0} min={0} step={0.1} onChange={(offset) => patchProps({ offset })} />
          </div>
          <Toggle label="Приглушить речь под звуком" checked={Boolean(layer.props.duck)} onChange={(duck) => patchProps({ duck })} />
        </Section>
      ) : null}

      {layer.type !== "audio" ? (
        <Section title="Положение и ключевые кадры">
          {!inside ? <div className="li-note muted">Курсор вне слоя — значения показаны на ближайшем краю.</div> : null}
          {transformRow("x", "X", 100, 0.5, -1, 2, "%")}
          {transformRow("y", "Y", 100, 0.5, -1, 2, "%")}
          {transformRow("scale", "Масштаб", 100, 1, 0.05, 10, "%")}
          {transformRow("rotation", "Поворот", 1, 1, -3600, 3600, "°")}
          {transformRow("opacity", "Непрозр.", 100, 1, 0, 1, "%")}
          {commonEase ? (
            <div className="li-ease">
              <span className="li-cap">Переход между ключами</span>
              <BezierEditor
                value={commonEase as Easing}
                onChange={(ease) => {
                  const next = Object.fromEntries(
                    Object.entries(layer.keys ?? {}).map(([k, pts]) => [k, pts?.map((p) => ({ ...p, ease }))]),
                  );
                  onChange({ ...layer, keys: next });
                }}
              />
            </div>
          ) : null}
          <div className="li-row">
            <button type="button" className="btn ghost sm" onClick={() => {
                const keys = { ...(layer.keys ?? {}) };
                delete keys.x;
                delete keys.y;
                onChange({ ...layer, x: 0.5, y: 0.5, keys });
              }}>
              ⊕ В центр
            </button>
            {layer.keys && Object.keys(layer.keys).length ? (
              <button type="button" className="btn ghost sm" onClick={() => onChange({ ...layer, keys: {} })}>
                Снять все ключи
              </button>
            ) : null}
          </div>
        </Section>
      ) : null}

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

      {layer.type !== "audio" ? (
        <Section title="Анимация">
          <div className="li-row">
            <label className="li-grow">
              <span className="li-cap">Появление</span>
              <select
                className="input"
                value={layer.in?.type ?? "none"}
                onChange={(e) => onChange({ ...layer, in: { type: e.target.value as InOutType, duration: layer.in?.duration ?? 0.35 } })}
              >
                {IN_TYPES.map((o) => (
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
                {IN_TYPES.map((o) => (
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
        </Section>
      ) : null}

      {layer.type !== "audio" ? (
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
      ) : null}
    </div>
  );
}
