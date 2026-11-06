// Раздел инспектора: анимированные субтитры, голоса и цвета отдельных слов.
import { useState } from "react";
import { CAPTION_STYLES, type CaptionWord, type Layer } from "@/motion";
import { Color, Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";

const WORD_COLORS = ["#ffd600", "#22c55e", "#ff3b30", "#22d3ee", "#ff3b8d"];
/** Фильтр голосов слоя субтитров: все, только главный (A), только остальные. */
const VOICE_FILTERS = { all: [] as string[], main: ["A"], others: ["B", "C", "D"] };
const voiceFilter = (speakers: string[] | undefined): keyof typeof VOICE_FILTERS =>
  !speakers?.length ? "all" : speakers.includes("A") ? "main" : "others";

/** Слова субтитров-слоя: клик — выбрать слово, дальше его цвет. */
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
  const inRange = words
    .map((w, i) => ({ w, i }))
    .filter(({ w }) => w.t0 >= layer.start - 0.05 && w.t1 <= layer.end + 0.05)
    .slice(0, 400);
  const setProps = (patch: Record<string, unknown>) => onChange({ ...layer, props: { ...layer.props, ...patch } } as Layer);
  const setColor = (i: number, color: string | null) =>
    setProps({
      // w — якорь: сервер переставит подсветку на то же слово, если слова субтитров перепишут.
      highlightWords: [...(layer.props.highlightWords ?? []).filter((h) => h.i !== i), ...(color ? [{ i, color, w: words[i]?.w }] : [])].sort((a, b) => a.i - b.i),
    });
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
        </div>
      ) : (
        <div className="li-note muted">Кликни слово — покрась его.</div>
      )}
    </div>
  );
}

export function CaptionsSection({
  layer,
  words,
  onChange,
  onSeek,
  patchProps,
}: {
  layer: LayerOf<"captions">;
  words: CaptionWord[];
  onChange: (next: Layer) => void;
  onSeek: (t: number) => void;
  patchProps: PatchProps;
}) {
  return (
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
                props: {
                  style: st.id,
                  highlightWords: layer.props.highlightWords,
                  speakers: layer.props.speakers,
                  speakerColors: layer.props.speakerColors,
                },
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
            <option value="stomp">Удар (акцент)</option>
            <option value="none">Без анимации</option>
          </select>
        </label>
        <Num label="Рост ×" value={layer.props.wordScale ?? 1.15} step={0.05} min={1} max={1.4} onChange={(wordScale) => patchProps({ wordScale })} />
      </div>
      <div className="li-row">
        <Toggle label="ЗАГЛАВНЫЕ" checked={Boolean(layer.props.uppercase)} onChange={(uppercase) => patchProps({ uppercase })} />
        <Toggle label="Знаки препинания" checked={Boolean(layer.props.punctuation)} onChange={(punctuation) => patchProps({ punctuation })} />
      </div>
      {words.some((w) => w.s && w.s !== "A") ? (
        <div className="li-row">
          <label className="li-grow" title="Голоса разбираются кнопкой «Голоса» в субтитрах. Второй слой с «только другие» — мельче и в другом месте, чтобы одновременная речь не мешалась.">
            <span className="li-cap">Голоса</span>
            <select
              className="input"
              value={voiceFilter(layer.props.speakers)}
              onChange={(e) => patchProps({ speakers: VOICE_FILTERS[e.target.value as keyof typeof VOICE_FILTERS] })}
            >
              <option value="all">Все (другие — своим цветом)</option>
              <option value="main">Только главный</option>
              <option value="others">Только другие голоса</option>
            </select>
          </label>
        </div>
      ) : null}
      <CaptionWords layer={layer} words={words} onChange={onChange} onSeek={onSeek} />
    </Section>
  );
}
