/**
 * Камера клипа: наезды на панчлайн и тряска на «бум». Ключи живут в
 * `render.camera`; здесь — готовые движения одной кнопкой и правка ключа,
 * выбранного на дорожке «Камера».
 */
import { EASING_NAMES, type CameraKey } from "@/motion";

type Props = {
  /** Слова клипа (время клипа) и громкость по корзинам — для «Наездов по речи». */
  words?: { word: string; start: number; end: number }[];
  peaks?: number[];
  camera: CameraKey[];
  selectedIndex: number | null;
  now: () => number;
  clipDuration: number;
  onChange: (next: CameraKey[]) => void;
  onSelect: (index: number | null) => void;
};

const r3 = (v: number) => Math.round(v * 1000) / 1000;

/** Готовые движения камеры от момента курсора. */
const MOVES: { id: string; label: string; hint: string; build: (t: number) => CameraKey[] }[] = [
  {
    id: "punch",
    label: "Панч-ин",
    hint: "Резкий наезд 1.3× на реакцию и плавный отъезд",
    build: (t) => [
      { t, zoom: 1, ease: "outCubic" },
      { t: t + 0.18, zoom: 1.3, ease: "linear" },
      { t: t + 1.6, zoom: 1.3, ease: "inOutCubic" },
      { t: t + 2.0, zoom: 1 },
    ],
  },
  {
    id: "slow",
    label: "Медленный наезд",
    hint: "Ползущий зум 1.0 → 1.15 за 3 с — напряжение перед развязкой",
    build: (t) => [
      { t, zoom: 1, ease: "linear" },
      { t: t + 3, zoom: 1.15, ease: "outCubic" },
      { t: t + 3.3, zoom: 1 },
    ],
  },
  {
    id: "boom",
    label: "Бум с тряской",
    hint: "Удар: зум 1.2× с тряской и быстрым возвратом",
    build: (t) => [
      { t, zoom: 1, ease: "outBack" },
      { t: t + 0.08, zoom: 1.2, shake: 0.8, ease: "linear" },
      { t: t + 0.45, zoom: 1.12, shake: 0.3, ease: "inOutCubic" },
      { t: t + 0.8, zoom: 1, shake: 0 },
    ],
  },
  {
    id: "face",
    label: "Крупно сверху",
    hint: "Наезд 1.4× на верхнюю часть кадра (лицо стримера)",
    build: (t) => [
      { t, zoom: 1, ease: "inOutCubic" },
      { t: t + 0.35, zoom: 1.4, y: 0.35, ease: "linear" },
      { t: t + 2.2, zoom: 1.4, y: 0.35, ease: "inOutCubic" },
      { t: t + 2.6, zoom: 1, y: 0.5 },
    ],
  },
];

/**
 * Наезды по речи: монтажёр бьёт зумом в ударные слова — громкие и стоящие перед
 * паузой (панчлайн «повисает»). Берём такие слова, не чаще раза в ~4 с, и
 * ставим на каждое короткий панч-ин 1.18–1.3× с возвратом.
 */
export function speechPunchIns(
  words: { word: string; start: number; end: number }[],
  peaks: number[],
  clipDuration: number,
): CameraKey[] {
  if (!words.length || clipDuration <= 0) return [];
  const loud = (a: number, b: number) => {
    if (!peaks.length) return 0.5;
    const i0 = Math.max(0, Math.floor((a / clipDuration) * peaks.length));
    const i1 = Math.min(peaks.length, Math.ceil((b / clipDuration) * peaks.length) + 1);
    let m = 0;
    for (let i = i0; i < i1; i++) m = Math.max(m, peaks[i]);
    return m;
  };
  const scored = words.map((w, i) => {
    const gap = (words[i + 1]?.start ?? clipDuration) - w.end;
    const len = w.word.replace(/[^\p{L}\p{N}]/gu, "").length;
    const exclaim = /[!?]/.test(w.word) ? 0.25 : 0;
    return { w, score: loud(w.start, w.end) + Math.min(0.6, gap) * 0.8 + exclaim + (len >= 5 ? 0.1 : 0) };
  });
  const sorted = [...scored].sort((a, b) => b.score - a.score);
  const picked: typeof scored = [];
  const budget = Math.max(1, Math.round(clipDuration / 6));
  for (const c of sorted) {
    if (picked.length >= budget) break;
    if (c.w.start < 0.6 || c.w.end > clipDuration - 0.8) continue;
    if (picked.some((p) => Math.abs(p.w.start - c.w.start) < 4)) continue;
    picked.push(c);
  }
  const keys: CameraKey[] = [];
  picked
    .sort((a, b) => a.w.start - b.w.start)
    .forEach((p, n) => {
      const t = Math.max(0, p.w.start - 0.06);
      const zoom = n % 2 ? 1.18 : 1.28;
      const hold = Math.min(1.4, Math.max(0.6, p.w.end - p.w.start + 0.6));
      keys.push(
        { t: r3(t), zoom: 1, ease: "outCubic" },
        { t: r3(t + 0.16), zoom, ease: "linear" },
        { t: r3(t + 0.16 + hold), zoom, ease: "inOutCubic" },
        { t: r3(t + 0.46 + hold), zoom: 1 },
      );
    });
  return keys;
}

export function CameraPanel({ camera, selectedIndex, now, clipDuration, onChange, onSelect, words = [], peaks = [] }: Props) {
  const key = selectedIndex != null ? camera[selectedIndex] : null;
  const patch = (p: Partial<CameraKey>) =>
    selectedIndex != null && onChange(camera.map((k, i) => (i === selectedIndex ? { ...k, ...p } : k)));
  const addMove = (build: (t: number) => CameraKey[]) => {
    const t = now();
    const keys = build(t).filter((k) => k.t <= clipDuration + 0.001);
    // Новое движение вытесняет старые ключи в своём отрезке — иначе получится каша.
    const from = keys[0]?.t ?? t;
    const to = keys[keys.length - 1]?.t ?? t;
    const kept = camera.filter((k) => k.t < from - 0.01 || k.t > to + 0.01);
    onChange([...kept, ...keys.map((k) => ({ ...k, t: r3(k.t) }))].sort((a, b) => a.t - b.t));
  };
  return (
    <div className="cam-panel">
      <button
        type="button"
        className="btn sm cam-auto"
        disabled={!words.length}
        title={words.length ? "Короткие наезды на ударные слова — громкие и перед паузой. Заменяет текущие наезды." : "Нужны субтитры клипа (распознать речь)"}
        onClick={() => onChange(speechPunchIns(words, peaks, clipDuration))}
      >
        ✨ Наезды по речи
      </button>
      <div className="cam-moves">
        {MOVES.map((m) => (
          <button key={m.id} type="button" className="btn sm" title={m.hint} onClick={() => addMove(m.build)}>
            {m.label}
          </button>
        ))}
      </div>
      <div className="li-row">
        <button
          type="button"
          className="btn ghost sm"
          onClick={() => {
            const t = r3(now());
            const next = [...camera.filter((k) => Math.abs(k.t - t) > 0.02), { t, zoom: 1.2 }].sort((a, b) => a.t - b.t);
            onChange(next);
            onSelect(next.findIndex((k) => k.t === t));
          }}
        >
          ◆ Ключ здесь
        </button>
        {camera.length ? (
          <button type="button" className="btn ghost sm" onClick={() => onChange([])}>
            Убрать все наезды
          </button>
        ) : null}
      </div>
      {key ? (
        <div className="cam-key">
          <div className="li-cap">Ключ на {key.t.toFixed(2)} с</div>
          <label className="field range">
            <span>Зум · {(key.zoom ?? 1).toFixed(2)}×</span>
            <input type="range" min={100} max={250} value={Math.round((key.zoom ?? 1) * 100)} onChange={(e) => patch({ zoom: Number(e.target.value) / 100 })} />
          </label>
          <label className="switch li-switch" title="Точка наезда остаётся на своём месте в кадре, кадр масштабируется вокруг неё (не уезжает в центр)">
            <input
              type="checkbox"
              checked={key.anchor === "in_place"}
              onChange={(e) => patch({ anchor: e.target.checked ? "in_place" : undefined })}
            />
            <span className="switch-track" />
            <span>Наезд на месте</span>
          </label>
          <label className="field range">
            <span>{key.anchor === "in_place" ? "Точка" : "Центр"} по X · {Math.round((key.x ?? 0.5) * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round((key.x ?? 0.5) * 100)} onChange={(e) => patch({ x: Number(e.target.value) / 100 })} />
          </label>
          <label className="field range">
            <span>{key.anchor === "in_place" ? "Точка" : "Центр"} по Y · {Math.round((key.y ?? 0.5) * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round((key.y ?? 0.5) * 100)} onChange={(e) => patch({ y: Number(e.target.value) / 100 })} />
          </label>
          <label className="field range">
            <span>Тряска · {Math.round((key.shake ?? 0) * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round((key.shake ?? 0) * 100)} onChange={(e) => patch({ shake: Number(e.target.value) / 100 })} />
          </label>
          <label>
            <span className="li-cap">Переход к следующему ключу</span>
            <select className="input" value={key.ease ?? "inOutCubic"} onChange={(e) => patch({ ease: e.target.value as CameraKey["ease"] })}>
              {EASING_NAMES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => {
              onChange(camera.filter((_, i) => i !== selectedIndex));
              onSelect(null);
            }}
          >
            🗑 Удалить ключ
          </button>
        </div>
      ) : (
        <div className="muted li-note">Ромбы на дорожке «Камера» — ключи наезда. Кликни ромб, чтобы поправить зум и центр.</div>
      )}
    </div>
  );
}
