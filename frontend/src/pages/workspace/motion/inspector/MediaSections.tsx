// Разделы инспектора: картинка и видео, фигура, прогресс.
import { Color, Num, Section, Swatches, Toggle, type LayerOf, type PatchProps } from "./fields";

/** Фигуры, которые умеют «от руки» (layers/sketch.ts). */
const SKETCH_KINDS: string[] = ["ellipse", "rect", "line", "arrow", "scribble"];

export function MediaSection({ layer, patchProps }: { layer: LayerOf<"image" | "video">; patchProps: PatchProps }) {
  return (
    <Section title={layer.type === "video" ? "Видео" : "Картинка"}>
      {layer.type === "video" ? (
        <>
          <div className="li-row">
            <label className="li-grow" title="id видео из «Файлов для монтажа»">
              <span className="li-cap">Файл (id)</span>
              <input className="input" value={layer.props.asset_id ?? ""} onChange={(e) => patchProps({ asset_id: e.target.value.trim() || undefined })} />
            </label>
            <Num label="С секунды" value={layer.props.from ?? 0} min={0} max={36000} step={0.1} onChange={(from) => patchProps({ from })} />
            <Num label="Скорость" value={layer.props.speed ?? 1} min={0.25} max={4} step={0.25} onChange={(speed) => patchProps({ speed })} />
          </div>
          <div className="li-row">
            <Toggle label="По кругу" checked={Boolean(layer.props.loop)} onChange={(loop) => patchProps({ loop })} />
            <Toggle label="Стоп-кадр в конце" checked={layer.props.freeze !== false} onChange={(freeze) => patchProps({ freeze })} />
            <Toggle label="Замереть на «С секунды»" checked={Boolean(layer.props.hold)} onChange={(hold) => patchProps({ hold })} />
          </div>
          <div title="Звук файла входит в микс рендера дорожкой (без автоподгонки под голос); стоп-кадр и скорость вне 0.5–2 — тишина">
            <Toggle label="Звук видео" checked={layer.props.audio === true} onChange={(audio) => patchProps({ audio: audio || undefined })} />
          </div>
          {layer.props.audio === true ? (
            <div className="li-row">
              <Num label="Громкость" value={layer.props.volume ?? 1} min={0} max={2} step={0.05} onChange={(volume) => patchProps({ volume })} />
              <Num label="Усиление, дБ" value={layer.props.gain_db ?? 0} min={-30} max={12} step={0.5} onChange={(gain_db) => patchProps({ gain_db })} />
            </div>
          ) : null}
        </>
      ) : null}
      <div className="li-row">
        <Num label="Ширина" value={layer.props.w ?? 600} min={20} max={2400} step={10} onChange={(w) => patchProps({ w })} />
        <Num label="Скругл." value={layer.props.radius ?? 0} min={0} max={600} onChange={(radius) => patchProps({ radius })} />
      </div>
      <Toggle label="Отразить" checked={Boolean(layer.props.flipX)} onChange={(flipX) => patchProps({ flipX })} />
      <Toggle
        label="В телефоне"
        checked={layer.props.device === "phone"}
        onChange={(on) => patchProps({ device: on ? "phone" : null })}
      />
      {layer.props.device === "phone" ? (
        <div className="li-row">
          <Color label="Корпус" value={layer.props.deviceColor ?? "#2c2c2e"} onChange={(deviceColor) => patchProps({ deviceColor })} />
          <Toggle
            label="Островок камеры"
            checked={layer.props.island ?? (Boolean(layer.props.statusBar) || layer.type !== "video")}
            onChange={(island) => patchProps({ island })}
          />
          <Toggle label="Строка статуса" checked={Boolean(layer.props.statusBar)} onChange={(on) => patchProps({ statusBar: on ? 0.14 : 0 })} />
          <Toggle label="Блик стекла" checked={Boolean(layer.props.screenGlare)} onChange={(screenGlare) => patchProps({ screenGlare })} />
        </div>
      ) : (
        <>
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
        </>
      )}
    </Section>
  );
}

export function ShapeSection({ layer, patchProps }: { layer: LayerOf<"shape">; patchProps: PatchProps }) {
  return (
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
            ["star4", "Искра ✦"],
            ["scribble", "Штриховка маркером"],
            ["cursor", "Курсор мыши"],
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
      {SKETCH_KINDS.includes(layer.props.kind) ? (
        <>
          <Toggle label="Нарисовано от руки" checked={(layer.props.rough ?? 0) > 0} onChange={(on) => patchProps({ rough: on ? 0.6 : 0 })} />
          {(layer.props.rough ?? 0) > 0 ? (
            <div className="li-row">
              <Num label="Неровность" value={layer.props.rough ?? 0.6} min={0} max={1} step={0.05} onChange={(rough) => patchProps({ rough })} />
              <Num label="Вариант" value={layer.props.seed ?? 0} min={0} max={999} onChange={(seed) => patchProps({ seed: Math.round(seed) })} />
              <Toggle label="Дрожит" checked={Boolean(layer.props.boil)} onChange={(boil) => patchProps({ boil })} />
            </div>
          ) : null}
        </>
      ) : null}
    </Section>
  );
}

export function ProgressSection({ layer, refWidth, patchProps }: { layer: LayerOf<"progress">; refWidth: number; patchProps: PatchProps }) {
  return (
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
        <Num label="Ширина" value={layer.props.w ?? 800} min={40} max={refWidth} step={10} onChange={(w) => patchProps({ w })} />
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
  );
}
