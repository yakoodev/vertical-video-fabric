import { type TransitionJoin, type TransitionOptions, type TransitionSettings } from "@/api/types";

/** Куда едет картинка у хлыста и заливки (app/transitions_fx_cut.py). */
export const DIRECTION_LABELS: Record<NonNullable<TransitionSettings["direction"]>, string> = {
  left: "← влево",
  right: "→ вправо",
  up: "↑ вверх",
  down: "↓ вниз",
};

const DIRECTIONAL = ["whippan", "colorfill"];

/**
 * Переход отдельного стыка (`transition.joins`): у каждой склейки кусков — «как общий» или свой вид
 * (и направление у хлыста и заливки). Рендер ставит свой переход и внутри одной сцены.
 */
export function TransitionJoins({
  count,
  transition,
  options,
  onChange,
}: {
  count: number;
  transition: TransitionSettings;
  options: TransitionOptions | undefined;
  onChange: (joins: TransitionJoin[]) => void;
}) {
  const joins = transition.joins ?? [];
  const types = (options?.types ?? []).filter((t) => t.key !== "frag");
  const durations = (options?.fx?.durations ?? {}) as Record<string, number>;
  const directional = options?.fx?.directional ?? DIRECTIONAL;
  const set = (index: number, next: TransitionJoin | null) => {
    const rest = joins.filter((j) => j.join !== index);
    onChange((next ? [...rest, next] : rest).sort((a, b) => a.join - b.join));
  };
  return (
    <details className="field">
      <summary title="Свой переход на отдельной склейке: остальные — как общий">
        По стыкам{joins.length ? ` — своих ${joins.length}` : ""}
      </summary>
      {Array.from({ length: Math.min(count, 60) }, (_, index) => {
        const own = joins.find((j) => j.join === index);
        return (
          <div key={index} className="sub-options">
            <label className="field">
              <span>Стык {index + 1}→{index + 2}</span>
              <select
                className="input"
                value={own?.type ?? "inherit"}
                onChange={(e) => {
                  const type = e.target.value;
                  if (type === "inherit") return set(index, null);
                  const next: TransitionJoin = { join: index, type: type as TransitionJoin["type"] };
                  if (durations[type]) next.duration = durations[type];
                  if (own?.reason) next.reason = own.reason;
                  set(index, next);
                }}
              >
                <option value="inherit">как общий</option>
                {types.map((t) => (
                  <option key={t.key} value={t.key}>{t.label}</option>
                ))}
              </select>
            </label>
            {own && directional.includes(own.type) ? (
              <label className="field">
                <span>Направление</span>
                <select
                  className="input"
                  value={own.direction ?? "auto"}
                  onChange={(e) => {
                    const rest: TransitionJoin = { ...own };
                    delete rest.direction;
                    set(index, e.target.value === "auto"
                      ? rest
                      : { ...rest, direction: e.target.value as TransitionJoin["direction"] });
                  }}
                >
                  <option value="auto">по движению камеры</option>
                  {Object.entries(DIRECTION_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
        );
      })}
    </details>
  );
}
