/**
 * «Следовать за…»: слой ведётся за лицом, объектом или точкой кадра (app/tracking.py).
 *
 * Трек считает сервис (кадры основы клипа + детектор / шаблон) фоновой задачей; панель показывает
 * ход и кладёт готовый слой (ключи x/y/scale с пометкой `tracking`) в слои редактора — дальше
 * автосохранение и Ctrl+Z как у любой правки. Точку цели указывают кликом по превью.
 */
import { useEffect, useRef, useState } from "react";
import type { Layer, TrackTarget } from "@/motion";
import { motionApi, untrackLayer, type TrackReport } from "@/api/motion";

export type PickedPoint = { x: number; y: number; t: number };

type Props = {
  layer: Layer;
  planId?: number;
  now: () => number;
  onChange: (next: Layer) => void;
  /** Взвести выбор точки кликом по превью (null — отменить). */
  onPickPoint?: (cb: ((p: PickedPoint) => void) | null) => void;
};

const KINDS: { v: TrackTarget["kind"]; label: string; hint: string }[] = [
  { v: "face", label: "Лицо", hint: "Детектор лиц (живое лицо в кадре/вебке)" },
  { v: "object", label: "Объект", hint: "Объект класса: человек, машина, собака…" },
  { v: "point", label: "Точка", hint: "Клик по превью: аниме-модель, предмет, HUD — всё, что лицом не найти" },
];
const OBJECTS: [string, string][] = [
  ["person", "Человек"], ["car", "Машина"], ["dog", "Собака"], ["cat", "Кошка"], ["sports ball", "Мяч"],
  ["cell phone", "Телефон"], ["bicycle", "Велосипед"], ["motorcycle", "Мотоцикл"], ["bird", "Птица"],
  ["cup", "Чашка"], ["laptop", "Ноутбук"], ["tv", "Экран"],
];
const PRESETS: [string, string][] = [
  ["above_head", "Над головой"],
  ["under_face", "Под лицом"],
  ["arrow_to", "Стрелка на цель"],
  ["on_target", "Прямо на цели"],
];

function defaultPreset(layer: Layer): string {
  if (layer.tracking?.preset) return layer.tracking.preset;
  if (layer.type === "shape" && (layer.props as { kind?: string }).kind === "arrow") return "arrow_to";
  if (layer.type === "nameplate" || layer.type === "callout" || layer.type === "text") return "under_face";
  if (layer.type === "emoji" || layer.type === "image" || layer.type === "pngtuber") return "above_head";
  return "on_target";
}

const KIND_TITLE: Record<string, string> = { face: "лицо", object: "объект", point: "точку" };

export function TrackPanel({ layer, planId, now, onChange, onPickPoint }: Props) {
  const tracked = layer.tracking ?? null;
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<TrackTarget["kind"]>(tracked?.target.kind ?? "face");
  const [objClass, setObjClass] = useState(tracked?.target.class ?? "person");
  const [pickTarget, setPickTarget] = useState(false); // лицо/объект: «ближе к клику»
  const [preset, setPreset] = useState(defaultPreset(layer));
  const [scale, setScale] = useState(Boolean(tracked?.follow?.scale));
  const [smooth, setSmooth] = useState(tracked?.smooth ?? 0.5);
  const [waitingClick, setWaitingClick] = useState(false);
  const [job, setJob] = useState<{ progress: number; stage: string } | null>(null);
  const [report, setReport] = useState<TrackReport | null>(null);
  const [error, setError] = useState<{ text: string; hints: string[] } | null>(null);
  const alive = useRef(true);
  const layerRef = useRef(layer);
  layerRef.current = layer;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      onPickPoint?.(null);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const busy = Boolean(job) || waitingClick;

  const run = async (target: TrackTarget) => {
    if (!planId) return;
    setError(null);
    setReport(null);
    setJob({ progress: 0, stage: "запуск" });
    try {
      const { job_id } = await motionApi.track(planId, {
        layer: layerRef.current,
        target,
        preset,
        follow: { scale: kind === "point" ? false : scale },
        smooth,
      });
      for (;;) {
        await new Promise((r) => window.setTimeout(r, 450));
        if (!alive.current) return;
        const st = await motionApi.trackJob(job_id);
        if (st.state === "done" && st.result) {
          onChange(st.result.layer);
          setReport(st.result.report);
          break;
        }
        if (st.state === "error") {
          setError({ text: st.error ?? "трек не построен", hints: st.hints ?? [] });
          break;
        }
        setJob({ progress: st.progress, stage: st.stage });
      }
    } catch (e) {
      const detail = (e as { detail?: { error?: string; hints?: string[] } }).detail;
      setError({ text: detail?.error ?? (e as Error).message, hints: detail?.hints ?? [] });
    } finally {
      if (alive.current) setJob(null);
    }
  };

  const start = () => {
    const needClick = kind === "point" || pickTarget;
    if (needClick) {
      setWaitingClick(true);
      onPickPoint?.((p) => {
        setWaitingClick(false);
        const target: TrackTarget =
          kind === "point" ? { kind, x: p.x, y: p.y, t: p.t } : { kind, mode: "near", x: p.x, y: p.y, t: p.t, ...(kind === "object" ? { class: objClass } : {}) };
        void run(target);
      });
      return;
    }
    const t = Math.min(layer.end, Math.max(layer.start, now()));
    void run(kind === "object" ? { kind, class: objClass, mode: "largest", t } : { kind, mode: "largest", t });
  };

  const cancelPick = () => {
    onPickPoint?.(null);
    setWaitingClick(false);
  };

  const keysCount = tracked ? Math.max(0, ...tracked.props.map((p) => layer.keys?.[p]?.length ?? 0)) : 0;

  return (
    <div className="li-track">
      {tracked ? (
        <div className="li-track-on">
          <span>
            🎯 Следует за: <b>{KIND_TITLE[tracked.target.kind] ?? tracked.target.kind}</b>
            {tracked.target.class ? ` «${tracked.target.class}»` : ""} · ключей {keysCount}
          </span>
          <div className="li-row">
            <button type="button" className="btn ghost sm" disabled={busy || !planId} title="Пересчитать трек на текущее время слоя" onClick={() => void run(tracked.target)}>
              ↻ Пересчитать
            </button>
            <button type="button" className="btn ghost sm" disabled={busy} title="Убрать ключи трека — слой встанет туда, где трек начинался" onClick={() => onChange(untrackLayer(layer))}>
              ✕ Снять трек
            </button>
            <button type="button" className="btn ghost sm" disabled={busy} onClick={() => setOpen((v) => !v)}>
              {open ? "Скрыть" : "Другая цель…"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn sm" disabled={!planId} onClick={() => setOpen((v) => !v)} title="Слой поедет за лицом, объектом или точкой кадра">
          🎯 Следовать за…
        </button>
      )}

      {open ? (
        <div className="li-track-form">
          <div className="seg li-track-seg">
            {KINDS.map((k) => (
              <button key={k.v} type="button" title={k.hint} className={`seg-item${kind === k.v ? " active" : ""}`} onClick={() => setKind(k.v)}>
                {k.label}
              </button>
            ))}
          </div>
          {kind === "object" ? (
            <label className="li-grow">
              <span className="li-cap">Что ищем</span>
              <select className="input" value={objClass} onChange={(e) => setObjClass(e.target.value)}>
                {OBJECTS.map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {kind !== "point" ? (
            <label className="switch li-switch" title="Несколько лиц/объектов: кликни по нужному на превью; без галочки — самое крупное">
              <input type="checkbox" checked={pickTarget} onChange={(e) => setPickTarget(e.target.checked)} />
              <span className="switch-track" />
              <span>Указать на превью</span>
            </label>
          ) : (
            <div className="li-note muted">Нажми «Начать» и кликни по цели на превью — в тот момент клипа, где она хорошо видна.</div>
          )}
          <label className="li-grow">
            <span className="li-cap">Где слой</span>
            <select className="input" value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {kind !== "point" ? (
            <label className="switch li-switch" title="Слой растёт и уменьшается вместе с целью (лицо ближе — эмодзи крупнее)">
              <input type="checkbox" checked={scale} onChange={(e) => setScale(e.target.checked)} />
              <span className="switch-track" />
              <span>Масштаб с целью</span>
            </label>
          ) : null}
          <label className="field range">
            <span>Плавность · {Math.round(smooth * 100)}%</span>
            <input type="range" min={0} max={100} value={Math.round(smooth * 100)} onChange={(e) => setSmooth(Number(e.target.value) / 100)} />
          </label>
          <div className="li-row">
            {waitingClick ? (
              <>
                <span className="li-note li-track-wait">Кликни по цели на превью…</span>
                <button type="button" className="btn ghost sm" onClick={cancelPick}>
                  Отмена
                </button>
              </>
            ) : (
              <button type="button" className="btn sm primary" disabled={busy || !planId} onClick={start}>
                {kind === "point" || pickTarget ? "Начать и указать" : "Начать"}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {job ? (
        <div className="li-track-progress" title={job.stage}>
          <div className="li-track-bar">
            <span style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <span className="li-note muted">
            {job.stage} · {Math.round(job.progress * 100)}%
          </span>
        </div>
      ) : null}
      {error ? (
        <div className="li-note li-track-err">
          {error.text}
          {error.hints.map((h) => (
            <div key={h} className="muted">
              · {h}
            </div>
          ))}
        </div>
      ) : null}
      {report ? (
        <div className="li-note muted">
          Цель найдена в {Math.round(report.found * 100)}% кадров{report.cached ? " (из кэша)" : report.seconds ? ` за ${report.seconds} с` : ""}
          {[...report.notes, ...report.warnings].map((n) => (
            <div key={n} className={report.warnings.includes(n) ? "li-track-err" : undefined}>
              · {n}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
