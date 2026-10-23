/**
 * Инспектор «Раскладка»: обычный кадр, вебка сверху/снизу над игрой или вебка
 * окошком. Вебку находит сервер (🎯) или её обводят рамкой на кадре исходника.
 * Значение — `render.layout` клипа (см. ./layout.ts и app/stream_layout.py).
 * Здесь же хромакей источника (персонаж на зелёнке) и фон за персонажем.
 * У пустого холста (app/canvas.py) раскладки и ключа нет — только выбор фона.
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { facecamApi, sourcesApi } from "@/api/sources";
import type { SourceDetail } from "@/api/types";
import type { Backdrop, ChromaKey, Despill } from "./chromaKey";
import { BackgroundPicker } from "./BackgroundPicker";
import { DEFAULT_CAM, normalizeCam, normalizeLayout, type LayoutRect, type StreamLayout } from "./layout";

type Props = {
  sourceId: number;
  layout: StreamLayout | null | undefined;
  onChange: (next: StreamLayout) => void;
  /** Секунда исходника для кадра в редакторе вебки (обычно — под курсором). */
  frameAt: number;
  /** Хромакей источника (общий для всех его клипов); null — фон не вырезается. */
  chromaKey?: ChromaKey | null;
  backdrop?: Backdrop;
  onBackdrop?: (next: Backdrop) => void;
  /** Акцент клипа (цвет звучащего слова субтитров) — им красится фон «в цветах ролика». */
  accent?: string | null;
  /** Пустой холст: видео нет, весь кадр — фон; вебка и хромакей не нужны (выключенный ключ — зелёный кадр). */
  canvas?: boolean;
};

type Choice = { id: string; label: string; hint: string; mode: StreamLayout["mode"]; pos?: StreamLayout["cam_pos"] };

const CHOICES: Choice[] = [
  { id: "single", label: "Обычный", hint: "Один кадр 9:16, как раньше", mode: "single" },
  { id: "split-top", label: "Вебка сверху", hint: "Лицо стримера полосой сверху, игра снизу", mode: "split", pos: "top" },
  { id: "split-bottom", label: "Вебка снизу", hint: "Игра сверху, лицо стримера полосой снизу", mode: "split", pos: "bottom" },
  { id: "pip", label: "Окошко", hint: "Обычный кадр 9:16 и вебка окошком поверх", mode: "pip_cam" },
  { id: "meme", label: "Мем", hint: "Горизонтальное видео целиком сверху, ниже фон под картинки и титры", mode: "meme" },
];

/** Ключ, если зелёнку не нашли, а включили руками: типичный хромакейный зелёный. */
const FALLBACK_KEY: ChromaKey = { color: "#00b140", similarity: 0.25, blend: 0.08 };

/**
 * Хромакей источника: вкл/выкл, поиск зелёнки по кадрам, ползунки допуска и
 * мягкости края. Превью вырезает фон само (./chromaKey.ts) из кэша источника,
 * поэтому ползунок сразу кладёт ключ в кэш, а на сервер уходит с паузой.
 */
function ChromaKeyControls({ sourceId, chromaKey }: { sourceId: number; chromaKey: ChromaKey | null }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);
  // Выключили и включили снова — возвращаем подобранный ключ, а не ищем заново.
  const lastKeyRef = useRef<ChromaKey | null>(chromaKey);
  if (chromaKey) lastKeyRef.current = chromaKey;
  const pendingRef = useRef<{ key: ChromaKey; timer: number } | null>(null);

  const putInCache = (key: ChromaKey | null) =>
    qc.setQueryData<SourceDetail>(qk.source(sourceId), (prev) => (prev ? { ...prev, chroma_key: key } : prev));

  const save = async (key: ChromaKey | null) => {
    try {
      const updated = await sourcesApi.setChromaKey(sourceId, key);
      putInCache(updated.chroma_key ?? null);
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError ? e.message : "Не удалось сохранить хромакей" });
      void qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    }
  };

  const flush = () => {
    const p = pendingRef.current;
    if (!p) return;
    pendingRef.current = null;
    window.clearTimeout(p.timer);
    void save(p.key);
  };
  // Ушли с панели посреди подстройки — последнее значение всё равно сохраняем.
  useEffect(() => flush, [sourceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const tune = (patch: Partial<ChromaKey>) => {
    if (!chromaKey) return;
    const key = { ...chromaKey, ...patch };
    putInCache(key);
    if (pendingRef.current) window.clearTimeout(pendingRef.current.timer);
    pendingRef.current = { key, timer: window.setTimeout(flush, 450) };
  };

  const detect = async (thenEnable: boolean) => {
    setBusy(true);
    setNote(null);
    try {
      const r = await sourcesApi.detectKey(sourceId);
      if (r.chroma_key) {
        putInCache(r.chroma_key);
        await save(r.chroma_key);
        setNote({ kind: "ok", text: `Нашёл зелёный фон ${r.chroma_key.color} — края подстройте ползунками` });
      } else if (thenEnable) {
        const key = lastKeyRef.current ?? FALLBACK_KEY;
        putInCache(key);
        await save(key);
        setNote({ kind: "warn", text: "Однотонного фона по краям кадра не нашёл — включил стандартный зелёный, подберите цвет" });
      } else {
        setNote({ kind: "warn", text: "Однотонного зелёного/синего фона по краям кадра не нашёл" });
      }
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError ? e.message : "Не удалось проверить кадры" });
    } finally {
      setBusy(false);
    }
  };

  const toggle = (on: boolean) => {
    setNote(null);
    if (pendingRef.current) {
      window.clearTimeout(pendingRef.current.timer);
      pendingRef.current = null;
    }
    if (!on) {
      putInCache(null);
      void save(null);
    } else if (lastKeyRef.current) {
      putInCache(lastKeyRef.current);
      void save(lastKeyRef.current);
    } else {
      void detect(true);
    }
  };

  return (
    <div className="lay-key">
      <label className="switch" title="Источник записан на зелёном (синем) фоне — вырезать его; ключ общий для всех клипов источника">
        <input type="checkbox" checked={Boolean(chromaKey)} disabled={busy} onChange={(e) => toggle(e.target.checked)} />
        <span className="switch-track" />
        <span>Вырезать зелёный фон (хромакей)</span>
      </label>
      <button type="button" className="btn sm lay-detect" disabled={busy} onClick={() => void detect(false)} title="Посмотреть кадры источника и найти цвет фона">
        {busy ? "Смотрю кадры…" : "🔍 Найти зелёный фон"}
      </button>
      {chromaKey ? (
        <>
          <div className="lay-row">
            <span className="lay-grow muted li-note">Цвет фона, который вырезаем</span>
            <input
              type="color"
              className="lay-color"
              value={chromaKey.color}
              title="Цвет ключа"
              onChange={(e) => tune({ color: e.target.value.toLowerCase() })}
            />
          </div>
          <label className="field range">
            <span title="Насколько далёкие от ключа оттенки тоже вырезаются: мало — остаётся зелёная кайма, много — съедает персонажа">
              Допуск · {chromaKey.similarity.toFixed(2)}
            </span>
            <input
              type="range"
              min={0.01}
              max={0.6}
              step={0.01}
              value={chromaKey.similarity}
              onChange={(e) => tune({ similarity: Number(e.target.value) })}
            />
          </label>
          <label className="field range">
            <span title="Ширина полупрозрачного края: волосы и контур мягче">Мягкость края · {chromaKey.blend.toFixed(2)}</span>
            <input type="range" min={0} max={0.5} step={0.01} value={chromaKey.blend} onChange={(e) => tune({ blend: Number(e.target.value) })} />
          </label>
          <DespillControl value={chromaKey.despill ?? "auto"} onChange={(despill) => tune({ despill })} />
        </>
      ) : null}
      {note ? <div className={`lay-note ${note.kind}`}>{note.text}</div> : null}
    </div>
  );
}

/** Мягкая сила по умолчанию: ореол заметно слабее, жёлтое почти не рыжеет. */
const SOFT_DESPILL = 0.4;

/**
 * Подавление зелёного ореола после выреза. Полное (как было) съедает жёлтые и
 * зелёные цвета в кадре — у промо с жёлтыми карточками и зелёными плашками его выключают
 * или ставят мягким.
 */
function DespillControl({ value, onChange }: { value: Despill; onChange: (v: Despill) => void }) {
  const mode = typeof value === "number" ? "soft" : value;
  return (
    <>
      <label className="field">
        <span title="После выреза зелёный отсвет на краях гасится — но вместе с ним рыжеет жёлтое и сереет зелёное в кадре">
          Подавление зелёного ореола
        </span>
        <select
          className="input"
          value={mode}
          onChange={(e) => onChange(e.target.value === "soft" ? SOFT_DESPILL : (e.target.value as Despill))}
        >
          <option value="auto">полное (как раньше)</option>
          <option value="soft">мягкое</option>
          <option value="none">выкл — жёлтое и зелёное в кадре целы</option>
        </select>
      </label>
      {typeof value === "number" ? (
        <label className="field range">
          <span>Сила подавления · {Math.round(value * 100)}%</span>
          <input type="range" min={0.05} max={1} step={0.05} value={value} onChange={(e) => onChange(Number(e.target.value))} />
        </label>
      ) : null}
    </>
  );
}

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

export function LayoutPanel({ sourceId, layout: raw, onChange, frameAt, chromaKey, backdrop, onBackdrop, accent, canvas }: Props) {
  const keyed = Boolean(chromaKey);
  const layout = normalizeLayout(raw);
  const [savedCam, setSavedCam] = useState<LayoutRect | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "warn"; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    setSavedCam(null);
    setNote(null);
    if (canvas) return;
    facecamApi
      .get(sourceId)
      .then((r) => alive && setSavedCam(r.cam ? normalizeCam(r.cam) : null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [sourceId, canvas]);

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

  if (canvas) {
    return (
      <div className="lay-panel">
        <div className="muted li-note">
          Пустой холст: видео нет, весь кадр — фон. Раскладка и хромакей здесь не нужны — картинки, титры и записи
          экрана добавляются слоями.
        </div>
        {backdrop && onBackdrop ? (
          <>
            <div className="li-cap">Фон холста</div>
            <BackgroundPicker value={backdrop} onChange={onBackdrop} accent={accent} />
          </>
        ) : null}
      </div>
    );
  }

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

      <ChromaKeyControls sourceId={sourceId} chromaKey={chromaKey ?? null} />
      {keyed && backdrop && onBackdrop ? (
        <>
          <div className="li-cap">Зелёный фон вырезан. Фон за персонажем</div>
          <BackgroundPicker value={backdrop} onChange={onBackdrop} accent={accent} />
        </>
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
          {keyed ? (
            <div className="muted li-note">Под видео — фон за персонажем (выше).</div>
          ) : (
            <>
              <div className="li-cap">Фон под видео — место для картинок и титров</div>
              <BackgroundPicker value={layout.bg} onChange={(bg) => patch({ bg })} accent={accent} />
            </>
          )}
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
