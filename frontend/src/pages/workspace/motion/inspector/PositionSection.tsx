// Раздел инспектора: положение, ключевые кадры, 3D, переход между ключами.
import { interpolateKeys, type AnimatableProp, type Easing, type Layer } from "@/motion";
import { ModuleOnly } from "@/components/RequireModule";
import { BezierEditor } from "@/pages/workspace/motion/BezierEditor";
import { baseValue, keyAt, setAnimated, toggleKey } from "@/pages/workspace/motion/edit";
import { TrackPanel, type PickedPoint } from "@/pages/workspace/motion/TrackPanel";
import { NumInput, Section, Toggle } from "./fields";

export function PositionSection({
  layer,
  t,
  now,
  keyed,
  hasCamera,
  planId,
  onChange,
  onPickPoint,
}: {
  layer: Exclude<Layer, { type: "audio" }>;
  t: number;
  now: () => number;
  keyed: boolean;
  hasCamera: boolean;
  planId?: number;
  onChange: (next: Layer) => void;
  onPickPoint?: (cb: ((p: PickedPoint) => void) | null) => void;
}) {
  const localT = Math.max(0, Math.min(layer.end - layer.start, t - layer.start));
  const inside = t >= layer.start && t <= layer.end;
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
    <Section title="Положение и ключевые кадры">
      {keyed || layer.behind ? (
        <div title="Слой ляжет между фоном и вырезанным персонажем: скрин, картинка или мем во весь кадр не закроют его. Без галочки — поверх персонажа">
          <Toggle
            label="За персонажем"
            checked={Boolean(layer.behind)}
            onChange={(on) => onChange({ ...layer, behind: on || undefined } as Layer)}
          />
          {!keyed ? <div className="li-note muted">У этого видео выключен хромакей — «за персонажем» не сработает.</div> : null}
        </div>
      ) : null}
      {hasCamera || layer.followCamera ? (
        <div title="Слой зумится и едет вместе с видео при наездах камеры — как часть кадра (фон за персонажем, рамка, подпись к объекту). Без галочки слой стоит на месте">
          <Toggle
            label="Ехать с камерой"
            checked={Boolean(layer.followCamera)}
            onChange={(on) => onChange({ ...layer, followCamera: on || undefined } as Layer)}
          />
          {!hasCamera ? <div className="li-note muted">У клипа нет наездов камеры — слой стоит на месте.</div> : null}
        </div>
      ) : null}
      {layer.type !== "fx" && layer.type !== "captions" ? (
        <ModuleOnly id="tracking">
          <TrackPanel layer={layer} planId={planId} now={now} onChange={onChange} onPickPoint={onPickPoint} />
        </ModuleOnly>
      ) : null}
      {!inside ? <div className="li-note muted">Курсор вне слоя — значения показаны на ближайшем краю.</div> : null}
      {transformRow("x", "X", 100, 0.5, -1, 2, "%")}
      {transformRow("y", "Y", 100, 0.5, -1, 2, "%")}
      {transformRow("scale", "Масштаб", 100, 1, 0.05, 10, "%")}
      {transformRow("rotation", "Поворот", 1, 1, -3600, 3600, "°")}
      {transformRow("opacity", "Непрозр.", 100, 1, 0, 1, "%")}
      {layer.type !== "fx" ? (
        <div
          className="li-row"
          title="Motion blur: на быстром движении (въезд, whip, вращение, наезд камеры у слоя «с камерой») слой смазывается по пути — затвор, доля кадра (50% — киношные 180°). Медленный и стоящий слой не меняется. Не задан — как у клипа"
        >
          <label className="field range li-grow">
            <span>Motion blur · {layer.motionBlur === undefined ? "как у клипа" : `${Math.round(layer.motionBlur * 100)}%`}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={Math.round((layer.motionBlur ?? 0) * 100)}
              onChange={(e) => onChange({ ...layer, motionBlur: Number(e.target.value) / 100 } as Layer)}
            />
          </label>
          {layer.motionBlur !== undefined ? (
            <button type="button" className="btn ghost sm" title="Как у клипа (настройка рендера)" onClick={() => onChange({ ...layer, motionBlur: undefined } as Layer)}>
              ↺
            </button>
          ) : null}
        </div>
      ) : null}
      {layer.type !== "fx" ? (
        <details className="li-sec" open={layer.z !== undefined || Boolean(layer.rotX || layer.rotY)}>
          <summary title="Псевдо-3D: глубина и наклон с перспективой. Задана глубина — слой в сцене: едет с 3D-камерой (параллакс)">3D</summary>
          {transformRow("z", "Глубина", 1, 10, -2000, 20000, "px")}
          {transformRow("rotX", "Наклон X", 1, 1, -720, 720, "°")}
          {transformRow("rotY", "Наклон Y", 1, 1, -720, 720, "°")}
          <div className="li-row">
            <button
              type="button"
              className="btn ghost sm"
              title="Переворот карточки с курсора: из ребра (90°) к лицу с перелётом за 0.9 с"
              disabled={!inside}
              onClick={() => {
                const t0 = Math.round(localT * 1000) / 1000;
                const t1 = Math.round(Math.min(layer.end - layer.start, localT + 0.9) * 1000) / 1000;
                const kept = (layer.keys?.rotY ?? []).filter((k) => k.t < t0 - 0.001 || k.t > t1 + 0.001);
                onChange({
                  ...layer,
                  keys: { ...(layer.keys ?? {}), rotY: [...kept, { t: t0, v: 90, ease: "bezier(0.34,1.45,0.64,1)" as const }, { t: t1, v: 0 }].sort((a, b) => a.t - b.t) },
                });
              }}
            >
              ⟲ Переворот карточки
            </button>
            {layer.z === undefined && !layer.followCamera ? (
              <button type="button" className="btn ghost sm" title="Слой станет частью сцены: поедет с 3D-камерой на глубине 0" onClick={() => onChange({ ...layer, z: 0 })}>
                В сцену
              </button>
            ) : null}
          </div>
        </details>
      ) : null}
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
  );
}
