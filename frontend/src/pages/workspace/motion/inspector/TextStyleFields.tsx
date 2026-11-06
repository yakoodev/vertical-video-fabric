import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { TextStyle } from "@/motion";
import { motionApi, type MotionFont } from "@/api/motion";
import { fontFamilies, reloadMotionFonts } from "@/pages/workspace/motion/runtime";
import { Color, Num, Swatches, Toggle } from "./fields";

/** Стиль текста — общий у «Текста» и «Счётчика». */
export function TextStyleFields({
  style,
  fonts,
  onPatch,
  refWidth = 1080,
}: {
  style: TextStyle;
  fonts: MotionFont[];
  onPatch: (patch: Partial<TextStyle>) => void;
  refWidth?: number;
}) {
  const families = fontFamilies(fonts);
  const fam = families.find((f) => f.family === style.font) ?? families[0];
  const qc = useQueryClient();
  const [fontNote, setFontNote] = useState("");
  // Свой шрифт (TTF/OTF/WOFF2): в data/fonts на сервере, сразу в списке и в превью (@font-face).
  const uploadFont = async (file: File | undefined) => {
    if (!file) return;
    setFontNote("Загружаю шрифт…");
    try {
      const font = await motionApi.uploadFont(file);
      await reloadMotionFonts();
      await qc.invalidateQueries({ queryKey: ["motion-fonts"] });
      onPatch({ font: font.family, weight: font.variable ? (style.weight ?? font.weight) : font.weight });
      setFontNote(
        font.cyrillic === false
          ? `«${font.family}» загружен — кириллицы в нём нет, русский текст будет запасным шрифтом`
          : `«${font.family}» загружен${font.variable ? " (вариативный: любая толщина)" : ""}`,
      );
    } catch (err) {
      setFontNote(`Шрифт не принят: ${(err as Error).message}`);
    }
  };
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
      <label className="btn ghost sm" title="TTF, OTF или WOFF2 до 20 МБ; лицензия шрифта — на совести загружающего">
        ＋ Свой шрифт
        <input
          type="file"
          accept=".ttf,.otf,.woff2,font/ttf,font/otf,font/woff2"
          hidden
          onChange={(e) => {
            void uploadFont(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </label>
      {fontNote ? <div className="muted li-note">{fontNote}</div> : null}
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
        <Num label="Ширина" value={style.maxWidth ?? 900} min={100} max={refWidth} step={10} onChange={(maxWidth) => onPatch({ maxWidth })} />
        <Num label="Интервал" value={style.lineHeight ?? 1.1} min={0.8} max={2} step={0.05} onChange={(lineHeight) => onPatch({ lineHeight })} />
        <Num label="Трекинг" value={style.letterSpacing ?? 0} min={-10} max={40} onChange={(letterSpacing) => onPatch({ letterSpacing })} />
      </div>
    </>
  );
}
