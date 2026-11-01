/**
 * Инспектор слоя `scene` (движок сцен, docs/motion/scene-engine.md): вьюпорт, камера ручками
 * (тип, облёт, поле зрения / zoom) и JSON сцены с проверкой на лету — те же сообщения, что
 * видит агент в preview_scene_frame. Анимированные поля камеры ручками не трогаем — только JSON.
 */
import { useEffect, useMemo, useState } from "react";
import { validateScene, type Layer, type SceneLayer } from "@/motion";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
/** Поле можно крутить ручкой: не задано или просто число. */
const isStatic = (v: unknown) => v === undefined || typeof v === "number";
const pretty = (v: unknown) => JSON.stringify(v ?? { nodes: [] }, null, 2);

function Slider({ label, value, min, max, step = 1, onChange, disabled }: { label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; disabled?: boolean }) {
  return (
    <label className="li-num" title={disabled ? "анимировано — правь в JSON" : undefined}>
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} />
      <em>{disabled ? "анимация" : Math.round(value * 100) / 100}</em>
    </label>
  );
}

function OptNum({ label, value, placeholder, onChange }: { label: string; value: number | undefined; placeholder: string; onChange: (v: number | undefined) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = value === undefined ? "" : String(value);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft.replace(",", "."));
    setDraft(null);
    onChange(draft.trim() === "" || !Number.isFinite(v) || v <= 0 ? undefined : Math.round(v));
  };
  return (
    <label className="li-num">
      <span>{label}</span>
      <input
        className="input"
        inputMode="decimal"
        placeholder={placeholder}
        value={draft ?? shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
      />
    </label>
  );
}

export function SceneInspector({ layer, onChange }: { layer: SceneLayer; onChange: (layer: Layer) => void }) {
  const scene = (isObj(layer.props?.scene) ? layer.props.scene : { nodes: [] }) as Obj;
  const [draft, setDraft] = useState(() => pretty(scene));
  const [dirty, setDirty] = useState(false);
  // Слой поменялся снаружи (Ctrl+Z, агент, ручки камеры) — показываем новый JSON, если нет своих правок.
  useEffect(() => {
    if (!dirty) setDraft(pretty(scene));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layer.props?.scene]);

  const parsed = useMemo(() => {
    try {
      return { value: JSON.parse(draft) as unknown, error: null as string | null };
    } catch (e) {
      return { value: null, error: (e as Error).message };
    }
  }, [draft]);
  const check = useMemo(() => (parsed.error ? null : validateScene(parsed.value)), [parsed]);

  const setProps = (patch: Obj) => onChange({ ...layer, props: { ...layer.props, ...patch } } as Layer);
  const setScene = (next: Obj) => {
    setDirty(false);
    setProps({ scene: next });
  };
  const apply = () => {
    if (!check || !check.ok || !isObj(parsed.value)) return;
    setScene(parsed.value);
  };

  const cam = (isObj(scene.camera) ? scene.camera : {}) as Obj;
  const ortho = cam.type === "orthographic" || cam.type === "ortho";
  const orbit = (isObj(cam.orbit) ? cam.orbit : {}) as Obj;
  const hasPosition = cam.position !== undefined && !isObj(cam.orbit);
  const setCam = (patch: Obj) => setScene({ ...scene, camera: { ...cam, ...patch } });
  const setOrbit = (patch: Obj) => {
    const { position: _p, ...rest } = cam;
    void _p;
    setScene({ ...scene, camera: { ...rest, orbit: { ...orbit, ...patch } } });
  };
  const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

  return (
    <>
      <div className="li-row">
        <OptNum label="Ширина" value={typeof layer.props.w === "number" ? layer.props.w : undefined} placeholder="кадр" onChange={(w) => setProps({ w })} />
        <OptNum label="Высота" value={typeof layer.props.h === "number" ? layer.props.h : undefined} placeholder="кадр" onChange={(h) => setProps({ h })} />
        <label className="switch li-switch">
          <input type="checkbox" checked={layer.props.clip !== false} onChange={(e) => setProps({ clip: e.target.checked ? undefined : false })} />
          <span className="switch-track" />
          <span>Обрезать</span>
        </label>
      </div>

      <span className="li-cap">Камера</span>
      <div className="li-row">
        <select className="input" value={ortho ? "orthographic" : "perspective"} onChange={(e) => setCam({ type: e.target.value })}>
          <option value="perspective">Перспектива</option>
          <option value="orthographic">Ортография (2D)</option>
        </select>
      </div>
      {hasPosition ? (
        <p className="muted" style={{ fontSize: 12, margin: "4px 0" }}>
          Камера задана position/target — облёт ручками заменит их на orbit.
        </p>
      ) : null}
      <Slider label="Облёт °" value={num(orbit.azimuth, 0)} min={-180} max={180} disabled={!isStatic(orbit.azimuth)} onChange={(azimuth) => setOrbit({ azimuth })} />
      <Slider label="Наклон °" value={num(orbit.elevation, 0)} min={-89} max={89} disabled={!isStatic(orbit.elevation)} onChange={(elevation) => setOrbit({ elevation })} />
      {ortho ? (
        <Slider label="Zoom" value={num(cam.zoom, 1)} min={0.1} max={4} step={0.01} disabled={!isStatic(cam.zoom)} onChange={(zoom) => setCam({ zoom })} />
      ) : (
        <>
          <Slider label="Поле зрения °" value={num(cam.fov, 40)} min={10} max={120} disabled={!isStatic(cam.fov)} onChange={(fov) => setCam({ fov })} />
          <Slider label="Zoom" value={num(cam.zoom, 1)} min={0.2} max={4} step={0.01} disabled={!isStatic(cam.zoom)} onChange={(zoom) => setCam({ zoom })} />
        </>
      )}

      <span className="li-cap">Сцена (JSON)</span>
      <textarea
        className="input"
        spellCheck={false}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            apply();
          }
        }}
        rows={16}
        style={{ fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12, lineHeight: 1.35, whiteSpace: "pre", resize: "vertical", width: "100%" }}
      />
      <div className="li-row">
        <button type="button" className="btn sm" disabled={!dirty || !check?.ok} onClick={apply} title="Ctrl+Enter">
          Применить
        </button>
        <button
          type="button"
          className="btn ghost sm"
          disabled={!dirty}
          onClick={() => {
            setDraft(pretty(scene));
            setDirty(false);
          }}
        >
          Отменить
        </button>
        {check ? (
          <span className="muted" style={{ fontSize: 12 }}>
            узлов {check.stats.nodes}, граней ≈{check.stats.faces}
          </span>
        ) : null}
      </div>
      {parsed.error ? <p style={{ color: "#ff6b6b", fontSize: 12, margin: "4px 0" }}>JSON: {parsed.error}</p> : null}
      {check && (check.errors.length || check.warnings.length) ? (
        <ul style={{ fontSize: 12, margin: "4px 0", paddingLeft: 16, maxHeight: 160, overflow: "auto" }}>
          {check.errors.map((p, i) => (
            <li key={`e${i}`} style={{ color: "#ff6b6b" }}>
              {p.path}: {p.message}
            </li>
          ))}
          {check.warnings.map((p, i) => (
            <li key={`w${i}`} style={{ color: "#f5c451" }}>
              {p.path}: {p.message}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="muted" style={{ fontSize: 12, margin: "4px 0" }}>
        Схема и примеры — docs/motion/scene-engine.md. y вверх, единица = опорный px на z = 0.
      </p>
    </>
  );
}
