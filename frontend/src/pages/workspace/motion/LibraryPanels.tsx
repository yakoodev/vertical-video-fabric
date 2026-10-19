/**
 * Библиотека студии: шаблоны, текст, стикеры и эмодзи, звуки, мемы, генерация
 * картинок. Всё добавляется слоем в момент курсора (клик) или бросается на
 * нужную дорожку таймлайна (перетаскивание).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CAPTION_STYLES,
  TEMPLATES,
  TEMPLATE_CATEGORIES,
  drawFrame,
  newLayerId,
  type CameraKey,
  type Layer,
  type MotionTemplate,
} from "@/motion";
import { motionApi, packFileUrl, type PackItem, type PackSummary } from "@/api/motion";
import { montageAssetsApi, type AssetSearchResult, type MontageAsset } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { LAYER_DND } from "@/pages/workspace/ClipTimeline";
import { browserEnv } from "@/pages/workspace/motion/runtime";

export type AddLayers = (layers: Layer[], camera?: CameraKey[]) => void;

/** Перетаскивание из библиотеки: слои с началом в 0, таймлайн сдвинет их на место броска. */
function dragPayload(e: React.DragEvent, layers: Layer[], camera?: CameraKey[]) {
  e.dataTransfer.setData(LAYER_DND, JSON.stringify({ layers, camera }));
  e.dataTransfer.effectAllowed = "copy";
}

type TemplateParams = Parameters<MotionTemplate["build"]>[0];

function defaultsOf(t: MotionTemplate): TemplateParams {
  return Object.fromEntries(t.params.map((p) => [p.name, p.default]));
}

/* ---------- живое превью шаблона ---------- */

// Слова-образец, чтобы карточка «Анимированные субтитры» не была пустой.
const DEMO_WORDS = "это был самый лучший момент всего стрима"
  .split(" ")
  .map((w, i) => ({ t0: 0.2 + i * 0.32, t1: 0.2 + i * 0.32 + 0.28, w }));

const PREVIEW_W = 135;
const PREVIEW_H = 240;

/** Мини-кадр 9:16: на наведении шаблон проигрывается по кругу, иначе стоит «плакат». */
function TemplatePreview({ template, params, autoplay = false }: { template: MotionTemplate; params?: TemplateParams; autoplay?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [hoverState, setHover] = useState(false);
  const hover = autoplay || hoverState;
  const built = useMemo(() => template.build(params ?? defaultsOf(template), 0), [template, params]);
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const env = browserEnv(cv.width, cv.height);
    const dur = Math.max(0.5, template.duration);
    const poster = Math.min(dur * 0.55, 1.4);
    let raf = 0;
    const started = performance.now();
    const paint = (t: number) => {
      ctx.clearRect(0, 0, cv.width, cv.height);
      try {
        drawFrame(ctx, { layers: built.layers.filter((l) => l.type !== "audio"), words: DEMO_WORDS }, t, env);
      } catch {
        /* шаблон без данных (субтитры без слов) — просто пустая карточка */
      }
    };
    if (hover) {
      const loop = () => {
        paint(((performance.now() - started) / 1000) % (dur + 0.4));
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
      return () => cancelAnimationFrame(raf);
    }
    // «Плакат»: рисуем сразу и ещё пару раз — когда догрузятся шрифты и картинки.
    paint(poster);
    const timers = [300, 1200, 3000].map((ms) => window.setTimeout(() => paint(poster), ms));
    const onFonts = () => paint(poster);
    document.fonts?.addEventListener?.("loadingdone", onFonts);
    return () => {
      timers.forEach((id) => window.clearTimeout(id));
      document.fonts?.removeEventListener?.("loadingdone", onFonts);
    };
  }, [built, hover, template.duration]);
  return (
    <canvas
      ref={ref}
      className="lib-tpl-canvas"
      width={PREVIEW_W * 2}
      height={PREVIEW_H * 2}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    />
  );
}

const NEWLINE = String.fromCharCode(10);

/** Настройка шаблона перед вставкой: поля параметров и живое превью. */
function TemplateDialog({ template, onClose, onInsert }: { template: MotionTemplate; onClose: () => void; onInsert: AddLayers }) {
  const [params, setParams] = useState<TemplateParams>(() => defaultsOf(template));
  const set = (name: string, value: string | number | boolean) => setParams((p) => ({ ...p, [name]: value }));
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal tpl-dialog" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="pub-head">
          <h3>{template.name}</h3>
          <button className="pub-x" title="Закрыть" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="tpl-dialog-body">
          <div className="tpl-dialog-preview">
            <TemplatePreview template={template} params={params} autoplay />
          </div>
          <div className="tpl-dialog-fields">
            <p className="muted">{template.description}</p>
            {template.params.map((p) => {
              const v = params[p.name] ?? p.default;
              if (p.type === "bool")
                return (
                  <label key={p.name} className="switch">
                    <input type="checkbox" checked={Boolean(v)} onChange={(e) => set(p.name, e.target.checked)} />
                    <span className="switch-track" />
                    <span>{p.label}</span>
                  </label>
                );
              return (
                <label key={p.name} className="field">
                  <span>{p.label}</span>
                  {p.type === "select" ? (
                    <select className="input" value={String(v)} onChange={(e) => set(p.name, e.target.value)}>
                      {(p.options ?? []).map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </select>
                  ) : p.type === "color" ? (
                    <input type="color" value={String(v).slice(0, 7)} onChange={(e) => set(p.name, e.target.value)} />
                  ) : p.type === "number" ? (
                    <input
                      className="input"
                      type="number"
                      value={Number(v)}
                      min={p.min}
                      max={p.max}
                      onChange={(e) => set(p.name, Number(e.target.value))}
                    />
                  ) : String(v).length > 40 || String(v).includes(NEWLINE) ? (
                    <textarea className="input" rows={3} value={String(v)} onChange={(e) => set(p.name, e.target.value)} />
                  ) : (
                    <input className="input" value={String(v)} onChange={(e) => set(p.name, e.target.value)} />
                  )}
                </label>
              );
            })}
          </div>
        </div>
        <div className="tpl-dialog-foot">
          <span className="muted">{template.category} · {template.duration} с</span>
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              const r = template.build(params, 0);
              onInsert(r.layers, r.camera);
              onClose();
            }}
          >
            ＋ Вставить в клип
          </button>
        </div>
      </div>
    </div>
  );
}

export function TemplatesPanel({ onAdd }: { onAdd: AddLayers }) {
  const [tuning, setTuning] = useState<MotionTemplate | null>(null);
  const [cat, setCat] = useState<string>("Все");
  const [q, setQ] = useState("");
  const list = TEMPLATES.filter(
    (t) =>
      (cat === "Все" || t.category === cat) &&
      (!q.trim() || `${t.name} ${t.description} ${t.category}`.toLowerCase().includes(q.trim().toLowerCase())),
  );
  return (
    <div className="lib-panel">
      <input className="input lib-search" placeholder="Найти шаблон…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="lib-cats">
        {["Все", ...TEMPLATE_CATEGORIES].map((c) => (
          <button key={c} type="button" className={`lib-cat${cat === c ? " on" : ""}`} onClick={() => setCat(c)}>
            {c}
          </button>
        ))}
      </div>
      <div className="lib-tpl-grid">
        {list.map((t) => (
          <button
            key={t.id}
            type="button"
            className="lib-tpl"
            title={`${t.description}\nКлик — в момент курсора, или перетащите на дорожку`}
            draggable
            onDragStart={(e) => {
              const r = t.build(defaultsOf(t), 0);
              dragPayload(e, r.layers, r.camera);
            }}
            onClick={() => {
              const r = t.build(defaultsOf(t), 0);
              onAdd(r.layers, r.camera);
            }}
          >
            <TemplatePreview template={t} />
            {t.params.length ? (
              <span
                className="lib-tpl-tune"
                role="button"
                title="Настроить текст, цвета и числа перед вставкой"
                onClick={(e) => {
                  e.stopPropagation();
                  setTuning(t);
                }}
              >
                ⚙
              </span>
            ) : null}
            <span className="lib-tpl-name">{t.name}</span>
            <span className="lib-tpl-desc muted">{t.description}</span>
          </button>
        ))}
        {!list.length ? <div className="muted">Ничего не нашлось</div> : null}
      </div>
      {tuning ? <TemplateDialog template={tuning} onClose={() => setTuning(null)} onInsert={onAdd} /> : null}
    </div>
  );
}

/* ---------- текст ---------- */

type TextPreset = { id: string; label: string; sample: string; css: React.CSSProperties; make: () => Layer };

const textLayer = (props: Record<string, unknown>, base: Partial<Layer> = {}): Layer =>
  ({
    id: newLayerId(),
    type: "text",
    start: 0,
    end: 3,
    x: 0.5,
    y: 0.32,
    in: { type: "pop", duration: 0.35 },
    out: { type: "fade", duration: 0.25 },
    ...base,
    props: { text: "ТЕКСТ", font: "Montserrat", weight: 900, size: 110, color: "#ffffff", align: "center", maxWidth: 920, ...props },
  }) as Layer;

const TEXT_PRESETS: TextPreset[] = [
  {
    id: "title",
    label: "Заголовок",
    sample: "ЗАГОЛОВОК",
    css: { fontWeight: 900, color: "#fff", WebkitTextStroke: "1px #000" },
    make: () => textLayer({ text: "ЗАГОЛОВОК", uppercase: true, stroke: { color: "#000000", width: 12 } }),
  },
  {
    id: "yellow",
    label: "Жёлтый акцент",
    sample: "ОН ЭТО СДЕЛАЛ",
    css: { fontWeight: 900, color: "#ffd600", WebkitTextStroke: "1px #000" },
    make: () =>
      textLayer(
        { text: "ОН ЭТО СДЕЛАЛ", uppercase: true, color: "#ffd600", size: 130, stroke: { color: "#000000", width: 14 } },
        { in: { type: "words", duration: 0.6 }, loop: { type: "pulse", speed: 1, amount: 0.6 } },
      ),
  },
  {
    id: "plate",
    label: "На плашке",
    sample: "Подпись на плашке",
    css: { fontWeight: 800, color: "#fff", background: "#7c5cff", borderRadius: 6, padding: "1px 6px" },
    make: () =>
      textLayer(
        { text: "Подпись на плашке", weight: 800, size: 64, bg: { color: "#7c5cff", opacity: 1, radius: 18, padX: 30, padY: 16, skew: -8 } },
        { y: 0.7, in: { type: "slideUp", duration: 0.35 } },
      ),
  },
  {
    id: "meme",
    label: "Мем-титр",
    sample: "КОГДА ПОНЯЛ",
    css: { fontWeight: 700, color: "#fff", WebkitTextStroke: "1px #000", fontFamily: "Oswald, Impact, sans-serif" },
    make: () =>
      textLayer(
        { text: "КОГДА ПОНЯЛ", font: "Oswald", weight: 700, size: 120, uppercase: true, stroke: { color: "#000000", width: 12 } },
        { y: 0.12, in: { type: "none" }, out: { type: "none" } },
      ),
  },
  {
    id: "neon",
    label: "Неон",
    sample: "НЕОН",
    css: { fontWeight: 900, color: "#22d3ee", textShadow: "0 0 6px #22d3ee" },
    make: () =>
      textLayer(
        { text: "НЕОН", color: "#e0fbff", size: 140, stroke: { color: "#22d3ee", width: 6 } },
        { shadow: { color: "#22d3ee", blur: 40, x: 0, y: 0, opacity: 1 }, loop: { type: "blink", speed: 0.6, amount: 0.3 } },
      ),
  },
  {
    id: "type",
    label: "Печатная машинка",
    sample: "печатаю текст…",
    css: { fontWeight: 600, color: "#fff", background: "rgba(0,0,0,.6)", padding: "1px 6px" },
    make: () =>
      textLayer(
        { text: "печатаю текст…", font: "Golos Text", weight: 600, size: 66, bg: { color: "#000000", opacity: 0.6, radius: 12, padX: 24, padY: 12 } },
        { y: 0.78, in: { type: "typewriter", duration: 1.2 }, end: 4 },
      ),
  },
  {
    id: "gradient",
    label: "Градиент",
    sample: "ВАУ",
    css: { fontWeight: 900, background: "linear-gradient(90deg,#ffd600,#ff3b30)", WebkitBackgroundClip: "text", color: "transparent" },
    make: () =>
      textLayer(
        { text: "ВАУ", font: "Unbounded", size: 180, gradient: ["#ffd600", "#ff3b30"], stroke: { color: "#000000", width: 10 } },
        { in: { type: "drop", duration: 0.5 } },
      ),
  },
  {
    id: "plain",
    label: "Обычный текст",
    sample: "Просто текст",
    css: { fontWeight: 600, color: "#fff" },
    make: () =>
      textLayer(
        { text: "Просто текст", font: "Inter", weight: 600, size: 60 },
        { y: 0.5, in: { type: "fade", duration: 0.25 }, shadow: { color: "#000000", blur: 18, x: 0, y: 4, opacity: 0.7 } },
      ),
  },
];

export function TextPanel({ onAdd, onCaptions }: { onAdd: AddLayers; onCaptions?: (style: string) => void }) {
  return (
    <div className="lib-panel">
      {onCaptions ? (
        <>
          <div className="lib-sub">Анимированные субтитры на весь клип</div>
          <div className="lib-cap-grid">
            {CAPTION_STYLES.map((st) => (
              <button key={st.id} type="button" className="lib-cap" title={st.description} onClick={() => onCaptions(st.id)}>
                {st.name}
              </button>
            ))}
          </div>
          <div className="lib-sub">Текст</div>
        </>
      ) : null}
      <div className="lib-text-grid">
        {TEXT_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className="lib-text"
            draggable
            onDragStart={(e) => dragPayload(e, [p.make()])}
            onClick={() => onAdd([p.make()])}
            title="Клик — в момент курсора, или перетащите на дорожку графики"
          >
            <span className="lib-text-sample" style={p.css}>
              {p.sample}
            </span>
            <span className="lib-text-label muted">{p.label}</span>
          </button>
        ))}
      </div>
      <div className="lib-sub">Фигуры</div>
      <div className="lib-shape-row">
        {(
          [
            ["arrow", "➜", "Стрелка «смотри»", { stroke: { color: "#ff3b30", width: 22 }, w: 360, h: 80 }, { in: { type: "draw", duration: 0.4 }, rotation: -35 }],
            ["ellipse", "◯", "Обвести", { stroke: { color: "#ff3b30", width: 14 }, w: 420, h: 300, fill: null }, { in: { type: "draw", duration: 0.45 } }],
            ["rect", "▭", "Плашка", { fill: "#000000", radius: 24, w: 900, h: 220 }, { opacity: 0.6, in: { type: "fade", duration: 0.25 } }],
            ["burst", "✸", "Взрыв", { fill: "#ffd600", points: 14, w: 420, h: 420 }, { in: { type: "pop", duration: 0.3 }, loop: { type: "spin", speed: 0.3, amount: 1 } }],
            ["star", "★", "Звезда", { fill: "#ffd600", points: 5, w: 260, h: 260 }, { in: { type: "spin", duration: 0.4 } }],
            ["line", "—", "Линия", { stroke: { color: "#ffffff", width: 10 }, w: 600, h: 10 }, { in: { type: "draw", duration: 0.35 } }],
          ] as const
        ).map(([kind, icon, label, props, base]) => {
          const make = () =>
            ({
              id: newLayerId(),
              type: "shape",
              start: 0,
              end: 2.5,
              x: 0.5,
              y: 0.45,
              out: { type: "fade", duration: 0.2 },
              ...base,
              props: { kind, ...props },
            }) as unknown as Layer;
          return (
            <button
              key={kind}
              type="button"
              className="lib-shape"
              title={label}
              draggable
              onDragStart={(e) => dragPayload(e, [make()])}
              onClick={() => onAdd([make()])}
            >
              <span>{icon}</span>
              <em>{label}</em>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- эмодзи и стикеры ---------- */

const QUICK_EMOJI = ["🔥", "😂", "💀", "😱", "🤯", "👀", "💯", "❤️", "👍", "🤡", "😭", "🗿", "⚡", "🎉", "❗", "❓", "✅", "❌", "💸", "🏆"];

const emojiLayer = (char: string): Layer =>
  ({
    id: newLayerId(),
    type: "emoji",
    start: 0,
    end: 2,
    x: 0.72,
    y: 0.3,
    in: { type: "pop", duration: 0.3 },
    out: { type: "fade", duration: 0.2 },
    loop: { type: "float", speed: 1, amount: 1 },
    props: { char, size: 220 },
  }) as Layer;

const imageLayer = (ref: { asset_id?: string; src?: string }, w = 640, end = 2.5): Layer =>
  ({
    id: newLayerId(),
    type: "image",
    start: 0,
    end,
    x: 0.5,
    y: 0.38,
    in: { type: "pop", duration: 0.3 },
    out: { type: "fade", duration: 0.2 },
    props: { ...ref, w, radius: 18 },
  }) as Layer;

function packRef(prefix: string, pack: string, item: PackItem) {
  return item.ref || `${prefix}:${pack}/${item.file}`;
}

export function StickersPanel({ onAdd }: { onAdd: AddLayers }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(q.trim()), 250);
    return () => window.clearTimeout(id);
  }, [q]);
  const emoji = useQuery({ queryKey: ["motion-emoji", debounced], queryFn: () => motionApi.emoji(debounced, 160), enabled: debounced.length > 0 });
  const packs = useQuery({ queryKey: ["motion-packs"], queryFn: motionApi.packs, staleTime: 300_000 });
  const stickerPacks = (packs.data ?? []).filter((p) => p.kind === "sticker");
  const [pack, setPack] = useState<string>("");
  const activePack = pack || stickerPacks[0]?.id || "";
  const items = useQuery({ queryKey: ["motion-pack", activePack, debounced], queryFn: () => motionApi.pack(activePack, debounced), enabled: Boolean(activePack) });
  const emojiList = debounced ? emoji.data ?? [] : QUICK_EMOJI.map((char) => ({ code: char, char }));
  return (
    <div className="lib-panel">
      <input className="input lib-search" placeholder="Поиск: огонь, смех, череп, сердце…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="lib-sub">Эмодзи</div>
      <div className="lib-emoji-grid">
        {emojiList.map((e) => (
          <button
            key={e.code}
            type="button"
            className="lib-emoji"
            title={"name" in e && e.name ? String(e.name) : e.char}
            draggable
            onDragStart={(ev) => dragPayload(ev, [emojiLayer(e.char)])}
            onClick={() => onAdd([emojiLayer(e.char)])}
          >
            {e.char}
          </button>
        ))}
        {debounced && emoji.isSuccess && !emojiList.length ? <span className="muted">не нашлось</span> : null}
      </div>
      {stickerPacks.length ? (
        <>
          <div className="lib-sub">Стикеры</div>
          <div className="lib-cats">
            {stickerPacks.map((p) => (
              <button key={p.id} type="button" className={`lib-cat${activePack === p.id ? " on" : ""}`} onClick={() => setPack(p.id)} title={p.license}>
                {p.name}
              </button>
            ))}
          </div>
          <div className="lib-sticker-grid">
            {(items.data ?? []).slice(0, 200).map((it) => {
              const make = () => imageLayer({ src: packRef("sticker", activePack, it) }, 360);
              return (
                <button
                  key={it.file}
                  type="button"
                  className="lib-sticker"
                  title={it.name ?? it.file}
                  draggable
                  onDragStart={(ev) => dragPayload(ev, [make()])}
                  onClick={() => onAdd([make()])}
                >
                  <img src={it.url || packFileUrl(activePack, it.file)} alt="" loading="lazy" />
                </button>
              );
            })}
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ---------- звуки ---------- */

export function SoundsPanel({ onAdd, clipDuration }: { onAdd: AddLayers; clipDuration: number }) {
  const [q, setQ] = useState("");
  const packs = useQuery({ queryKey: ["motion-packs"], queryFn: motionApi.packs, staleTime: 300_000 });
  const sfxPacks = (packs.data ?? []).filter((p) => p.kind === "sfx");
  const [pack, setPack] = useState("");
  const activePack = pack || sfxPacks[0]?.id || "";
  const items = useQuery({ queryKey: ["motion-pack", activePack, q.trim()], queryFn: () => motionApi.pack(activePack, q.trim()), enabled: Boolean(activePack) });
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const play = (url: string) => {
    audioRef.current?.pause();
    if (playing === url) {
      setPlaying(null);
      return;
    }
    const a = new Audio(url);
    audioRef.current = a;
    a.onended = () => setPlaying(null);
    void a.play().catch(() => setPlaying(null));
    setPlaying(url);
  };
  useEffect(() => () => audioRef.current?.pause(), []);
  const make = (it: PackItem): Layer =>
    ({
      id: newLayerId(),
      type: "audio",
      start: 0,
      end: Math.max(0.2, Math.min(it.duration || 1.5, clipDuration || 60, 12)),
      track: 0,
      props: { asset_id: packRef("sfx", activePack, it), volume: 1, fadeOut: 0.05 },
    }) as Layer;
  return (
    <div className="lib-panel">
      <input className="input lib-search" placeholder="Поиск: whoosh, boom, pop, ding…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="lib-cats">
        {sfxPacks.map((p: PackSummary) => (
          <button key={p.id} type="button" className={`lib-cat${activePack === p.id ? " on" : ""}`} onClick={() => setPack(p.id)} title={p.license}>
            {p.name}
          </button>
        ))}
      </div>
      {!sfxPacks.length ? <div className="muted">Паки звуков ещё не установлены</div> : null}
      <div className="lib-sfx-list">
        {(items.data ?? []).slice(0, 300).map((it) => {
          const url = it.url || packFileUrl(activePack, it.file);
          return (
            <div
              key={it.file}
              className="lib-sfx"
              draggable
              onDragStart={(ev) => dragPayload(ev, [make(it)])}
              title="Перетащите на дорожку «Звуки» или нажмите ＋ — встанет под курсор"
            >
              <button type="button" className={`lib-sfx-play${playing === url ? " on" : ""}`} onClick={() => play(url)} aria-label="Послушать">
                {playing === url ? "■" : "▶"}
              </button>
              <span className="lib-sfx-name">{it.name || it.file}</span>
              <span className="muted mono lib-sfx-dur">{it.duration ? `${it.duration.toFixed(1)}с` : ""}</span>
              <button type="button" className="btn ghost sm" onClick={() => onAdd([make(it)])} aria-label="Добавить">
                ＋
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------- мемы ---------- */

export function MemesPanel({ onAdd, assets }: { onAdd: AddLayers; assets: MontageAsset[] }) {
  const [q, setQ] = useState("");
  const packs = useQuery({ queryKey: ["motion-packs"], queryFn: motionApi.packs, staleTime: 300_000 });
  const memePacks = (packs.data ?? []).filter((p) => p.kind === "meme");
  const activePack = memePacks[0]?.id ?? "";
  const items = useQuery({ queryKey: ["motion-pack", activePack, q.trim()], queryFn: () => motionApi.pack(activePack, q.trim()), enabled: Boolean(activePack) });
  const own = assets.filter(
    (a) => (a.kind === "image" || a.kind === "gif") && (!q.trim() || `${a.label} ${a.description} ${a.tags}`.toLowerCase().includes(q.trim().toLowerCase())),
  );
  return (
    <div className="lib-panel">
      <input className="input lib-search" placeholder="Поиск мема: drake, кот, стоп…" value={q} onChange={(e) => setQ(e.target.value)} />
      {own.length ? (
        <>
          <div className="lib-sub">Мои файлы</div>
          <div className="lib-sticker-grid lib-meme-grid">
            {own.map((a) => {
              const make = () => imageLayer({ asset_id: String(a.id) }, 760, 3);
              return (
                <button
                  key={a.id}
                  type="button"
                  className="lib-sticker"
                  title={a.label}
                  draggable
                  onDragStart={(ev) => dragPayload(ev, [make()])}
                  onClick={() => onAdd([make()])}
                >
                  <img src={a.url} alt="" loading="lazy" />
                </button>
              );
            })}
          </div>
        </>
      ) : null}
      {activePack ? (
        <>
          <div className="lib-sub">Популярные шаблоны мемов</div>
          <div className="lib-sticker-grid lib-meme-grid">
            {(items.data ?? []).slice(0, 120).map((it) => {
              const make = () => imageLayer({ src: packRef("sticker", activePack, it) }, 820, 3);
              return (
                <button
                  key={it.file}
                  type="button"
                  className="lib-sticker"
                  title={it.name ?? it.file}
                  draggable
                  onDragStart={(ev) => dragPayload(ev, [make()])}
                  onClick={() => onAdd([make()])}
                >
                  <img src={it.url || packFileUrl(activePack, it.file)} alt="" loading="lazy" />
                </button>
              );
            })}
          </div>
        </>
      ) : null}
    </div>
  );
}

/* ---------- генерация и поиск картинок ---------- */

export function GeneratePanel({ onAdd }: { onAdd: AddLayers }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [prompt, setPrompt] = useState("");
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [results, setResults] = useState<AssetSearchResult[]>([]);
  const [made, setMade] = useState<MontageAsset[]>([]);
  const generate = useMutation({
    mutationFn: () => montageAssetsApi.generate(prompt.trim()),
    onSuccess: (asset) => {
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      setMade((prev) => [asset, ...prev]);
      onAdd([imageLayer({ asset_id: String(asset.id) }, 760, 3)]);
      toast.success("Картинка готова и уже на кадре");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось нарисовать"),
  });
  const search = useMutation({
    mutationFn: () => montageAssetsApi.search(query.trim(), 8, freeOnly),
    onSuccess: (r) => {
      setResults(r.results);
      if (!r.results.length) toast.push("Ничего не нашлось — попробуйте иначе", "info");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Поиск не удался"),
  });
  const [adding, setAdding] = useState<string | null>(null);
  const take = async (item: AssetSearchResult) => {
    setAdding(item.url);
    try {
      const asset = await montageAssetsApi.fromUrl(item.url, item.title, item.license ? `Источник: ${item.source} · ${item.license}` : "");
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      onAdd([imageLayer({ asset_id: String(asset.id) }, 760, 3)]);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось скачать");
    } finally {
      setAdding(null);
    }
  };
  return (
    <div className="lib-panel">
      <div className="lib-sub">✨ Сгенерировать картинку</div>
      <textarea
        className="input lib-prompt"
        rows={3}
        placeholder="Что нарисовать: «кот в шапке-ушанке орёт в микрофон, стикер на прозрачном фоне»"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <button type="button" className="btn primary" disabled={generate.isPending || prompt.trim().length < 3} onClick={() => generate.mutate()}>
        {generate.isPending ? "Рисую… (до минуты)" : "✨ Нарисовать и поставить на кадр"}
      </button>
      {made.length ? (
        <div className="lib-sticker-grid">
          {made.map((a) => (
            <button key={a.id} type="button" className="lib-sticker" onClick={() => onAdd([imageLayer({ asset_id: String(a.id) }, 760, 3)])}>
              <img src={a.url} alt="" />
            </button>
          ))}
        </div>
      ) : null}
      <div className="lib-sub">🔎 Найти в интернете</div>
      <div className="lib-row">
        <input
          className="input"
          placeholder="«грустный кот», «стрелка вниз»…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && query.trim() && search.mutate()}
        />
        <button type="button" className="btn" disabled={search.isPending || query.trim().length < 2} onClick={() => search.mutate()}>
          {search.isPending ? "…" : "Искать"}
        </button>
      </div>
      <label className="switch">
        <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} />
        <span className="switch-track" />
        <span>только свободные лицензии</span>
      </label>
      {results.length ? (
        <div className="lib-sticker-grid lib-meme-grid">
          {results.map((r) => (
            <button
              key={r.url}
              type="button"
              className="lib-sticker"
              disabled={adding === r.url}
              title={`${r.title} · ${r.source}${r.license ? ` · ${r.license}` : ""}`}
              onClick={() => void take(r)}
            >
              <img src={`/media/asset-preview?url=${encodeURIComponent(r.url)}`} alt="" loading="lazy" />
              {adding === r.url ? <span className="lib-busy">…</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
