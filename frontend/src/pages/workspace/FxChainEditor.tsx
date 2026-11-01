/**
 * 🎛 Цепочка звуковых эффектов (app/audio_fx.py) — компактно: пресет, «+ эффект», параметры
 * каждого звена. Один компонент для звукового слоя (props.fx), голоса и музыки клипа
 * (render_settings.mix.voice_fx / music_fx). Параметры и пределы — с сервера (audio_fx_reference).
 */
import { useQuery } from "@tanstack/react-query";
import { audioMixApi, defaultEffect, type FxEffect, type FxReference } from "@/api/audioMix";

const LEGACY = ["muffle", "underwater", "echo", "reverb", "phone", "radio", "denoise"];

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
                {eff.fx === "legacy" ? ` (${String(eff.kind)})` : ""}
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
                            {k}
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                }
                const raw = eff[name];
                const value = typeof raw === "number" ? raw : typeof p.default === "number" ? p.default : "";
                return (
                  <label key={name} className="li-num" title={p.about}>
                    <span>{name}</span>
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
