/**
 * 🎛 Цепочка звуковых эффектов (app/audio_fx.py) — компактно: пресет, «+ эффект», параметры
 * каждого звена. Один компонент для звукового слоя (props.fx), голоса и музыки клипа
 * (render_settings.mix.voice_fx / music_fx). Параметры и пределы — с сервера (audio_fx_reference).
 */
import { useQuery } from "@tanstack/react-query";
import { audioMixApi, defaultEffect, type FxEffect, type FxReference } from "@/api/audioMix";

const LEGACY = ["muffle", "underwater", "echo", "reverb", "phone", "radio", "denoise"];
// Подписи по-русски: раньше показывались служебные имена (threshold_db, sweep_start, muffle…).
const LEGACY_LABEL: Record<string, string> = {
  muffle: "глухо (за стеной)",
  underwater: "под водой",
  echo: "эхо",
  reverb: "зал",
  phone: "телефон",
  radio: "рация",
  denoise: "шумодав",
};
const PARAM_LABEL: Record<string, string> = {
  low_cut: "Срез низа",
  high_cut: "Срез верха",
  low: "Низ",
  presence: "Разборчивость",
  high: "Воздух",
  f: "Частота",
  to: "Свип до",
  sweep_start: "Свип с, с",
  sweep_end: "Свип по, с",
  threshold_db: "Порог",
  ratio: "Сжатие",
  attack_ms: "Атака",
  release_ms: "Отпускание",
  makeup_db: "Подъём",
  knee_db: "Мягкость",
  amount: "Сила",
  mix: "Доля",
  ms: "Задержка",
  feedback: "Затухание",
  taps: "Повторов",
  semitones: "Полутоны",
  rate: "Скорость",
  width: "Ширина",
  drive: "Перегруз",
  bits: "Бит",
  downsample: "Прореживание",
  depth: "Глубина",
  db: "Громкость",
};
/** Единица из пояснения сервера («дБ на 3.5 кГц…» → «дБ»): короткая, без хвоста. */
function unitOf(about?: string): string {
  const head = (about ?? "").split(/[\s,(]/)[0];
  return ["Гц", "дБ", "мс", "с", "×"].includes(head) ? head : "";
}

function step(min?: number, max?: number): number {
  const span = (max ?? 1) - (min ?? 0);
  if (span <= 2) return 0.05;
  if (span <= 30) return 0.5;
  if (span <= 600) return 1;
  return 10;
}

export function FxChainEditor({
  chain,
  onChange,
  title = "Эффекты",
}: {
  chain: FxEffect[] | undefined;
  onChange: (next: FxEffect[] | undefined) => void;
  title?: string;
}) {
  const ref = useQuery({ queryKey: ["audio-fx-reference"], queryFn: audioMixApi.reference, staleTime: Infinity });
  const list = chain ?? [];
  const data: FxReference | undefined = ref.data;
  const set = (next: FxEffect[]) => onChange(next.length ? next : undefined);
  const patch = (i: number, p: Record<string, unknown>) => set(list.map((e, k) => (k === i ? { ...e, ...p } : e)));

  return (
    <div className="fx-chain" title="Цепочка по порядку, как педали: эквалайзер → компрессор → пространство">
      <div className="li-row">
        <span className="muted fx-chain-title">{title}</span>
        <select
          className="input"
          value=""
          disabled={!data}
          onChange={(e) => {
            const preset = data?.presets[e.target.value];
            if (preset) set(preset.chain.map((x) => ({ ...x })));
          }}
          title="Готовая цепочка (заменит текущую)"
        >
          <option value="">Пресет…</option>
          {Object.entries(data?.presets ?? {}).map(([name, p]) => (
            <option key={name} value={name}>
              {name} — {p.about}
            </option>
          ))}
        </select>
        <select
          className="input"
          value=""
          disabled={!data || list.length >= (data?.max_chain ?? 8)}
          onChange={(e) => data && e.target.value && set([...list, defaultEffect(e.target.value, data)])}
          title="Добавить звено в конец цепочки"
        >
          <option value="">+ эффект</option>
          {Object.entries(data?.effects ?? {}).map(([kind, spec]) => (
            <option key={kind} value={kind}>
              {spec.label}
            </option>
          ))}
        </select>
      </div>
      {list.map((eff, i) => {
        const spec = data?.effects[eff.fx];
        return (
          <details key={`${eff.fx}-${i}`} className="fx-link">
            <summary>
              <span>
                {i + 1}. {spec?.label ?? eff.fx}
                {eff.fx === "legacy" ? ` (${LEGACY_LABEL[String(eff.kind)] ?? String(eff.kind)})` : ""}
              </span>
              <span className="fx-link-actions">
                {i > 0 ? (
                  <button
                    type="button"
                    className="btn ghost sm"
                    title="Выше в цепочке"
                    onClick={(e) => {
                      e.preventDefault();
                      const next = [...list];
                      [next[i - 1], next[i]] = [next[i], next[i - 1]];
                      set(next);
                    }}
                  >
                    ↑
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn ghost sm"
                  title="Убрать звено"
                  onClick={(e) => {
                    e.preventDefault();
                    set(list.filter((_, k) => k !== i));
                  }}
                >
                  ✕
                </button>
              </span>
            </summary>
            <div className="li-row fx-params">
              {Object.entries(spec?.params ?? {}).map(([name, p]) => {
                if (name === "preset") {
                  return (
                    <label key={name} className="li-num" title={p.about}>
                      <span>Пространство</span>
                      <select className="input" value={String(eff.preset ?? p.default)} onChange={(e) => patch(i, { preset: e.target.value })}>
                        {Object.entries(data?.reverb_presets ?? {}).map(([k, label]) => (
                          <option key={k} value={k}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                }
                if (name === "kind") {
                  return (
                    <label key={name} className="li-num" title={p.about}>
                      <span>Вид</span>
                      <select className="input" value={String(eff.kind ?? p.default)} onChange={(e) => patch(i, { kind: e.target.value })}>
                        {LEGACY.map((k) => (
                          <option key={k} value={k}>
                            {LEGACY_LABEL[k] ?? k}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                }
                // Не числа (полосы эквалайзера bands — список) правит агент: числовое поле их ломало.
                if (p.default !== null && p.default !== undefined && typeof p.default !== "number") return null;
                const raw = eff[name];
                const value = typeof raw === "number" ? raw : typeof p.default === "number" ? p.default : "";
                return (
                  <label key={name} className="li-num" title={p.about}>
                    <span>
                      {PARAM_LABEL[name] ?? name}
                      {unitOf(p.about) ? `, ${unitOf(p.about)}` : ""}
                    </span>
                    <input
                      className="input"
                      type="number"
                      min={p.min}
                      max={p.max}
                      step={step(p.min, p.max)}
                      value={value}
                      placeholder={p.default === null ? "—" : undefined}
                      onChange={(e) => {
                        const v = e.target.value === "" ? undefined : Number(e.target.value);
                        if (v === undefined) {
                          const next = { ...eff };
                          delete next[name];
                          set(list.map((x, k) => (k === i ? next : x)));
                        } else if (Number.isFinite(v)) {
                          patch(i, { [name]: v });
                        }
                      }}
                    />
                  </label>
                );
              })}
              {spec?.about ? <div className="li-note muted">{spec.about}</div> : null}
            </div>
          </details>
        );
      })}
      {list.length ? (
        <div className="li-note muted">Превью редактора эффекты не играет — слышно в рендере; громкость проверит «Сведение».</div>
      ) : null}
    </div>
  );
}
