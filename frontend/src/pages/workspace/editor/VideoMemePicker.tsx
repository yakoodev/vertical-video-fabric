import { useDeferredValue, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { videoMemesApi, type VideoMeme } from "@/api/videoMemes";
import { useToast } from "@/components/Toast";
import { MEME_DRAG_TYPE, type MemeMode } from "@/pages/workspace/editor/useVideoMemePlace";
import "@/styles/video-memes.css";

const MODES: { key: MemeMode; label: string; hint: string }[] = [
  { key: "full", label: "Врезка", hint: "Мем во весь кадр со своим звуком, звук клипа на это время выключен" },
  { key: "pip", label: "Окно", hint: "Мем окошком поверх клипа, звук клипа приглушён" },
];
const MAX_TAG_CHIPS = 12;
const LENGTHS: { sec: number; label: string }[] = [
  { sec: 0, label: "любые" },
  { sec: 2, label: "≤ 2 с" },
  { sec: 4, label: "≤ 4 с" },
];

function chipTags(memes: VideoMeme[], suggested: string[], picked: string | null): string[] {
  const count = new Map<string, number>();
  for (const m of memes) for (const t of m.tags) count.set(t, (count.get(t) ?? 0) + 1);
  const order = (t: string) => (suggested.includes(t) ? suggested.indexOf(t) : suggested.length);
  const own = [...count.keys()].sort((a, b) => (count.get(b) ?? 0) - (count.get(a) ?? 0) || order(a) - order(b));
  const list = (own.length ? own : suggested).slice(0, MAX_TAG_CHIPS);
  return picked && !list.includes(picked) ? [picked, ...list] : list;
}

/** Одна карточка: кадр; под курсором мем играет без звука, нажатие — послушать со звуком. */
function MemeCard({
  meme, mode, hearing, onHear, onPlace, busy,
}: {
  meme: VideoMeme;
  mode: MemeMode;
  hearing: boolean;
  onHear: (id: number | null) => void;
  onPlace: (meme: VideoMeme) => void;
  busy: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const ref = useRef<HTMLVideoElement>(null);
  // Другая карточка заговорила — эта замолкает.
  useEffect(() => {
    const v = ref.current;
    if (!v || hearing || v.muted) return;
    v.muted = true;
    v.pause();
    v.currentTime = 0;
  }, [hearing]);
  const approve = useMutation({
    mutationFn: () => videoMemesApi.update(meme.id, { approved: true }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.videoMemes });
      toast.success(`«${meme.title}» одобрен — можно ставить`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось одобрить"),
  });
  const hear = () => {
    const v = ref.current;
    if (!v) return;
    if (hearing) {
      v.muted = true;
      v.pause();
      onHear(null);
      return;
    }
    v.muted = false;
    v.loop = false;
    v.currentTime = 0;
    void v.play().catch(() => undefined);
    onHear(meme.id);
  };
  const said = meme.transcript.trim();
  return (
    <div
      className={`vmp-card${meme.approved ? "" : " is-off"}${hearing ? " is-hearing" : ""}`}
      draggable={meme.approved}
      onDragStart={(e) => {
        e.dataTransfer.setData(MEME_DRAG_TYPE, JSON.stringify({ id: meme.id, mode }));
        e.dataTransfer.effectAllowed = "copy";
      }}
      title={
        meme.approved
          ? `${meme.title}${meme.description ? ` — ${meme.description}` : ""}${meme.when_to_use ? `\nКогда: ${meme.when_to_use}` : ""}\n\nПеретащите на дорожку «Вставки» или «На плейхед»`
          : `${meme.title} — владелец ещё не одобрил: сначала одобрить`
      }
    >
      <button
        type="button"
        className="vmp-media"
        aria-label={hearing ? `Остановить: ${meme.title}` : `Послушать со звуком: ${meme.title}`}
        onClick={hear}
        onMouseEnter={() => {
          const v = ref.current;
          if (v && !hearing) {
            v.loop = true;
            void v.play().catch(() => undefined);
          }
        }}
        onMouseLeave={() => {
          const v = ref.current;
          if (v && !hearing) {
            v.pause();
            v.currentTime = 0;
          }
        }}
      >
        <video
          ref={ref}
          src={meme.url}
          poster={meme.thumb_url}
          muted
          playsInline
          preload="none"
          onEnded={() => {
            if (ref.current) ref.current.muted = true;
            onHear(null);
          }}
        />
        <span className="vmp-dur mono">{meme.duration_sec.toFixed(1)} с</span>
        <span className="vmp-sound" aria-hidden>
          {hearing ? "🔊" : meme.has_audio ? "🔈" : "🔇"}
        </span>
        {!meme.approved ? <span className="vmp-off">не одобрен</span> : null}
      </button>
      <div className="vmp-title">{meme.title || `Мем #${meme.id}`}</div>
      <div className={`vmp-said${said ? "" : " muted"}`}>{said ? `«${said}»` : meme.when_to_use || meme.description || "без слов"}</div>
      {meme.approved ? (
        <button type="button" className="btn sm primary vmp-put" disabled={busy} onClick={() => onPlace(meme)}>
          ⤓ На плейхед
        </button>
      ) : (
        // В студию входит только владелец (кука входа); агент с токеном одобрить не может — сервер ответит 403.
        <button
          type="button"
          className="btn sm vmp-put"
          disabled={approve.isPending}
          onClick={() => approve.mutate()}
          title="Одобряете вы: после этого мем можно ставить руками и ИИ"
        >
          {approve.isPending ? "…" : "✓ Одобрить"}
        </button>
      )}
    </div>
  );
}

/**
 * «Мемы → Видео» в библиотеке студии: поиск реакций по смыслу (GET /api/video-memes), теги, длина; карточку тащат
 * на дорожку «Вставки» или ставят на плейхед. Режим — «Врезка» или «Окно». Неодобренные — серые, внизу.
 */
export function VideoMemePicker({
  onPlace, placing, disabled,
}: {
  onPlace: (memeId: number, mode: MemeMode) => void;
  placing: boolean;
  disabled?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const q = useDeferredValue(typed.trim());
  const [tag, setTag] = useState<string | null>(null);
  const [maxSec, setMaxSec] = useState(0);
  const [mode, setMode] = useState<MemeMode>("full");
  const [hearId, setHearId] = useState<number | null>(null);
  const filter = { q, tag: tag ?? undefined, maxSec: maxSec || undefined };
  const approved = useQuery({
    queryKey: [...qk.videoMemes, "picker", true, filter],
    queryFn: () => videoMemesApi.list({ ...filter, approved: true }),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
  const waiting = useQuery({
    queryKey: [...qk.videoMemes, "picker", false, filter],
    queryFn: () => videoMemesApi.list({ ...filter, approved: false }),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
  // Чипы — теги, что реально есть у мемов (частые первыми); пустая библиотека — подсказки сервера.
  const library = useQuery({
    queryKey: [...qk.videoMemes, "picker", "tags"],
    queryFn: () => videoMemesApi.list({}),
    staleTime: 60_000,
  });
  const ready = approved.data?.memes ?? [];
  const off = waiting.data?.memes ?? [];
  const tags = chipTags(library.data?.memes ?? [], library.data?.suggested_tags ?? [], tag);
  const card = (m: VideoMeme) => (
    <MemeCard
      key={m.id}
      meme={m}
      mode={mode}
      hearing={hearId === m.id}
      onHear={setHearId}
      onPlace={(meme) => onPlace(meme.id, mode)}
      busy={placing || Boolean(disabled)}
    />
  );
  return (
    <div className="lib-panel vmp">
      <div className="vmp-modes" role="radiogroup" aria-label="Как вставить">
        {MODES.map((m) => (
          <button
            key={m.key}
            type="button"
            role="radio"
            aria-checked={mode === m.key}
            className={`vmp-mode${mode === m.key ? " on" : ""}`}
            onClick={() => setMode(m.key)}
            title={m.hint}
          >
            <strong>{m.label}</strong>
            <span>{m.key === "full" ? "весь кадр, звук мема" : "поверх, игра тише"}</span>
          </button>
        ))}
      </div>
      <div className="lib-row">
        <input
          className="input lib-search"
          placeholder="Поиск: фейл, шок, победа…"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          aria-label="Поиск видеомема"
        />
        <select className="input vmp-len" value={maxSec} onChange={(e) => setMaxSec(Number(e.target.value))} aria-label="Длина">
          {LENGTHS.map((l) => (
            <option key={l.sec} value={l.sec}>
              {l.label}
            </option>
          ))}
        </select>
      </div>
      {tags.length ? (
        <div className="lib-cats">
          {tags.map((t) => (
            <button key={t} type="button" className={`lib-cat${tag === t ? " on" : ""}`} onClick={() => setTag(tag === t ? null : t)}>
              #{t}
            </button>
          ))}
        </div>
      ) : null}
      {approved.isError ? <p className="muted vmp-empty">Список не загрузился</p> : null}
      {ready.length ? (
        <div className="vmp-grid">{ready.map(card)}</div>
      ) : approved.isSuccess ? (
        <p className="muted vmp-empty">
          {q || tag || maxSec ? "Ничего не нашлось — другие слова или без тега" : "Одобренных видеомемов пока нет"}
        </p>
      ) : null}
      {off.length ? (
        <>
          <div className="lib-sub">Ждут одобрения · {off.length}</div>
          <div className="vmp-grid">{off.map(card)}</div>
        </>
      ) : null}
      <Link className="btn sm ghost" to="/files?tab=memes" title="Загрузить реакции файлом или по ссылке, описать, одобрить">
        + Видеомемы в библиотеку
      </Link>
    </div>
  );
}

/** Вкладка «Мемы» студии: видеомемы-реакции и картинки (шаблоны и свои файлы) — переключателем сверху. */
export function MemesTab({
  onPlace, placing, disabled, images,
}: {
  onPlace: (memeId: number, mode: MemeMode) => void;
  placing: boolean;
  disabled?: boolean;
  images: React.ReactNode;
}) {
  const [kind, setKind] = useState<"video" | "image">("video");
  return (
    <>
      <div className="lib-cats vmp-kind" role="tablist" aria-label="Вид мемов">
        <button type="button" role="tab" aria-selected={kind === "video"} className={`lib-cat${kind === "video" ? " on" : ""}`} onClick={() => setKind("video")}>
          🎬 Видео-реакции
        </button>
        <button type="button" role="tab" aria-selected={kind === "image"} className={`lib-cat${kind === "image" ? " on" : ""}`} onClick={() => setKind("image")}>
          🖼 Картинки
        </button>
      </div>
      {kind === "video" ? <VideoMemePicker onPlace={onPlace} placing={placing} disabled={disabled} /> : images}
    </>
  );
}
