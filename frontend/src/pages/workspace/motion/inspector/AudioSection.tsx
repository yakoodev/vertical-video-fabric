import { FxChainEditor } from "@/pages/workspace/FxChainEditor";
import { Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";

export function AudioSection({ layer, patchProps }: { layer: LayerOf<"audio">; patchProps: PatchProps }) {
  return (
    <Section title="Звук">
      <div title="Эффект — сервис сам ставит его под голос клипа, громкость только тише. Дорожка (песня, звук ролика) — без автоподгонки: громкость × усиление">
        <Toggle
          label="Дорожка (без подгонки под голос)"
          checked={layer.props.mode === "track"}
          onChange={(on) =>
            patchProps(on ? { mode: "track" } : { mode: undefined, gain_db: undefined, volume: Math.min(1, layer.props.volume ?? 1) })
          }
        />
      </div>
      <label className="field range">
        <span>Громкость · {Math.round((layer.props.volume ?? 1) * 100)}%</span>
        <input
          type="range"
          min={0}
          max={layer.props.mode === "track" ? 200 : 100}
          value={Math.round((layer.props.volume ?? 1) * 100)}
          onChange={(e) => patchProps({ volume: Number(e.target.value) / 100 })}
        />
      </label>
      {layer.props.mode === "track" ? (
        <div className="li-row">
          <Num label="Усиление, дБ" value={layer.props.gain_db ?? 0} min={-30} max={12} step={0.5} onChange={(gain_db) => patchProps({ gain_db })} />
        </div>
      ) : null}
      <div className="li-row">
        <Num label="Вход, с" value={layer.props.fadeIn ?? 0} min={0} max={5} step={0.05} onChange={(fadeIn) => patchProps({ fadeIn })} />
        <Num label="Выход, с" value={layer.props.fadeOut ?? 0} min={0} max={5} step={0.05} onChange={(fadeOut) => patchProps({ fadeOut })} />
        <Num label="С секунды" value={layer.props.offset ?? 0} min={0} step={0.1} onChange={(offset) => patchProps({ offset })} />
      </div>
      <Toggle label="Приглушить речь под звуком" checked={Boolean(layer.props.duck)} onChange={(duck) => patchProps({ duck })} />
      <div className="li-row" title="Роль в сведении: по ней «Сведение» сравнивает голос с музыкой и звуками. Авто — по имени слоя и длине">
        <label className="li-num li-wide">
          <span>Роль в сведении</span>
          <select
            className="input"
            value={layer.props.role ?? ""}
            onChange={(e) => patchProps({ role: (e.target.value || undefined) as typeof layer.props.role })}
          >
            <option value="">авто</option>
            <option value="voice">голос / озвучка</option>
            <option value="music">музыка</option>
            <option value="sfx">звук-эффект</option>
            <option value="ambience">фон</option>
          </select>
        </label>
      </div>
      <FxChainEditor chain={layer.props.fx} onChange={(fx) => patchProps({ fx })} />
    </Section>
  );
}
