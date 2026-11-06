// Разделы инспектора: эффект кадра и частицы.
import { FX_DEFAULTS, FX_KINDS, type FxKind } from "@/motion";
import { FX_TITLES } from "@/pages/workspace/motion/edit";
import { Color, Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";

export function FxSection({ layer, patchProps }: { layer: LayerOf<"fx">; patchProps: PatchProps }) {
  return (
    <Section title="Эффект кадра">
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Вид</span>
          <select
            className="input"
            value={layer.props.kind ?? "vhs"}
            // Смена вида — сила и наложение «как у вида»: у зерна и вспышки они свои.
            onChange={(e) => patchProps({ kind: e.target.value as FxKind, intensity: undefined, blend: undefined })}
          >
            {FX_KINDS.map((k) => (
              <option key={k} value={k}>
                {FX_TITLES[k]}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="li-cap">Наложение</span>
          <select className="input" value={layer.props.blend ?? "auto"} onChange={(e) => patchProps({ blend: e.target.value === "auto" ? undefined : e.target.value })}>
            <option value="auto">Как у вида</option>
            <option value="normal">Обычное</option>
            <option value="screen">Осветление</option>
            <option value="multiply">Умножение</option>
            <option value="overlay">Перекрытие</option>
            <option value="add">Сложение</option>
          </select>
        </label>
      </div>
      <div className="li-row">
        <Num
          label="Сила"
          value={layer.props.intensity ?? FX_DEFAULTS[layer.props.kind ?? "vhs"]?.intensity ?? 0.6}
          min={0}
          max={1}
          step={0.05}
          onChange={(intensity) => patchProps({ intensity })}
        />
        <Num label="Скорость" value={layer.props.speed ?? 1} min={0.1} max={4} step={0.1} onChange={(speed) => patchProps({ speed })} />
        {layer.props.kind === "grain_overlay" || layer.props.kind === "scanlines" ? (
          <Num label="Размер" value={layer.props.size ?? (layer.props.kind === "scanlines" ? 4 : 1.6)} min={0.5} max={12} step={0.1} onChange={(size) => patchProps({ size })} />
        ) : null}
        {layer.props.kind === "flash_frame" ? (
          <Color label="Цвет" value={layer.props.color ?? "#ffffff"} onChange={(color) => patchProps({ color })} />
        ) : null}
      </div>
      <div className="li-note muted">Ложится на видео и слои ниже по дорожкам; слои выше — чистые. Больше 1–2 эффектов на ролик — каша.</div>
    </Section>
  );
}

export function ParticlesSection({ layer, patchProps }: { layer: LayerOf<"particles">; patchProps: PatchProps }) {
  return (
    <Section title="Частицы">
      <div className="li-row">
        <label className="li-grow">
          <span className="li-cap">Вид</span>
          <select className="input" value={layer.props.kind ?? "burst"} onChange={(e) => patchProps({ kind: e.target.value })}>
            {[
              ["burst", "Разлёт искр"],
              ["sparkle", "Мерцание"],
              ["confetti", "Конфетти"],
              ["rise", "Искорки вверх"],
            ].map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="li-grow">
          <span className="li-cap">Форма</span>
          <select className="input" value={layer.props.shape ?? ""} onChange={(e) => patchProps({ shape: e.target.value || undefined })}>
            {[
              ["", "По виду"],
              ["star4", "Искра ✦"],
              ["star", "Звезда ★"],
              ["dot", "Точка"],
              ["circle", "Кольцо"],
              ["square", "Листок"],
              ["heart", "Сердце"],
            ].map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="li-row">
        <Num label="Сколько" value={layer.props.count ?? 40} min={1} max={200} onChange={(count) => patchProps({ count })} />
        <Num label="Размер" value={layer.props.size ?? 28} min={2} max={400} onChange={(size) => patchProps({ size })} />
        <Num label="Разлёт" value={layer.props.spread ?? 360} min={0} max={3000} step={10} onChange={(spread) => patchProps({ spread })} />
      </div>
      <div className="li-row">
        <Num label="Жизнь, с" value={layer.props.duration ?? 1.2} min={0.1} max={20} step={0.1} onChange={(duration) => patchProps({ duration })} />
        <Num label="Тяжесть" value={layer.props.gravity ?? 0} min={-5000} max={5000} step={50} onChange={(gravity) => patchProps({ gravity })} />
        <Num label="Зерно" value={layer.props.seed ?? 0} min={0} max={999999} onChange={(seed) => patchProps({ seed })} />
      </div>
      <div className="li-row">
        <Color label="Цвет 1" value={layer.props.colors?.[0] ?? "#ffd400"} onChange={(c) => patchProps({ colors: [c, ...(layer.props.colors ?? ["#ffd400", "#ffffff"]).slice(1)] })} />
        <Color label="Цвет 2" value={layer.props.colors?.[1] ?? "#ffffff"} onChange={(c) => patchProps({ colors: [layer.props.colors?.[0] ?? "#ffd400", c, ...(layer.props.colors ?? []).slice(2)] })} />
      </div>
      <div className="li-row">
        <Toggle label="Свечение" checked={Boolean(layer.props.glow)} onChange={(glow) => patchProps({ glow })} />
        <Toggle label="Повторять" checked={Boolean(layer.props.repeat)} onChange={(repeat) => patchProps({ repeat })} />
      </div>
    </Section>
  );
}
