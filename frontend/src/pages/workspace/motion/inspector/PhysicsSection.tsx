// Раздел инспектора: слой физики (motion/physics.ts).
import { MAX_PHYSICS_BODIES, type PhysicsBody } from "@/motion";
import { Color, Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";

const SPAWN_LABELS: [string, string][] = [
  ["drop", "Падают строкой"],
  ["rain", "Дождь сверху"],
  ["burst", "Разлёт из точки"],
  ["stack", "Куча (стоит до толчка)"],
];

const WALL_LABELS: [string, string][] = [
  ["frame", "Пол и бока рамки"],
  ["floor", "Только пол"],
  ["none", "Без стенок (улетают)"],
];

/** Физика: что падает (первое тело слоя), как появляется, стенки, тяжесть, упругость, трение, толчок. */
export function PhysicsSection({ layer, patchProps }: { layer: LayerOf<"physics">; patchProps: PatchProps }) {
  const p = layer.props;
  const bodies = p.bodies ?? [];
  const first: PhysicsBody = bodies[0] ?? { kind: "text", text: "БАМ!", split: "letters", size: 200 };
  const setFirst = (patch: Partial<PhysicsBody>) => patchProps({ bodies: [{ ...first, ...patch }, ...bodies.slice(1)] });
  const impulse = p.impulse ?? null;
  return (
    <Section title="Физика">
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Тела</span>
          <select className="input" value={first.kind} onChange={(e) => setFirst({ kind: e.target.value as PhysicsBody["kind"] })}>
            <option value="text">Текст</option>
            <option value="image">Картинка / эмодзи</option>
            <option value="shape">Фигура</option>
          </select>
        </label>
        {first.kind === "text" ? (
          <label className="li-grow">
            <span className="li-cap">Делить на</span>
            <select className="input" value={first.split ?? "letters"} onChange={(e) => setFirst({ split: e.target.value as PhysicsBody["split"] })}>
              <option value="letters">буквы</option>
              <option value="words">слова</option>
              <option value="whole">целиком</option>
            </select>
          </label>
        ) : (
          <label className="li-grow">
            <span className="li-cap">Контур</span>
            <select
              className="input"
              value={first.shape ?? (first.kind === "image" ? "circle" : "box")}
              onChange={(e) => setFirst({ shape: e.target.value as PhysicsBody["shape"] })}
            >
              <option value="box">Брусок</option>
              <option value="circle">Круг</option>
              <option value="triangle">Треугольник</option>
              <option value="plate">Тарелка</option>
            </select>
          </label>
        )}
      </div>
      {first.kind === "text" ? (
        <label className="li-grow">
          <span className="li-cap">Текст</span>
          <input className="input" value={first.text ?? ""} maxLength={200} onChange={(e) => setFirst({ text: e.target.value })} />
        </label>
      ) : first.kind === "image" ? (
        <label className="li-grow">
          <span className="li-cap">Картинка (emoji:…, sticker:… или id файла)</span>
          <input
            className="input"
            value={first.src ?? first.asset_id ?? ""}
            onChange={(e) => {
              const v = e.target.value.trim();
              setFirst(/^\d+$/.test(v) ? { asset_id: v, src: undefined } : { src: v || undefined, asset_id: undefined });
            }}
          />
        </label>
      ) : null}
      <div className="li-row">
        <Num label="Размер" value={first.size ?? (first.kind === "text" ? 140 : 120)} min={8} max={800} onChange={(size) => setFirst({ size })} />
        {first.kind !== "text" ? (
          <Num label="Сколько" value={first.count ?? 1} min={1} max={MAX_PHYSICS_BODIES} onChange={(count) => setFirst({ count })} />
        ) : null}
        <Color
          label="Цвет"
          value={first.colors?.[0] ?? (first.kind === "text" ? "#ffffff" : "#ffd400")}
          onChange={(c) => setFirst({ colors: [c, ...(first.colors ?? []).slice(1)] })}
        />
      </div>
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Появление</span>
          <select className="input" value={p.spawn ?? "drop"} onChange={(e) => patchProps({ spawn: e.target.value })}>
            {SPAWN_LABELS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="li-grow">
          <span className="li-cap">Стенки</span>
          <select className="input" value={p.walls ?? "frame"} onChange={(e) => patchProps({ walls: e.target.value })}>
            {WALL_LABELS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="li-row">
        <Num label="Тяжесть" value={p.gravity ?? 1} min={-3} max={3} step={0.1} onChange={(gravity) => patchProps({ gravity })} />
        <Num label="Упругость" value={p.restitution ?? 0.3} min={0} max={1} step={0.05} onChange={(restitution) => patchProps({ restitution })} />
        <Num label="Трение" value={p.friction ?? 0.3} min={0} max={1} step={0.05} onChange={(friction) => patchProps({ friction })} />
      </div>
      <div className="li-row">
        <Num label="Старт, с" value={p.at ?? 0} min={0} max={60} step={0.05} onChange={(at) => patchProps({ at })} />
        {p.spawn === "rain" ? <Num label="Дождь, с" value={p.spread ?? 1.5} min={0} max={20} step={0.1} onChange={(spread) => patchProps({ spread })} /> : null}
        {p.spawn === "burst" ? <Num label="Сила разлёта" value={p.power ?? 1400} min={0} max={8000} step={50} onChange={(power) => patchProps({ power })} /> : null}
        <Num label="Зерно" value={p.seed ?? 0} min={0} max={999999} onChange={(seed) => patchProps({ seed })} />
      </div>
      <div className="li-row">
        <Toggle
          label="Толчок"
          checked={Boolean(impulse)}
          onChange={(on) => patchProps({ impulse: on ? { at: (p.at ?? 0) + 0.6, x: 0.5, y: 0.85, force: 1600, radius: 0.35 } : null })}
        />
        {impulse ? (
          <>
            <Num label="Когда, с" value={impulse.at} min={0} max={60} step={0.05} onChange={(at) => patchProps({ impulse: { ...impulse, at } })} />
            <Num label="Сила" value={impulse.force ?? 1600} min={0} max={10000} step={50} onChange={(force) => patchProps({ impulse: { ...impulse, force } })} />
          </>
        ) : null}
      </div>
      <div className="li-note muted">
        Мир считается заранее — в превью и в рендере одинаково.
        {bodies.length > 1 ? ` Видов тел в слое: ${bodies.length} — здесь правится первый.` : ""} Рамку мира, точку
        толчка и невидимые опоры точнее ставит add_physics.
      </div>
    </Section>
  );
}
