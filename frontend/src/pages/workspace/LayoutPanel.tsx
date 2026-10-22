/**
 * Инспектор «Раскладка»: обычный кадр, вебка сверху/снизу над игрой или вебка
 * окошком. Вебку находит сервер (🎯) или её обводят рамкой на кадре исходника.
 * Значение — `render.layout` клипа (см. ./layout.ts и app/stream_layout.py).
 */
import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { facecamApi } from "@/api/sources";
import type { Backdrop } from "./chromaKey";
import { DEFAULT_CAM, normalizeCam, normalizeLayout, type LayoutRect, type StreamLayout } from "./layout";

type Props = {
  sourceId: number;
  layout: StreamLayout | null | undefined;
  onChange: (next: StreamLayout) => void;
  /** Секунда исходника для кадра в редакторе вебки (обычно — под курсором). */
  frameAt: number;
  /** Источник на зелёнке: показываем фон за вырезанным персонажем. */
  keyed?: boolean;
  backdrop?: Backdrop;
  onBackdrop?: (next: Backdrop) => void;
};

type Choice = { id: string; label: string; hint: string; mode: StreamLayout["mode"]; pos?: StreamLayout["cam_pos"] };

const CHOICES: Choice[] = [
  { id: "single", label: "Обычный", hint: "Один кадр 9:16, как раньше", mode: "single" },
  { id: "split-top", label: "Вебка сверху", hint: "Лицо стримера полосой сверху, игра снизу", mode: "split", pos: "top" },
  { id: "split-bottom", label: "Вебка снизу", hint: "Игра сверху, лицо стримера полосой снизу", mode: "split", pos: "bottom" },
  { id: "pip", label: "Окошко", hint: "Обычный кадр 9:16 и вебка окошком поверх", mode: "pip_cam" },
  { id: "meme", label: "Мем", hint: "Горизонтальное видео целиком сверху, ниже фон под картинки и титры", mode: "meme" },
];

const sameCam = (a: LayoutRect, b: LayoutRect) =>
  Math.abs(a.x - b.x) < 1e-3 && Math.abs(a.y - b.y) < 1e-3 && Math.abs(a.w - b.w) < 1e-3 && Math.abs(a.h - b.h) < 1e-3;

/** Схема раскладки — маленький телефон 9:16. */
function Thumb({ choice }: { choice: Choice }) {
  const cam = "var(--accent)";
  const game = "var(--surface-3)";
  return (
    <svg className="lay-thumb" viewBox="0 0 18 32" aria-hidden="true">
      {choice.mode === "single" && <rect x="0" y="0" width="18" height="32" rx="2" fill={game} />}
      {choice.mode === "split" && (
        <>
          <rect x="0" y={choice.pos === "top" ? 0 : 20} width="18" height="12" rx="2" fill={cam} />
          <rect x="0" y={choice.pos === "top" ? 13 : 0} width="18" height="19" rx="2" fill={game} />
        </>
      )}
      {choice.mode === "pip_cam" && (
        <>
          <rect x="0" y="0" width="18" height="32" rx="2" fill={game} />
          <rect x="4" y="3" width="10" height="7" rx="1.5" fill={cam} />
        </>
      )}
      {choice.mode === "meme" && (
        <>
          <rect x="0" y="0" width="18" height="32" rx="2" fill="var(--surface-2)" />
          <rect x="0" y="2" width="18" height="10" rx="1" fill={game} />
          <rect x="4" y="15" width="10" height="14" rx="1.5" fill={cam} />
        </>
      )}
    </svg>
  );
}

type Drag = { mode: "move" | "nw" | "ne" | "sw" | "se"; px: number; py: number; start: LayoutRect };

/** Рамка вебки на кадре исходника: тянуть за середину — двигать, за углы — менять размер. */
function CamEditor({ src, cam, onCommit }: { src: string; cam: LayoutRect; onCommit: (cam: LayoutRect) => void }) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState<LayoutRect | null>(null);
  const [aspect, setAspect] = useState(16 / 9);
  const [broken, setBroken] = useState(false);
  const dragRef = useRef<Drag | null>(null);
  const shown = draft ?? cam;

  useEffect(() => setBroken(false), [src]);

  const toFrac = (clientX: number, clientY: number) => {
    const r = frameRef.current?.getBoundingClientRect();
    if (!r || !r.width || !r.height) return { fx: 0, fy: 0 };
    return { fx: (clientX - r.left) / r.width, fy: (clientY - r.top) / r.height };
  };

  const begin = (mode: Drag["mode"]) => (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const { fx, fy } = toFrac(e.clientX, e.clientY);
    dragRef.current = { mode, px: fx, py: fy, start: shown };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const move = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const { fx, fy } = toFrac(e.clientX, e.clientY);
    const dx = fx - d.px;
    const dy = fy - d.py;
    const s = d.start;
    const MIN = 0.04;
    let next: LayoutRect;
    if (d.mode === "move") {
      next = { ...s, x: Math.min(1 - s.w, Math.max(0, s.x + dx)), y: Math.min(1 - s.h, Math.max(0, s.y + dy)) };
    } else {
      let left = s.x;
      let top = s.y;
      let right = s.x + s.w;
      let bottom = s.y + s.h;
      if (d.mode === "nw" || d.mode === "sw") left = Math.min(right - MIN, Math.max(0, s.x + dx));
      if (d.mode === "ne" || d.mode === "se") right = Math.max(left + MIN, Math.min(1, right + dx));
      if (d.mode === "nw" || d.mode === "ne") top = Math.min(bottom - MIN, Math.max(0, s.y + dy));
      if (d.mode === "sw" || d.mode === "se") bottom = Math.max(top + MIN, Math.min(1, bottom + dy));
      next = { x: left, y: top, w: right - left, h: bottom - top };
    }
    setDraft(next);
  };

  const end = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    if (draft) onCommit(normalizeCam(draft));
    setDraft(null);
  };

  return (
    <div className="lay-frame" ref={frameRef} style={{ aspectRatio: String(aspect) }} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
      {broken ? (
        <div className="lay-frame-empty muted">Кадр исходника недоступен</div>
      ) : (
        <img
          src={src}
          alt=""
          draggable={false}
          onLoad={(e) => {
            const img = e.currentTarget;
            if (img.naturalWidth && img.naturalHeight) setAspect(img.naturalWidth / img.naturalHeight);
          }}
          onError={() => setBroken(true)}
        />
      )}
      <div
        className="lay-cam"
        style={{ left: `${shown.x * 100}%`, top: `${shown.y * 100}%`, width: `${shown.w * 100}%`, height: `${shown.h * 100}%` }}
        onPointerDown={begin("move")}
        title="Вебка: тяни, чтобы сдвинуть; за углы — размер"
      >
        <span className="lay-cam-tag">вебка</span>
        {(["nw", "ne", "sw", "se"] as const).map((corner) => (
          <span key={corner} className={`lay-handle lay-${corner}`} onPointerDown={begin(corner)} />
        ))}
      </div>
    </div>
  );
}

export function LayoutPanel({ sourceId, layout: raw, onChange, frameAt, keyed, backdrop, onBackdrop }: Props) {
  const layout = normalizeLayout(raw);
  const [savedCam, setSavedCam] = useState<LayoutRect | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    setSavedCam(null);
    setNote(null);
    facecamApi
      .get(sourceId)
      .then((r) => alive && setSavedCam(r.cam ? normalizeCam(r.cam) : null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [sourceId]);

  const patch = (p: Partial<StreamLayout>) => onChange(normalizeLayout({ ...layout, ...p }));

  const pick = (c: Choice) => {
    const next: Partial<StreamLayout> = { mode: c.mode };
    if (c.pos) next.cam_pos = c.pos;
    // Первое включение вебки на клипе — берём вебку, запомненную на источнике.
    if (c.mode !== "single" && layout.mode === "single" && savedCam && sameCam(layout.cam, DEFAULT_CAM)) next.cam = savedCam;
    patch(next);
  };

  const detect = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await facecamApi.detect(sourceId);
      if (r.cam) {
        const cam = normalizeCam(r.cam);
        setSavedCam(cam);
        patch({ cam, mode: layout.mode === "single" ? "split" : layout.mode });
        setNote({ kind: "ok", text: `Нашёл вебку · уверенность ${Math.round(r.confidence * 100)}% (${r.frames} кадров)` });
      } else {
        setNote({ kind: "warn", text: "Устойчивого лица не нашёл — обведи вебку рамкой на кадре" });
      }
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError ? e.message : "Не удалось найти вебку" });
    } finally {
      setBusy(false);
    }
  };

  const remember = async () => {
    try {
      const r = await facecamApi.save(sourceId, layout.cam);
      setSavedCam(r.cam ? normalizeCam(r.cam) : null);
      setNote({ kind: "ok", text: "Запомнил: новые клипы этого источника возьмут эту вебку" });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError ? e.message : "Не удалось сохранить" });
    }
  };

  const active = CHOICES.find((c) => c.mode === layout.mode && (!c.pos || c.pos === layout.cam_pos))?.id ?? "single";
  // Кадр раз в секунду, а не каждый тик плеера.
  const frameSrc = `/api/sources/${sourceId}/frame?t=${Math.max(0, Math.round(frameAt))}&w=480`;

  return (
    <div className="lay-panel">
      <div className="lay-modes" role="radiogroup" aria-label="Раскладка кадра">
        {CHOICES.map((c) => (
          <button
            key={c.id}
            type="button"
            role="radio"
            aria-checked={active === c.id}
            className={`lay-mode${active === c.id ? " on" : ""}`}
            title={c.hint}
            onClick={() => pick(c)}
          >
            <Thumb choice={c} />
            <span>{c.label}</span>
          </button>
        ))}
      </div>

      {layout.mode !== "meme" ? (
        <button type="button" className="btn sm lay-detect" disabled={busy} onClick={detect}>
          {busy ? "Ищу лицо в кадрах…" : "🎯 Найти вебку автоматически"}
        </button>
      ) : null}
      {note ? <div className={`lay-note ${note.kind}`}>{note.text}</div> : null}

      {keyed && backdrop && onBackdrop ? (
        <div className="lay-row">
          <span className="lay-grow muted li-note">Зелёный фон вырезан. Фон за персонажем (верх → низ)</span>
          <input type="color" className="lay-color" value={backdrop.color} title="Цвет фона сверху" onChange={(e) => onBackdrop({ ...backdrop, color: e.target.value })} />
          <input
            type="color"
            className="lay-color"
            value={backdrop.color2 || backdrop.color}
            title="Цвет фона снизу (градиент)"
            onChange={(e) => onBackdrop({ ...backdrop, color2: e.target.value })}
          />
        </div>
      ) : null}

      {layout.mode === "meme" ? (
        <>
          <label className="field range">
            <span>Видео от верха · {Math.round(layout.video_y * 100)}% кадра</span>
            <input type="range" min={0} max={60} value={Math.round(layout.video_y * 100)} onChange={(e) => patch({ video_y: Number(e.target.value) / 100 })} />
          </label>
          <label className="field range">
            <span>Размер видео · {Math.round((layout.video_scale || 1) * 100)}% ширины</span>
            <input type="range" min={100} max={400} step={5} value={Math.round((layout.video_scale || 1) * 100)} onChange={(e) => patch({ video_scale: Number(e.target.value) / 100 })} />
          </label>
          <div className="lay-row">
            <span className="lay-grow muted li-note">Фон под видео — место для картинок и титров</span>
            <input type="color" className="lay-color" value={layout.bg} title="Цвет фона" onChange={(e) => patch({ bg: e.target.value })} />
          </div>
        </>
      ) : layout.mode !== "single" ? (
        <>
          <div className="li-cap">Где вебка в исходнике</div>
          <CamEditor src={frameSrc} cam={layout.cam} onCommit={(cam) => patch({ cam })} />
          <div className="lay-row">
            <button
              type="button"
              className="btn ghost sm"
              disabled={!!savedCam && sameCam(savedCam, layout.cam)}
              title="Новые клипы этого источника сразу возьмут эту рамку"
              onClick={remember}
            >
              📌 Запомнить для источника
            </button>
          </div>

          {layout.mode === "split" ? (
            <>
              <label className="field range">
                <span>Высота вебки · {Math.round(layout.ratio * 100)}% кадра</span>
                <input type="range" min={20} max={60} value={Math.round(layout.ratio * 100)} onChange={(e) => patch({ ratio: Number(e.target.value) / 100 })} />
              </label>
              <label className="field range">
                <span>Зазор · {layout.gap} px</span>
                <input type="range" min={0} max={60} step={2} value={layout.gap} onChange={(e) => patch({ gap: Number(e.target.value) })} />
              </label>
            </>
          ) : (
            <>
              <label className="field range">
                <span>Ширина окошка · {Math.round(layout.pip.w * 100)}%</span>
                <input type="range" min={15} max={90} value={Math.round(layout.pip.w * 100)} onChange={(e) => patch({ pip: { ...layout.pip, w: Number(e.target.value) / 100 } })} />
              </label>
              <label className="field range">
                <span>По горизонтали · {Math.round(layout.pip.x * 100)}%</span>
                <input type="range" min={0} max={100} value={Math.round(layout.pip.x * 100)} onChange={(e) => patch({ pip: { ...layout.pip, x: Number(e.target.value) / 100 } })} />
              </label>
              <label className="field range">
                <span>По вертикали · {Math.round(layout.pip.y * 100)}%</span>
                <input type="range" min={0} max={100} value={Math.round(layout.pip.y * 100)} onChange={(e) => patch({ pip: { ...layout.pip, y: Number(e.target.value) / 100 } })} />
              </label>
            </>
          )}

          <div className="lay-row">
            <label className="field range lay-grow">
              <span>Рамка · {layout.border.width} px</span>
              <input type="range" min={0} max={20} value={layout.border.width} onChange={(e) => patch({ border: { ...layout.border, width: Number(e.target.value) } })} />
            </label>
            <input
              type="color"
              className="lay-color"
              value={layout.border.color}
              title="Цвет рамки и зазора"
              onChange={(e) => patch({ border: { ...layout.border, color: e.target.value } })}
            />
          </div>
        </>
      ) : (
        <div className="muted li-note">Для стримов: вебка полосой над игрой — самый частый формат шортсов.</div>
      )}
    </div>
  );
}
