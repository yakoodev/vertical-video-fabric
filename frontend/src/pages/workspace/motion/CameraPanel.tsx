/**
 * Камера клипа: наезды на панчлайн и тряска на «бум». Ключи живут в
 * `render.camera`; здесь — готовые движения одной кнопкой и правка ключа,
 * выбранного на дорожке «Камера».
 */
import { EASING_NAMES, type CameraKey } from "@/motion";

type Props = {
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

export function CameraPanel({ camera, selectedIndex, now, clipDuration, onChange, onSelect }: Props) {
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
          <label className="field range">
            <span>Центр по X · {Math.round((key.x ?? 0.5) * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round((key.x ?? 0.5) * 100)} onChange={(e) => patch({ x: Number(e.target.value) / 100 })} />
          </label>
          <label className="field range">
            <span>Центр по Y · {Math.round((key.y ?? 0.5) * 100)}%</span>
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
